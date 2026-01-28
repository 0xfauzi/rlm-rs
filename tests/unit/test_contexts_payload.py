import json
from typing import Any

from fastapi.testclient import TestClient

from rlm_rs.api import auth
from rlm_rs.api import dependencies as deps
from rlm_rs.api.app import create_app
from rlm_rs.api.auth import ApiKeyContext
from rlm_rs.settings import Settings
from rlm_rs.storage import ddb
from rlm_rs.storage import contexts as contexts_store
from rlm_rs.storage.ddb import DdbTableNames
from rlm_rs.storage import s3


class _FakeBody:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload


class _FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], dict[str, Any]] = {}

    def put_object(self, **kwargs: Any) -> dict[str, str]:
        key = (kwargs["Bucket"], kwargs["Key"])
        self.objects[key] = dict(kwargs)
        return {"ETag": "fake"}

    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        key = (kwargs["Bucket"], kwargs["Key"])
        stored = self.objects.get(key)
        if stored is None:
            raise KeyError("Missing object")
        return {"Body": _FakeBody(stored["Body"])}


class _FakeTable:
    def __init__(self) -> None:
        self.items: dict[tuple[str, str], dict[str, Any]] = {}

    def put_item(self, *, Item: dict[str, Any], ConditionExpression: str | None = None) -> None:
        key = (Item["PK"], Item["SK"])
        self.items[key] = dict(Item)

    def scan(
        self,
        *,
        ExclusiveStartKey: dict[str, Any] | None = None,
        Limit: int | None = None,
    ) -> dict[str, Any]:
        items = list(self.items.values())
        return {"Items": items}


class _FakeDdbResource:
    def __init__(self) -> None:
        self.tables: dict[str, _FakeTable] = {}

    def Table(self, name: str) -> _FakeTable:  # noqa: N802 - boto3 uses this casing
        table = self.tables.get(name)
        if table is None:
            table = _FakeTable()
            self.tables[name] = table
        return table


def _table_names() -> DdbTableNames:
    return DdbTableNames(
        sessions="sessions",
        documents="documents",
        executions="executions",
        execution_state="execution_state",
        evaluations="evaluations",
        code_log="code_log",
        api_keys="api_keys",
        audit_log="audit_log",
    )


def _build_client(
    tenant_id: str,
    resource: _FakeDdbResource,
    s3_client: _FakeS3Client,
) -> TestClient:
    app = create_app()
    app.dependency_overrides[deps.get_ddb_resource] = lambda: resource
    app.dependency_overrides[deps.get_table_names] = _table_names
    app.dependency_overrides[deps.get_settings] = lambda: Settings()
    app.dependency_overrides[deps.get_s3_client] = lambda: s3_client
    app.dependency_overrides[auth.require_api_key] = lambda: ApiKeyContext(tenant_id=tenant_id)
    return TestClient(app)


def _context_item(
    *,
    sequence_index: int,
    turn_index: int,
    span_index: int,
    tag: str,
    text: str,
    source_name: str = "sample.txt",
    mime_type: str = "text/plain",
    tenant_id: str = "tenant-a",
    session_id: str = "session-a",
    doc_id: str = "doc-a",
    doc_index: int = 0,
    start_char: int = 0,
    end_char: int | None = None,
) -> dict[str, Any]:
    if end_char is None:
        end_char = len(text)
    return {
        "sequence_index": sequence_index,
        "turn_index": turn_index,
        "span_index": span_index,
        "tag": tag,
        "text": text,
        "text_char_length": len(text),
        "source_name": source_name,
        "mime_type": mime_type,
        "ref": {
            "tenant_id": tenant_id,
            "session_id": session_id,
            "doc_id": doc_id,
            "doc_index": doc_index,
            "start_char": start_char,
            "end_char": end_char,
            "checksum": "sha256:deadbeef",
        },
    }


