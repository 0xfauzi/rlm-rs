from __future__ import annotations

import base64
import binascii
import json
import time
from datetime import datetime, timezone
from typing import Any, Mapping
from urllib.parse import urlparse
from uuid import uuid4

from boto3.resources.base import ServiceResource
from botocore.client import BaseClient
from fastapi import APIRouter, Depends, Query
from pydantic import JsonValue
from structlog.stdlib import BoundLogger

from rlm_rs import code_log
from rlm_rs.api.auth import ApiKeyContext, ensure_tenant_access, require_api_key
from rlm_rs.api.dependencies import (
    get_ddb_resource,
    get_logger,
    get_s3_client,
    get_sandbox_runner,
    get_settings,
    get_table_names,
)
from rlm_rs.api.rate_limits import enforce_rate_limit
from rlm_rs.api.sessions import _compute_readiness, _detect_foreign_session, _normalize_options
from rlm_rs.api.sessions import _query_documents as _query_session_documents
from rlm_rs.errors import ErrorCode, raise_http_error
from rlm_rs.models import (
    Budgets,
    CodeLogEntry,
    CodeLogResponse,
    ContextDocument,
    ContextItem,
    ContextManifest,
    CreateExecutionRequest,
    CreateExecutionResponse,
    CreateRuntimeExecutionResponse,
    ExecutionOptions,
    ExecutionStepHistoryResponse,
    ExecutionStepSnapshot,
    ExecutionEvaluationResponse,
    ExecutionContextsResponse,
    ExecutionStatus,
    ExecutionStatusResponse,
    ExecutionWaitRequest,
    ExecutionListItem,
    LimitsSnapshot,
    ListExecutionsResponse,
    LLMToolResult,
    ModelsConfig,
    RecomputeEvaluationRequest,
    SearchToolResult,
    SessionOptions,
    StepEvent,
    StepRequest,
    StepResult,
    ToolRequestsEnvelope,
    ToolResolveRequest,
    ToolResolveResponse,
    ToolResultsEnvelope,
)
from rlm_rs.search import search_disabled_error_meta
from rlm_rs.settings import Settings
from rlm_rs.sandbox.runner import SandboxRunner
from rlm_rs.sandbox.tool_api import build_tool_schema
from rlm_rs.orchestrator.providers import FakeLLMProvider
from rlm_rs.orchestrator.worker import OrchestratorWorker
from rlm_rs.storage import contexts as contexts_store
from rlm_rs.storage import ddb, s3, state as state_store
from rlm_rs.storage.ddb import DdbTableNames


router = APIRouter(prefix="/v1", dependencies=[Depends(enforce_rate_limit)])

_EXECUTION_PREFIX = "exec_"
_WAIT_POLL_SECONDS = 0.2


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _format_timestamp(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _new_execution_id() -> str:
    return f"{_EXECUTION_PREFIX}{uuid4().hex}"


def _encode_cursor(key: Mapping[str, Any]) -> str:
    payload = json.dumps(
        {"PK": key.get("PK"), "SK": key.get("SK")},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str) -> dict[str, str]:
    padding = "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(cursor + padding)
        data = json.loads(raw.decode("utf-8"))
    except (binascii.Error, json.JSONDecodeError, UnicodeDecodeError):
        raise_http_error(ErrorCode.VALIDATION_ERROR, "Invalid cursor")
    if not isinstance(data, dict):
        raise_http_error(ErrorCode.VALIDATION_ERROR, "Invalid cursor")
    pk = data.get("PK")
    sk = data.get("SK")
    if not isinstance(pk, str) or not isinstance(sk, str):
        raise_http_error(ErrorCode.VALIDATION_ERROR, "Invalid cursor")
    return {"PK": pk, "SK": sk}


def _serialize_model(
    model: Budgets | ModelsConfig | ExecutionOptions | None,
) -> dict[str, Any] | None:
    if model is None:
        return None
    return model.model_dump(exclude_none=True)


def _normalize_execution_options(
    options: ExecutionOptions | None,
    settings: Settings,
) -> ExecutionOptions:
    payload = {} if options is None else options.model_dump(exclude_none=True)
    return_trace = bool(payload.get("return_trace", False))
    redact_trace = bool(payload.get("redact_trace", False))
    if return_trace and not settings.enable_return_trace:
        return_trace = False
    if redact_trace and not settings.enable_trace_redaction:
        redact_trace = False
    payload["return_trace"] = return_trace
    payload["redact_trace"] = redact_trace
    return ExecutionOptions.model_validate(payload)


def _resolve_output_mode(options: Mapping[str, Any] | None) -> str:
    if isinstance(options, dict):
        output_mode = options.get("output_mode")
        if output_mode in ("ANSWER", "CONTEXTS"):
            return output_mode
    return "ANSWER"


def _resolve_models(
    request_models: ModelsConfig | None,
    session_item: Mapping[str, Any],
    settings: Settings,
) -> ModelsConfig | None:
    if request_models is not None:
        return request_models
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
    request_budgets: Budgets | None,
    session_item: Mapping[str, Any],
    settings: Settings,
) -> Budgets | None:
    if request_budgets is not None:
        return request_budgets
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


def _split_s3_uri(uri: str) -> tuple[str, str]:
    parsed = urlparse(uri)
    if parsed.scheme != "s3" or not parsed.netloc:
        raise ValueError(f"Invalid S3 URI: {uri}")
    return parsed.netloc, parsed.path.lstrip("/")


def _load_state_payload(
    state_item: Mapping[str, Any],
    *,
    s3_client: BaseClient | None,
) -> JsonValue | None:
    state_json = state_item.get("state_json")
    state_s3_uri = state_item.get("state_s3_uri")
    if state_s3_uri:
        if s3_client is None:
            raise_http_error(ErrorCode.S3_READ_ERROR, "S3 client is not configured")
        try:
            bucket, key = _split_s3_uri(str(state_s3_uri))
        except ValueError as exc:
            raise_http_error(ErrorCode.S3_READ_ERROR, str(exc))
        try:
            state_json = s3.get_gzip_json(s3_client, bucket, key)
        except Exception as exc:  # noqa: BLE001
            raise_http_error(ErrorCode.S3_READ_ERROR, f"Failed to read state: {exc}")
    state_json = state_store.normalize_json_value(state_json)
    try:
        state_store.validate_state_payload(state_json)
    except state_store.StateValidationError as exc:
        raise_http_error(ErrorCode.STATE_INVALID_TYPE, str(exc))
    return state_json


