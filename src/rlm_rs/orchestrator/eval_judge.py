from __future__ import annotations

import math
import re
from typing import Sequence

from langchain_openai import OpenAIEmbeddings as LangChainOpenAIEmbeddings
from openai import APIStatusError, OpenAI
from structlog.stdlib import BoundLogger

from ragas import EvaluationDataset, SingleTurnSample, evaluate
from ragas.llms import llm_factory
# NOTE: ragas 0.4.x `evaluate()` validates metrics against `ragas.metrics.base.Metric`.
# The `ragas.metrics.collections.*` classes are `BaseMetric` and fail that validation.
# Use the Metric-based implementations for now so judge metrics actually run.
from ragas.metrics._answer_relevance import AnswerRelevancy
from ragas.metrics._faithfulness import Faithfulness

from rlm_rs.models import EvaluationJudgeMetrics, EvaluationJudgeScores, SpanLogEntry
from rlm_rs.orchestrator.citations import DocumentText, merge_span_log
from rlm_rs.orchestrator.providers import (
    DEFAULT_OPENAI_BASE_URL,
    DEFAULT_OPENAI_MAX_RETRIES,
    DEFAULT_OPENAI_TIMEOUT_SECONDS,
)
from rlm_rs.settings import Settings


DEFAULT_EVAL_JUDGE_PROVIDER = "openai"

_FAITHFULNESS_CONTEXT_CHUNK_SIZE_CHARS = 8000
_FAITHFULNESS_CONTEXT_CHUNK_OVERLAP_CHARS = 400
_FAITHFULNESS_CONTEXT_MAX_CHUNKS = 12
_FAITHFULNESS_CONTEXT_MAX_TOTAL_CHARS = 120_000
_FAITHFULNESS_CONTEXT_MAX_CHUNK_CHARS = 10_000

_TERM_RE = re.compile(r"[a-zA-Z0-9]{3,}")
_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "are",
        "with",
        "that",
        "this",
        "from",
        "into",
        "your",
        "what",
        "does",
        "about",
        "they",
        "their",
        "have",
        "has",
        "was",
        "were",
        "not",
        "but",
        "you",
        "our",
        "use",
        "used",
        "using",
        "can",
        "will",
        "may",
        "also",
        "than",
        "then",
        "been",
        "over",
        "under",
        "more",
        "most",
        "such",
        "these",
        "those",
        "its",
        "it's",
        "in",
        "on",
        "at",
        "to",
        "of",
        "a",
        "an",
        "as",
        "by",
        "or",
        "is",
        "it",
    }
)


def build_answerer_contexts(
    span_log: Sequence[SpanLogEntry],
    documents: Sequence[DocumentText],
) -> list[str]:
    if not span_log or not documents:
        return []
    doc_lookup = {doc.doc_index: doc.text for doc in documents}
    contexts: list[str] = []
    for span in merge_span_log(span_log):
        text = doc_lookup.get(span.doc_index)
        if text is None:
            continue
        start = max(0, int(span.start_char))
        end = min(len(text), int(span.end_char))
        if end <= start:
            continue
        contexts.append(text[start:end])
    return contexts


def _extract_query_terms(text: str, *, limit: int = 32) -> list[str]:
    if not text:
        return []
    terms: set[str] = set()
    for match in _TERM_RE.findall(text.lower()):
        if match in _STOPWORDS:
            continue
        terms.add(match)
        if len(terms) >= limit:
            break
    return sorted(terms, key=lambda value: (-len(value), value))[:limit]


def _limit_contexts(
    contexts: Sequence[str],
    *,
    max_total_chars: int,
    max_chunks: int,
    max_chunk_chars: int,
) -> list[str]:
    if not contexts or max_total_chars <= 0 or max_chunks <= 0:
        return []
    trimmed: list[str] = []
    total = 0
    for raw in contexts:
        if not raw:
            continue
        chunk = raw[:max_chunk_chars] if max_chunk_chars > 0 else raw
        if not chunk:
            continue
        if total + len(chunk) > max_total_chars and trimmed:
            break
        trimmed.append(chunk)
        total += len(chunk)
        if len(trimmed) >= max_chunks:
            break
    return trimmed


