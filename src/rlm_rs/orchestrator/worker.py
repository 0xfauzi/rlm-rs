from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse
from uuid import uuid4

from boto3.dynamodb.conditions import Attr, Key
from boto3.resources.base import ServiceResource
from botocore.client import BaseClient
from pydantic import JsonValue
from structlog.stdlib import BoundLogger

from rlm_rs import code_log
from rlm_rs.finetune.traces import TraceCollector, persist_trace_artifact
from rlm_rs.errors import ErrorCode
from rlm_rs.logging import get_logger
from rlm_rs.models import (
    Budgets,
    BudgetsConsumed,
    ContextDocument,
    ContextManifest,
    ContextItem,
    LimitsSnapshot,
    LLMToolResult,
    ModelsConfig,
    SearchToolResult,
    SpanLogEntry,
    StepEvent,
    StepResult,
    ToolRequestsEnvelope,
    ToolResultsEnvelope,
)
from rlm_rs.orchestrator import baseline as baseline_eval
from rlm_rs.orchestrator import eval_judge
from rlm_rs.orchestrator.citations import (
    DocumentText,
    build_span_ref,
    make_spanrefs,
)
from rlm_rs.orchestrator.providers import (
    AZURE_OPENAI_PROVIDER_NAME,
    OPENAI_PROVIDER_NAME,
    FakeLLMProvider,
    LLMProvider,
    OpenAIProvider,
)
from rlm_rs.orchestrator.root_prompt import (
    build_root_prompt,
    parse_root_output,
    root_prompt_version,
)
from rlm_rs.search.backends import (
    CachedSearchBackend,
    FakeSearchBackend,
    S3SearchCache,
    SearchBackend,
    build_error_meta,
    search_disabled_error_meta,
)
from rlm_rs.settings import Settings
from rlm_rs.sandbox.runner import SandboxRunner, build_sandbox_runner
from rlm_rs.sandbox.tool_api import build_tool_schema
from rlm_rs.storage import contexts as contexts_store
from rlm_rs.storage import ddb, s3, state as state_store
from rlm_rs.storage.ddb import DdbTableNames, build_ddb_resource, build_table_names
from rlm_rs.storage.s3 import build_s3_client


