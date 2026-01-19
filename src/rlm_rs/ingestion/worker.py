from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence
from uuid import uuid4

from boto3.dynamodb.conditions import Attr, Key
from boto3.resources.base import ServiceResource
from botocore.client import BaseClient
from structlog.stdlib import BoundLogger

from rlm_rs.logging import get_logger
from rlm_rs.models import SessionOptions
from rlm_rs.parser.client import ParserClient
from rlm_rs.parser.models import (
    ParseFailure,
    ParseOutput,
    ParseRequest,
    ParseSource,
    ParseSuccess,
)
from rlm_rs.search.indexing import index_document, load_search_index_config
from rlm_rs.settings import Settings
from rlm_rs.storage import ddb
from rlm_rs.storage.ddb import DdbTableNames, build_ddb_resource, build_table_names
from rlm_rs.storage.s3 import build_s3_client

_PENDING_STATUSES = ("REGISTERED", "PARSING")
_PARSED_READY = {"PARSED", "INDEXING", "INDEXED"}
_SEARCH_READY = {"INDEXED"}


def _normalize_options(
    options: Mapping[str, Any] | None, settings: Settings
) -> SessionOptions:
    payload = {} if options is None else dict(options)
    if "enable_search" not in payload:
        payload["enable_search"] = settings.enable_search
    if "readiness_mode" not in payload:
        payload["readiness_mode"] = "LAX"
    return SessionOptions.model_validate(payload)


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


def _scan_documents(table: Any, statuses: Sequence[str]) -> list[dict[str, Any]]:
    response = table.scan(FilterExpression=Attr("ingest_status").is_in(list(statuses)))
    items = list(response.get("Items", []))
    while response.get("LastEvaluatedKey"):
        response = table.scan(
            FilterExpression=Attr("ingest_status").is_in(list(statuses)),
            ExclusiveStartKey=response["LastEvaluatedKey"],
        )
        items.extend(response.get("Items", []))
    return items


def _parsed_prefix(bucket: str, tenant_id: str, session_id: str, doc_id: str) -> str:
    return f"s3://{bucket}/parsed/{tenant_id}/{session_id}/{doc_id}/"