def build_baseline_contexts(
    *,
    question: str,
    answer: str | None,
    documents: Sequence[DocumentText],
    max_total_chars: int = _FAITHFULNESS_CONTEXT_MAX_TOTAL_CHARS,
    max_chunks: int = _FAITHFULNESS_CONTEXT_MAX_CHUNKS,
    chunk_size_chars: int = _FAITHFULNESS_CONTEXT_CHUNK_SIZE_CHARS,
    chunk_overlap_chars: int = _FAITHFULNESS_CONTEXT_CHUNK_OVERLAP_CHARS,
) -> list[str]:
    """Return full baseline document contexts for faithfulness scoring.

    We intentionally do **not** compact or truncate baseline contexts to "make them fit".
    If the full parsed documents exceed the judge model's context window, faithfulness is
    treated as **not computable** (score remains null with an explicit skip reason).
    """

    if not documents:
        return []

    ordered = sorted(documents, key=lambda item: item.doc_index)
    return [doc.text for doc in ordered if doc.text]


def _coerce_score(value: object | None) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(parsed):
        return None
    return parsed


def _uses_max_completion_tokens(model: str | None) -> bool:
    if not model:
        return False
    normalized = model.lower()
    if normalized.startswith("gpt-5"):
        return True
    if normalized.startswith("o") and len(normalized) > 1 and normalized[1].isdigit():
        return True
    return False


def _wants_max_completion_tokens(exc: BaseException) -> bool:
    text = str(exc)
    return "max_completion_tokens" in text and "max_tokens" in text