def _load_contexts_payload(
    execution_item: Mapping[str, Any],
    *,
    s3_client: BaseClient | None,
) -> list[ContextItem]:
    contexts_s3_uri = execution_item.get("contexts_s3_uri")
    if contexts_s3_uri:
        if s3_client is None:
            raise_http_error(ErrorCode.S3_READ_ERROR, "S3 client is not configured")
        try:
            payload = contexts_store.load_contexts_payload(
                s3_client=s3_client,
                contexts_s3_uri=str(contexts_s3_uri),
            )
            return [ContextItem.model_validate(item) for item in payload]
        except contexts_store.ContextsValidationError as exc:
            raise_http_error(ErrorCode.VALIDATION_ERROR, str(exc))
        except ValueError as exc:
            raise_http_error(ErrorCode.S3_READ_ERROR, str(exc))
        except Exception as exc:  # noqa: BLE001
            raise_http_error(ErrorCode.S3_READ_ERROR, f"Failed to read contexts: {exc}")

    contexts_payload = state_store.normalize_json_value(execution_item.get("contexts"))
    if contexts_payload is None:
        return []
    if not isinstance(contexts_payload, list):
        raise_http_error(ErrorCode.VALIDATION_ERROR, "Contexts payload is invalid")
    try:
        contexts_store.validate_contexts_payload(contexts_payload)
    except contexts_store.ContextsValidationError as exc:
        raise_http_error(ErrorCode.VALIDATION_ERROR, str(exc))
    return [ContextItem.model_validate(item) for item in contexts_payload]


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
            raise state_store.StateValidationError(
                f"_tool_results.{key} must be an object."
            )
    tool_status = state.get("_tool_status")
    if tool_status is None:
        state["_tool_status"] = {}
    elif not isinstance(tool_status, dict):
        raise state_store.StateValidationError("_tool_status must be an object.")


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


def _build_context_manifest(docs: list[Mapping[str, Any]]) -> ContextManifest:
    sorted_docs = sorted(docs, key=lambda item: int(item.get("doc_index", 0)))
    manifest_docs: list[ContextDocument] = []
    for item in sorted_docs:
        text_s3_uri = item.get("text_s3_uri")
        offsets_s3_uri = item.get("offsets_s3_uri")
        if not text_s3_uri or not offsets_s3_uri:
            raise_http_error(ErrorCode.SESSION_NOT_READY, "Session not ready")
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


def _extract_step_snapshot(state_item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "success": state_item.get("success"),
        "stdout": state_item.get("stdout"),
        "span_log": state_item.get("span_log"),
        "tool_requests": state_item.get("tool_requests"),
        "final": state_item.get("final"),
        "error": state_item.get("error"),
    }


def _fake_llm_result(prompt: str, model: str) -> LLMToolResult:
    text = f"fake:{prompt}"
    return LLMToolResult(text=text, meta={"model": model})


def _resolve_tool_requests(
    request: ToolResolveRequest,
    *,
    enable_search: bool,
) -> tuple[ToolResultsEnvelope, dict[str, str]]:
    results = ToolResultsEnvelope()
    statuses: dict[str, str] = {}
    model = request.models.sub_model

    for tool_request in request.tool_requests.llm:
        results.llm[tool_request.key] = _fake_llm_result(tool_request.prompt, model)
        statuses[tool_request.key] = "resolved"

    for tool_request in request.tool_requests.search:
        if not enable_search:
            results.search[tool_request.key] = SearchToolResult(
                hits=[],
                meta=search_disabled_error_meta(),
            )
            statuses[tool_request.key] = "error"
            continue
        results.search[tool_request.key] = SearchToolResult(
            hits=[],
            meta={"query": tool_request.query},
        )
        statuses[tool_request.key] = "resolved"

    return results, statuses


def _scan_execution_items(table: Any, execution_id: str) -> list[dict[str, Any]]:
    target_sk = f"{ddb.EXECUTION_SK_PREFIX}{execution_id}"
    items: list[dict[str, Any]] = []
    response = table.scan()
    items.extend(response.get("Items", []))
    while response.get("LastEvaluatedKey"):
        response = table.scan(ExclusiveStartKey=response["LastEvaluatedKey"])
        items.extend(response.get("Items", []))
    return [item for item in items if item.get("SK") == target_sk]


def _scan_executions(
    table: Any,
    *,
    tenant_id: str,
    status: ExecutionStatus | None,
    mode: str | None,
    session_prefix: str | None,
    limit: int,
    start_key: dict[str, str] | None,
) -> tuple[list[dict[str, Any]], dict[str, str] | None]:
    items: list[dict[str, Any]] = []
    last_key = start_key
    while len(items) < limit:
        scan_params: dict[str, Any] = {"Limit": limit}
        if last_key is not None:
            scan_params["ExclusiveStartKey"] = last_key
        response = table.scan(**scan_params)
        batch = response.get("Items", [])
        for item in batch:
            if item.get("tenant_id") != tenant_id:
                continue
            if status and item.get("status") != status:
                continue
            if mode and item.get("mode") != mode:
                continue
            if session_prefix:
                session_id = str(item.get("session_id", ""))
                if not session_id.startswith(session_prefix):
                    continue
            items.append(item)
            if len(items) >= limit:
                break
        last_key = response.get("LastEvaluatedKey")
        if not last_key or len(items) >= limit:
            break
    return items, last_key


def _get_execution_for_tenant(
    table: Any, execution_id: str, tenant_id: str
) -> dict[str, Any]:
    items = _scan_execution_items(table, execution_id)
    if not items:
        raise_http_error(ErrorCode.EXECUTION_NOT_FOUND, "Execution not found")
    for item in items:
        if item.get("tenant_id") == tenant_id:
            return item
    raise_http_error(ErrorCode.FORBIDDEN, "Forbidden")


