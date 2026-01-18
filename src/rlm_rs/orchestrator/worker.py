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

from rlm_rs.errors import ErrorCode
from rlm_rs.logging import get_logger
from rlm_rs.models import (
    Budgets,
    BudgetsConsumed,
    ContextDocument,
    ContextManifest,
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
from rlm_rs.orchestrator.citations import DocumentText, make_spanrefs
from rlm_rs.orchestrator.providers import FakeLLMProvider, LLMProvider, OpenAIProvider
from rlm_rs.orchestrator.root_prompt import build_root_prompt, parse_root_output
from rlm_rs.search.backends import (
    CachedSearchBackend,
    FakeSearchBackend,
    S3SearchCache,
    SearchBackend,
    build_error_meta,
    search_disabled_error_meta,
)
from rlm_rs.settings import Settings
from rlm_rs.sandbox.step_executor import execute_step
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
    for key in ("_tool_results", "_tool_status", "_budgets", "_trace"):
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


def _step_snapshot(result: StepResult) -> dict[str, Any]:
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
        bucket, key = _split_s3_uri(str(text_s3_uri))
        payload = s3.get_bytes(s3_client, bucket, key)
        documents.append(
            DocumentText(
                doc_id=str(item["doc_id"]),
                doc_index=int(item["doc_index"]),
                text=payload.decode("utf-8"),
            )
        )
    return documents


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
) -> tuple[ToolResultsEnvelope, dict[str, str]]:
    results = ToolResultsEnvelope()
    statuses: dict[str, str] = {}

    for request in requests.llm:
        prompt_len = len(request.prompt)
        if not tracker.can_accept_prompt(prompt_len):
            raise BudgetExceededError("BUDGET_EXCEEDED", "LLM prompt budget exceeded")
        if not tracker.can_accept_subcalls(1):
            raise BudgetExceededError("BUDGET_EXCEEDED", "LLM subcall budget exceeded")
        tracker.record_prompt(prompt_len)
        tracker.record_subcalls(1)
        try:
            text = provider.complete_subcall(
                request.prompt,
                model,
                request.max_tokens,
                request.temperature,
                tenant_id=tenant_id,
            )
            results.llm[request.key] = LLMToolResult(text=text, meta={"model": model})
            statuses[request.key] = "resolved"
        except Exception as exc:  # noqa: BLE001
            results.llm[request.key] = LLMToolResult(
                text="",
                meta={"error": str(exc)},
            )
            statuses[request.key] = "error"

    for request in requests.search:
        if not enable_search:
            results.search[request.key] = SearchToolResult(
                hits=[],
                meta=search_disabled_error_meta(),
            )
            statuses[request.key] = "error"
            continue
        try:
            hits = search_backend.search(
                tenant_id=tenant_id,
                session_id=session_id,
                request=request,
                doc_indexes=doc_indexes,
                doc_lengths=doc_lengths,
            )
            results.search[request.key] = SearchToolResult(
                hits=hits,
                meta={"query": request.query},
            )
            statuses[request.key] = "resolved"
        except Exception as exc:  # noqa: BLE001
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
    logger: BoundLogger | None = None
    owner_id: str = field(default_factory=lambda: uuid4().hex)
    lease_duration_seconds: int = 30

    def __post_init__(self) -> None:
        if self.logger is None:
            self.logger = get_logger("rlm_rs.orchestrator")
        if not self.settings.s3_bucket:
            raise ValueError("s3_bucket is required for orchestrator")

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

        session_item = ddb.get_session(
            sessions_table,
            tenant_id=tenant_id,
            session_id=session_id,
        )
        if session_item is None:
            self._finalize_status(
                executions_table,
                session_id=session_id,
                execution_id=execution_id,
                status="FAILED",
                tracker=None,
            )
            return True

        documents = _query_documents(documents_table, session_id)
        if not documents:
            self._finalize_status(
                executions_table,
                session_id=session_id,
                execution_id=execution_id,
                status="FAILED",
                tracker=None,
            )
            return True

        models = _resolve_models(execution_item, session_item, self.settings)
        budgets = _resolve_budgets(execution_item, session_item, self.settings)
        if models is None or not models.root_model:
            self._finalize_status(
                executions_table,
                session_id=session_id,
                execution_id=execution_id,
                status="FAILED",
                tracker=None,
            )
            return True

        root_model = models.root_model
        sub_model = models.sub_model
        subcalls_enabled = sub_model is not None

        try:
            context_manifest = _build_context_manifest(documents)
        except ValueError:
            self._finalize_status(
                executions_table,
                session_id=session_id,
                execution_id=execution_id,
                status="FAILED",
                tracker=None,
            )
            return True

        doc_lengths_chars = _doc_lengths(documents, self.s3_client)
        doc_indexes = _doc_indexes(documents)
        state_item = ddb.get_execution_state(execution_state_table, execution_id=execution_id)
        if state_item is None:
            self._finalize_status(
                executions_table,
                session_id=session_id,
                execution_id=execution_id,
                status="FAILED",
                tracker=None,
            )
            return True

        try:
            state_payload = _load_state_payload(state_item, s3_client=self.s3_client)
        except Exception:  # noqa: BLE001
            self._finalize_status(
                executions_table,
                session_id=session_id,
                execution_id=execution_id,
                status="FAILED",
                tracker=None,
            )
            return True

        if state_payload is None:
            state_payload = {}
        if not isinstance(state_payload, dict):
            self._finalize_status(
                executions_table,
                session_id=session_id,
                execution_id=execution_id,
                status="FAILED",
                tracker=None,
            )
            return True

        try:
            _ensure_tool_state(state_payload)
        except state_store.StateValidationError:
            self._finalize_status(
                executions_table,
                session_id=session_id,
                execution_id=execution_id,
                status="FAILED",
                tracker=None,
            )
            return True

        tracker = _budget_tracker_from_state(state_payload, budgets)
        turn_index = _next_turn_index(state_item)
        last_stdout = state_item.get("stdout") or ""
        last_error = _format_step_error(state_item.get("error"))
        span_log: list[SpanLogEntry] = []
        execution_start = time.monotonic()
        limits = _limits_from_budgets(budgets)
        enable_search = bool(
            (session_item.get("options") or {}).get(
                "enable_search",
                self.settings.enable_search,
            )
        )

        while True:
            if tracker.over_max_turns():
                self._finalize_status(
                    executions_table,
                    session_id=session_id,
                    execution_id=execution_id,
                    status="MAX_TURNS_EXCEEDED",
                    tracker=tracker,
                    duration_ms=self._duration_ms(execution_start),
                )
                return True
            if tracker.over_total_seconds():
                self._finalize_status(
                    executions_table,
                    session_id=session_id,
                    execution_id=execution_id,
                    status="BUDGET_EXCEEDED",
                    tracker=tracker,
                    duration_ms=self._duration_ms(execution_start),
                )
                return True

            budget_snapshot = tracker.snapshot()
            state_payload["_budgets"] = budget_snapshot
            prompt = build_root_prompt(
                question=question,
                doc_count=len(doc_lengths_chars),
                doc_lengths_chars=doc_lengths_chars,
                budget_snapshot=budget_snapshot,
                last_stdout=last_stdout or None,
                last_error=last_error,
                subcalls_enabled=subcalls_enabled,
            )
            prompt_len = len(prompt)
            if not tracker.can_accept_prompt(prompt_len):
                self._finalize_status(
                    executions_table,
                    session_id=session_id,
                    execution_id=execution_id,
                    status="BUDGET_EXCEEDED",
                    tracker=tracker,
                    duration_ms=self._duration_ms(execution_start),
                )
                return True

            root_output = self.provider.complete_root(
                prompt,
                root_model,
                tenant_id=tenant_id,
            )
            tracker.record_prompt(prompt_len)
            try:
                code = parse_root_output(root_output)
            except ValueError as exc:
                last_error = str(exc)
                tracker.record_turn()
                continue

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

            result = execute_step(
                event,
                s3_client=self.s3_client,
                region=self.settings.aws_region,
                endpoint_url=self.settings.localstack_endpoint_url,
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
                    self._finalize_status(
                        executions_table,
                        session_id=session_id,
                        execution_id=execution_id,
                        status="FAILED",
                        tracker=tracker,
                        duration_ms=self._duration_ms(execution_start),
                    )
                    return True
                next_state["_budgets"] = tracker.snapshot()

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
                self._finalize_status(
                    executions_table,
                    session_id=session_id,
                    execution_id=execution_id,
                    status="FAILED",
                    tracker=tracker,
                    duration_ms=self._duration_ms(execution_start),
                )
                return True

            updated_at = _format_timestamp(_utc_now())
            step_snapshot = _step_snapshot(result)
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

            state_payload = next_state
            last_stdout = result.stdout
            last_error = _format_step_error(step_snapshot.get("error"))
            turn_index += 1

            if result.final and result.final.is_final:
                try:
                    documents_text = _load_documents_text(documents, self.s3_client)
                    citations = make_spanrefs(
                        span_log=span_log,
                        documents=documents_text,
                        tenant_id=tenant_id,
                        session_id=session_id,
                    )
                    citations_payload = [
                        citation.model_dump(exclude_none=True) for citation in citations
                    ]
                except Exception:  # noqa: BLE001
                    citations_payload = []
                self._finalize_status(
                    executions_table,
                    session_id=session_id,
                    execution_id=execution_id,
                    status="COMPLETED",
                    answer=result.final.answer or "",
                    citations=citations_payload,
                    tracker=tracker,
                    duration_ms=self._duration_ms(execution_start),
                )
                return True

            if not result.success or not result.tool_requests:
                continue

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
                )
            except BudgetExceededError:
                self._finalize_status(
                    executions_table,
                    session_id=session_id,
                    execution_id=execution_id,
                    status="BUDGET_EXCEEDED",
                    tracker=tracker,
                    duration_ms=self._duration_ms(execution_start),
                )
                return True

            try:
                _apply_tool_results(state_payload, tool_results, statuses)
                state_payload["_budgets"] = tracker.snapshot()
                state_record = state_store.persist_state_payload(
                    state=state_payload,
                    tenant_id=tenant_id,
                    execution_id=execution_id,
                    turn_index=turn_index - 1,
                    s3_client=self.s3_client,
                    bucket=self.settings.s3_bucket,
                )
            except state_store.StateValidationError:
                self._finalize_status(
                    executions_table,
                    session_id=session_id,
                    execution_id=execution_id,
                    status="FAILED",
                    tracker=tracker,
                    duration_ms=self._duration_ms(execution_start),
                )
                return True

            ddb.put_execution_state(
                execution_state_table,
                execution_id=execution_id,
                turn_index=turn_index - 1,
                updated_at=_format_timestamp(_utc_now()),
                ttl_epoch=int(session_item["ttl_epoch"]),
                state_json=state_record.state_json,
                state_s3_uri=state_record.state_s3_uri,
                checksum=state_record.checksum,
                summary=state_record.summary,
                **step_snapshot,
            )

    def _duration_ms(self, start_time: float) -> int:
        return int((time.monotonic() - start_time) * 1000)

    def _finalize_status(
        self,
        executions_table: Any,
        *,
        session_id: str,
        execution_id: str,
        status: str,
        tracker: BudgetTracker | None,
        answer: str | None = None,
        citations: list[dict[str, JsonValue]] | None = None,
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
        ddb.update_execution_status(
            executions_table,
            session_id=session_id,
            execution_id=execution_id,
            expected_status="RUNNING",
            new_status=status,
            answer=answer,
            citations=citations,
            budgets_consumed=budgets_consumed,
            completed_at=_format_timestamp(_utc_now()),
            duration_ms=duration_ms,
        )


def build_worker(
    settings: Settings | None = None,
    provider: LLMProvider | None = None,
) -> OrchestratorWorker:
    resolved = settings or Settings()
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
        if resolved.llm_provider and resolved.llm_provider != "fake":
            if resolved.llm_provider == "openai":
                provider = OpenAIProvider(
                    api_key=resolved.openai_api_key,
                    base_url=resolved.openai_base_url,
                    timeout_seconds=resolved.openai_timeout_seconds,
                    max_retries=resolved.openai_max_retries,
                    s3_client=s3_client,
                    s3_bucket=resolved.s3_bucket,
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
        logger=get_logger("rlm_rs.orchestrator"),
    )