@dataclass
class IngestionWorker:
    settings: Settings
    ddb_resource: ServiceResource
    table_names: DdbTableNames
    parser_client: ParserClient
    s3_client: BaseClient
    logger: BoundLogger | None = None

    def __post_init__(self) -> None:
        if self.logger is None:
            self.logger = get_logger("rlm_rs.ingestion")
        if not self.settings.s3_bucket:
            raise ValueError("s3_bucket is required for ingestion")

    def close(self) -> None:
        self.parser_client.close()

    def __enter__(self) -> "IngestionWorker":
        return self

    def __exit__(self, exc_type: Any, exc: Any, exc_tb: Any) -> None:
        self.close()

    def run_once(self, *, limit: int | None = None) -> int:
        documents_table = self.ddb_resource.Table(self.table_names.documents)
        sessions_table = self.ddb_resource.Table(self.table_names.sessions)

        candidates = _scan_documents(documents_table, _PENDING_STATUSES)
        candidates = [
            item
            for item in candidates
            if item.get("ingest_status") in _PENDING_STATUSES
        ]
        candidates.sort(
            key=lambda item: (item.get("session_id", ""), item.get("doc_index", 0))
        )

        processed = 0
        for item in candidates:
            if limit is not None and processed >= limit:
                break
            if self._process_document(item, sessions_table, documents_table):
                processed += 1
        return processed

    def _process_document(
        self,
        item: Mapping[str, Any],
        sessions_table: Any,
        documents_table: Any,
    ) -> bool:
        tenant_id = str(item.get("tenant_id") or "")
        session_id = str(item.get("session_id") or "")
        doc_id = str(item.get("doc_id") or "")
        if not tenant_id or not session_id or not doc_id:
            self.logger.warning("ingestion.skip.missing_ids", item=item)
            return False

        ingest_status = str(item.get("ingest_status") or "")
        if ingest_status not in _PENDING_STATUSES:
            return False

        session_item = ddb.get_session(
            sessions_table, tenant_id=tenant_id, session_id=session_id
        )
        if session_item is None:
            self.logger.warning(
                "ingestion.skip.session_missing",
                tenant_id=tenant_id,
                session_id=session_id,
                doc_id=doc_id,
            )
            return False
        if session_item.get("status") != "CREATING":
            return False

        if ingest_status == "REGISTERED":
            claimed = ddb.update_document_status(
                documents_table,
                session_id=session_id,
                doc_id=doc_id,
                expected_status="REGISTERED",
                new_status="PARSING",
            )
            if not claimed:
                return False

        raw_s3_uri = item.get("raw_s3_uri")
        if not raw_s3_uri:
            self.logger.error(
                "ingestion.skip.missing_raw_uri",
                tenant_id=tenant_id,
                session_id=session_id,
                doc_id=doc_id,
            )
            return False

        request = ParseRequest(
            request_id=uuid4().hex,
            source=ParseSource(
                s3_uri=raw_s3_uri,
                s3_version_id=item.get("raw_s3_version_id"),
                s3_etag=item.get("raw_s3_etag"),
            ),
            output=ParseOutput(
                s3_prefix=_parsed_prefix(
                    self.settings.s3_bucket, tenant_id, session_id, doc_id
                )
            ),
        )

        response = self.parser_client.parse(request)
        if isinstance(response, ParseFailure):
            failure_reason = f"{response.error.code}: {response.error.message}"
            updated = ddb.update_document_status(
                documents_table,
                session_id=session_id,
                doc_id=doc_id,
                expected_status="PARSING",
                new_status="FAILED",
                parser_version=response.parser_version,
                failure_reason=failure_reason,
            )
            if not updated:
                return False
            self.logger.error(
                "ingestion.parse_failed",
                tenant_id=tenant_id,
                session_id=session_id,
                doc_id=doc_id,
                error_code=response.error.code,
            )
            return True

        assert isinstance(response, ParseSuccess)
        updated = ddb.update_document_status(
            documents_table,
            session_id=session_id,
            doc_id=doc_id,
            expected_status="PARSING",
            new_status="PARSED",
            text_s3_uri=response.outputs.text_s3_uri,
            meta_s3_uri=response.outputs.meta_s3_uri,
            offsets_s3_uri=response.outputs.offsets_s3_uri,
            char_length=response.stats.char_length,
            byte_length=response.stats.byte_length,
            page_count=response.stats.page_count,
            parser_version=response.parser_version,
            text_checksum=response.text_checksum,
        )
        if not updated:
            return False

        self.logger.info(
            "ingestion.parsed",
            tenant_id=tenant_id,
            session_id=session_id,
            doc_id=doc_id,
        )
        options = _normalize_options(session_item.get("options") or {}, self.settings)
        self._maybe_mark_session_ready(session_item, sessions_table, documents_table)
        if options.enable_search:
            self._index_document(
                tenant_id=tenant_id,
                session_id=session_id,
                doc_id=doc_id,
                doc_index=int(item.get("doc_index", 0)),
                text_s3_uri=str(response.outputs.text_s3_uri),
                documents_table=documents_table,
            )
            self._maybe_mark_session_ready(session_item, sessions_table, documents_table)
        return True

    def _index_document(
        self,
        *,
        tenant_id: str,
        session_id: str,
        doc_id: str,
        doc_index: int,
        text_s3_uri: str,
        documents_table: Any,
    ) -> None:
        if not text_s3_uri:
            self.logger.error(
                "ingestion.index_missing_text",
                tenant_id=tenant_id,
                session_id=session_id,
                doc_id=doc_id,
            )
            return
        claimed = ddb.update_document_status(
            documents_table,
            session_id=session_id,
            doc_id=doc_id,
            expected_status="PARSED",
            new_status="INDEXING",
        )
        if not claimed:
            return

        try:
            config = load_search_index_config(self.settings.search_backend_config)
            bucket = self.settings.s3_bucket
            if not bucket:
                raise ValueError("s3_bucket is required for indexing")
            index_uri, chunk_count = index_document(
                s3_client=self.s3_client,
                bucket=bucket,
                tenant_id=tenant_id,
                session_id=session_id,
                doc_id=doc_id,
                doc_index=doc_index,
                text_s3_uri=text_s3_uri,
                config=config,
            )
        except Exception as exc:  # noqa: BLE001
            failure_reason = f"index_failed: {exc}"
            ddb.update_document_status(
                documents_table,
                session_id=session_id,
                doc_id=doc_id,
                expected_status="INDEXING",
                new_status="FAILED",
                failure_reason=failure_reason,
            )
            self.logger.error(
                "ingestion.index_failed",
                tenant_id=tenant_id,
                session_id=session_id,
                doc_id=doc_id,
                error=str(exc),
            )
            return

        updated = ddb.update_document_status(
            documents_table,
            session_id=session_id,
            doc_id=doc_id,
            expected_status="INDEXING",
            new_status="INDEXED",
            search_index_s3_uri=index_uri,
            search_chunk_count=chunk_count,
            search_chunk_size=config.chunk_size_chars,
            search_chunk_overlap=config.chunk_overlap_chars,
        )
        if not updated:
            return
        self.logger.info(
            "ingestion.indexed",
            tenant_id=tenant_id,
            session_id=session_id,
            doc_id=doc_id,
            chunk_count=chunk_count,
        )

    def _maybe_mark_session_ready(
        self,
        session_item: Mapping[str, Any],
        sessions_table: Any,
        documents_table: Any,
    ) -> None:
        if session_item.get("status") != "CREATING":
            return

        options = _normalize_options(session_item.get("options") or {}, self.settings)
        docs = _query_documents(documents_table, session_item["session_id"])
        parsed_ready = all(
            doc.get("ingest_status") in _PARSED_READY for doc in docs
        )
        search_ready = all(
            doc.get("ingest_status") in _SEARCH_READY for doc in docs
        )
        if not options.enable_search:
            search_ready = False

        ready = search_ready if options.readiness_mode == "STRICT" else parsed_ready
        if not ready:
            return

        ddb.update_session_status(
            sessions_table,
            tenant_id=session_item["tenant_id"],
            session_id=session_item["session_id"],
            expected_status="CREATING",
            new_status="READY",
        )


def build_worker(settings: Settings | None = None) -> IngestionWorker:
    resolved = settings or Settings()
    if not resolved.parser_service_url:
        raise ValueError("parser_service_url is required for ingestion")
    if not resolved.s3_bucket:
        raise ValueError("s3_bucket is required for ingestion")

    ddb_resource = build_ddb_resource(
        region=resolved.aws_region,
        endpoint_url=resolved.localstack_endpoint_url,
    )
    table_names = build_table_names(resolved.ddb_table_prefix)
    s3_client = build_s3_client(
        region=resolved.aws_region,
        endpoint_url=resolved.localstack_endpoint_url,
    )
    parser_client = ParserClient(resolved.parser_service_url)

    return IngestionWorker(
        settings=resolved,
        ddb_resource=ddb_resource,
        table_names=table_names,
        parser_client=parser_client,
        s3_client=s3_client,
        logger=get_logger("rlm_rs.ingestion"),
    )