def _build_execution_response(item: Mapping[str, Any]) -> ExecutionStatusResponse:
    options = item.get("options")
    return_trace = False
    if isinstance(options, dict):
        return_trace = options.get("return_trace") is True
    output_mode = _resolve_output_mode(options)
    answer = item.get("answer")
    if output_mode == "CONTEXTS":
        answer = None
    contexts_s3_uri = item.get("contexts_s3_uri")
    contexts_payload = None
    if not contexts_s3_uri:
        contexts_payload = state_store.normalize_json_value(item.get("contexts"))
    return ExecutionStatusResponse(
        execution_id=str(item["execution_id"]),
        mode=item.get("mode"),
        output_mode=output_mode,
        status=str(item["status"]),
        question=item.get("question"),
        answer=answer,
        citations=item.get("citations"),
        contexts=contexts_payload,
        contexts_s3_uri=contexts_s3_uri,
        budgets_requested=item.get("budgets_requested"),
        budgets_consumed=item.get("budgets_consumed"),
        started_at=item.get("started_at"),
        completed_at=item.get("completed_at"),
        trace_s3_uri=item.get("trace_s3_uri") if return_trace else None,
    )


def _build_evaluation_response(item: Mapping[str, Any]) -> ExecutionEvaluationResponse:
    return ExecutionEvaluationResponse(
        evaluation_id=str(item["evaluation_id"]),
        tenant_id=str(item["tenant_id"]),
        session_id=str(item["session_id"]),
        execution_id=str(item["execution_id"]),
        mode=str(item["mode"]),
        question=str(item["question"]),
        answer=item.get("answer"),
        baseline_status=str(item["baseline_status"]),
        baseline_skip_reason=item.get("baseline_skip_reason"),
        baseline_answer=item.get("baseline_answer"),
        baseline_input_tokens=item.get("baseline_input_tokens"),
        baseline_context_window=item.get("baseline_context_window"),
        judge_metrics=item.get("judge_metrics"),
        created_at=str(item["created_at"]),
    )


def _build_execution_list_item(item: Mapping[str, Any]) -> ExecutionListItem:
    options = item.get("options")
    return ExecutionListItem(
        execution_id=str(item["execution_id"]),
        session_id=str(item["session_id"]),
        tenant_id=str(item["tenant_id"]),
        mode=item.get("mode"),
        output_mode=_resolve_output_mode(options),
        status=str(item["status"]),
        question=item.get("question"),
        answer=item.get("answer"),
        citations=item.get("citations"),
        budgets_consumed=item.get("budgets_consumed"),
        started_at=item.get("started_at"),
        completed_at=item.get("completed_at"),
    )


def _wait_for_execution(
    table: Any,
    *,
    execution_id: str,
    session_id: str,
    tenant_id: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    item = ddb.get_execution(table, session_id=session_id, execution_id=execution_id)
    if item is None:
        raise_http_error(ErrorCode.EXECUTION_NOT_FOUND, "Execution not found")
    ensure_tenant_access(item, tenant_id)

    if item.get("status") != "RUNNING" or timeout_seconds <= 0:
        return item

    deadline = time.monotonic() + timeout_seconds
    while item.get("status") == "RUNNING":
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(_WAIT_POLL_SECONDS, remaining))
        item = ddb.get_execution(table, session_id=session_id, execution_id=execution_id)
        if item is None:
            raise_http_error(ErrorCode.EXECUTION_NOT_FOUND, "Execution not found")
        ensure_tenant_access(item, tenant_id)
    return item


@router.post("/sessions/{session_id}/executions", response_model=CreateExecutionResponse)
def create_execution(
    session_id: str,
    request: CreateExecutionRequest,
    context: ApiKeyContext = Depends(require_api_key),
    settings: Settings = Depends(get_settings),
    s3_client: BaseClient = Depends(get_s3_client),
    ddb_resource: ServiceResource = Depends(get_ddb_resource),
    table_names: DdbTableNames = Depends(get_table_names),
    logger: BoundLogger = Depends(get_logger),
) -> CreateExecutionResponse:
    sessions_table = ddb_resource.Table(table_names.sessions)
    documents_table = ddb_resource.Table(table_names.documents)
    executions_table = ddb_resource.Table(table_names.executions)
    execution_state_table = ddb_resource.Table(table_names.execution_state)

    session_item = ddb.get_session(
        sessions_table, tenant_id=context.tenant_id, session_id=session_id
    )
    if session_item is None:
        _detect_foreign_session(documents_table, session_id, context.tenant_id)
        raise_http_error(ErrorCode.SESSION_NOT_FOUND, "Session not found")
    ensure_tenant_access(session_item, context.tenant_id)

    if session_item.get("status") == "EXPIRED":
        raise_http_error(ErrorCode.SESSION_EXPIRED, "Session expired")

    options = _normalize_options(
        SessionOptions.model_validate(session_item.get("options") or {}), settings
    )
    document_items = _query_session_documents(documents_table, session_id)
    for item in document_items:
        ensure_tenant_access(item, context.tenant_id)
    readiness = _compute_readiness(
        document_items,
        options.readiness_mode,
        options.enable_search,
        s3_client=s3_client,
        verify_s3_objects=settings.verify_s3_objects_for_readiness,
    )
    if not readiness.ready:
        raise_http_error(ErrorCode.SESSION_NOT_READY, "Session not ready")
    if session_item.get("status") != "READY":
        raise_http_error(ErrorCode.SESSION_NOT_READY, "Session not ready")

    execution_id = _new_execution_id()
    started_at = _utc_now()

    models = _resolve_models(request.models, session_item, settings)
    budgets = _resolve_budgets(request.budgets, session_item, settings)

    execution_options = _normalize_execution_options(request.options, settings)

    ddb.create_execution(
        executions_table,
        tenant_id=context.tenant_id,
        session_id=session_id,
        execution_id=execution_id,
        status="RUNNING",
        mode="ANSWERER",
        question=request.question,
        budgets_requested=_serialize_model(budgets),
        models=_serialize_model(models),
        options=_serialize_model(execution_options),
        started_at=_format_timestamp(started_at),
    )

    state_record = state_store.persist_state_payload(
        state={},
        tenant_id=context.tenant_id,
        execution_id=execution_id,
        turn_index=0,
    )
    ddb.put_execution_state(
        execution_state_table,
        execution_id=execution_id,
        turn_index=0,
        updated_at=_format_timestamp(started_at),
        ttl_epoch=int(session_item["ttl_epoch"]),
        state_json=state_record.state_json,
        state_s3_uri=state_record.state_s3_uri,
        checksum=state_record.checksum,
        summary=state_record.summary,
    )

    logger.info(
        "executions.create",
        tenant_id=context.tenant_id,
        session_id=session_id,
        execution_id=execution_id,
    )

    return CreateExecutionResponse(execution_id=execution_id, status="RUNNING")


