from datetime import datetime, timezone
from typing import Any

from botocore.exceptions import ClientError
from fastapi.testclient import TestClient

from rlm_rs.api import auth
from rlm_rs.api.app import create_app
from rlm_rs.api.auth import ApiKeyContext
from rlm_rs.api import dependencies as deps
from rlm_rs.errors import ErrorCode
from rlm_rs.settings import Settings
from rlm_rs.storage import ddb
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
        self,
        *,
        KeyConditionExpression: Any,
        ExclusiveStartKey: dict[str, Any] | None = None,
        Limit: int | None = None,
    ) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        last_key: dict[str, Any] | None = None
        if hasattr(KeyConditionExpression, "_values"):
            key_obj, value = KeyConditionExpression._values
            if getattr(key_obj, "name", None) == "PK":
                matches = [
                    dict(item)
                    for (pk, _), item in self.items.items()
                    if pk == value
                ]
                matches.sort(key=lambda item: item.get("SK", ""))
                start_index = 0
                if ExclusiveStartKey:
                    for index, item in enumerate(matches):
                        if (
                            item.get("PK") == ExclusiveStartKey.get("PK")
                            and item.get("SK") == ExclusiveStartKey.get("SK")
                        ):
                            start_index = index + 1
                            break
                if Limit is not None:
                    items = matches[start_index : start_index + Limit]
                    if start_index + Limit < len(matches):
                        last = items[-1]
                        last_key = {"PK": last["PK"], "SK": last["SK"]}
                else:
                    items = matches[start_index:]
        response = {"Items": items}
        if last_key:
            response["LastEvaluatedKey"] = last_key
        return response

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
        api_keys="api_keys",
        audit_log="audit_log",
    )


def _build_client(tenant_id: str, resource: _FakeDdbResource) -> TestClient:
    app = create_app()
    app.dependency_overrides[deps.get_ddb_resource] = lambda: resource
    app.dependency_overrides[deps.get_table_names] = _table_names
    app.dependency_overrides[deps.get_settings] = lambda: Settings()
    app.dependency_overrides[auth.require_api_key] = lambda: ApiKeyContext(tenant_id=tenant_id)
    return TestClient(app)


def test_create_session_persists_session_and_docs() -> None:
    resource = _FakeDdbResource()
    client = _build_client("tenant-a", resource)

    payload = {
        "ttl_minutes": 120,
        "docs": [
            {
                "source_name": "contract.pdf",
                "mime_type": "application/pdf",
                "raw_s3_uri": "s3://bucket/raw/contract.pdf",
            }
        ],
        "options": {"enable_search": False, "readiness_mode": "LAX"},
    }
    response = client.post("/v1/sessions", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "CREATING"
    assert data["docs"][0]["ingest_status"] == "REGISTERED"
    assert data["session_id"].startswith("sess_")

    sessions_table = resource.tables["sessions"]
    documents_table = resource.tables["documents"]
    assert len(sessions_table.items) == 1
    assert len(documents_table.items) == 1

    session_item = next(iter(sessions_table.items.values()))
    assert session_item["tenant_id"] == "tenant-a"
    assert session_item["doc_count"] == 1

    document_item = next(iter(documents_table.items.values()))
    assert document_item["tenant_id"] == "tenant-a"
    assert document_item["ingest_status"] == "REGISTERED"
    assert document_item["session_id"] == session_item["session_id"]


def test_get_session_returns_readiness_and_blocks_foreign_access() -> None:
    resource = _FakeDdbResource()
    tables = _table_names()
    sessions_table = resource.Table(tables.sessions)
    documents_table = resource.Table(tables.documents)

    tenant_id = "tenant-a"
    session_id = "sess-123"
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
        doc_id="doc-1",
        doc_index=0,
        source_name="sample.txt",
        mime_type="text/plain",
        raw_s3_uri="s3://raw/sample.txt",
        ingest_status="PARSED",
        text_s3_uri="s3://parsed/text.txt",
        meta_s3_uri="s3://parsed/meta.json",
        offsets_s3_uri="s3://parsed/offsets.json",
    )

    client = _build_client(tenant_id, resource)
    response = client.get(f"/v1/sessions/{session_id}")

    assert response.status_code == 200
    data = response.json()
    assert data["readiness"] == {"parsed_ready": True, "search_ready": False, "ready": True}
    assert data["docs"][0]["text_s3_uri"] == "s3://parsed/text.txt"

    foreign_client = _build_client("tenant-b", resource)
    forbidden = foreign_client.get(f"/v1/sessions/{session_id}")
    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == ErrorCode.FORBIDDEN


