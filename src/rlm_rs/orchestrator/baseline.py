from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Literal, Mapping, Sequence, TypeAlias
from urllib.parse import urlparse

from botocore.client import BaseClient

from rlm_rs.settings import Settings
from rlm_rs.storage import s3
from rlm_rs.orchestrator.providers import (
    AZURE_OPENAI_PROVIDER_NAME,
    OPENAI_PROVIDER_NAME,
    _uses_max_completion_tokens,
    _wants_max_completion_tokens,
    build_openai_client,
)

BaselineSkipReason: TypeAlias = Literal[
    "RUNTIME_MODE",
    "MISSING_PARSED_TEXT",
    "UNKNOWN_CONTEXT_WINDOW",
    "CONTEXT_WINDOW_EXCEEDED",
]

_OUTPUT_TOKEN_PROBES = (1, 16, 64)


@dataclass(frozen=True)
class BaselineCheckResult:
    prompt: str | None
    input_tokens: int | None
    context_window: int | None
    skip_reason: BaselineSkipReason | None


def _sorted_documents(
    documents: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    return sorted(documents, key=lambda item: int(item.get("doc_index", 0)))


def _split_s3_uri(uri: str) -> tuple[str, str]:
    parsed = urlparse(uri)
    if parsed.scheme != "s3" or not parsed.netloc:
        raise ValueError(f"Invalid S3 URI: {uri}")
    return parsed.netloc, parsed.path.lstrip("/")


def _documents_have_parsed_text(documents: Sequence[Mapping[str, Any]]) -> bool:
    if not documents:
        return False
    for item in documents:
        if item.get("ingest_status") not in {"PARSED", "INDEXING", "INDEXED"}:
            return False
        text_s3_uri = item.get("text_s3_uri")
        if not isinstance(text_s3_uri, str) or not text_s3_uri:
            return False
    return True


def build_baseline_prompt(
    documents: Sequence[Mapping[str, Any]],
    s3_client: BaseClient,
) -> str:
    texts: list[str] = []
    for item in _sorted_documents(documents):
        text_s3_uri = item.get("text_s3_uri")
        if not isinstance(text_s3_uri, str) or not text_s3_uri:
            raise ValueError("Missing text_s3_uri for baseline prompt")
        bucket, key = _split_s3_uri(text_s3_uri)
        payload = s3.get_bytes(s3_client, bucket, key)
        texts.append(payload.decode("utf-8"))
    return "\n\n".join(texts)


def build_baseline_answer_prompt(document_text: str, question: str) -> str:
    return f"{document_text}\n\nQuestion: {question}\nAnswer:"


def _context_window_for_model(settings: Settings, model: str | None) -> int | None:
    if not model:
        return None
    mapping = settings.model_context_windows_json
    if not isinstance(mapping, dict):
        return None
    if model in mapping:
        value = mapping.get(model)
    else:
        normalized = model.lower()
        normalized_mapping = {
            str(key).lower(): value
            for key, value in mapping.items()
            if isinstance(key, str)
        }
        value = normalized_mapping.get(normalized)
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    if parsed <= 0:
        return None
    return parsed


def _extract_input_tokens(response: Any) -> int:
    usage = getattr(response, "usage", None)
    if isinstance(usage, dict):
        value = usage.get("input_tokens")
    else:
        value = getattr(usage, "input_tokens", None)
    if value is None:
        raise ValueError("OpenAI response missing input_tokens")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("OpenAI response input_tokens is invalid") from exc


def _extract_prompt_tokens(response: Any) -> int:
    usage = getattr(response, "usage", None)
    if isinstance(usage, dict):
        value = usage.get("prompt_tokens")
    else:
        value = getattr(usage, "prompt_tokens", None)
    if value is None:
        raise ValueError("OpenAI response missing prompt_tokens")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("OpenAI response prompt_tokens is invalid") from exc


def _output_limit_error(exc: Exception) -> bool:
    lowered = str(exc).lower()
    if "output limit" not in lowered:
        return False
    return any(
        token in lowered for token in ("max_tokens", "max_completion_tokens", "max_output_tokens")
    )


def _build_openai_client(settings: Settings) -> Any:
    provider_name = (settings.llm_provider or OPENAI_PROVIDER_NAME).strip().lower()
    if provider_name != AZURE_OPENAI_PROVIDER_NAME:
        provider_name = OPENAI_PROVIDER_NAME
    return build_openai_client(
        provider_name=provider_name,
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
        api_version=settings.openai_api_version,
        timeout_seconds=settings.openai_timeout_seconds,
        max_retries=0,
    )


def _count_input_tokens_chat(
    *,
    prompt: str,
    model: str,
    client: Any,
) -> int:
    last_exc: Exception | None = None
    for output_tokens in _OUTPUT_TOKEN_PROBES:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
        }
        if _uses_max_completion_tokens(model):
            payload["max_completion_tokens"] = output_tokens
        else:
            payload["max_tokens"] = output_tokens
        try:
            response = client.chat.completions.create(**payload)
        except Exception as exc:  # noqa: BLE001
            if "max_tokens" in payload and _wants_max_completion_tokens(exc):
                retry_payload = dict(payload)
                retry_payload.pop("max_tokens", None)
                retry_payload["max_completion_tokens"] = output_tokens
                try:
                    response = client.chat.completions.create(**retry_payload)
                except Exception as retry_exc:  # noqa: BLE001
                    if _output_limit_error(retry_exc):
                        last_exc = retry_exc
                        continue
                    raise
            else:
                if _output_limit_error(exc):
                    last_exc = exc
                    continue
                raise
        return _extract_prompt_tokens(response)
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("OpenAI token probe failed")