@router.get("/executions/{execution_id}", response_model=ExecutionStatusResponse)
def get_execution(
    execution_id: str,
    context: ApiKeyContext = Depends(require_api_key),
    ddb_resource: ServiceResource = Depends(get_ddb_resource),
    table_names: DdbTableNames = Depends(get_table_names),
    logger: BoundLogger = Depends(get_logger),
) -> ExecutionStatusResponse:
    executions_table = ddb_resource.Table(table_names.executions)
    item = _get_execution_for_tenant(executions_table, execution_id, context.tenant_id)
    ensure_tenant_access(item, context.tenant_id)

    logger.info(
        "executions.get",
        tenant_id=context.tenant_id,
        session_id=item.get("session_id"),
        execution_id=execution_id,
        status=item.get("status"),
    )

    return _build_execution_response(item)


@router.get(
    "/executions/{execution_id}/contexts",
    response_model=ExecutionContextsResponse,
)
def get_execution_contexts(
    execution_id: str,
    context: ApiKeyContext = Depends(require_api_key),
    ddb_resource: ServiceResource = Depends(get_ddb_resource),
    table_names: DdbTableNames = Depends(get_table_names),
    s3_client: BaseClient = Depends(get_s3_client),
    logger: BoundLogger = Depends(get_logger),
) -> ExecutionContextsResponse:
    executions_table = ddb_resource.Table(table_names.executions)
    item = _get_execution_for_tenant(executions_table, execution_id, context.tenant_id)
    ensure_tenant_access(item, context.tenant_id)

    contexts_payload = _load_contexts_payload(item, s3_client=s3_client)

    logger.info(
        "executions.contexts",
        tenant_id=context.tenant_id,
        session_id=item.get("session_id"),
        execution_id=execution_id,
        returned=len(contexts_payload),
    )

    return ExecutionContextsResponse(contexts=contexts_payload)


@router.get(
    "/executions/{execution_id}/evaluation",
    response_model=ExecutionEvaluationResponse,
)
def get_execution_evaluation(
    execution_id: str,
    context: ApiKeyContext = Depends(require_api_key),
    ddb_resource: ServiceResource = Depends(get_ddb_resource),
    table_names: DdbTableNames = Depends(get_table_names),
    logger: BoundLogger = Depends(get_logger),
) -> ExecutionEvaluationResponse:
    executions_table = ddb_resource.Table(table_names.executions)
    execution_item = _get_execution_for_tenant(
        executions_table, execution_id, context.tenant_id
    )
    ensure_tenant_access(execution_item, context.tenant_id)

    mode = execution_item.get("mode")
    if mode == "RUNTIME":
        raise_http_error(
            ErrorCode.VALIDATION_ERROR,
            "Evaluation is not available for runtime executions",
        )

    evaluations_table = ddb_resource.Table(table_names.evaluations)
    evaluation_item = ddb.get_evaluation(evaluations_table, execution_id=execution_id)
    if evaluation_item is None:
        raise_http_error(ErrorCode.EXECUTION_NOT_FOUND, "Evaluation not found")
    ensure_tenant_access(evaluation_item, context.tenant_id)

    logger.info(
        "executions.evaluation",
        tenant_id=context.tenant_id,
        session_id=execution_item.get("session_id"),
        execution_id=execution_id,
        status=evaluation_item.get("baseline_status"),
    )

    return _build_evaluation_response(evaluation_item)


@router.post(
    "/executions/{execution_id}/evaluation/recompute",
    response_model=ExecutionEvaluationResponse,
)
def recompute_execution_evaluation(
    execution_id: str,
    request: RecomputeEvaluationRequest,
    context: ApiKeyContext = Depends(require_api_key),
    settings: Settings = Depends(get_settings),
    ddb_resource: ServiceResource = Depends(get_ddb_resource),
    s3_client: BaseClient = Depends(get_s3_client),
    table_names: DdbTableNames = Depends(get_table_names),
    logger: BoundLogger = Depends(get_logger),
) -> ExecutionEvaluationResponse:
    executions_table = ddb_resource.Table(table_names.executions)
    execution_item = _get_execution_for_tenant(executions_table, execution_id, context.tenant_id)
    ensure_tenant_access(execution_item, context.tenant_id)

    mode = execution_item.get("mode")
    if mode == "RUNTIME":
        raise_http_error(
            ErrorCode.VALIDATION_ERROR,
            "Evaluation is not available for runtime executions",
        )
    status = str(execution_item.get("status") or "")
    if status != "COMPLETED":
        raise_http_error(
            ErrorCode.VALIDATION_ERROR,
            "Evaluation can only be recomputed for completed executions",
        )

    if request.recompute_baseline:
        raise_http_error(
            ErrorCode.VALIDATION_ERROR,
            "Baseline recomputation is not supported from the API",
        )

    worker = OrchestratorWorker(
        settings=settings,
        ddb_resource=ddb_resource,
        table_names=table_names,
        s3_client=s3_client,
        provider=FakeLLMProvider(),
        logger=logger,
    )
    ok = worker.recompute_evaluation(
        execution_id=execution_id,
        tenant_id=context.tenant_id,
        recompute_baseline=False,
    )
    if not ok:
        raise_http_error(ErrorCode.EXECUTION_NOT_FOUND, "Evaluation not found")

    evaluations_table = ddb_resource.Table(table_names.evaluations)
    evaluation_item = ddb.get_evaluation(evaluations_table, execution_id=execution_id)
    if evaluation_item is None:
        raise_http_error(ErrorCode.EXECUTION_NOT_FOUND, "Evaluation not found")
    ensure_tenant_access(evaluation_item, context.tenant_id)

    logger.info(
        "executions.evaluation_recompute",
        tenant_id=context.tenant_id,
        session_id=execution_item.get("session_id"),
        execution_id=execution_id,
    )

    return _build_evaluation_response(evaluation_item)


