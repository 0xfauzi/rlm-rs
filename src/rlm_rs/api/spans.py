from __future__ import annotations

from typing import Any, Mapping

from boto3.resources.base import ServiceResource
from botocore.client import BaseClient
from fastapi import APIRouter, Depends
from structlog.stdlib import BoundLogger

from rlm_rs.api.auth import ApiKeyContext, ensure_tenant_access, require_api_key
from rlm_rs.api.dependencies import (
    get_ddb_resource,
    get_logger,
    get_s3_client,
    get_table_names,
)
from rlm_rs.api.rate_limits import enforce_rate_limit
from rlm_rs.api.sessions import _detect_foreign_session
from rlm_rs.errors import ErrorCode, raise_http_error
from rlm_rs.models import (
    CharRange,
    CitationVerifyRequest,
    CitationVerifyResponse,
    ContextDocument,
    SpanGetRequest,
    SpanGetResponse,
    SpanRef,
)
from rlm_rs.orchestrator.citations import checksum_text
from rlm_rs.sandbox.context import DocView
from rlm_rs.storage import ddb
from rlm_rs.storage.ddb import DdbTableNames


router = APIRouter(prefix="/v1", dependencies=[Depends(enforce_rate_limit)])


def _load_session(
    sessions_table: Any,
    documents_table: Any,
    *,
    tenant_id: str,
    session_id: str,
) -> Mapping[str, Any]:
    session_item = ddb.get_session(
        sessions_table, tenant_id=tenant_id, session_id=session_id
    )
    if session_item is None:
        _detect_foreign_session(documents_table, session_id, tenant_id)
        raise_http_error(ErrorCode.SESSION_NOT_FOUND, "Session not found")
    ensure_tenant_access(session_item, tenant_id)
    if session_item.get("status") == "EXPIRED":
        raise_http_error(ErrorCode.SESSION_EXPIRED, "Session expired")
    return session_item


def _load_document(
    documents_table: Any,
    *,
    tenant_id: str,
    session_id: str,
    doc_id: str,
) -> Mapping[str, Any]:
    document_item = ddb.get_document(documents_table, session_id=session_id, doc_id=doc_id)
    if document_item is None:
        raise_http_error(ErrorCode.VALIDATION_ERROR, "Document not found")
    ensure_tenant_access(document_item, tenant_id)
    return document_item


def _read_span_text(
    document_item: Mapping[str, Any],
    *,
    s3_client: BaseClient,
    start_char: int,
    end_char: int,
) -> str:
    if start_char < 0 or end_char < 0:
        raise_http_error(ErrorCode.VALIDATION_ERROR, "Span bounds must be non-negative")
    if end_char < start_char:
        raise_http_error(ErrorCode.VALIDATION_ERROR, "Span end_char precedes start_char")

    text_s3_uri = document_item.get("text_s3_uri")
    offsets_s3_uri = document_item.get("offsets_s3_uri")
    if not text_s3_uri or not offsets_s3_uri:
        raise_http_error(
            ErrorCode.VALIDATION_ERROR,
            "Parsed text and offsets are required for span access",
        )

    context_doc = ContextDocument(
        doc_id=str(document_item["doc_id"]),
        doc_index=int(document_item["doc_index"]),
        text_s3_uri=str(text_s3_uri),
        meta_s3_uri=document_item.get("meta_s3_uri"),
        offsets_s3_uri=str(offsets_s3_uri),
    )
    doc_view = DocView(context_doc, s3_client=s3_client, span_logger=lambda _: None)
    try:
        return doc_view.slice(start_char, end_char, tag=None)
    except (ValueError, IndexError) as exc:
        raise_http_error(ErrorCode.VALIDATION_ERROR, str(exc))
    except Exception as exc:  # noqa: BLE001
        raise_http_error(ErrorCode.S3_READ_ERROR, f"Failed to read span: {exc}")


@router.post("/spans/get", response_model=SpanGetResponse)
def spans_get(
    request: SpanGetRequest,
    context: ApiKeyContext = Depends(require_api_key),
    ddb_resource: ServiceResource = Depends(get_ddb_resource),
    table_names: DdbTableNames = Depends(get_table_names),
    s3_client: BaseClient = Depends(get_s3_client),
    logger: BoundLogger = Depends(get_logger),
) -> SpanGetResponse:
    sessions_table = ddb_resource.Table(table_names.sessions)
    documents_table = ddb_resource.Table(table_names.documents)

    _load_session(
        sessions_table,
        documents_table,
        tenant_id=context.tenant_id,
        session_id=request.session_id,
    )
    document_item = _load_document(
        documents_table,
        tenant_id=context.tenant_id,
        session_id=request.session_id,
        doc_id=request.doc_id,
    )

    text = _read_span_text(
        document_item,
        s3_client=s3_client,
        start_char=request.start_char,
        end_char=request.end_char,
    )
    span_ref = SpanRef(
        tenant_id=context.tenant_id,
        session_id=request.session_id,
        doc_id=str(document_item["doc_id"]),
        doc_index=int(document_item["doc_index"]),
        start_char=request.start_char,
        end_char=request.end_char,
        checksum=checksum_text(text),
    )

    logger.info(
        "spans.get",
        tenant_id=context.tenant_id,
        session_id=request.session_id,
        doc_id=request.doc_id,
        start_char=request.start_char,
        end_char=request.end_char,
    )

    return SpanGetResponse(text=text, ref=span_ref)


@router.post("/citations/verify", response_model=CitationVerifyResponse)
def citations_verify(
    request: CitationVerifyRequest,
    context: ApiKeyContext = Depends(require_api_key),
    ddb_resource: ServiceResource = Depends(get_ddb_resource),
    table_names: DdbTableNames = Depends(get_table_names),
    s3_client: BaseClient = Depends(get_s3_client),
    logger: BoundLogger = Depends(get_logger),
) -> CitationVerifyResponse:
    ref = request.ref
    if ref.tenant_id != context.tenant_id:
        raise_http_error(ErrorCode.FORBIDDEN, "Forbidden")

    sessions_table = ddb_resource.Table(table_names.sessions)
    documents_table = ddb_resource.Table(table_names.documents)

    _load_session(
        sessions_table,
        documents_table,
        tenant_id=context.tenant_id,
        session_id=ref.session_id,
    )
    document_item = _load_document(
        documents_table,
        tenant_id=context.tenant_id,
        session_id=ref.session_id,
        doc_id=ref.doc_id,
    )

    if int(document_item["doc_index"]) != ref.doc_index:
        return CitationVerifyResponse(valid=False)

    text = _read_span_text(
        document_item,
        s3_client=s3_client,
        start_char=ref.start_char,
        end_char=ref.end_char,
    )
    checksum = checksum_text(text)
    valid = checksum == ref.checksum
    response = CitationVerifyResponse(valid=valid)
    if valid:
        response.text = text
        response.source_name = document_item.get("source_name")
        response.char_range = CharRange(
            start_char=ref.start_char, end_char=ref.end_char
        )

    logger.info(
        "citations.verify",
        tenant_id=context.tenant_id,
        session_id=ref.session_id,
        doc_id=ref.doc_id,
        valid=valid,
    )

    return response