def _count_input_tokens_responses(
    *,
    prompt: str,
    model: str,
    client: Any,
) -> int:
    last_exc: Exception | None = None
    for output_tokens in _OUTPUT_TOKEN_PROBES:
        try:
            response = client.responses.create(
                model=model,
                input=prompt,
                max_output_tokens=output_tokens,
            )
        except Exception as exc:  # noqa: BLE001
            if _output_limit_error(exc):
                last_exc = exc
                continue
            raise
        return _extract_input_tokens(response)
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("OpenAI token probe failed")


def _count_input_tokens(
    *,
    prompt: str,
    model: str,
    client: Any,
) -> int:
    try:
        return _count_input_tokens_chat(prompt=prompt, model=model, client=client)
    except Exception as exc:  # noqa: BLE001
        try:
            return _count_input_tokens_responses(
                prompt=prompt,
                model=model,
                client=client,
            )
        except Exception:  # noqa: BLE001
            raise exc


def _context_window_exceeded_error(exc: Exception) -> tuple[bool, int | None]:
    if _output_limit_error(exc):
        return True, None
    message = str(exc)
    lowered = message.lower()
    if "context length" not in lowered and "context window" not in lowered:
        return False, None
    token_match = re.search(r"(?:resulted in|requested) (\d+) tokens", lowered)
    if token_match:
        try:
            return True, int(token_match.group(1))
        except (TypeError, ValueError):
            return True, None
    return True, None


def prepare_baseline_prompt(
    *,
    mode: str,
    model: str | None,
    question: str,
    documents: Sequence[Mapping[str, Any]],
    s3_client: BaseClient,
    settings: Settings,
    openai_client: Any | None = None,
) -> BaselineCheckResult:
    if mode != "ANSWERER":
        return BaselineCheckResult(
            prompt=None,
            input_tokens=None,
            context_window=None,
            skip_reason="RUNTIME_MODE",
        )
    if not _documents_have_parsed_text(documents):
        return BaselineCheckResult(
            prompt=None,
            input_tokens=None,
            context_window=None,
            skip_reason="MISSING_PARSED_TEXT",
        )
    context_window = _context_window_for_model(settings, model)
    if context_window is None:
        return BaselineCheckResult(
            prompt=None,
            input_tokens=None,
            context_window=None,
            skip_reason="UNKNOWN_CONTEXT_WINDOW",
        )
    document_text = build_baseline_prompt(documents, s3_client)
    prompt = build_baseline_answer_prompt(document_text, question)
    client = openai_client or _build_openai_client(settings)
    try:
        input_tokens = _count_input_tokens(prompt=prompt, model=model or "", client=client)
    except Exception as exc:  # noqa: BLE001
        exceeded, requested_tokens = _context_window_exceeded_error(exc)
        if exceeded:
            return BaselineCheckResult(
                prompt=None,
                input_tokens=requested_tokens,
                context_window=context_window,
                skip_reason="CONTEXT_WINDOW_EXCEEDED",
            )
        raise
    if input_tokens > context_window:
        return BaselineCheckResult(
            prompt=None,
            input_tokens=input_tokens,
            context_window=context_window,
            skip_reason="CONTEXT_WINDOW_EXCEEDED",
        )
    return BaselineCheckResult(
        prompt=prompt,
        input_tokens=input_tokens,
        context_window=context_window,
        skip_reason=None,
    )