def test_delete_session_marks_deleting() -> None:
    resource = _FakeDdbResource()
    tables = _table_names()
    sessions_table = resource.Table(tables.sessions)

    tenant_id = "tenant-a"
    session_id = "sess-999"
    ttl_epoch = int(datetime(2026, 1, 2, tzinfo=timezone.utc).timestamp())
    ddb.create_session(
        sessions_table,
        tenant_id=tenant_id,
        session_id=session_id,
        status="READY",
        created_at="2026-01-01T00:00:00Z",
        expires_at="2026-01-02T00:00:00Z",
        ttl_epoch=ttl_epoch,
        doc_count=0,
        options={"enable_search": False, "readiness_mode": "LAX"},
    )

    client = _build_client(tenant_id, resource)
    response = client.delete(f"/v1/sessions/{session_id}")

    assert response.status_code == 200
    assert response.json() == {"status": "DELETING"}
    item = next(iter(sessions_table.items.values()))
    assert item["status"] == "DELETING"


def test_list_sessions_filters_and_paginates() -> None:
    resource = _FakeDdbResource()
    tables = _table_names()
    sessions_table = resource.Table(tables.sessions)
    documents_table = resource.Table(tables.documents)
    ttl_epoch = int(datetime(2026, 1, 2, tzinfo=timezone.utc).timestamp())

    def seed_session(tenant_id: str, session_id: str, status: str) -> None:
        ddb.create_session(
            sessions_table,
            tenant_id=tenant_id,
            session_id=session_id,
            status=status,
            created_at="2026-01-01T00:00:00Z",
            expires_at="2026-01-02T00:00:00Z",
            ttl_epoch=ttl_epoch,
            doc_count=1,
            options={"enable_search": False, "readiness_mode": "STRICT"},
        )
        ddb.create_document(
            documents_table,
            tenant_id=tenant_id,
            session_id=session_id,
            doc_id=f"doc-{session_id}",
            doc_index=0,
            source_name="sample.txt",
            mime_type="text/plain",
            raw_s3_uri="s3://raw/sample.txt",
            ingest_status="REGISTERED",
        )

    seed_session("tenant-a", "sess_001", "CREATING")
    seed_session("tenant-a", "sess_002", "READY")
    seed_session("tenant-b", "sess_999", "READY")

    client = _build_client("tenant-a", resource)
    response = client.get("/v1/sessions?limit=1")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["sessions"]) == 1
    assert payload["sessions"][0]["id"] == "sess_001"
    assert payload["sessions"][0]["readiness_mode"] == "STRICT"
    assert payload["next_cursor"]

    cursor = payload["next_cursor"]
    followup = client.get(f"/v1/sessions?limit=1&cursor={cursor}")
    assert followup.status_code == 200
    second_payload = followup.json()
    assert len(second_payload["sessions"]) == 1
    assert second_payload["sessions"][0]["id"] == "sess_002"

    filtered = client.get("/v1/sessions?status=READY")
    assert filtered.status_code == 200
    filtered_payload = filtered.json()
    assert len(filtered_payload["sessions"]) == 1
    assert filtered_payload["sessions"][0]["id"] == "sess_002"
