from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping
from uuid import uuid4

from boto3.dynamodb.conditions import Key
from boto3.resources.base import ServiceResource
from fastapi import APIRouter, Depends
from structlog.stdlib import BoundLogger

from rlm_rs.api.auth import ApiKeyContext, ensure_tenant_access, require_api_key
from rlm_rs.api.dependencies import (
    get_ddb_resource,
    get_logger,
    get_settings,
    get_table_names,
)
from rlm_rs.api.rate_limits import enforce_rate_limit
from rlm_rs.errors import ErrorCode, raise_http_error
from rlm_rs.models import (
    Budgets,
    CreateSessionRequest,
    CreateSessionResponse,
    DeleteSessionResponse,
    GetSessionResponse,
    ModelsConfig,
    SessionDocumentStatus,
    SessionOptions,
    SessionReadiness,
)
from rlm_rs.settings import Settings
from rlm_rs.storage import ddb
from rlm_rs.storage.ddb import DdbTableNames


router = APIRouter(prefix="/v1", dependencies=[Depends(enforce_rate_limit)])

_SESSION_PREFIX = "sess_"
_DOC_PREFIX = "doc_"
_PARSED_READY = {"PARSED", "INDEXING", "INDEXED"}
_SEARCH_READY = {"INDEXED"}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _format_timestamp(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _new_id(prefix: str) -> str:
    return f"{prefix}{uuid4().hex}"


def _normalize_options(options: SessionOptions | None, settings: Settings) -> SessionOptions:
    payload: dict[str, Any] = {} if options is None else options.model_dump(exclude_none=True)
    if "enable_search" not in payload:
        payload["enable_search"] = settings.enable_search
    if "readiness_mode" not in payload:
        payload["readiness_mode"] = "LAX"
    return SessionOptions.model_validate(payload)


def _resolve_models_default(
    request_models: ModelsConfig | None, settings: Settings
) -> ModelsConfig | None:
    if request_models is not None:
        return request_models
    if settings.default_models_json is None:
        return None
    return ModelsConfig.model_validate(settings.default_models_json)


def _resolve_budgets_default(
    request_budgets: Budgets | None, settings: Settings
) -> Budgets | None:
    if request_budgets is not None:
        return request_budgets
    if settings.default_budgets_json is None:
        return None
    return Budgets.model_validate(settings.default_budgets_json)


def _serialize_model(model: ModelsConfig | Budgets | SessionOptions | None) -> dict[str, Any] | None:
    if model is None:
        return None
    return model.model_dump(exclude_none=True)


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


def _detect_foreign_session(
    documents_table: Any, session_id: str, tenant_id: str
) -> None:
    for item in _query_documents(documents_table, session_id):
        doc_tenant = item.get("tenant_id")
        if doc_tenant and doc_tenant != tenant_id:
            raise_http_error(ErrorCode.FORBIDDEN, "Forbidden")


def _build_document_status(item: Mapping[str, Any]) -> SessionDocumentStatus:
    return SessionDocumentStatus(
        doc_id=str(item["doc_id"]),
        doc_index=int(item["doc_index"]),
        ingest_status=str(item["ingest_status"]),
        text_s3_uri=item.get("text_s3_uri"),
        meta_s3_uri=item.get("meta_s3_uri"),
        offsets_s3_uri=item.get("offsets_s3_uri"),
    )


def _compute_readiness(
    docs: list[Mapping[str, Any]],
    readiness_mode: str,
    enable_search: bool,
) -> SessionReadiness:
    parsed_ready = all(doc.get("ingest_status") in _PARSED_READY for doc in docs)
    search_ready = all(doc.get("ingest_status") in _SEARCH_READY for doc in docs)
    if not enable_search:
        search_ready = False
    ready = search_ready if readiness_mode == "STRICT" else parsed_ready
    return SessionReadiness(parsed_ready=parsed_ready, search_ready=search_ready, ready=ready)


@router.post("/sessions", response_model=CreateSessionResponse)
def create_session(
    request: CreateSessionRequest,
    context: ApiKeyContext = Depends(require_api_key),
    settings: Settings = Depends(get_settings),
    ddb_resource: ServiceResource = Depends(get_ddb_resource),
    table_names: DdbTableNames = Depends(get_table_names),
    logger: BoundLogger = Depends(get_logger),
) -> CreateSessionResponse:
    if request.ttl_minutes is None:
        raise_http_error(ErrorCode.VALIDATION_ERROR, "ttl_minutes is required")
    if request.ttl_minutes <= 0:
        raise_http_error(ErrorCode.VALIDATION_ERROR, "ttl_minutes must be positive")

    now = _utc_now()
    expires_at_dt = now + timedelta(minutes=request.ttl_minutes)
    session_id = _new_id(_SESSION_PREFIX)

    options = _normalize_options(request.options, settings)
    models_default = _resolve_models_default(request.models_default, settings)
    budgets_default = _resolve_budgets_default(request.budgets_default, settings)

    sessions_table = ddb_resource.Table(table_names.sessions)
    documents_table = ddb_resource.Table(table_names.documents)

    session_item = ddb.create_session(
        sessions_table,
        tenant_id=context.tenant_id,
        session_id=session_id,
        status="CREATING",
        created_at=_format_timestamp(now),
        expires_at=_format_timestamp(expires_at_dt),
        ttl_epoch=int(expires_at_dt.timestamp()),
        doc_count=len(request.docs),
        options=_serialize_model(options),
        models_default=_serialize_model(models_default),
        budgets_default=_serialize_model(budgets_default),
    )

    docs: list[SessionDocumentStatus] = []
    for index, doc in enumerate(request.docs):
        doc_id = _new_id(_DOC_PREFIX)
        ddb.create_document(
            documents_table,
            tenant_id=context.tenant_id,
            session_id=session_id,
            doc_id=doc_id,
            doc_index=index,
            source_name=doc.source_name,
            mime_type=doc.mime_type,
            raw_s3_uri=doc.raw_s3_uri,
            raw_s3_version_id=doc.raw_s3_version_id,
            raw_s3_etag=doc.raw_s3_etag,
            ingest_status="REGISTERED",
        )
        docs.append(
            SessionDocumentStatus(
                doc_id=doc_id,
                doc_index=index,
                ingest_status="REGISTERED",
            )
        )

    logger.info(
        "sessions.create",
        tenant_id=context.tenant_id,
        session_id=session_id,
        doc_count=len(docs),
    )

    return CreateSessionResponse(
        session_id=session_item["session_id"],
        status=session_item["status"],
        created_at=session_item["created_at"],
        expires_at=session_item["expires_at"],
        docs=docs,
    )


@router.get("/sessions/{session_id}", response_model=GetSessionResponse)
def get_session(
    session_id: str,
    context: ApiKeyContext = Depends(require_api_key),
    settings: Settings = Depends(get_settings),
    ddb_resource: ServiceResource = Depends(get_ddb_resource),
    table_names: DdbTableNames = Depends(get_table_names),
    logger: BoundLogger = Depends(get_logger),
) -> GetSessionResponse:
    sessions_table = ddb_resource.Table(table_names.sessions)
    documents_table = ddb_resource.Table(table_names.documents)

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

    document_items = _query_documents(documents_table, session_id)
    for item in document_items:
        ensure_tenant_access(item, context.tenant_id)

    document_items.sort(key=lambda item: item.get("doc_index", 0))
    docs = [_build_document_status(item) for item in document_items]
    readiness = _compute_readiness(document_items, options.readiness_mode, options.enable_search)

    logger.info(
        "sessions.get",
        tenant_id=context.tenant_id,
        session_id=session_id,
        status=session_item.get("status"),
    )

    return GetSessionResponse(
        session_id=session_item["session_id"],
        status=session_item["status"],
        readiness=readiness,
        docs=docs,
    )


@router.delete("/sessions/{session_id}", response_model=DeleteSessionResponse)
def delete_session(
    session_id: str,
    context: ApiKeyContext = Depends(require_api_key),
    ddb_resource: ServiceResource = Depends(get_ddb_resource),
    table_names: DdbTableNames = Depends(get_table_names),
    logger: BoundLogger = Depends(get_logger),
) -> DeleteSessionResponse:
    sessions_table = ddb_resource.Table(table_names.sessions)
    documents_table = ddb_resource.Table(table_names.documents)

    session_item = ddb.get_session(
        sessions_table, tenant_id=context.tenant_id, session_id=session_id
    )
    if session_item is None:
        _detect_foreign_session(documents_table, session_id, context.tenant_id)
        raise_http_error(ErrorCode.SESSION_NOT_FOUND, "Session not found")

    ensure_tenant_access(session_item, context.tenant_id)

    sessions_table.update_item(
        Key=ddb.session_key(context.tenant_id, session_id),
        UpdateExpression="SET #status = :new_status",
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={":new_status": "DELETING"},
    )

    logger.info(
        "sessions.delete",
        tenant_id=context.tenant_id,
        session_id=session_id,
    )

    return DeleteSessionResponse(status="DELETING")