class BudgetExceededError(RuntimeError):
    def __init__(self, status: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass
class BudgetTracker:
    budgets: Budgets | None
    start_time: float
    turns: int = 0
    llm_subcalls: int = 0
    total_prompt_chars: int = 0

    def elapsed_seconds(self) -> int:
        return int(time.monotonic() - self.start_time)

    def over_max_turns(self) -> bool:
        if self.budgets is None or self.budgets.max_turns is None:
            return False
        return self.turns >= self.budgets.max_turns

    def over_total_seconds(self) -> bool:
        if self.budgets is None or self.budgets.max_total_seconds is None:
            return False
        return self.elapsed_seconds() > self.budgets.max_total_seconds

    def can_accept_prompt(self, prompt_len: int) -> bool:
        if self.budgets is None:
            return True
        max_prompt = self.budgets.max_llm_prompt_chars
        if max_prompt is not None and prompt_len > max_prompt:
            return False
        max_total = self.budgets.max_total_llm_prompt_chars
        if max_total is not None and self.total_prompt_chars + prompt_len > max_total:
            return False
        return True

    def can_accept_subcalls(self, count: int) -> bool:
        if self.budgets is None:
            return True
        max_subcalls = self.budgets.max_llm_subcalls
        if max_subcalls is None:
            return True
        return self.llm_subcalls + count <= max_subcalls

    def record_prompt(self, prompt_len: int) -> None:
        self.total_prompt_chars += prompt_len

    def record_subcalls(self, count: int) -> None:
        self.llm_subcalls += count

    def record_turn(self) -> None:
        self.turns += 1

    def snapshot(self) -> dict[str, JsonValue] | None:
        if self.budgets is None:
            return None
        limits = self.budgets.model_dump(exclude_none=True)
        consumed: dict[str, JsonValue] = {
            "turns": self.turns,
            "llm_subcalls": self.llm_subcalls,
            "total_seconds": self.elapsed_seconds(),
            "total_prompt_chars": self.total_prompt_chars,
        }
        remaining: dict[str, JsonValue] = {}
        if self.budgets.max_turns is not None:
            remaining["turns"] = max(self.budgets.max_turns - self.turns, 0)
        if self.budgets.max_llm_subcalls is not None:
            remaining["llm_subcalls"] = max(
                self.budgets.max_llm_subcalls - self.llm_subcalls, 0
            )
        if self.budgets.max_total_seconds is not None:
            remaining["total_seconds"] = max(
                self.budgets.max_total_seconds - self.elapsed_seconds(), 0
            )
        if self.budgets.max_total_llm_prompt_chars is not None:
            remaining["total_prompt_chars"] = max(
                self.budgets.max_total_llm_prompt_chars - self.total_prompt_chars,
                0,
            )
        return {
            "limits": limits,
            "consumed": consumed,
            "remaining": remaining,
        }


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _format_timestamp(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _elapsed_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _resolve_models(
    execution_item: Mapping[str, Any],
    session_item: Mapping[str, Any],
    settings: Settings,
) -> ModelsConfig | None:
    models = execution_item.get("models")
    if models:
        return ModelsConfig.model_validate(models)
    session_default = session_item.get("models_default")
    if session_default:
        return ModelsConfig.model_validate(session_default)
    if settings.default_models_json is not None:
        return ModelsConfig.model_validate(settings.default_models_json)
    if settings.default_root_model or settings.default_sub_model:
        return ModelsConfig(
            root_model=settings.default_root_model,
            sub_model=settings.default_sub_model,
        )
    return None


def _resolve_budgets(
    execution_item: Mapping[str, Any],
    session_item: Mapping[str, Any],
    settings: Settings,
) -> Budgets | None:
    budgets = execution_item.get("budgets_requested")
    if budgets:
        return Budgets.model_validate(budgets)
    session_default = session_item.get("budgets_default")
    if session_default:
        return Budgets.model_validate(session_default)
    if settings.default_budgets_json is None:
        return None
    return Budgets.model_validate(settings.default_budgets_json)


def _resolve_output_mode(execution_item: Mapping[str, Any]) -> str:
    options = execution_item.get("options")
    if isinstance(options, dict):
        output_mode = options.get("output_mode")
        if output_mode in ("ANSWER", "CONTEXTS"):
            return output_mode
    return "ANSWER"


def _limits_from_budgets(budgets: Budgets | None) -> LimitsSnapshot | None:
    if budgets is None:
        return None
    return LimitsSnapshot(
        max_step_seconds=budgets.max_step_seconds,
        max_spans_per_step=budgets.max_spans_per_step,
        max_tool_requests_per_step=budgets.max_tool_requests_per_step,
        max_stdout_chars=budgets.max_stdout_chars,
        max_state_chars=budgets.max_state_chars,
    )


def _scan_executions(table: Any) -> list[dict[str, Any]]:
    response = table.scan(
        FilterExpression=Attr("status").eq("RUNNING") & Attr("mode").eq("ANSWERER")
    )
    items = list(response.get("Items", []))
    while response.get("LastEvaluatedKey"):
        response = table.scan(
            FilterExpression=Attr("status").eq("RUNNING")
            & Attr("mode").eq("ANSWERER"),
            ExclusiveStartKey=response["LastEvaluatedKey"],
        )
        items.extend(response.get("Items", []))
    return items


def _scan_execution_items(table: Any, execution_id: str) -> list[dict[str, Any]]:
    target_sk = f"{ddb.EXECUTION_SK_PREFIX}{execution_id}"
    items: list[dict[str, Any]] = []
    response = table.scan()
    items.extend(response.get("Items", []))
    while response.get("LastEvaluatedKey"):
        response = table.scan(ExclusiveStartKey=response["LastEvaluatedKey"])
        items.extend(response.get("Items", []))
    return [item for item in items if item.get("SK") == target_sk]


def _load_execution_span_log(table: Any, execution_id: str) -> list[SpanLogEntry]:
    items = ddb.list_execution_state_steps(table, execution_id=execution_id)
    items.sort(key=lambda item: int(item.get("turn_index") or 0))
    span_log: list[SpanLogEntry] = []
    for item in items:
        raw = item.get("span_log")
        if not isinstance(raw, list):
            continue
        for entry in raw:
            try:
                span_log.append(SpanLogEntry.model_validate(entry))
            except Exception:  # noqa: BLE001
                continue
    return span_log


def _query_documents(table: Any, session_id: str) -> list[dict[str, Any]]:
    pk = f"{ddb.DOCUMENT_PK_PREFIX}{session_id}"
    response = table.query(KeyConditionExpression=Key("PK").eq(pk))
    items = list(response.get("Items", []))
    while response.get("LastEvaluatedKey"):
        response = table.query(
            KeyConditionExpression=Key("PK").eq(pk),
            ExclusiveStartKey=response["LastEvaluatedKey"],
        )
        items.extend(response.get("Items", []))
    return items


def _sorted_documents(docs: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return sorted(docs, key=lambda item: int(item.get("doc_index", 0)))


def _doc_indexes(docs: Sequence[Mapping[str, Any]]) -> list[int]:
    return [int(item.get("doc_index", 0)) for item in _sorted_documents(docs)]


def _build_context_manifest(docs: Sequence[Mapping[str, Any]]) -> ContextManifest:
    manifest_docs: list[ContextDocument] = []
    for item in _sorted_documents(docs):
        text_s3_uri = item.get("text_s3_uri")
        offsets_s3_uri = item.get("offsets_s3_uri")
        if not text_s3_uri or not offsets_s3_uri:
            raise ValueError("Session not ready")
        manifest_docs.append(
            ContextDocument(
                doc_id=str(item["doc_id"]),
                doc_index=int(item["doc_index"]),
                text_s3_uri=str(text_s3_uri),
                meta_s3_uri=item.get("meta_s3_uri"),
                offsets_s3_uri=str(offsets_s3_uri),
            )
        )
    return ContextManifest(docs=manifest_docs)


def _split_s3_uri(uri: str) -> tuple[str, str]:
    parsed = urlparse(uri)
    if parsed.scheme != "s3" or not parsed.netloc:
        raise ValueError(f"Invalid S3 URI: {uri}")
    return parsed.netloc, parsed.path.lstrip("/")


def _doc_lengths(
    docs: Sequence[Mapping[str, Any]],
    s3_client: BaseClient,
) -> list[int]:
    lengths: list[int] = []
    for item in _sorted_documents(docs):
        raw_length = item.get("char_length")
        if raw_length is not None:
            lengths.append(int(raw_length))
            continue
        offsets_uri = item.get("offsets_s3_uri")
        if offsets_uri:
            bucket, key = _split_s3_uri(str(offsets_uri))
            payload = s3.get_json(s3_client, bucket, key)
            if isinstance(payload, dict) and payload.get("char_length") is not None:
                lengths.append(int(payload["char_length"]))
                continue
        text_uri = item.get("text_s3_uri")
        if text_uri:
            bucket, key = _split_s3_uri(str(text_uri))
            payload = s3.get_bytes(s3_client, bucket, key)
            lengths.append(len(payload.decode("utf-8")))
            continue
        lengths.append(0)
    return lengths


def _load_state_payload(
    state_item: Mapping[str, Any],
    *,
    s3_client: BaseClient,
) -> JsonValue | None:
    state_json = state_item.get("state_json")
    state_s3_uri = state_item.get("state_s3_uri")
    if state_s3_uri:
        bucket, key = _split_s3_uri(str(state_s3_uri))
        state_json = s3.get_gzip_json(s3_client, bucket, key)
    state_json = state_store.normalize_json_value(state_json)
    state_store.validate_state_payload(state_json)
    return state_json


def _ensure_tool_state(state: dict[str, JsonValue]) -> None:
    tool_results = state.get("_tool_results")
    if tool_results is None:
        tool_results = {"llm": {}, "search": {}}
        state["_tool_results"] = tool_results
    if not isinstance(tool_results, dict):
        raise state_store.StateValidationError("_tool_results must be an object.")
    for key in ("llm", "search"):
        bucket = tool_results.get(key)
        if bucket is None:
            tool_results[key] = {}
        elif not isinstance(bucket, dict):
            raise state_store.StateValidationError(f"_tool_results.{key} must be an object.")
    tool_status = state.get("_tool_status")
    if tool_status is None:
        state["_tool_status"] = {}
    elif not isinstance(tool_status, dict):
        raise state_store.StateValidationError("_tool_status must be an object.")


def _search_cache_prefix(config: JsonValue | None) -> str:
    if isinstance(config, dict):
        prefix = config.get("cache_prefix")
        if isinstance(prefix, str) and prefix.strip():
            return prefix.strip().strip("/")
    return "cache"


def _merge_reserved_state(
    state: dict[str, JsonValue],
    reserved: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    merged = dict(state)
    for key in ("_tool_results", "_tool_status", "_tool_schema", "_budgets", "_trace"):
        if key in reserved:
            merged[key] = reserved[key]
    return merged


def _tool_results_from_state(state: JsonValue | None) -> ToolResultsEnvelope | None:
    if not isinstance(state, dict):
        return None
    raw = state.get("_tool_results")
    if raw is None:
        return None
    try:
        return ToolResultsEnvelope.model_validate(raw)
    except Exception:  # noqa: BLE001
        return None


def _step_snapshot(
    result: StepResult,
    *,
    timings: Mapping[str, JsonValue] | None = None,
) -> dict[str, Any]:
    tool_requests_payload = (
        result.tool_requests or ToolRequestsEnvelope()
    ).model_dump(exclude_none=True)
    span_log_payload = [entry.model_dump(exclude_none=True) for entry in result.span_log]
    final_payload = result.final.model_dump(exclude_none=True) if result.final else None
    error_payload = result.error.model_dump(exclude_none=True) if result.error else None
    return {
        "success": result.success,
        "stdout": result.stdout,
        "span_log": span_log_payload,
        "tool_requests": tool_requests_payload,
        "final": final_payload,
        "error": error_payload,
        "timings": dict(timings or {}),
    }


def _format_step_error(error: dict[str, Any] | None) -> str | None:
    if not error:
        return None
    code = error.get("code")
    message = error.get("message")
    if code and message:
        return f"{code}: {message}"
    return str(error)


def _load_documents_text(
    docs: Sequence[Mapping[str, Any]],
    s3_client: BaseClient,
) -> list[DocumentText]:
    documents: list[DocumentText] = []
    for item in _sorted_documents(docs):
        text_s3_uri = item.get("text_s3_uri")
        if not text_s3_uri:
            raise ValueError("Missing text_s3_uri")
        source_name = item.get("source_name")
        mime_type = item.get("mime_type")
        if source_name is None or mime_type is None:
            raise ValueError("Missing source_name or mime_type")
        bucket, key = _split_s3_uri(str(text_s3_uri))
        payload = s3.get_bytes(s3_client, bucket, key)
        documents.append(
            DocumentText(
                doc_id=str(item["doc_id"]),
                doc_index=int(item["doc_index"]),
                text=payload.decode("utf-8"),
                source_name=str(source_name),
                mime_type=str(mime_type),
            )
        )
    return documents


def _is_context_tag(tag: str | None) -> bool:
    if tag == "context":
        return True
    return bool(tag) and tag.startswith("context:")


@dataclass(frozen=True)
class ContextSpanEntry:
    turn_index: int
    span_index: int
    span: SpanLogEntry


def _build_contexts_and_citations(
    *,
    span_log: Sequence[ContextSpanEntry],
    documents: Sequence[DocumentText],
    tenant_id: str,
    session_id: str,
) -> tuple[list[dict[str, JsonValue]], list[dict[str, JsonValue]]]:
    doc_lookup = {doc.doc_index: doc for doc in documents}
    contexts: list[dict[str, JsonValue]] = []
    citations_payload: list[dict[str, JsonValue]] = []
    seen: set[tuple[int, int, int]] = set()
    sequence_index = 0
    for span in span_log:
        entry = span.span
        if not _is_context_tag(entry.tag):
            continue
        key = (entry.doc_index, entry.start_char, entry.end_char)
        if key in seen:
            continue
        seen.add(key)
        document = doc_lookup.get(entry.doc_index)
        if document is None:
            raise KeyError(f"Missing document for doc_index={entry.doc_index}")
        start_char = int(entry.start_char)
        end_char = int(entry.end_char)
        if end_char <= start_char:
            continue
        text = document.text[start_char:end_char]
        ref = build_span_ref(
            tenant_id=tenant_id,
            session_id=session_id,
            doc_id=document.doc_id,
            doc_index=document.doc_index,
            start_char=start_char,
            end_char=end_char,
            text=document.text,
        )
        context_item = ContextItem(
            sequence_index=sequence_index,
            turn_index=span.turn_index,
            span_index=span.span_index,
            tag=str(entry.tag),
            text=text,
            text_char_length=len(text),
            source_name=str(document.source_name),
            mime_type=str(document.mime_type),
            ref=ref,
        )
        contexts.append(context_item.model_dump(exclude_none=True))
        citations_payload.append(ref.model_dump(exclude_none=True))
        sequence_index += 1
    return contexts, citations_payload


def _pre_step_state(state_item: Mapping[str, Any]) -> bool:
    return not any(
        key in state_item
        for key in ("success", "stdout", "tool_requests", "final", "error")
    )


def _next_turn_index(state_item: Mapping[str, Any]) -> int:
    current = int(state_item.get("turn_index", -1))
    if _pre_step_state(state_item):
        return current
    return current + 1


def _budget_tracker_from_state(
    state: JsonValue | None,
    budgets: Budgets | None,
) -> BudgetTracker:
    now = time.monotonic()
    tracker = BudgetTracker(budgets=budgets, start_time=now)
    if isinstance(state, dict):
        raw = state.get("_budgets")
        if isinstance(raw, dict):
            consumed = raw.get("consumed")
            if isinstance(consumed, dict):
                tracker.turns = int(consumed.get("turns") or 0)
                tracker.llm_subcalls = int(consumed.get("llm_subcalls") or 0)
                tracker.total_prompt_chars = int(consumed.get("total_prompt_chars") or 0)
                total_seconds = consumed.get("total_seconds")
                if total_seconds is not None:
                    tracker.start_time = now - max(0, int(total_seconds))
    return tracker


def _apply_tool_results(
    state: dict[str, JsonValue],
    tool_results: ToolResultsEnvelope,
    statuses: dict[str, str],
) -> None:
    _ensure_tool_state(state)
    tool_results_state = state["_tool_results"]
    tool_status_state = state["_tool_status"]
    if not isinstance(tool_results_state, dict) or not isinstance(tool_status_state, dict):
        raise state_store.StateValidationError("Tool state is invalid")
    llm_bucket = tool_results_state.setdefault("llm", {})
    search_bucket = tool_results_state.setdefault("search", {})
    if not isinstance(llm_bucket, dict) or not isinstance(search_bucket, dict):
        raise state_store.StateValidationError("Tool state is invalid")
    for key, result in tool_results.llm.items():
        llm_bucket[key] = result.model_dump(exclude_none=True)
    for key, result in tool_results.search.items():
        search_bucket[key] = result.model_dump(exclude_none=True)
    for key, status in statuses.items():
        tool_status_state[key] = status


def _build_state_summary(state: JsonValue | None, *, max_keys: int = 50) -> JsonValue | None:
    if not isinstance(state, dict):
        return None

    def _limit(keys: list[str]) -> list[str]:
        return sorted(keys)[:max_keys]

    def _safe_count(value: JsonValue) -> int | None:
        if isinstance(value, (list, dict, tuple, set)):
            return len(value)
        return None

    summary: dict[str, JsonValue] = {"state_keys": _limit([str(k) for k in state.keys()])}

    work = state.get("work")
    if isinstance(work, dict):
        work_keys = _limit([str(k) for k in work.keys()])
        summary["work_keys"] = work_keys
        work_counts: dict[str, JsonValue] = {}
        for key in work_keys:
            value = work.get(key)
            count = _safe_count(value)
            if count is not None:
                work_counts[key] = count
        if work_counts:
            summary["work_counts"] = work_counts

    tool_results = state.get("_tool_results")
    if isinstance(tool_results, dict):
        llm_bucket = tool_results.get("llm")
        search_bucket = tool_results.get("search")
        if isinstance(llm_bucket, dict):
            summary["tool_llm_keys"] = _limit([str(k) for k in llm_bucket.keys()])
        if isinstance(search_bucket, dict):
            summary["tool_search_keys"] = _limit([str(k) for k in search_bucket.keys()])

    return summary


def _normalize_blank_lines(lines: list[str]) -> str:
    cleaned: list[str] = []
    prev_blank = False
    for line in lines:
        is_blank = not line.strip()
        if is_blank and prev_blank:
            continue
        cleaned.append(line)
        prev_blank = is_blank
    while cleaned and not cleaned[0].strip():
        cleaned.pop(0)
    while cleaned and not cleaned[-1].strip():
        cleaned.pop()
    return "\n".join(cleaned)


def _sanitize_final_answer(answer: str) -> str:
    if not answer:
        return answer
    lines = answer.splitlines()
    header_prefix = "supporting points mentioned in the document"
    header_index = None
    for idx, line in enumerate(lines):
        if line.strip().lower().startswith(header_prefix):
            header_index = idx
            break
    if header_index is None:
        return answer
    end = header_index + 1
    while end < len(lines):
        stripped = lines[end].strip()
        if stripped.startswith(("-", "•")) or not stripped:
            end += 1
            continue
        break
    new_lines = lines[:header_index] + lines[end:]
    return _normalize_blank_lines(new_lines)


def _resolve_tool_requests(
    requests: ToolRequestsEnvelope,
    *,
    tenant_id: str,
    session_id: str,
    provider: LLMProvider,
    tracker: BudgetTracker,
    model: str | None,
    enable_search: bool,
    search_backend: SearchBackend,
    doc_indexes: Sequence[int],
    doc_lengths: Sequence[int],
    code_logger: code_log.CodeLogWriter | None = None,
    max_concurrency: int = 4,
) -> tuple[ToolResultsEnvelope, dict[str, str]]:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    results = ToolResultsEnvelope()
    statuses: dict[str, str] = {}

    max_concurrency = max(1, int(max_concurrency or 1))

    llm_requests = list(requests.llm)
    for request in llm_requests:
        prompt_len = len(request.prompt)
        if not tracker.can_accept_prompt(prompt_len):
            raise BudgetExceededError("BUDGET_EXCEEDED", "LLM prompt budget exceeded")
        if not tracker.can_accept_subcalls(1):
            raise BudgetExceededError("BUDGET_EXCEEDED", "LLM subcall budget exceeded")
        tracker.record_prompt(prompt_len)
        tracker.record_subcalls(1)

    search_requests = list(requests.search)
    if not llm_requests and not search_requests:
        return results, statuses

    def _run_llm(request: Any) -> str:
        return provider.complete_subcall(
            request.prompt,
            model,
            request.max_tokens,
            request.temperature,
            tenant_id=tenant_id,
        )

    def _run_search(request: Any) -> list[JsonValue]:
        return search_backend.search(
            tenant_id=tenant_id,
            session_id=session_id,
            request=request,
            doc_indexes=doc_indexes,
            doc_lengths=doc_lengths,
        )

    with ThreadPoolExecutor(max_workers=max_concurrency) as executor:
        future_map: dict[Any, tuple[str, Any]] = {}
        for request in llm_requests:
            future = executor.submit(_run_llm, request)
            future_map[future] = ("llm", request)
        for request in search_requests:
            if not enable_search:
                results.search[request.key] = SearchToolResult(
                    hits=[],
                    meta=search_disabled_error_meta(),
                )
                statuses[request.key] = "error"
                continue
            future = executor.submit(_run_search, request)
            future_map[future] = ("search", request)

        for future in as_completed(future_map):
            tool_type, request = future_map[future]
            try:
                payload = future.result()
                if tool_type == "llm":
                    text = str(payload)
                    if code_logger is not None:
                        repl_code = code_log.extract_repl_code(text) or text
                        code_logger.write(
                            [
                                code_log.build_repl_entry(
                                    source="SUB",
                                    model_name=model,
                                    content=repl_code,
                                )
                            ]
                        )
                    results.llm[request.key] = LLMToolResult(
                        text=text, meta={"model": model}
                    )
                    statuses[request.key] = "resolved"
                else:
                    hits = payload
                    results.search[request.key] = SearchToolResult(
                        hits=hits,
                        meta={"query": request.query},
                    )
                    statuses[request.key] = "resolved"
            except Exception as exc:  # noqa: BLE001
                if tool_type == "llm":
                    results.llm[request.key] = LLMToolResult(
                        text="",
                        meta={"error": str(exc)},
                    )
                    statuses[request.key] = "error"
                else:
                    results.search[request.key] = SearchToolResult(
                        hits=[],
                        meta=build_error_meta(
                            ErrorCode.INTERNAL_ERROR,
                            "Search backend error",
                            details={"error": str(exc)},
                        ),
                    )
                    statuses[request.key] = "error"

    return results, statuses


@dataclass
class OrchestratorWorker:
    settings: Settings
    ddb_resource: ServiceResource
    table_names: DdbTableNames
    s3_client: BaseClient
    provider: LLMProvider
    search_backend: SearchBackend = field(default_factory=FakeSearchBackend)
    sandbox_runner: SandboxRunner | None = None
    logger: BoundLogger | None = None
    owner_id: str = field(default_factory=lambda: uuid4().hex)
    lease_duration_seconds: int = 30

    def __post_init__(self) -> None:
        if self.logger is None:
            self.logger = get_logger("rlm_rs.orchestrator")
        if not self.settings.s3_bucket:
            raise ValueError("s3_bucket is required for orchestrator")
        if self.sandbox_runner is None:
            self.sandbox_runner = build_sandbox_runner(self.settings, logger=self.logger)

    def run_once(self, *, limit: int | None = None) -> int:
        executions_table = self.ddb_resource.Table(self.table_names.executions)
        candidates = _scan_executions(executions_table)
        candidates.sort(key=lambda item: (item.get("session_id", ""), item.get("execution_id", "")))

        processed = 0
        for item in candidates:
            if limit is not None and processed >= limit:
                break
            session_id = str(item.get("session_id") or "")
            execution_id = str(item.get("execution_id") or "")
            if not session_id or not execution_id:
                continue
            if not ddb.acquire_execution_lease(
                executions_table,
                session_id=session_id,
                execution_id=execution_id,
                owner_id=self.owner_id,
                now_epoch=int(time.time()),
                lease_duration_seconds=self.lease_duration_seconds,
            ):
                continue
            try:
                if self._run_execution(item):
                    processed += 1
            finally:
                ddb.release_execution_lease(
                    executions_table,
                    session_id=session_id,
                    execution_id=execution_id,
                    owner_id=self.owner_id,
                )
        return processed

    def _is_execution_running(
        self,
        executions_table: Any,
        *,
        session_id: str,
        execution_id: str,
    ) -> bool:
        item = ddb.get_execution(
            executions_table,
            session_id=session_id,
            execution_id=execution_id,
        )
        if item is None:
            return False
        return item.get("status") == "RUNNING"

    def _build_trace_documents(
        self, documents: Sequence[Mapping[str, Any]]
    ) -> list[dict[str, JsonValue]]:
        payload: list[dict[str, JsonValue]] = []
        for item in documents:
            payload.append(
                state_store.normalize_json_value(
                    {
                        "doc_id": item.get("doc_id"),
                        "doc_index": item.get("doc_index"),
                        "source_name": item.get("source_name"),
                        "mime_type": item.get("mime_type"),
                        "text_s3_uri": item.get("text_s3_uri"),
                        "meta_s3_uri": item.get("meta_s3_uri"),
                        "offsets_s3_uri": item.get("offsets_s3_uri"),
                        "char_length": item.get("char_length"),
                        "byte_length": item.get("byte_length"),
                        "page_count": item.get("page_count"),
                        "parser_version": item.get("parser_version"),
                        "text_checksum": item.get("text_checksum"),
                        "ingest_status": item.get("ingest_status"),
                    }
                )
            )
        return payload

    def _build_trace_session(
        self, session_item: Mapping[str, Any]
    ) -> dict[str, JsonValue]:
        return state_store.normalize_json_value(
            {
                "session_id": session_item.get("session_id"),
                "tenant_id": session_item.get("tenant_id"),
                "status": session_item.get("status"),
                "options": session_item.get("options"),
                "models_default": session_item.get("models_default"),
                "budgets_default": session_item.get("budgets_default"),
                "created_at": session_item.get("created_at"),
                "expires_at": session_item.get("expires_at"),
            }
        )

    def _build_trace_execution(
        self,
        *,
        execution_item: Mapping[str, Any],
        status: str,
        answer: str | None,
        budgets_consumed: dict[str, JsonValue] | None,
        completed_at: str,
        duration_ms: int | None,
    ) -> dict[str, JsonValue]:
        return state_store.normalize_json_value(
            {
                "execution_id": execution_item.get("execution_id"),
                "session_id": execution_item.get("session_id"),
                "tenant_id": execution_item.get("tenant_id"),
                "mode": execution_item.get("mode"),
                "status": status,
                "question": execution_item.get("question"),
                "answer": answer,
                "budgets_requested": execution_item.get("budgets_requested"),
                "budgets_consumed": budgets_consumed,
                "models": execution_item.get("models"),
                "options": execution_item.get("options"),
                "started_at": execution_item.get("started_at"),
                "completed_at": completed_at,
                "duration_ms": duration_ms,
            }
        )

    def _finalize_execution(
        self,
        executions_table: Any,
        evaluations_table: Any,
        *,
        execution_item: Mapping[str, Any],
        session_item: Mapping[str, Any] | None,
        documents: Sequence[Mapping[str, Any]] | None,
        status: str,
        tracker: BudgetTracker | None,
        trace_collector: TraceCollector | None,
        answer: str | None = None,
        citations: list[dict[str, JsonValue]] | None = None,
        contexts: list[JsonValue] | None = None,
        duration_ms: int | None = None,
    ) -> None:
        budgets_consumed = None
        if tracker is not None:
            consumed = BudgetsConsumed(
                turns=tracker.turns,
                llm_subcalls=tracker.llm_subcalls,
                total_seconds=tracker.elapsed_seconds(),
            )
            budgets_consumed = consumed.model_dump(exclude_none=True)
        completed_at = _format_timestamp(_utc_now())
        trace_s3_uri: str | None = None
        if (
            trace_collector is not None
            and session_item is not None
            and documents is not None
            and execution_item.get("mode") == "ANSWERER"
        ):
            evaluation_item = ddb.get_evaluation(
                evaluations_table,
                execution_id=str(execution_item.get("execution_id")),
            )
            execution_payload = self._build_trace_execution(
                execution_item=execution_item,
                status=status,
                answer=answer,
                budgets_consumed=budgets_consumed,
                completed_at=completed_at,
                duration_ms=duration_ms,
            )
            session_payload = self._build_trace_session(session_item)
            documents_payload = self._build_trace_documents(documents)
            evaluation_payload = (
                state_store.normalize_json_value(evaluation_item)
                if evaluation_item
                else None
            )
            artifact = trace_collector.build_artifact(
                execution=execution_payload,
                session=session_payload,
                documents=documents_payload,
                evaluation=evaluation_payload,
            )
            try:
                trace_s3_uri = persist_trace_artifact(
                    s3_client=self.s3_client,
                    bucket=self.settings.s3_bucket,
                    tenant_id=str(execution_item.get("tenant_id")),
                    execution_id=str(execution_item.get("execution_id")),
                    artifact=artifact,
                )
            except Exception as exc:  # noqa: BLE001
                if self.logger is not None:
                    self.logger.warning(
                        "execution_trace_persist_failed",
                        execution_id=str(execution_item.get("execution_id")),
                        error=str(exc),
                    )

        contexts_payload = contexts
        contexts_s3_uri: str | None = None
        if contexts is not None:
            contexts_record = contexts_store.persist_contexts_payload(
                contexts=contexts,
                tenant_id=str(execution_item.get("tenant_id")),
                execution_id=str(execution_item.get("execution_id")),
                s3_client=self.s3_client,
                bucket=self.settings.s3_bucket,
            )
            contexts_payload = contexts_record.contexts_json
            contexts_s3_uri = contexts_record.contexts_s3_uri

        ddb.update_execution_status(
            executions_table,
            session_id=str(execution_item.get("session_id")),
            execution_id=str(execution_item.get("execution_id")),
            expected_status="RUNNING",
            new_status=status,
            answer=answer,
            citations=citations,
            contexts=contexts_payload,
            contexts_s3_uri=contexts_s3_uri,
            trace_s3_uri=trace_s3_uri,
            budgets_consumed=budgets_consumed,
            completed_at=completed_at,
            duration_ms=duration_ms,
        )

    def _run_execution(self, execution_item: Mapping[str, Any]) -> bool:
        session_id = str(execution_item.get("session_id") or "")
        execution_id = str(execution_item.get("execution_id") or "")
        tenant_id = str(execution_item.get("tenant_id") or "")
        question = str(execution_item.get("question") or "")
        if not session_id or not execution_id or not tenant_id:
            return False

        sessions_table = self.ddb_resource.Table(self.table_names.sessions)
        documents_table = self.ddb_resource.Table(self.table_names.documents)
        executions_table = self.ddb_resource.Table(self.table_names.executions)
        execution_state_table = self.ddb_resource.Table(self.table_names.execution_state)
        evaluations_table = self.ddb_resource.Table(self.table_names.evaluations)
        code_log_table = self.ddb_resource.Table(self.table_names.code_log)
        code_logger = code_log.CodeLogWriter(
            table=code_log_table,
            execution_id=execution_id,
            settings=self.settings,
            logger=self.logger,
        )
        trace_collector = TraceCollector(settings=self.settings)

        if not self._is_execution_running(
            executions_table,
            session_id=session_id,
            execution_id=execution_id,
        ):
            return True

        session_item = ddb.get_session(
            sessions_table,
            tenant_id=tenant_id,
            session_id=session_id,
        )
        if session_item is None:
            self._finalize_execution(
                executions_table,
                evaluations_table,
                execution_item=execution_item,
                session_item=None,
                documents=None,
                status="FAILED",
                tracker=None,
                trace_collector=None,
            )
            return True

        documents = _query_documents(documents_table, session_id)
        if not documents:
            self._finalize_execution(
                executions_table,
                evaluations_table,
                execution_item=execution_item,
                session_item=session_item,
                documents=None,
                status="FAILED",
                tracker=None,
                trace_collector=trace_collector,
            )
            return True

        models = _resolve_models(execution_item, session_item, self.settings)
        budgets = _resolve_budgets(execution_item, session_item, self.settings)
        output_mode = _resolve_output_mode(execution_item)
        if models is None or not models.root_model:
            self._finalize_execution(
                executions_table,
                evaluations_table,
                execution_item=execution_item,
                session_item=session_item,
                documents=documents,
                status="FAILED",
                tracker=None,
                trace_collector=trace_collector,
            )
            return True

        root_model = models.root_model
        sub_model = models.sub_model
        subcalls_enabled = sub_model is not None

        try:
            context_manifest = _build_context_manifest(documents)
        except ValueError:
            self._finalize_execution(
                executions_table,
                evaluations_table,
                execution_item=execution_item,
                session_item=session_item,
                documents=documents,
                status="FAILED",
                tracker=None,
                trace_collector=trace_collector,
            )
            return True

        doc_lengths_chars = _doc_lengths(documents, self.s3_client)
        doc_indexes = _doc_indexes(documents)
        state_item = ddb.get_execution_state(execution_state_table, execution_id=execution_id)
        if state_item is None:
            self._finalize_execution(
                executions_table,
                evaluations_table,
                execution_item=execution_item,
                session_item=session_item,
                documents=documents,
                status="FAILED",
                tracker=None,
                trace_collector=trace_collector,
            )
            return True

        try:
            state_payload = _load_state_payload(state_item, s3_client=self.s3_client)
        except Exception:  # noqa: BLE001
            self._finalize_execution(
                executions_table,
                evaluations_table,
                execution_item=execution_item,
                session_item=session_item,
                documents=documents,
                status="FAILED",
                tracker=None,
                trace_collector=trace_collector,
            )
            return True

        if state_payload is None:
            state_payload = {}
        if not isinstance(state_payload, dict):
            self._finalize_execution(
                executions_table,
                evaluations_table,
                execution_item=execution_item,
                session_item=session_item,
                documents=documents,
                status="FAILED",
                tracker=None,
                trace_collector=trace_collector,
            )
            return True

        try:
            _ensure_tool_state(state_payload)
        except state_store.StateValidationError:
            self._finalize_execution(
                executions_table,
                evaluations_table,
                execution_item=execution_item,
                session_item=session_item,
                documents=documents,
                status="FAILED",
                tracker=None,
                trace_collector=trace_collector,
            )
            return True

        tracker = _budget_tracker_from_state(state_payload, budgets)
        turn_index = _next_turn_index(state_item)
        last_stdout = state_item.get("stdout") or ""
        last_error = _format_step_error(state_item.get("error"))
        span_log: list[SpanLogEntry] = []
        context_span_log: list[ContextSpanEntry] = []
        execution_start = time.monotonic()
        limits = _limits_from_budgets(budgets)
        enable_search = bool(
            (session_item.get("options") or {}).get(
                "enable_search",
                self.settings.enable_search,
            )
        )

        while True:
            if not self._is_execution_running(
                executions_table,
                session_id=session_id,
                execution_id=execution_id,
            ):
                return True
            if tracker.over_max_turns():
                self._finalize_execution(
                    executions_table,
                    evaluations_table,
                    execution_item=execution_item,
                    session_item=session_item,
                    documents=documents,
                    status="MAX_TURNS_EXCEEDED",
                    tracker=tracker,
                    trace_collector=trace_collector,
                    duration_ms=self._duration_ms(execution_start),
                )
                return True
            if tracker.over_total_seconds():
                self._finalize_execution(
                    executions_table,
                    evaluations_table,
                    execution_item=execution_item,
                    session_item=session_item,
                    documents=documents,
                    status="BUDGET_EXCEEDED",
                    tracker=tracker,
                    trace_collector=trace_collector,
                    duration_ms=self._duration_ms(execution_start),
                )
                return True

            turn_timings: dict[str, int] = {}
            if isinstance(state_payload, dict):
                state_payload["_tool_schema"] = build_tool_schema(
                    subcalls_enabled=subcalls_enabled,
                    search_enabled=enable_search,
                )
            budget_snapshot = tracker.snapshot()
            state_payload["_budgets"] = budget_snapshot
            state_summary = None
            if self.settings.enable_root_state_summary:
                state_summary = _build_state_summary(state_payload)
            root_prompt_inputs = {
                "question": question,
                "doc_count": len(doc_lengths_chars),
                "doc_lengths_chars": list(doc_lengths_chars),
                "budget_snapshot": budget_snapshot,
                "last_stdout": last_stdout or None,
                "last_error": last_error,
                "state_summary": state_summary,
                "subcalls_enabled": subcalls_enabled,
                "output_mode": output_mode,
            }
            prompt_version = root_prompt_version(
                subcalls_enabled=subcalls_enabled,
                output_mode=output_mode,
            )
            prompt_start = time.perf_counter()
            prompt = build_root_prompt(
                question=question,
                doc_count=len(doc_lengths_chars),
                doc_lengths_chars=doc_lengths_chars,
                budget_snapshot=budget_snapshot,
                last_stdout=last_stdout or None,
                last_error=last_error,
                state_summary=state_summary,
                subcalls_enabled=subcalls_enabled,
                output_mode=output_mode,
            )
            turn_timings["prompt_build_ms"] = _elapsed_ms(prompt_start)
            trace_collector.start_turn(
                turn_index=turn_index,
                root_prompt=prompt,
                root_prompt_version=prompt_version,
                root_prompt_inputs=root_prompt_inputs,
                budget_snapshot=budget_snapshot,
            )
            prompt_len = len(prompt)
            if not tracker.can_accept_prompt(prompt_len):
                self._finalize_execution(
                    executions_table,
                    evaluations_table,
                    execution_item=execution_item,
                    session_item=session_item,
                    documents=documents,
                    status="BUDGET_EXCEEDED",
                    tracker=tracker,
                    trace_collector=trace_collector,
                    duration_ms=self._duration_ms(execution_start),
                )
                return True

            root_call_start = time.perf_counter()
            if self.logger is not None:
                self.logger.info(
                    "root_call_start",
                    execution_id=execution_id,
                    turn_index=turn_index,
                    model=root_model,
                    prompt_chars=prompt_len,
                )
            root_output = self.provider.complete_root(
                prompt,
                root_model,
                tenant_id=tenant_id,
            )
            turn_timings["root_call_ms"] = _elapsed_ms(root_call_start)
            if self.logger is not None:
                self.logger.info(
                    "root_call_complete",
                    execution_id=execution_id,
                    turn_index=turn_index,
                    model=root_model,
                    output_chars=len(root_output),
                    duration_ms=turn_timings["root_call_ms"],
                )
            tracker.record_prompt(prompt_len)
            parse_start = time.perf_counter()
            try:
                code = parse_root_output(root_output)
            except ValueError as exc:
                turn_timings["root_parse_ms"] = _elapsed_ms(parse_start)
                last_error = str(exc)
                code_logger.write(
                    [code_log.build_repl_parse_error_entry(
                        model_name=root_model,
                        error=str(exc),
                        output=root_output,
                    )]
                )
                trace_collector.record_parse_error(
                    turn_index=turn_index,
                    error=str(exc),
                    output=root_output,
                    root_prompt=prompt,
                    root_prompt_version=prompt_version,
                    root_prompt_inputs=root_prompt_inputs,
                    timings=turn_timings,
                )
                tracker.record_turn()
                continue
            turn_timings["root_parse_ms"] = _elapsed_ms(parse_start)
            code_logger.write(
                [
                    code_log.build_repl_entry(
                        source="ROOT",
                        model_name=root_model,
                        content=code,
                        turn_index=turn_index,
                    )
                ]
            )
            trace_collector.record_repl_code(turn_index=turn_index, repl_code=code)

            event = StepEvent(
                tenant_id=tenant_id,
                session_id=session_id,
                execution_id=execution_id,
                turn_index=turn_index,
                code=code,
                state=state_payload,
                context_manifest=context_manifest,
                tool_results=_tool_results_from_state(state_payload),
                limits=limits,
            )

            sandbox_start = time.perf_counter()
            result = self.sandbox_runner.run(
                event,
                s3_client=self.s3_client,
                region=self.settings.aws_region,
                endpoint_url=self.settings.localstack_endpoint_url,
            )
            turn_timings["sandbox_ms"] = _elapsed_ms(sandbox_start)
            if result.tool_requests:
                code_logger.write(code_log.build_tool_request_entries(result.tool_requests))
            for span_index, span in enumerate(result.span_log):
                context_span_log.append(
                    ContextSpanEntry(
                        turn_index=turn_index,
                        span_index=span_index,
                        span=span,
                    )
                )
            span_log.extend(result.span_log)
            tracker.record_turn()

            next_state = state_payload
            if isinstance(result.state, dict):
                next_state = _merge_reserved_state(result.state, state_payload)
            if isinstance(next_state, dict):
                try:
                    _ensure_tool_state(next_state)
                except state_store.StateValidationError:
                    self._finalize_execution(
                        executions_table,
                        evaluations_table,
                        execution_item=execution_item,
                        session_item=session_item,
                        documents=documents,
                        status="FAILED",
                        tracker=tracker,
                        trace_collector=trace_collector,
                        duration_ms=self._duration_ms(execution_start),
                    )
                    return True
                next_state["_budgets"] = tracker.snapshot()

            state_persist_start = time.perf_counter()
            try:
                state_record = state_store.persist_state_payload(
                    state=next_state,
                    tenant_id=tenant_id,
                    execution_id=execution_id,
                    turn_index=turn_index,
                    s3_client=self.s3_client,
                    bucket=self.settings.s3_bucket,
                )
            except (state_store.StateValidationError, state_store.StateOffloadError):
                self._finalize_execution(
                    executions_table,
                    evaluations_table,
                    execution_item=execution_item,
                    session_item=session_item,
                    documents=documents,
                    status="FAILED",
                    tracker=tracker,
                    trace_collector=trace_collector,
                    duration_ms=self._duration_ms(execution_start),
                )
                return True
            turn_timings["state_persist_ms"] = _elapsed_ms(state_persist_start)

            updated_at = _format_timestamp(_utc_now())
            step_snapshot = _step_snapshot(result, timings=turn_timings)
            trace_collector.record_step_result(
                turn_index=turn_index,
                result=result,
                state_summary=state_record.summary,
                checksum=state_record.checksum,
                timings=turn_timings,
            )
            ddb.put_execution_state(
                execution_state_table,
                execution_id=execution_id,
                turn_index=turn_index,
                updated_at=updated_at,
                ttl_epoch=int(session_item["ttl_epoch"]),
                state_json=state_record.state_json,
                state_s3_uri=state_record.state_s3_uri,
                checksum=state_record.checksum,
                summary=state_record.summary,
                **step_snapshot,
            )
            ddb.put_execution_state_step(
                execution_state_table,
                execution_id=execution_id,
                turn_index=turn_index,
                updated_at=updated_at,
                ttl_epoch=int(session_item["ttl_epoch"]),
                state_json=state_record.state_json,
                state_s3_uri=state_record.state_s3_uri,
                checksum=state_record.checksum,
                summary=state_record.summary,
                **step_snapshot,
            )

            state_payload = next_state
            last_stdout = result.stdout
            last_error = _format_step_error(step_snapshot.get("error"))
            turn_index += 1

            if not self._is_execution_running(
                executions_table,
                session_id=session_id,
                execution_id=execution_id,
            ):
                return True

            if result.final and result.final.is_final:
                if output_mode == "CONTEXTS":
                    contexts_payload: list[dict[str, JsonValue]] = []
                    citations_payload: list[dict[str, JsonValue]] = []
                    context_spans = [
                        entry for entry in context_span_log if _is_context_tag(entry.span.tag)
                    ]
                    if not context_spans and self.logger is not None:
                        self.logger.warning(
                            "contexts_missing_no_tagged_spans",
                            execution_id=execution_id,
                            span_count=len(span_log),
                        )
                    try:
                        documents_text = _load_documents_text(documents, self.s3_client)
                        if context_spans:
                            contexts_payload, citations_payload = _build_contexts_and_citations(
                                span_log=context_span_log,
                                documents=documents_text,
                                tenant_id=tenant_id,
                                session_id=session_id,
                            )
                    except Exception as exc:  # noqa: BLE001
                        if self.logger is not None:
                            self.logger.warning(
                                "contexts_build_failed",
                                execution_id=execution_id,
                                error=str(exc),
                                span_count=len(span_log),
                                context_span_count=len(context_spans),
                            )
                        contexts_payload = []
                        citations_payload = []
                    self._finalize_execution(
                        executions_table,
                        evaluations_table,
                        execution_item=execution_item,
                        session_item=session_item,
                        documents=documents,
                        status="COMPLETED",
                        tracker=tracker,
                        trace_collector=trace_collector,
                        answer=None,
                        citations=citations_payload,
                        contexts=contexts_payload,
                        duration_ms=self._duration_ms(execution_start),
                    )
                    return True

                answer_text = _sanitize_final_answer(result.final.answer or "")
                documents_text: list[DocumentText] = []
                citations_payload = []
                citeable_spans = [
                    span for span in span_log if not (span.tag or "").startswith("scan:")
                ]
                if not citeable_spans and self.logger is not None:
                    self.logger.warning(
                        "citations_missing_no_citeable_spans",
                        execution_id=execution_id,
                        span_count=len(span_log),
                    )
                try:
                    documents_text = _load_documents_text(documents, self.s3_client)
                    if citeable_spans:
                        citations = make_spanrefs(
                            span_log=span_log,
                            documents=documents_text,
                            tenant_id=tenant_id,
                            session_id=session_id,
                        )
                        citations_payload = [
                            citation.model_dump(exclude_none=True) for citation in citations
                        ]
                except Exception as exc:  # noqa: BLE001
                    if self.logger is not None:
                        self.logger.warning(
                            "citations_build_failed",
                            execution_id=execution_id,
                            error=str(exc),
                            span_count=len(span_log),
                            citeable_span_count=len(citeable_spans),
                        )
                    citations_payload = []
                self._finalize_execution(
                    executions_table,
                    evaluations_table,
                    execution_item=execution_item,
                    session_item=session_item,
                    documents=documents,
                    status="COMPLETED",
                    tracker=tracker,
                    trace_collector=trace_collector,
                    answer=answer_text,
                    citations=citations_payload,
                    duration_ms=self._duration_ms(execution_start),
                )
                self._create_evaluation_record(
                    evaluations_table,
                    execution_item=execution_item,
                    session_id=session_id,
                    execution_id=execution_id,
                    tenant_id=tenant_id,
                    question=question,
                    answer=answer_text,
                    models=models,
                    documents=documents,
                    span_log=span_log,
                    documents_text=documents_text,
                )
                return True

            if not result.success or not result.tool_requests:
                continue

            tool_resolve_start = time.perf_counter()
            try:
                tool_results, statuses = _resolve_tool_requests(
                    result.tool_requests,
                    tenant_id=tenant_id,
                    session_id=session_id,
                    provider=self.provider,
                    tracker=tracker,
                    model=sub_model,
                    enable_search=enable_search,
                    search_backend=self.search_backend,
                    doc_indexes=doc_indexes,
                    doc_lengths=doc_lengths_chars,
                    code_logger=code_logger,
                    max_concurrency=self.settings.tool_resolution_max_concurrency,
                )
            except BudgetExceededError:
                self._finalize_execution(
                    executions_table,
                    evaluations_table,
                    execution_item=execution_item,
                    session_item=session_item,
                    documents=documents,
                    status="BUDGET_EXCEEDED",
                    tracker=tracker,
                    trace_collector=trace_collector,
                    duration_ms=self._duration_ms(execution_start),
                )
                return True
            turn_timings["tool_resolve_ms"] = _elapsed_ms(tool_resolve_start)
            code_logger.write(code_log.build_tool_result_entries(tool_results, statuses))
            trace_collector.record_tool_results(
                turn_index=turn_index - 1,
                tool_results=tool_results,
                tool_status=statuses,
            )

            try:
                tool_apply_start = time.perf_counter()
                _apply_tool_results(state_payload, tool_results, statuses)
                turn_timings["tool_apply_ms"] = _elapsed_ms(tool_apply_start)
                state_payload["_budgets"] = tracker.snapshot()
                tool_state_persist_start = time.perf_counter()
                state_record = state_store.persist_state_payload(
                    state=state_payload,
                    tenant_id=tenant_id,
                    execution_id=execution_id,
                    turn_index=turn_index - 1,
                    s3_client=self.s3_client,
                    bucket=self.settings.s3_bucket,
                )
                turn_timings["tool_state_persist_ms"] = _elapsed_ms(tool_state_persist_start)
            except state_store.StateValidationError:
                self._finalize_execution(
                    executions_table,
                    evaluations_table,
                    execution_item=execution_item,
                    session_item=session_item,
                    documents=documents,
                    status="FAILED",
                    tracker=tracker,
                    trace_collector=trace_collector,
                    duration_ms=self._duration_ms(execution_start),
                )
                return True

            updated_at = _format_timestamp(_utc_now())
            step_snapshot["timings"] = dict(turn_timings)
            ddb.put_execution_state(
                execution_state_table,
                execution_id=execution_id,
                turn_index=turn_index - 1,
                updated_at=updated_at,
                ttl_epoch=int(session_item["ttl_epoch"]),
                state_json=state_record.state_json,
                state_s3_uri=state_record.state_s3_uri,
                checksum=state_record.checksum,
                summary=state_record.summary,
                **step_snapshot,
            )
            ddb.put_execution_state_step(
                execution_state_table,
                execution_id=execution_id,
                turn_index=turn_index - 1,
                updated_at=updated_at,
                ttl_epoch=int(session_item["ttl_epoch"]),
                state_json=state_record.state_json,
                state_s3_uri=state_record.state_s3_uri,
                checksum=state_record.checksum,
                summary=state_record.summary,
                **step_snapshot,
            )
            trace_collector.record_step_result(
                turn_index=turn_index - 1,
                result=result,
                state_summary=state_record.summary,
                checksum=state_record.checksum,
                timings=turn_timings,
            )

    def _duration_ms(self, start_time: float) -> int:
        return int((time.monotonic() - start_time) * 1000)

    def _create_evaluation_record(
        self,
        evaluations_table: Any,
        *,
        execution_item: Mapping[str, Any],
        session_id: str,
        execution_id: str,
        tenant_id: str,
        question: str,
        answer: str,
        models: ModelsConfig,
        documents: Sequence[Mapping[str, Any]],
        span_log: Sequence[SpanLogEntry] | None = None,
        documents_text: Sequence[DocumentText] | None = None,
    ) -> None:
        mode = str(execution_item.get("mode") or "ANSWERER")
        if mode != "ANSWERER":
            return

        evaluation_id = f"eval_{uuid4().hex}"
        created_at = _format_timestamp(_utc_now())
        try:
            ddb.create_evaluation(
                evaluations_table,
                evaluation_id=evaluation_id,
                tenant_id=tenant_id,
                session_id=session_id,
                execution_id=execution_id,
                mode=mode,
                question=question,
                answer=answer,
                baseline_status="RUNNING",
                baseline_skip_reason=None,
                baseline_answer=None,
                baseline_input_tokens=None,
                baseline_context_window=None,
                judge_metrics=None,
                created_at=created_at,
            )
        except Exception as exc:  # noqa: BLE001
            if self.logger is not None:
                self.logger.warning(
                    "baseline_evaluation_seed_failed",
                    execution_id=execution_id,
                    error=str(exc),
                )

        baseline_status = "SKIPPED"
        baseline_skip_reason: str | None = None
        baseline_answer: str | None = None
        baseline_input_tokens: int | None = None
        baseline_context_window: int | None = None
        judge_metrics = None

        try:
            baseline_check = baseline_eval.prepare_baseline_prompt(
                mode=mode,
                model=models.root_model,
                question=question,
                documents=documents,
                s3_client=self.s3_client,
                settings=self.settings,
            )
        except Exception as exc:  # noqa: BLE001
            baseline_skip_reason = "BASELINE_ERROR"
            if self.logger is not None:
                self.logger.warning(
                    "baseline_prompt_failed",
                    execution_id=execution_id,
                    error=str(exc),
                )
        else:
            baseline_input_tokens = baseline_check.input_tokens
            baseline_context_window = baseline_check.context_window
            baseline_skip_reason = baseline_check.skip_reason
            if baseline_check.skip_reason is None and baseline_check.prompt is not None:
                try:
                    baseline_answer = self.provider.complete_baseline(
                        baseline_check.prompt,
                        models.root_model,
                        tenant_id=tenant_id,
                    )
                    baseline_status = "COMPLETED"
                    baseline_skip_reason = None
                except Exception as exc:  # noqa: BLE001
                    baseline_skip_reason = "BASELINE_ERROR"
                    if self.logger is not None:
                        self.logger.warning(
                            "baseline_completion_failed",
                            execution_id=execution_id,
                            error=str(exc),
                        )

        if self.settings.enable_eval_judge:
            answerer_contexts = []
            if span_log and documents_text:
                answerer_contexts = eval_judge.build_answerer_contexts(
                    span_log,
                    documents_text,
                )
            baseline_contexts: list[str] = []
            if documents_text:
                baseline_contexts = eval_judge.build_baseline_contexts(
                    question=question,
                    answer=baseline_answer,
                    documents=documents_text,
                )
            judge_metrics = eval_judge.evaluate_judge(
                question=question,
                answer=answer,
                answerer_contexts=answerer_contexts,
                baseline_answer=baseline_answer,
                baseline_contexts=baseline_contexts,
                settings=self.settings,
                logger=self.logger,
            )

        judge_payload = None
        if judge_metrics is not None:
            judge_payload = judge_metrics.model_dump(exclude_none=True)

        try:
            updated = ddb.update_evaluation(
                evaluations_table,
                execution_id=execution_id,
                baseline_status=baseline_status,
                baseline_skip_reason=baseline_skip_reason,
                baseline_answer=baseline_answer,
                baseline_input_tokens=baseline_input_tokens,
                baseline_context_window=baseline_context_window,
                judge_metrics=judge_payload,
            )
            if not updated and self.logger is not None:
                self.logger.warning(
                    "baseline_evaluation_missing",
                    execution_id=execution_id,
                )
        except Exception as exc:  # noqa: BLE001
            if self.logger is not None:
                self.logger.warning(
                    "baseline_evaluation_update_failed",
                    execution_id=execution_id,
                    error=str(exc),
                )

    def recompute_evaluation(
        self,
        *,
        execution_id: str,
        tenant_id: str | None = None,
        recompute_baseline: bool = False,
    ) -> bool:
        """Recompute evaluation artifacts for a completed Answerer execution.

        - By default recomputes only LLM judge metrics (keeps baseline intact).
        - When `recompute_baseline=True`, recomputes baseline + judge metrics (can be expensive).
        """

        executions_table = self.ddb_resource.Table(self.table_names.executions)
        sessions_table = self.ddb_resource.Table(self.table_names.sessions)
        documents_table = self.ddb_resource.Table(self.table_names.documents)
        execution_state_table = self.ddb_resource.Table(self.table_names.execution_state)
        evaluations_table = self.ddb_resource.Table(self.table_names.evaluations)

        candidates = _scan_execution_items(executions_table, execution_id)
        if tenant_id is not None:
            candidates = [item for item in candidates if item.get("tenant_id") == tenant_id]
        if not candidates:
            return False
        if len(candidates) > 1:
            raise ValueError("Multiple executions found; pass tenant_id to disambiguate")

        execution_item = candidates[0]
        mode = str(execution_item.get("mode") or "")
        if mode != "ANSWERER":
            return False
        status = str(execution_item.get("status") or "")
        if status != "COMPLETED":
            return False

        resolved_tenant_id = str(execution_item.get("tenant_id") or "")
        session_id = str(execution_item.get("session_id") or "")
        question = str(execution_item.get("question") or "")
        answer = str(execution_item.get("answer") or "")
        if not resolved_tenant_id or not session_id:
            return False

        session_item = ddb.get_session(
            sessions_table,
            tenant_id=resolved_tenant_id,
            session_id=session_id,
        )
        if session_item is None:
            return False

        documents = _query_documents(documents_table, session_id)
        if not documents:
            return False

        models = _resolve_models(execution_item, session_item, self.settings)
        if models is None or not models.root_model:
            return False

        span_log = _load_execution_span_log(execution_state_table, execution_id)
        documents_text: list[DocumentText] = []
        try:
            documents_text = _load_documents_text(documents, self.s3_client)
        except Exception:  # noqa: BLE001
            documents_text = []

        if recompute_baseline:
            self._create_evaluation_record(
                evaluations_table,
                execution_item=execution_item,
                session_id=session_id,
                execution_id=execution_id,
                tenant_id=resolved_tenant_id,
                question=question,
                answer=answer,
                models=models,
                documents=documents,
                span_log=span_log,
                documents_text=documents_text,
            )
            return True

        if not self.settings.enable_eval_judge:
            raise ValueError("ENABLE_EVAL_JUDGE must be true to recompute judge metrics")

        evaluation_item = ddb.get_evaluation(evaluations_table, execution_id=execution_id)
        if evaluation_item is None:
            created_at = _format_timestamp(_utc_now())
            evaluation_id = f"eval_{uuid4().hex}"
            try:
                ddb.create_evaluation(
                    evaluations_table,
                    evaluation_id=evaluation_id,
                    tenant_id=resolved_tenant_id,
                    session_id=session_id,
                    execution_id=execution_id,
                    mode=mode,
                    question=question,
                    answer=answer,
                    baseline_status="SKIPPED",
                    baseline_skip_reason="MISSING_EVALUATION",
                    baseline_answer=None,
                    baseline_input_tokens=None,
                    baseline_context_window=None,
                    judge_metrics=None,
                    created_at=created_at,
                )
            except Exception as exc:  # noqa: BLE001
                if self.logger is not None:
                    self.logger.warning(
                        "evaluation_recompute_seed_failed",
                        execution_id=execution_id,
                        error=str(exc),
                    )
            evaluation_item = ddb.get_evaluation(evaluations_table, execution_id=execution_id)
            if evaluation_item is None:
                return False

        baseline_status = str(evaluation_item.get("baseline_status") or "SKIPPED")
        if baseline_status not in {"COMPLETED", "SKIPPED", "RUNNING"}:
            baseline_status = "SKIPPED"

        baseline_answer = evaluation_item.get("baseline_answer")
        if not isinstance(baseline_answer, str) or not baseline_answer.strip():
            baseline_answer = None

        answerer_contexts: list[str] = []
        if span_log and documents_text:
            answerer_contexts = eval_judge.build_answerer_contexts(span_log, documents_text)

        baseline_contexts: list[str] = []
        if documents_text:
            baseline_contexts = eval_judge.build_baseline_contexts(
                question=question,
                answer=baseline_answer,
                documents=documents_text,
            )

        judge_metrics = eval_judge.evaluate_judge(
            question=question,
            answer=answer,
            answerer_contexts=answerer_contexts,
            baseline_answer=baseline_answer,
            baseline_contexts=baseline_contexts,
            settings=self.settings,
            logger=self.logger,
        )
        if judge_metrics is None:
            return False

        judge_payload = judge_metrics.model_dump(exclude_none=True)
        updated = ddb.update_evaluation(
            evaluations_table,
            execution_id=execution_id,
            baseline_status=baseline_status,
            judge_metrics=judge_payload,
        )
        return bool(updated)


def build_worker(
    settings: Settings | None = None,
    provider: LLMProvider | None = None,
) -> OrchestratorWorker:
    resolved = settings or Settings()
    logger = get_logger("rlm_rs.orchestrator")
    if not resolved.s3_bucket:
        raise ValueError("s3_bucket is required for orchestrator")
    ddb_resource = build_ddb_resource(
        region=resolved.aws_region,
        endpoint_url=resolved.localstack_endpoint_url,
    )
    table_names = build_table_names(resolved.ddb_table_prefix)
    s3_client = build_s3_client(
        region=resolved.aws_region,
        endpoint_url=resolved.localstack_endpoint_url,
    )
    if provider is None:
        provider_name = (resolved.llm_provider or "fake").strip().lower()
        if provider_name and provider_name != "fake":
            if provider_name in {OPENAI_PROVIDER_NAME, AZURE_OPENAI_PROVIDER_NAME}:
                use_responses_api = (
                    bool(resolved.openai_use_responses_api)
                    and provider_name == OPENAI_PROVIDER_NAME
                )
                if resolved.openai_use_responses_api and not use_responses_api:
                    logger.warning(
                        "responses_api_ignored_for_azure",
                        note="Azure uses chat completions with current SDK; OPENAI_USE_RESPONSES_API applies only to openai provider",
                    )
                provider = OpenAIProvider(
                    provider_name=provider_name,
                    api_key=resolved.openai_api_key,
                    base_url=resolved.openai_base_url,
                    api_version=resolved.openai_api_version,
                    timeout_seconds=resolved.openai_timeout_seconds,
                    max_retries=resolved.openai_max_retries,
                    s3_client=s3_client,
                    s3_bucket=resolved.s3_bucket,
                    use_responses_api=use_responses_api,
                    subcall_reasoning_effort=resolved.subcall_reasoning_effort,
                    subcall_min_completion_tokens=resolved.subcall_min_completion_tokens,
                )
            else:
                raise ValueError("LLM provider is not configured")
        else:
            provider = FakeLLMProvider()
    search_backend: SearchBackend = FakeSearchBackend()
    cache_prefix = _search_cache_prefix(resolved.search_backend_config)
    search_backend = CachedSearchBackend(
        backend=search_backend,
        cache=S3SearchCache(s3_client, resolved.s3_bucket, prefix=cache_prefix),
        backend_name="fake",
    )
    return OrchestratorWorker(
        settings=resolved,
        ddb_resource=ddb_resource,
        table_names=table_names,
        s3_client=s3_client,
        provider=provider,
        search_backend=search_backend,
        logger=logger,
    )
