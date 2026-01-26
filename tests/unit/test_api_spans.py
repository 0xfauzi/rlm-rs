import io
import json
from datetime import datetime, timezone
from typing import Any

from botocore.exceptions import ClientError
from fastapi.testclient import TestClient

from rlm_rs.api import auth
from rlm_rs.api import dependencies as deps
from rlm_rs.api.app import create_app
from rlm_rs.api.auth import ApiKeyContext
from rlm_rs.orchestrator.citations import checksum_text
from rlm_rs.settings import Settings
from rlm_rs.storage import ddb
from rlm_rs.storage.ddb import DdbTableNames


class _FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}

    def put_object(self, *, Bucket: str, Key: str, Body: Any, **_kwargs: Any) -> dict[str, Any]:
        if hasattr(Body, "read"):
            Body = Body.read()
        if isinstance(Body, str):
            Body = Body.encode("utf-8")
        if not isinstance(Body, bytes):
            raise TypeError("Body must be bytes-like")
        self.objects[(Bucket, Key)] = Body
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}

    def get_object(
        self, *, Bucket: str, Key: str, Range: str | None = None, **_kwargs: Any
    ) -> dict[str, Any]:
        data = self.objects[(Bucket, Key)]
        if Range:
            range_spec = Range.removeprefix("bytes=")
            start_str, end_str = range_spec.split("-", 1)
            start = int(start_str)
            end = int(end_str) if end_str else len(data) - 1
            if start > len(data):
                sliced = b""
            else:
                sliced = data[start : min(end + 1, len(data))]
            return {"Body": io.BytesIO(sliced)}
        return {"Body": io.BytesIO(data)}


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


def _conditional_error(operation: str) -> ClientError:
    return ClientError(
        {"Error": {"Code": "ConditionalCheckFailedException", "Message": "Condition failed"}},
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
        evaluations="evaluations",
        code_log="code_log",
        api_keys="api_keys",
        audit_log="audit_log",
    )


def _build_offsets_payload(text: str, interval: int = 5) -> dict[str, Any]:
    checkpoints: list[dict[str, int]] = [{"char": 0, "byte": 0}]
    byte_offset = 0
    for index, char in enumerate(text, start=1):
        byte_offset += len(char.encode("utf-8"))
        if index % interval == 0:
            checkpoints.append({"char": index, "byte": byte_offset})
    if checkpoints[-1]["char"] != len(text):
        checkpoints.append({"char": len(text), "byte": byte_offset})
    return {
        "version": "1.0",
        "doc_id": "doc-1",
        "char_length": len(text),
        "byte_length": byte_offset,
        "encoding": "utf-8",
        "checkpoints": checkpoints,
        "checkpoint_interval": interval,
    }


def _build_client(
    tenant_id: str, resource: _FakeDdbResource, s3_client: _FakeS3Client
) -> TestClient:
    app = create_app()
    app.dependency_overrides[deps.get_ddb_resource] = lambda: resource
    app.dependency_overrides[deps.get_table_names] = _table_names
    app.dependency_overrides[deps.get_settings] = lambda: Settings()
    app.dependency_overrides[deps.get_s3_client] = lambda: s3_client
    app.dependency_overrides[auth.require_api_key] = lambda: ApiKeyContext(
        tenant_id=tenant_id
    )
    return TestClient(app)


def test_spans_get_and_verify_checksum() -> None:
    resource = _FakeDdbResource()
    s3_client = _FakeS3Client()
    tenant_id = "tenant-a"
    session_id = "sess-123"
    doc_id = "doc-1"

    text = "Alpha beta gamma delta"
    offsets_payload = _build_offsets_payload(text, interval=5)
    bucket = "docs"
    text_key = "parsed/doc-1/text.txt"
    offsets_key = "parsed/doc-1/offsets.json"
    s3_client.put_object(Bucket=bucket, Key=text_key, Body=text.encode("utf-8"))
    s3_client.put_object(
        Bucket=bucket,
        Key=offsets_key,
        Body=json.dumps(offsets_payload).encode("utf-8"),
    )

    tables = _table_names()
    sessions_table = resource.Table(tables.sessions)
    documents_table = resource.Table(tables.documents)
    ttl_epoch = int(datetime(2026, 1, 2, tzinfo=timezone.utc).timestamp())
    ddb.create_session(
        sessions_table,
        tenant_id=tenant_id,
        session_id=session_id,
        status="READY",
        created_at="2026-01-01T00:00:00Z",
        expires_at="2026-01-02T00:00:00Z",
        ttl_epoch=ttl_epoch,
        doc_count=1,
        options={"enable_search": False, "readiness_mode": "LAX"},
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
        ingest_status="PARSED",
        text_s3_uri=f"s3://{bucket}/{text_key}",
        offsets_s3_uri=f"s3://{bucket}/{offsets_key}",
    )

    client = _build_client(tenant_id, resource, s3_client)
    response = client.post(
        "/v1/spans/get",
        json={
            "session_id": session_id,
            "doc_id": doc_id,
            "start_char": 6,
            "end_char": 10,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["text"] == "beta"
    assert data["ref"]["doc_index"] == 0
    assert data["ref"]["checksum"] == checksum_text("beta")

    verify_response = client.post("/v1/citations/verify", json={"ref": data["ref"]})
    assert verify_response.status_code == 200
    verify_data = verify_response.json()
    assert verify_data["valid"] is True
    assert verify_data["text"] == "beta"
    assert verify_data["source_name"] == "sample.txt"
    assert verify_data["char_range"] == {"start_char": 6, "end_char": 10}

    tampered = dict(data["ref"])
    last_char = tampered["checksum"][-1]
    tampered["checksum"] = tampered["checksum"][:-1] + ("0" if last_char != "0" else "1")
    invalid_response = client.post("/v1/citations/verify", json={"ref": tampered})
    assert invalid_response.status_code == 200
    invalid_data = invalid_response.json()
    assert invalid_data["valid"] is False