def _wants_default_temperature(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "temperature" in text and "only the default" in text


def _wants_more_output_tokens(exc: BaseException) -> bool:
    text = str(exc).lower()
    return (
        ("max_tokens" in text or "max_completion_tokens" in text)
        and "length limit" in text
        and "incomplete" in text
    )


def _context_window_exceeded_error(exc: BaseException) -> bool:
    lowered = str(exc).lower()
    if any(
        marker in lowered
        for marker in (
            "maximum context length",
            "context length",
            "context window",
            "please reduce the length of the messages",
        )
    ):
        return True
    if "too many tokens" in lowered and ("requested" in lowered or "maximum" in lowered):
        return True
    return False


def _truncate_for_faithfulness(answer: str, *, max_chars: int = 1800) -> str:
    if len(answer) <= max_chars:
        return answer
    prefix = answer[:max_chars]
    # Prefer cutting at a boundary for stability/readability.
    cutoff = max(prefix.rfind("\n\n"), prefix.rfind("\n"), prefix.rfind(". "))
    if cutoff > max_chars // 2:
        prefix = prefix[:cutoff]
    return prefix.rstrip()


def _patch_openai_chat_completions(client: OpenAI) -> None:
    # Ragas uses instructor (JSON mode) under the hood, which currently sends `max_tokens`
    # for OpenAI chat completions. OpenAI reasoning models (o1/o3/...) and `gpt-5*` require
    # `max_completion_tokens` instead, otherwise the request returns 400 and Ragas silently
    # converts the failure into NaN scores. Patch the client's create() to rewrite/retry.
    original_create = client.chat.completions.create

    def create(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        if args:
            # Preserve any unexpected calling convention.
            return original_create(*args, **kwargs)  # type: ignore[misc]

        payload = dict(kwargs)
        model = payload.get("model")
        if str(model or "").lower().startswith("gpt-5"):
            # For GPT-5 family, minimize reasoning + verbosity for evaluator calls.
            # This helps avoid cases where the model consumes all completion tokens
            # as reasoning tokens and produces no user-visible content.
            payload["reasoning_effort"] = "none"
            payload.setdefault("verbosity", "low")
        max_tokens = payload.get("max_tokens")
        if max_tokens is not None and _uses_max_completion_tokens(str(model or "")):
            payload.pop("max_tokens", None)
            payload["max_completion_tokens"] = max_tokens
        if str(model or "").lower().startswith("gpt-5"):
            max_completion_tokens = payload.get("max_completion_tokens")
            if isinstance(max_completion_tokens, int):
                payload["max_completion_tokens"] = max(max_completion_tokens, 4096)

        try:
            return original_create(**payload)  # type: ignore[arg-type]
        except APIStatusError as exc:
            retry_payload = dict(payload)
            retry = False
            if max_tokens is not None and _wants_max_completion_tokens(exc):
                retry_payload.pop("max_tokens", None)
                retry_payload["max_completion_tokens"] = max_tokens
                retry = True
            if _wants_default_temperature(exc) and "temperature" in retry_payload:
                retry_payload.pop("temperature", None)
                retry = True
            if not retry:
                raise
            return original_create(**retry_payload)  # type: ignore[arg-type]

    client.chat.completions.create = create  # type: ignore[assignment]


def _build_openai_client(settings: Settings) -> OpenAI:
    resolved_timeout = (
        settings.openai_timeout_seconds
        if settings.openai_timeout_seconds is not None
        else DEFAULT_OPENAI_TIMEOUT_SECONDS
    )
    resolved_base_url = (
        settings.openai_base_url.strip()
        if isinstance(settings.openai_base_url, str) and settings.openai_base_url.strip()
        else DEFAULT_OPENAI_BASE_URL
    )
    resolved_retries = (
        settings.openai_max_retries
        if settings.openai_max_retries is not None
        else DEFAULT_OPENAI_MAX_RETRIES
    )
    client = OpenAI(
        api_key=settings.openai_api_key,
        base_url=resolved_base_url,
        timeout=resolved_timeout,
        max_retries=resolved_retries,
    )
    _patch_openai_chat_completions(client)
    return client


def _build_ragas_components(settings: Settings) -> tuple[object, object]:
    provider = settings.eval_judge_provider or DEFAULT_EVAL_JUDGE_PROVIDER
    provider = provider.strip().lower()
    model = settings.eval_judge_model
    if not model:
        raise ValueError("EVAL_JUDGE_MODEL is required")
    if provider != "openai":
        raise ValueError("EVAL_JUDGE_PROVIDER must be openai")
    if not settings.openai_api_key:
        raise ValueError("OPENAI_API_KEY is required for eval judge")
    client = _build_openai_client(settings)
    llm = llm_factory(model, provider=provider, client=client)

    # `AnswerRelevancy` expects a LangChain-style embeddings interface
    # (embed_query / embed_documents), not the ragas BaseRagasEmbedding interface.
    resolved_timeout = (
        settings.openai_timeout_seconds
        if settings.openai_timeout_seconds is not None
        else DEFAULT_OPENAI_TIMEOUT_SECONDS
    )
    resolved_base_url = (
        settings.openai_base_url.strip()
        if isinstance(settings.openai_base_url, str) and settings.openai_base_url.strip()
        else DEFAULT_OPENAI_BASE_URL
    )
    resolved_retries = (
        settings.openai_max_retries
        if settings.openai_max_retries is not None
        else DEFAULT_OPENAI_MAX_RETRIES
    )
    embeddings = LangChainOpenAIEmbeddings(
        model="text-embedding-3-small",
        openai_api_key=settings.openai_api_key,
        openai_api_base=resolved_base_url,
        request_timeout=resolved_timeout,
        max_retries=resolved_retries,
    )
    return llm, embeddings


def _score_answer(
    *,
    question: str,
    answer: str,
    contexts: Sequence[str],
    llm: object,
    embeddings: object,
    logger: BoundLogger | None = None,
    label: str = "answer",
) -> EvaluationJudgeScores | None:
    if not question or not answer:
        return None
    retrieved_contexts = [value for value in (list(contexts) if contexts else []) if value]

    values: dict[str, float | None] = {"answer_relevancy": None, "faithfulness": None}
    skip_reasons: dict[str, str | None] = {"answer_relevancy": None, "faithfulness": None}
    metrics = {
        "answer_relevancy": AnswerRelevancy(llm=llm, embeddings=embeddings),
        "faithfulness": Faithfulness(llm=llm),
    }
    for metric_key, metric in metrics.items():
        metric_answer = answer
        metric_contexts: list[str] = []
        if metric_key == "faithfulness":
            metric_contexts = retrieved_contexts
            # Faithfulness can trigger very large structured outputs when the answer contains many
            # statements. For reasoning models, we can hit the max token limit and get no content.
            # We'll retry with a truncated answer to avoid failing the whole metric.

        try:
            sample = SingleTurnSample(
                user_input=question,
                response=metric_answer,
                retrieved_contexts=metric_contexts,
            )
            dataset = EvaluationDataset(samples=[sample])
            result = evaluate(
                dataset,
                metrics=[metric],
                llm=llm,
                embeddings=embeddings,
                show_progress=False,
                raise_exceptions=True,
            )
            score = result.scores[0].get(metric_key)
        except Exception as exc:  # noqa: BLE001
            score = None
            if metric_key == "faithfulness" and _context_window_exceeded_error(exc):
                skip_reasons["faithfulness"] = "CONTEXT_WINDOW_EXCEEDED"
                if logger is not None:
                    logger.warning(
                        "eval_judge_metric_skipped",
                        metric=metric_key,
                        label=label,
                        reason="CONTEXT_WINDOW_EXCEEDED",
                        error=str(exc),
                    )
            elif metric_key == "faithfulness" and _wants_more_output_tokens(exc):
                truncated = _truncate_for_faithfulness(answer)
                if truncated and truncated != metric_answer:
                    try:
                        sample = SingleTurnSample(
                            user_input=question,
                            response=truncated,
                            retrieved_contexts=metric_contexts,
                        )
                        dataset = EvaluationDataset(samples=[sample])
                        result = evaluate(
                            dataset,
                            metrics=[metric],
                            llm=llm,
                            embeddings=embeddings,
                            show_progress=False,
                            raise_exceptions=True,
                        )
                        score = result.scores[0].get(metric_key)
                    except Exception as retry_exc:  # noqa: BLE001
                        if logger is not None:
                            logger.warning(
                                "eval_judge_metric_failed",
                                metric=metric_key,
                                label=label,
                                error=str(retry_exc),
                            )
                        score = None
                else:
                    if logger is not None:
                        logger.warning(
                            "eval_judge_metric_failed",
                            metric=metric_key,
                            label=label,
                            error=str(exc),
                        )
            else:
                if logger is not None:
                    logger.warning(
                        "eval_judge_metric_failed",
                        metric=metric_key,
                        label=label,
                        error=str(exc),
                    )
        values[metric_key] = _coerce_score(score)

    return EvaluationJudgeScores(
        answer_relevancy=values["answer_relevancy"],
        faithfulness=values["faithfulness"],
        faithfulness_skip_reason=skip_reasons["faithfulness"],
    )


def evaluate_judge(
    *,
    question: str,
    answer: str | None,
    answerer_contexts: Sequence[str],
    baseline_answer: str | None,
    baseline_contexts: Sequence[str],
    settings: Settings,
    logger: BoundLogger | None = None,
) -> EvaluationJudgeMetrics | None:
    if not settings.enable_eval_judge:
        return None
    try:
        llm, embeddings = _build_ragas_components(settings)
    except ValueError as exc:
        if logger is not None:
            logger.warning("eval_judge_config_invalid", error=str(exc))
        return None

    answerer_scores: EvaluationJudgeScores | None = None
    if answer:
        try:
            answerer_scores = _score_answer(
                question=question,
                answer=answer,
                contexts=answerer_contexts,
                llm=llm,
                embeddings=embeddings,
                logger=logger,
                label="answerer",
            )
        except Exception as exc:  # noqa: BLE001
            if logger is not None:
                logger.warning("eval_judge_answerer_failed", error=str(exc))

    baseline_scores: EvaluationJudgeScores | None = None
    if baseline_answer:
        try:
            baseline_scores = _score_answer(
                question=question,
                answer=baseline_answer,
                contexts=baseline_contexts,
                llm=llm,
                embeddings=embeddings,
                logger=logger,
                label="baseline",
            )
        except Exception as exc:  # noqa: BLE001
            if logger is not None:
                logger.warning("eval_judge_baseline_failed", error=str(exc))

    def _has_any_value(scores: EvaluationJudgeScores | None) -> bool:
        if scores is None:
            return False
        return scores.answer_relevancy is not None or scores.faithfulness is not None

    if not _has_any_value(answerer_scores):
        answerer_scores = None
    if not _has_any_value(baseline_scores):
        baseline_scores = None

    if answerer_scores is None and baseline_scores is None:
        return None
    return EvaluationJudgeMetrics(answerer=answerer_scores, baseline=baseline_scores)
