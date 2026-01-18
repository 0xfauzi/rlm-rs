from datetime import datetime, timezone
from typing import Any

from botocore.exceptions import ClientError
from fastapi.testclient import TestClient

from rlm_rs.api import auth
from rlm_rs.api import dependencies as deps
from rlm_rs.api.app import create_app
from rlm_rs.api.auth import ApiKeyContext
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

    def scan(self, *, ExclusiveStartKey: dict[str, Any] | None = None) -> dict[str, Any]:
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
    app.dependency_overrides[deps.get_s3_client] = lambda: object()
    app.dependency_overrides[auth.require_api_key] = lambda: ApiKeyContext(
        tenant_id=tenant_id
    )
    return TestClient(app)


def _seed_ready_session(
    resource: _FakeDdbResource, tenant_id: str, session_id: str
) -> None:
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
        doc_id="doc-1",
        doc_index=0,
        source_name="sample.txt",
        mime_type="text/plain",
        raw_s3_uri="s3://raw/sample.txt",
        ingest_status="PARSED",
        text_s3_uri="s3://parsed/text.txt",
        offsets_s3_uri="s3://parsed/offsets.json",
    )


def test_runtime_execution_step_and_tool_resolution() -> None:
    resource = _FakeDdbResource()
    tenant_id = "tenant-a"
    session_id = "sess-123"
    _seed_ready_session(resource, tenant_id, session_id)

    client = _build_client(tenant_id, resource)

    create_response = client.post(f"/v1/sessions/{session_id}/executions/runtime")
    assert create_response.status_code == 200
    create_data = create_response.json()
    execution_id = create_data["execution_id"]
    assert create_data["status"] == "RUNNING"

    step_response = client.post(
        f"/v1/executions/{execution_id}/steps",
        json={
            "code": "tool.queue_llm('k1', 'ping', max_tokens=3)\n"
            "tool.YIELD('waiting')",
            "state": None,
        },
    )
    assert step_response.status_code == 200
    step_data = step_response.json()
    assert step_data["success"] is True
    assert step_data["tool_requests"]["llm"][0]["key"] == "k1"
    assert step_data["final"]["is_final"] is False

    state_table = resource.tables["execution_state"]
    state_item = next(iter(state_table.items.values()))
    assert state_item["stdout"] == ""
    assert state_item["tool_requests"]["llm"][0]["key"] == "k1"
    assert state_item["turn_index"] == 0

    resolve_response = client.post(
        f"/v1/executions/{execution_id}/tools/resolve",
        json={
            "tool_requests": step_data["tool_requests"],
            "models": {"sub_model": "fake-model"},
        },
    )
    assert resolve_response.status_code == 200
    resolve_data = resolve_response.json()
    assert resolve_data["statuses"]["k1"] == "resolved"
    assert resolve_data["tool_results"]["llm"]["k1"]["text"] == "fake:ping"

    state_item = next(iter(state_table.items.values()))
    tool_results = state_item["state_json"]["_tool_results"]["llm"]
    assert tool_results["k1"]["text"] == "fake:ping"
    assert state_item["state_json"]["_tool_status"]["k1"] == "resolved"

    final_response = client.post(
        f"/v1/executions/{execution_id}/steps",
        json={
            "code": "answer = state['_tool_results']['llm']['k1']['text']\n"
            "tool.FINAL(answer)",
        },
    )
    assert final_response.status_code == 200
    final_data = final_response.json()
    assert final_data["final"]["is_final"] is True
    assert final_data["final"]["answer"] == "fake:ping"

    execution_item = next(iter(resource.tables["executions"].items.values()))
    assert execution_item["status"] == "COMPLETED"
    assert execution_item["answer"] == "fake:ping"