@router.get("/executions/{execution_id}/code", response_model=CodeLogResponse)
def get_execution_code(
    execution_id: str,
    limit: int = Query(200, ge=1, le=1000),
    cursor: str | None = Query(default=None),
    context: ApiKeyContext = Depends(require_api_key),
    ddb_resource: ServiceResource = Depends(get_ddb_resource),
    table_names: DdbTableNames = Depends(get_table_names),
    logger: BoundLogger = Depends(get_logger),
) -> CodeLogResponse:
    executions_table = ddb_resource.Table(table_names.executions)
    code_log_table = ddb_resource.Table(table_names.code_log)

    execution_item = _get_execution_for_tenant(
        executions_table, execution_id, context.tenant_id
    )
    ensure_tenant_access(execution_item, context.tenant_id)

    start_key = _decode_cursor(cursor) if cursor else None
    items, last_key = ddb.list_code_log_entries(
        code_log_table,
        execution_id=execution_id,
        limit=limit,
        exclusive_start_key=start_key,
    )
    entries: list[CodeLogEntry] = []
    for item in items:
        turn_index = item.get("turn_index")
        if turn_index is not None:
            try:
                turn_index = int(turn_index)
            except (TypeError, ValueError):
                turn_index = None
        entries.append(
            CodeLogEntry(
                execution_id=str(item.get("execution_id") or execution_id),
                sequence=int(item.get("sequence") or 0),
                turn_index=turn_index,
                created_at=str(item.get("created_at") or ""),
                source=str(item["source"]),
                kind=str(item["kind"]),
                model_name=item.get("model_name"),
                tool_type=item.get("tool_type"),
                content=state_store.normalize_json_value(item.get("content")),
            )
        )
    next_cursor = None
    if items:
        key = last_key or {"PK": items[-1]["PK"], "SK": items[-1]["SK"]}
        next_cursor = _encode_cursor(key)

    logger.info(
        "executions.code",
        tenant_id=context.tenant_id,
        execution_id=execution_id,
        returned=len(entries),
    )

    return CodeLogResponse(entries=entries, next_cursor=next_cursor)


@router.post("/executions/{execution_id}/cancel", response_model=ExecutionStatusResponse)
def cancel_execution(
    execution_id: str,
    context: ApiKeyContext = Depends(require_api_key),
    ddb_resource: ServiceResource = Depends(get_ddb_resource),
    table_names: DdbTableNames = Depends(get_table_names),
    logger: BoundLogger = Depends(get_logger),
) -> ExecutionStatusResponse:
    executions_table = ddb_resource.Table(table_names.executions)
    item = _get_execution_for_tenant(executions_table, execution_id, context.tenant_id)
    ensure_tenant_access(item, context.tenant_id)

    status = str(item.get("status") or "")
    if status != "RUNNING":
        logger.info(
            "executions.cancel",
            tenant_id=context.tenant_id,
            session_id=item.get("session_id"),
            execution_id=execution_id,
            status=status,
            cancelled=False,
        )
        return _build_execution_response(item)

    session_id = str(item.get("session_id") or "")
    if not session_id:
        raise_http_error(ErrorCode.EXECUTION_NOT_FOUND, "Execution not found")

    updated = ddb.update_execution_status(
        executions_table,
        session_id=session_id,
        execution_id=execution_id,
        expected_status="RUNNING",
        new_status="CANCELLED",
        completed_at=_format_timestamp(_utc_now()),
    )
    if not updated:
        item = _get_execution_for_tenant(executions_table, execution_id, context.tenant_id)
        ensure_tenant_access(item, context.tenant_id)
        return _build_execution_response(item)

    item = ddb.get_execution(executions_table, session_id=session_id, execution_id=execution_id)
    if item is None:
        raise_http_error(ErrorCode.EXECUTION_NOT_FOUND, "Execution not found")

    logger.info(
        "executions.cancel",
        tenant_id=context.tenant_id,
        session_id=session_id,
        execution_id=execution_id,
        status="CANCELLED",
        cancelled=True,
    )

    return _build_execution_response(item)


@router.get("/executions/{execution_id}/steps", response_model=ExecutionStepHistoryResponse)
def get_execution_steps(
    execution_id: str,
    context: ApiKeyContext = Depends(require_api_key),
    ddb_resource: ServiceResource = Depends(get_ddb_resource),
    table_names: DdbTableNames = Depends(get_table_names),
    s3_client: BaseClient = Depends(get_s3_client),
    logger: BoundLogger = Depends(get_logger),
) -> ExecutionStepHistoryResponse:
    executions_table = ddb_resource.Table(table_names.executions)
    item = _get_execution_for_tenant(executions_table, execution_id, context.tenant_id)
    ensure_tenant_access(item, context.tenant_id)

    execution_state_table = ddb_resource.Table(table_names.execution_state)
    state_items = ddb.list_execution_state_steps(
        execution_state_table,
        execution_id=execution_id,
    )
    steps: list[ExecutionStepSnapshot] = []
    for state_item in state_items:
        state_payload = _load_state_payload(state_item, s3_client=s3_client)
        step = ExecutionStepSnapshot(
            turn_index=int(state_item.get("turn_index", 0)),
            updated_at=state_item.get("updated_at"),
            success=state_item.get("success"),
            stdout=state_item.get("stdout"),
            state=state_payload,
            span_log=state_store.normalize_json_value(state_item.get("span_log") or []),
            tool_requests=state_store.normalize_json_value(state_item.get("tool_requests")),
            final=state_store.normalize_json_value(state_item.get("final")),
            error=state_store.normalize_json_value(state_item.get("error")),
            checksum=state_item.get("checksum"),
            summary=state_store.normalize_json_value(state_item.get("summary")),
            timings=state_store.normalize_json_value(state_item.get("timings")),
        )
        steps.append(step)

    steps.sort(key=lambda step: step.turn_index)

    logger.info(
        "executions.steps",
        tenant_id=context.tenant_id,
        session_id=item.get("session_id"),
        execution_id=execution_id,
        returned=len(steps),
    )

    return ExecutionStepHistoryResponse(steps=steps)


