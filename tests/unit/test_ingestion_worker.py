import json
import os
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from botocore.exceptions import ClientError

from rlm_rs.ingestion.worker import IngestionWorker
from rlm_rs.parser.models import ParseOutputs, ParseRequest, ParseStats, ParseSuccess
from rlm_rs.settings import Settings
from rlm_rs.storage import ddb, s3
from rlm_rs.storage.ddb import DdbTableNames


class _FakeTable:
    def __init__(self) -> None:
        self.items: dict[tuple[str, str], dict[str, Any]] = {}

    def put_item(self, *, Item: dict[str, Any], ConditionExpression: str | None = None) -> None:
        key = (Item["PK"], Item["SK"])
        if ConditionExpression and "attribute_not_exists" in ConditionExpression:
            if key in self.items:
                raise _conditional_error("PutItem")
        self.items[key] = dict(Item)

    def get_item(self, *, Key: dict[str, str]) -> dict[str, Any]:
        item = self.items.get((Key["PK"], Key["SK"]))
        if item is None:
            return {}
        return {"Item": dict(item)}

    def query(
        self, *, KeyConditionExpression: Any, ExclusiveStartKey: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        if hasattr(KeyConditionExpression, "_values"):
            key_obj, value = KeyConditionExpression._values
            if getattr(key_obj, "name", None) == "PK":
                items = [
                    dict(item)
                    for (pk, _), item in self.items.items()
                    if pk == value
                ]
        return {"Items": items}

    def scan(
        self,
        *,
        FilterExpression: Any | None = None,
        ExclusiveStartKey: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        items = [dict(item) for item in self.items.values()]
        return {"Items": items}

    def update_item(
        self,
        *,
        Key: dict[str, str],
        UpdateExpression: str,
        ExpressionAttributeNames: dict[str, str] | None = None,
        ExpressionAttributeValues: dict[str, Any] | None = None,
        ConditionExpression: str | None = None,
    ) -> None:
        item = self.items.get((Key["PK"], Key["SK"]))
        if item is None:
            item = {"PK": Key["PK"], "SK": Key["SK"]}
            self.items[(Key["PK"], Key["SK"])] = item

        if ConditionExpression:
            _apply_condition(
                item,
                ConditionExpression,
                ExpressionAttributeNames or {},
                ExpressionAttributeValues or {},
            )

        _apply_update(
            item,
            UpdateExpression,
            ExpressionAttributeNames or {},
            ExpressionAttributeValues or {},
        )


class _FakeDdbResource:
    def __init__(self) -> None:
        self.tables: dict[str, _FakeTable] = {}

    def Table(self, name: str) -> _FakeTable:  # noqa: N802 - boto3 uses this casing
        table = self.tables.get(name)
        if table is None:
            table = _FakeTable()
            self.tables[name] = table
        return table


class _FakeParserClient:
    def __init__(self) -> None:
        self.calls: list[ParseRequest] = []

    def parse(self, request: ParseRequest) -> ParseSuccess:
        self.calls.append(request)
        parsed = urlparse(request.output.s3_prefix)
        prefix = parsed.path.lstrip("/")
        if prefix and not prefix.endswith("/"):
            prefix = f"{prefix}/"
        outputs = ParseOutputs(
            text_s3_uri=f"s3://{parsed.netloc}/{prefix}text.txt",
            meta_s3_uri=f"s3://{parsed.netloc}/{prefix}meta.json",
            offsets_s3_uri=f"s3://{parsed.netloc}/{prefix}offsets.json",
        )
        stats = ParseStats(
            char_length=12,
            byte_length=12,
            page_count=1,
            parse_duration_ms=5,
        )
        return ParseSuccess(
            request_id=request.request_id,
            outputs=outputs,
            stats=stats,
            parser_version="parser-1",
            text_checksum="sha256:abc",
            warnings=None,
        )


class _FakeS3Body:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload


class _FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}

    def put_object(self, *, Bucket: str, Key: str, Body: Any, **kwargs: Any) -> None:
        payload = Body if isinstance(Body, bytes) else str(Body).encode("utf-8")
        self.objects[(Bucket, Key)] = payload

    def get_object(self, *, Bucket: str, Key: str, **kwargs: Any) -> dict[str, Any]:
        payload = self.objects.get((Bucket, Key))
        if payload is None:
            raise _not_found_error("GetObject")
        return {"Body": _FakeS3Body(payload)}


def _conditional_error(operation: str) -> ClientError:
    return ClientError(
        {
            "Error": {
                "Code": "ConditionalCheckFailedException",
                "Message": "Condition failed",
            }
        },
        operation,
    )


def _not_found_error(operation: str) -> ClientError:
    return ClientError(
        {
            "Error": {
                "Code": "NoSuchKey",
                "Message": "Not found",
            }
        },
        operation,
    )


def _resolve_attr_name(token: str, names: dict[str, str]) -> str:
    token = token.strip()
    return names.get(token, token)


def _apply_condition(
    item: dict[str, Any],
    expression: str,
    names: dict[str, str],
    values: dict[str, Any],
) -> None:
    if "=" not in expression:
        raise ValueError("Unsupported condition expression")
    left, right = expression.split("=", 1)
    attr = _resolve_attr_name(left, names)
    value = values[right.strip()]
    if item.get(attr) != value:
        raise _conditional_error("UpdateItem")


def _apply_update(
    item: dict[str, Any],
    expression: str,
    names: dict[str, str],
    values: dict[str, Any],
) -> None:
    if not expression.startswith("SET "):
        raise ValueError("Unsupported update expression")
    updates = expression.removeprefix("SET ").split(",")
    for update in updates:
        left, right = update.split("=", 1)
        attr = _resolve_attr_name(left, names)
        item[attr] = values[right.strip()]


def _table_names() -> DdbTableNames:
    return DdbTableNames(
        sessions="sessions",
        documents="documents",
        executions="executions",
        execution_state="execution_state",
        api_keys="api_keys",
        audit_log="audit_log",
    )


def _settings_with_env() -> Settings:
    previous = {
        "S3_BUCKET": os.environ.get("S3_BUCKET"),
        "PARSER_SERVICE_URL": os.environ.get("PARSER_SERVICE_URL"),
        "SEARCH_BACKEND_CONFIG": os.environ.get("SEARCH_BACKEND_CONFIG"),
    }
    os.environ["S3_BUCKET"] = "bucket"
    os.environ["PARSER_SERVICE_URL"] = "http://parser"
    os.environ["SEARCH_BACKEND_CONFIG"] = json.dumps(
        {"chunk_size_chars": 4, "chunk_overlap_chars": 1, "index_prefix": "search-index"}
    )
    try:
        return Settings()
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _seed_session_and_doc(
    resource: _FakeDdbResource,
    *,
    tenant_id: str,
    session_id: str,
    doc_id: str,
    readiness_mode: str,
    enable_search: bool,
) -> tuple[_FakeTable, _FakeTable]:
    tables = _table_names()
    sessions_table = resource.Table(tables.sessions)
    documents_table = resource.Table(tables.documents)
    ttl_epoch = int(datetime(2026, 1, 2, tzinfo=timezone.utc).timestamp())
    ddb.create_session(
        sessions_table,
        tenant_id=tenant_id,
        session_id=session_id,
        status="CREATING",
        created_at="2026-01-01T00:00:00Z",
        expires_at="2026-01-02T00:00:00Z",
        ttl_epoch=ttl_epoch,
        doc_count=1,
        options={"enable_search": enable_search, "readiness_mode": readiness_mode},
    )
    ddb.create_document(
        documents_table,
        tenant_id=tenant_id,
        session_id=session_id,
        doc_id=doc_id,
        doc_index=0,
        source_name="sample.txt",
        mime_type="text/plain",
        raw_s3_uri="s3://raw/sample.txt",
        ingest_status="REGISTERED",
    )
    return sessions_table, documents_table


def test_ingestion_worker_marks_ready_for_lax_sessions() -> None:
    resource = _FakeDdbResource()
    tenant_id = "tenant-a"
    session_id = "sess-1"
    doc_id = "doc-1"
    sessions_table, documents_table = _seed_session_and_doc(
        resource,
        tenant_id=tenant_id,
        session_id=session_id,
        doc_id=doc_id,
        readiness_mode="LAX",
        enable_search=False,
    )

    parser_client = _FakeParserClient()
    settings = _settings_with_env()
    s3_client = _FakeS3Client()
    worker = IngestionWorker(
        settings=settings,
        ddb_resource=resource,
        table_names=_table_names(),
        parser_client=parser_client,
        s3_client=s3_client,
    )

    processed = worker.run_once()
    assert processed == 1
    assert len(parser_client.calls) == 1

    doc_item = ddb.get_document(documents_table, session_id=session_id, doc_id=doc_id)
    assert doc_item is not None
    assert doc_item["ingest_status"] == "PARSED"
    expected_prefix = f"s3://bucket/parsed/{tenant_id}/{session_id}/{doc_id}/"
    assert doc_item["text_s3_uri"] == f"{expected_prefix}text.txt"
    assert doc_item["meta_s3_uri"] == f"{expected_prefix}meta.json"
    assert doc_item["offsets_s3_uri"] == f"{expected_prefix}offsets.json"
    assert doc_item["parser_version"] == "parser-1"
    assert doc_item["text_checksum"] == "sha256:abc"

    session_item = ddb.get_session(
        sessions_table, tenant_id=tenant_id, session_id=session_id
    )
    assert session_item is not None
    assert session_item["status"] == "READY"

    processed_again = worker.run_once()
    assert processed_again == 0
    assert len(parser_client.calls) == 1


def test_ingestion_worker_strict_sessions_wait_for_indexing() -> None:
    resource = _FakeDdbResource()
    tenant_id = "tenant-b"
    session_id = "sess-2"
    doc_id = "doc-2"
    sessions_table, documents_table = _seed_session_and_doc(
        resource,
        tenant_id=tenant_id,
        session_id=session_id,
        doc_id=doc_id,
        readiness_mode="STRICT",
        enable_search=True,
    )

    parser_client = _FakeParserClient()
    settings = _settings_with_env()
    s3_client = _FakeS3Client()
    text_prefix = f"parsed/{tenant_id}/{session_id}/{doc_id}/text.txt"
    s3_client.put_object(Bucket="bucket", Key=text_prefix, Body="abcde")
    worker = IngestionWorker(
        settings=settings,
        ddb_resource=resource,
        table_names=_table_names(),
        parser_client=parser_client,
        s3_client=s3_client,
    )

    processed = worker.run_once()
    assert processed == 1
    doc_item = ddb.get_document(documents_table, session_id=session_id, doc_id=doc_id)
    assert doc_item is not None
    assert doc_item["ingest_status"] == "INDEXED"
    assert doc_item["search_index_s3_uri"].startswith("s3://bucket/search-index/")
    assert doc_item["search_chunk_count"] == 2
    assert doc_item["search_chunk_size"] == 4
    assert doc_item["search_chunk_overlap"] == 1

    parsed = urlparse(doc_item["search_index_s3_uri"])
    index_payload = s3.get_json(
        s3_client, parsed.netloc, parsed.path.lstrip("/")
    )
    assert isinstance(index_payload, dict)
    chunks = index_payload.get("chunks")
    assert isinstance(chunks, list)
    assert len(chunks) == 2
    for chunk in chunks:
        assert chunk["doc_index"] == 0
        assert chunk["start_char"] < chunk["end_char"]

    session_item = ddb.get_session(
        sessions_table, tenant_id=tenant_id, session_id=session_id
    )
    assert session_item is not None
    assert session_item["status"] == "READY"
