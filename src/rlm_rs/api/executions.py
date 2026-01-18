from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Mapping
from urllib.parse import urlparse
from uuid import uuid4

from boto3.resources.base import ServiceResource
from botocore.client import BaseClient
from fastapi import APIRouter, Depends
from pydantic import JsonValue
from structlog.stdlib import BoundLogger

from rlm_rs.api.auth import ApiKeyContext, ensure_tenant_access, require_api_key
from rlm_rs.api.dependencies import (
    get_ddb_resource,
    get_logger,
    get_s3_client,
    get_settings,
    get_table_names,
)
from rlm_rs.api.rate_limits import enforce_rate_limit
from rlm_rs.api.sessions import _compute_readiness, _detect_foreign_session, _normalize_options
from rlm_rs.api.sessions import _query_documents as _query_session_documents
from rlm_rs.errors import ErrorCode, raise_http_error
from rlm_rs.models import (
    Budgets,
    ContextDocument,
    ContextManifest,
    CreateExecutionRequest,
    CreateExecutionResponse,
    CreateRuntimeExecutionResponse,
    ExecutionOptions,
    ExecutionStatusResponse,
    ExecutionWaitRequest,
    LimitsSnapshot,
    LLMToolResult,
    ModelsConfig,
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
from rlm_rs.settings import Settings
from rlm_rs.sandbox.step_executor import execute_step
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
    try:
        state_store.validate_state_payload(state_json)
    except state_store.StateValidationError as exc:
        raise_http_error(ErrorCode.STATE_INVALID_TYPE, str(exc))
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
    return ExecutionStatusResponse(
        execution_id=str(item["execution_id"]),
        status=str(item["status"]),
        answer=item.get("answer"),
        citations=item.get("citations"),
        budgets_consumed=item.get("budgets_consumed"),
        trace_s3_uri=item.get("trace_s3_uri") if return_trace else None,
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
    ddb_resource: ServiceResource = Depends(get_ddb_resource),
    table_names: DdbTableNames = Depends(get_table_names),
    s3_client: BaseClient = Depends(get_s3_client),
    logger: BoundLogger = Depends(get_logger),
) -> StepResult:
    sessions_table = ddb_resource.Table(table_names.sessions)
    documents_table = ddb_resource.Table(table_names.documents)
    executions_table = ddb_resource.Table(table_names.executions)
    execution_state_table = ddb_resource.Table(table_names.execution_state)

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

    result = execute_step(
        event,
        s3_client=s3_client,
        region=settings.aws_region,
        endpoint_url=settings.localstack_endpoint_url,
    )

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
        success=result.success,
        stdout=result.stdout,
        span_log=span_log_payload,
        tool_requests=tool_requests_payload,
        final=final_payload,
        error=error_payload,
    )

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
            raise_http_error(ErrorCode.INTERNAL_ERROR, "Failed to update execution status")

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