@router.get("/executions", response_model=ListExecutionsResponse)
def list_executions(
    status: ExecutionStatus | None = None,
    mode: str | None = None,
    session_id: str | None = None,
    limit: int = Query(default=100),
    cursor: str | None = None,
    context: ApiKeyContext = Depends(require_api_key),
    ddb_resource: ServiceResource = Depends(get_ddb_resource),
    table_names: DdbTableNames = Depends(get_table_names),
    logger: BoundLogger = Depends(get_logger),
) -> ListExecutionsResponse:
    if limit < 1 or limit > 1000:
        raise_http_error(ErrorCode.VALIDATION_ERROR, "limit must be between 1 and 1000")

    start_key = _decode_cursor(cursor) if cursor else None
    executions_table = ddb_resource.Table(table_names.executions)

    items, last_key = _scan_executions(
        executions_table,
        tenant_id=context.tenant_id,
        status=status,
        mode=mode,
        session_prefix=session_id,
        limit=limit,
        start_key=start_key,
    )

    logger.info(
        "executions.list",
        tenant_id=context.tenant_id,
        status=status,
        mode=mode,
        session_id_prefix=session_id,
        limit=limit,
        returned=len(items),
    )

    next_cursor = _encode_cursor(last_key) if last_key else None
    return ListExecutionsResponse(
        executions=[_build_execution_list_item(item) for item in items],
        next_cursor=next_cursor,
    )


@router.post("/executions/{execution_id}/wait", response_model=ExecutionStatusResponse)
def wait_execution(
    execution_id: str,
    request: ExecutionWaitRequest,
    context: ApiKeyContext = Depends(require_api_key),
    ddb_resource: ServiceResource = Depends(get_ddb_resource),
    table_names: DdbTableNames = Depends(get_table_names),
    logger: BoundLogger = Depends(get_logger),
) -> ExecutionStatusResponse:
    if request.timeout_seconds < 0:
        raise_http_error(ErrorCode.VALIDATION_ERROR, "timeout_seconds must be non-negative")

    executions_table = ddb_resource.Table(table_names.executions)
    item = _get_execution_for_tenant(executions_table, execution_id, context.tenant_id)
    ensure_tenant_access(item, context.tenant_id)

    session_id = str(item["session_id"])
    item = _wait_for_execution(
        executions_table,
        execution_id=execution_id,
        session_id=session_id,
        tenant_id=context.tenant_id,
        timeout_seconds=request.timeout_seconds,
    )

    logger.info(
        "executions.wait",
        tenant_id=context.tenant_id,
        session_id=session_id,
        execution_id=execution_id,
        status=item.get("status"),
        timeout_seconds=request.timeout_seconds,
    )

    return _build_execution_response(item)


@router.post(
    "/sessions/{session_id}/executions/runtime",
    response_model=CreateRuntimeExecutionResponse,
)
def create_runtime_execution(
    session_id: str,
    context: ApiKeyContext = Depends(require_api_key),
    settings: Settings = Depends(get_settings),
    s3_client: BaseClient = Depends(get_s3_client),
    ddb_resource: ServiceResource = Depends(get_ddb_resource),
    table_names: DdbTableNames = Depends(get_table_names),
    logger: BoundLogger = Depends(get_logger),
) -> CreateRuntimeExecutionResponse:
    sessions_table = ddb_resource.Table(table_names.sessions)
    documents_table = ddb_resource.Table(table_names.documents)
    executions_table = ddb_resource.Table(table_names.executions)
    execution_state_table = ddb_resource.Table(table_names.execution_state)

    session_item = ddb.get_session(
        sessions_table, tenant_id=context.tenant_id, session_id=session_id
    )
    if session_item is None:
        _detect_foreign_session(documents_table, session_id, context.tenant_id)
        raise_http_error(ErrorCode.SESSION_NOT_FOUND, "Session not found")
    ensure_tenant_access(session_item, context.tenant_id)

    if session_item.get("status") == "EXPIRED":
        raise_http_error(ErrorCode.SESSION_EXPIRED, "Session expired")

    options = _normalize_options(
        SessionOptions.model_validate(session_item.get("options") or {}), settings
    )
    document_items = _query_session_documents(documents_table, session_id)
    for item in document_items:
        ensure_tenant_access(item, context.tenant_id)
    readiness = _compute_readiness(
        document_items,
        options.readiness_mode,
        options.enable_search,
        s3_client=s3_client,
        verify_s3_objects=settings.verify_s3_objects_for_readiness,
    )
    if not readiness.ready:
        raise_http_error(ErrorCode.SESSION_NOT_READY, "Session not ready")
    if session_item.get("status") != "READY":
        raise_http_error(ErrorCode.SESSION_NOT_READY, "Session not ready")

    execution_id = _new_execution_id()
    started_at = _utc_now()

    ddb.create_execution(
        executions_table,
        tenant_id=context.tenant_id,
        session_id=session_id,
        execution_id=execution_id,
        status="RUNNING",
        mode="RUNTIME",
        started_at=_format_timestamp(started_at),
    )

    state_payload: dict[str, JsonValue] = {
        "_tool_results": {"llm": {}, "search": {}},
        "_tool_status": {},
        "_tool_schema": build_tool_schema(
            subcalls_enabled=True,
            search_enabled=options.enable_search,
        ),
    }
    state_record = state_store.persist_state_payload(
        state=state_payload,
        tenant_id=context.tenant_id,
        execution_id=execution_id,
        turn_index=-1,
    )
    ddb.put_execution_state(
        execution_state_table,
        execution_id=execution_id,
        turn_index=-1,
        updated_at=_format_timestamp(started_at),
        ttl_epoch=int(session_item["ttl_epoch"]),
        state_json=state_record.state_json,
        state_s3_uri=state_record.state_s3_uri,
        checksum=state_record.checksum,
        summary=state_record.summary,
    )

    logger.info(
        "executions.runtime.create",
        tenant_id=context.tenant_id,
        session_id=session_id,
        execution_id=execution_id,
    )

    return CreateRuntimeExecutionResponse(execution_id=execution_id, status="RUNNING")