def test_contexts_payload_offload_roundtrip() -> None:
    contexts = [
        _context_item(
            sequence_index=0,
            turn_index=0,
            span_index=0,
            tag="context",
            text="Alpha",
            tenant_id="tenant-a",
            session_id="session-a",
        ),
        _context_item(
            sequence_index=1,
            turn_index=0,
            span_index=1,
            tag="context:foo",
            text="beta",
            tenant_id="tenant-a",
            session_id="session-a",
        ),
        _context_item(
            sequence_index=2,
            turn_index=1,
            span_index=1,
            tag="context",
            text="delta",
            tenant_id="tenant-a",
            session_id="session-a",
        ),
    ]
    contexts_bytes = contexts_store.canonical_contexts_bytes(contexts)
    inline_limit = max(1, len(contexts_bytes) - 1)

    s3_client = _FakeS3Client()
    record = contexts_store.persist_contexts_payload(
        contexts=contexts,
        tenant_id="tenant-a",
        execution_id="exec-a",
        max_inline_bytes=inline_limit,
        s3_client=s3_client,
        bucket="contexts-bucket",
    )

    key = contexts_store.build_contexts_s3_key(
        tenant_id="tenant-a",
        execution_id="exec-a",
    )
    assert record.contexts_json is None
    assert record.contexts_s3_uri == f"s3://contexts-bucket/{key}"
    assert record.byte_length == len(contexts_bytes)

    stored = s3_client.objects[("contexts-bucket", key)]
    assert stored["ContentEncoding"] == "gzip"
    assert stored["ContentType"] == "application/json"

    restored = json.loads(s3.gunzip_bytes(stored["Body"]))
    assert restored == contexts

    loaded = contexts_store.load_contexts_payload(
        s3_client=s3_client,
        contexts_s3_uri=record.contexts_s3_uri,
    )
    assert loaded == contexts


def test_contexts_payload_endpoint_roundtrip() -> None:
    resource = _FakeDdbResource()
    tables = _table_names()
    executions_table = resource.Table(tables.executions)
    tenant_id = "tenant-ctx"
    session_id = "sess-ctx"

    inline_execution_id = "exec-inline"
    ddb.create_execution(
        executions_table,
        tenant_id=tenant_id,
        session_id=session_id,
        execution_id=inline_execution_id,
        status="COMPLETED",
        mode="ANSWERER",
        question="Show contexts",
        options={"output_mode": "CONTEXTS"},
    )
    inline_key = ddb.execution_key(session_id, inline_execution_id)
    inline_contexts = [
        _context_item(
            sequence_index=0,
            turn_index=0,
            span_index=0,
            tag="context",
            text="Inline A",
            tenant_id=tenant_id,
            session_id=session_id,
        ),
        _context_item(
            sequence_index=1,
            turn_index=1,
            span_index=0,
            tag="context",
            text="Inline B",
            tenant_id=tenant_id,
            session_id=session_id,
        ),
    ]
    executions_table.items[(inline_key["PK"], inline_key["SK"])][
        "contexts"
    ] = inline_contexts

    offload_execution_id = "exec-offload"
    ddb.create_execution(
        executions_table,
        tenant_id=tenant_id,
        session_id=session_id,
        execution_id=offload_execution_id,
        status="COMPLETED",
        mode="ANSWERER",
        question="Show contexts",
        options={"output_mode": "CONTEXTS"},
    )

    s3_client = _FakeS3Client()
    offload_contexts = [
        _context_item(
            sequence_index=0,
            turn_index=0,
            span_index=0,
            tag="context",
            text="Offload A",
            tenant_id=tenant_id,
            session_id=session_id,
        ),
        _context_item(
            sequence_index=1,
            turn_index=1,
            span_index=0,
            tag="context:foo",
            text="Offload B",
            tenant_id=tenant_id,
            session_id=session_id,
        ),
    ]
    record = contexts_store.persist_contexts_payload(
        contexts=offload_contexts,
        tenant_id=tenant_id,
        execution_id=offload_execution_id,
        max_inline_bytes=1,
        s3_client=s3_client,
        bucket="contexts-bucket",
    )
    offload_key = ddb.execution_key(session_id, offload_execution_id)
    executions_table.items[(offload_key["PK"], offload_key["SK"])][
        "contexts_s3_uri"
    ] = record.contexts_s3_uri

    client = _build_client(tenant_id, resource, s3_client)
    inline_response = client.get(f"/v1/executions/{inline_execution_id}/contexts")
    assert inline_response.status_code == 200
    assert inline_response.json()["contexts"] == inline_contexts

    offload_response = client.get(f"/v1/executions/{offload_execution_id}/contexts")
    assert offload_response.status_code == 200
    assert offload_response.json()["contexts"] == offload_contexts

    other_client = _build_client("tenant-other", resource, s3_client)
    forbidden_response = other_client.get(
        f"/v1/executions/{inline_execution_id}/contexts"
    )
    assert forbidden_response.status_code == 403