@router.post("/executions/{execution_id}/steps", response_model=StepResult)
def runtime_step(
    execution_id: str,
    request: StepRequest,
    context: ApiKeyContext = Depends(require_api_key),
    settings: Settings = Depends(get_settings),
    sandbox_runner: SandboxRunner = Depends(get_sandbox_runner),
    ddb_resource: ServiceResource = Depends(get_ddb_resource),
    table_names: DdbTableNames = Depends(get_table_names),
    s3_client: BaseClient = Depends(get_s3_client),
    logger: BoundLogger = Depends(get_logger),
) -> StepResult:
    sessions_table = ddb_resource.Table(table_names.sessions)
    documents_table = ddb_resource.Table(table_names.documents)
    executions_table = ddb_resource.Table(table_names.executions)
    execution_state_table = ddb_resource.Table(table_names.execution_state)
    code_log_table = ddb_resource.Table(table_names.code_log)

    execution_item = _get_execution_for_tenant(
        executions_table, execution_id, context.tenant_id
    )
    ensure_tenant_access(execution_item, context.tenant_id)
    if execution_item.get("mode") != "RUNTIME":
        raise_http_error(ErrorCode.VALIDATION_ERROR, "Execution is not runtime mode")
    if execution_item.get("status") != "RUNNING":
        raise_http_error(ErrorCode.VALIDATION_ERROR, "Execution is not running")

    session_id = str(execution_item["session_id"])
    session_item = ddb.get_session(
        sessions_table, tenant_id=context.tenant_id, session_id=session_id
    )
    if session_item is None:
        raise_http_error(ErrorCode.SESSION_NOT_FOUND, "Session not found")
    ensure_tenant_access(session_item, context.tenant_id)
    if session_item.get("status") == "EXPIRED":
        raise_http_error(ErrorCode.SESSION_EXPIRED, "Session expired")

    options = _normalize_options(
        SessionOptions.model_validate(session_item.get("options") or {}), settings
    )
    document_items = _query_session_documents(documents_table, session_id)
    for item in document_items:
        ensure_tenant_access(item, context.tenant_id)
    readiness = _compute_readiness(
        document_items,
        options.readiness_mode,
        options.enable_search,
    )
    if not readiness.ready:
        raise_http_error(ErrorCode.SESSION_NOT_READY, "Session not ready")
    if session_item.get("status") != "READY":
        raise_http_error(ErrorCode.SESSION_NOT_READY, "Session not ready")

    state_item = ddb.get_execution_state(execution_state_table, execution_id=execution_id)
    if state_item is None:
        raise_http_error(ErrorCode.EXECUTION_NOT_FOUND, "Execution state not found")

    stored_state = _load_state_payload(state_item, s3_client=s3_client)
    if request.state is None:
        state_input = stored_state
    else:
        state_input = request.state
        if isinstance(state_input, dict) and isinstance(stored_state, dict):
            state_input = _merge_reserved_state(state_input, stored_state)

    if isinstance(state_input, dict):
        try:
            _ensure_tool_state(state_input)
        except state_store.StateValidationError as exc:
            raise_http_error(ErrorCode.STATE_INVALID_TYPE, str(exc))
        state_input["_tool_schema"] = build_tool_schema(
            subcalls_enabled=True,
            search_enabled=options.enable_search,
        )

    turn_index = int(state_item.get("turn_index", -1)) + 1
    budgets = _resolve_budgets(None, session_item, settings)
    limits = _limits_from_budgets(budgets)

    event = StepEvent(
        tenant_id=context.tenant_id,
        session_id=session_id,
        execution_id=execution_id,
        turn_index=turn_index,
        code=request.code,
        state=state_input,
        context_manifest=_build_context_manifest(document_items),
        tool_results=_tool_results_from_state(state_input),
        limits=limits,
    )

    timings: dict[str, int] = {}
    sandbox_start = time.perf_counter()
    result = sandbox_runner.run(
        event,
        s3_client=s3_client,
        region=settings.aws_region,
        endpoint_url=settings.localstack_endpoint_url,
    )
    timings["sandbox_ms"] = int((time.perf_counter() - sandbox_start) * 1000)

    state_persist_start = time.perf_counter()
    try:
        state_record = state_store.persist_state_payload(
            state=result.state,
            tenant_id=context.tenant_id,
            execution_id=execution_id,
            turn_index=turn_index,
            s3_client=s3_client,
            bucket=settings.s3_bucket,
        )
    except state_store.StateValidationError as exc:
        raise_http_error(ErrorCode.STATE_INVALID_TYPE, str(exc))
    except state_store.StateOffloadError as exc:
        raise_http_error(ErrorCode.STATE_TOO_LARGE, str(exc))
    timings["state_persist_ms"] = int((time.perf_counter() - state_persist_start) * 1000)

    updated_at = _format_timestamp(_utc_now())
    tool_requests_payload = (
        result.tool_requests or ToolRequestsEnvelope()
    ).model_dump(exclude_none=True)
    span_log_payload = [
        entry.model_dump(exclude_none=True) for entry in result.span_log
    ]
    final_payload = result.final.model_dump(exclude_none=True) if result.final else None
    error_payload = result.error.model_dump(exclude_none=True) if result.error else None

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
        timings=timings,
        success=result.success,
        stdout=result.stdout,
        span_log=span_log_payload,
        tool_requests=tool_requests_payload,
        final=final_payload,
        error=error_payload,
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
        timings=timings,
        success=result.success,
        stdout=result.stdout,
        span_log=span_log_payload,
        tool_requests=tool_requests_payload,
        final=final_payload,
        error=error_payload,
    )
    if result.tool_requests:
        code_logger = code_log.CodeLogWriter(
            table=code_log_table,
            execution_id=execution_id,
            settings=settings,
            logger=logger,
        )
        code_logger.write(code_log.build_tool_request_entries(result.tool_requests))

    if result.final and result.final.is_final:
        completed_at = _utc_now()
        updated = ddb.update_execution_status(
            executions_table,
            session_id=session_id,
            execution_id=execution_id,
            expected_status="RUNNING",
            new_status="COMPLETED",
            answer=result.final.answer or "",
            completed_at=_format_timestamp(completed_at),
        )
        if not updated:
            execution_item = ddb.get_execution(
                executions_table,
                session_id=session_id,
                execution_id=execution_id,
            )
            if execution_item is None:
                raise_http_error(ErrorCode.EXECUTION_NOT_FOUND, "Execution not found")
            if execution_item.get("status") == "RUNNING":
                raise_http_error(
                    ErrorCode.INTERNAL_ERROR,
                    "Failed to update execution status",
                )

    logger.info(
        "executions.runtime.step",
        tenant_id=context.tenant_id,
        session_id=session_id,
        execution_id=execution_id,
        turn_index=turn_index,
        success=result.success,
    )

    return result


@router.post("/executions/{execution_id}/tools/resolve", response_model=ToolResolveResponse)
def resolve_tools(
    execution_id: str,
    request: ToolResolveRequest,
    context: ApiKeyContext = Depends(require_api_key),
    settings: Settings = Depends(get_settings),
    ddb_resource: ServiceResource = Depends(get_ddb_resource),
    table_names: DdbTableNames = Depends(get_table_names),
    s3_client: BaseClient = Depends(get_s3_client),
    logger: BoundLogger = Depends(get_logger),
) -> ToolResolveResponse:
    sessions_table = ddb_resource.Table(table_names.sessions)
    executions_table = ddb_resource.Table(table_names.executions)
    execution_state_table = ddb_resource.Table(table_names.execution_state)
    code_log_table = ddb_resource.Table(table_names.code_log)

    execution_item = _get_execution_for_tenant(
        executions_table, execution_id, context.tenant_id
    )
    ensure_tenant_access(execution_item, context.tenant_id)
    if execution_item.get("mode") != "RUNTIME":
        raise_http_error(ErrorCode.VALIDATION_ERROR, "Execution is not runtime mode")
    if execution_item.get("status") != "RUNNING":
        raise_http_error(ErrorCode.VALIDATION_ERROR, "Execution is not running")

    session_id = str(execution_item["session_id"])
    session_item = ddb.get_session(
        sessions_table, tenant_id=context.tenant_id, session_id=session_id
    )
    if session_item is None:
        raise_http_error(ErrorCode.SESSION_NOT_FOUND, "Session not found")
    ensure_tenant_access(session_item, context.tenant_id)

    state_item = ddb.get_execution_state(execution_state_table, execution_id=execution_id)
    if state_item is None:
        raise_http_error(ErrorCode.EXECUTION_NOT_FOUND, "Execution state not found")

    state_payload = _load_state_payload(state_item, s3_client=s3_client)
    if not isinstance(state_payload, dict):
        raise_http_error(ErrorCode.STATE_INVALID_TYPE, "State must be a JSON object")

    try:
        _ensure_tool_state(state_payload)
    except state_store.StateValidationError as exc:
        raise_http_error(ErrorCode.STATE_INVALID_TYPE, str(exc))

    options = _normalize_options(
        SessionOptions.model_validate(session_item.get("options") or {}), settings
    )
    tool_results, statuses = _resolve_tool_requests(
        request,
        enable_search=options.enable_search,
    )

    tool_results_state = state_payload["_tool_results"]
    tool_status_state = state_payload["_tool_status"]
    if not isinstance(tool_results_state, dict) or not isinstance(tool_status_state, dict):
        raise_http_error(ErrorCode.STATE_INVALID_TYPE, "Tool state is invalid")

    llm_bucket = tool_results_state.setdefault("llm", {})
    search_bucket = tool_results_state.setdefault("search", {})
    if not isinstance(llm_bucket, dict) or not isinstance(search_bucket, dict):
        raise_http_error(ErrorCode.STATE_INVALID_TYPE, "Tool state is invalid")

    for key, result in tool_results.llm.items():
        llm_bucket[key] = result.model_dump(exclude_none=True)
    for key, result in tool_results.search.items():
        search_bucket[key] = result.model_dump(exclude_none=True)
    for key, status in statuses.items():
        tool_status_state[key] = status

    turn_index = int(state_item.get("turn_index", 0))
    try:
        state_record = state_store.persist_state_payload(
            state=state_payload,
            tenant_id=context.tenant_id,
            execution_id=execution_id,
            turn_index=turn_index,
            s3_client=s3_client,
            bucket=settings.s3_bucket,
        )
    except state_store.StateValidationError as exc:
        raise_http_error(ErrorCode.STATE_INVALID_TYPE, str(exc))
    except state_store.StateOffloadError as exc:
        raise_http_error(ErrorCode.STATE_TOO_LARGE, str(exc))

    updated_at = _format_timestamp(_utc_now())
    step_snapshot = _extract_step_snapshot(state_item)
    ddb.put_execution_state(
        execution_state_table,
        execution_id=execution_id,
        turn_index=turn_index,
        updated_at=updated_at,
        ttl_epoch=int(state_item["ttl_epoch"]),
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
        ttl_epoch=int(state_item["ttl_epoch"]),
        state_json=state_record.state_json,
        state_s3_uri=state_record.state_s3_uri,
        checksum=state_record.checksum,
        summary=state_record.summary,
        **step_snapshot,
    )
    code_logger = code_log.CodeLogWriter(
        table=code_log_table,
        execution_id=execution_id,
        settings=settings,
        logger=logger,
    )
    code_logger.write(code_log.build_tool_result_entries(tool_results, statuses))

    logger.info(
        "executions.runtime.resolve_tools",
        tenant_id=context.tenant_id,
        session_id=session_id,
        execution_id=execution_id,
        resolved=len(statuses),
    )

    return ToolResolveResponse(
        tool_results=tool_results,
        statuses=statuses,
    )
