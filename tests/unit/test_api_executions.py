from datetime import datetime, timezone
from typing import Any

from botocore.exceptions import ClientError
from fastapi.testclient import TestClient

from rlm_rs.api import auth
from rlm_rs.api import dependencies as deps
from rlm_rs.api.app import create_app
from rlm_rs.api.auth import ApiKeyContext
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
        ExclusiveStartKey: dict[str, Any] | None = None,
        Limit: int | None = None,
    ) -> dict[str, Any]:
        items = sorted(
            (dict(item) for item in self.items.values()),
            key=lambda entry: (entry["PK"], entry["SK"]),
        )
        start_index = 0
        if ExclusiveStartKey is not None:
            for index, item in enumerate(items):
                if (
                    item.get("PK") == ExclusiveStartKey.get("PK")
                    and item.get("SK") == ExclusiveStartKey.get("SK")
                ):
                    start_index = index + 1
                    break
        end_index = len(items) if Limit is None else start_index + Limit
        page = items[start_index:end_index]
        response: dict[str, Any] = {"Items": page}
        if end_index < len(items):
            last = items[end_index - 1]
            response["LastEvaluatedKey"] = {"PK": last["PK"], "SK": last["SK"]}
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
        evaluations="evaluations",
        code_log="code_log",
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


def _seed_ready_session(resource: _FakeDdbResource, tenant_id: str, session_id: str) -> None:
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
    )


def _seed_execution(
    resource: _FakeDdbResource,
    *,
    tenant_id: str,
    session_id: str,
    execution_id: str,
    status: str = "RUNNING",
    mode: str = "ANSWERER",
) -> None:
    tables = _table_names()
    executions_table = resource.Table(tables.executions)
    ddb.create_execution(
        executions_table,
        tenant_id=tenant_id,
        session_id=session_id,
        execution_id=execution_id,
        status=status,
        mode=mode,
        question="hello?",
        started_at="2026-01-01T00:00:00Z",
    )


def _seed_evaluation(
    resource: _FakeDdbResource,
    *,
    tenant_id: str,
    session_id: str,
    execution_id: str,
    mode: str = "ANSWERER",
) -> None:
    tables = _table_names()
    evaluations_table = resource.Table(tables.evaluations)
    ddb.create_evaluation(
        evaluations_table,
        evaluation_id="eval-123",
        tenant_id=tenant_id,
        session_id=session_id,
        execution_id=execution_id,
        mode=mode,
        question="What is the plan?",
        answer="Answer here.",
        baseline_status="SKIPPED",
        baseline_skip_reason="MISSING_PARSED_TEXT",
        baseline_answer=None,
        baseline_input_tokens=None,
        baseline_context_window=None,
        created_at="2026-01-01T00:00:00Z",
    )


def test_create_execution_persists_execution_and_state() -> None:
    resource = _FakeDdbResource()
    tenant_id = "tenant-a"
    session_id = "sess-123"
    _seed_ready_session(resource, tenant_id, session_id)

    client = _build_client(tenant_id, resource)
    response = client.post(
        f"/v1/sessions/{session_id}/executions",
        json={"question": "What is the summary?"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "RUNNING"
    assert data["execution_id"].startswith("exec_")

    executions_table = resource.tables["executions"]
    execution_state_table = resource.tables["execution_state"]
    assert len(executions_table.items) == 1
    assert len(execution_state_table.items) == 1

    execution_item = next(iter(executions_table.items.values()))
    assert execution_item["tenant_id"] == tenant_id
    assert execution_item["session_id"] == session_id
    assert execution_item["mode"] == "ANSWERER"
    assert execution_item["status"] == "RUNNING"
    assert execution_item["question"] == "What is the summary?"
    assert execution_item["options"] == {"return_trace": False, "redact_trace": False}

    state_item = next(iter(execution_state_table.items.values()))
    assert state_item["execution_id"] == execution_item["execution_id"]
    assert state_item["turn_index"] == 0
    assert state_item["state_json"] == {}
    assert state_item["checksum"].startswith("sha256:")
    summary = state_item["summary"]
    assert isinstance(summary.get("byte_length"), int)
    assert isinstance(summary.get("char_length"), int)


def test_create_execution_allows_trace_options_when_enabled(monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_RETURN_TRACE", "true")
    monkeypatch.setenv("ENABLE_TRACE_REDACTION", "true")

    resource = _FakeDdbResource()
    tenant_id = "tenant-a"
    session_id = "sess-123"
    _seed_ready_session(resource, tenant_id, session_id)

    client = _build_client(tenant_id, resource)
    response = client.post(
        f"/v1/sessions/{session_id}/executions",
        json={
            "question": "What is the summary?",
            "options": {"return_trace": True, "redact_trace": True},
        },
    )

    assert response.status_code == 200
    execution_item = next(iter(resource.tables["executions"].items.values()))
    assert execution_item["options"] == {"return_trace": True, "redact_trace": True}


def test_create_execution_clamps_trace_options_when_disabled(monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_RETURN_TRACE", "false")
    monkeypatch.setenv("ENABLE_TRACE_REDACTION", "false")

    resource = _FakeDdbResource()
    tenant_id = "tenant-a"
    session_id = "sess-123"
    _seed_ready_session(resource, tenant_id, session_id)

    client = _build_client(tenant_id, resource)
    response = client.post(
        f"/v1/sessions/{session_id}/executions",
        json={
            "question": "What is the summary?",
            "options": {"return_trace": True, "redact_trace": True},
        },
    )

    assert response.status_code == 200
    execution_item = next(iter(resource.tables["executions"].items.values()))
    assert execution_item["options"] == {"return_trace": False, "redact_trace": False}


def test_get_execution_hides_trace_when_return_trace_disabled() -> None:
    resource = _FakeDdbResource()
    tables = _table_names()
    executions_table = resource.Table(tables.executions)
    tenant_id = "tenant-a"
    session_id = "sess-123"
    execution_id = "exec-123"

    ddb.create_execution(
        executions_table,
        tenant_id=tenant_id,
        session_id=session_id,
        execution_id=execution_id,
        status="COMPLETED",
        mode="ANSWERER",
        question="What is the summary?",
        options={"return_trace": False, "redact_trace": False},
    )
    item = next(iter(executions_table.items.values()))
    item["trace_s3_uri"] = "s3://bucket/traces/exec-123.json.gz"

    client = _build_client(tenant_id, resource)
    response = client.get(f"/v1/executions/{execution_id}")

    assert response.status_code == 200
    data = response.json()
    assert data["trace_s3_uri"] is None


def test_evaluation_api_returns_record() -> None:
    resource = _FakeDdbResource()
    tenant_id = "tenant-eval"
    session_id = "sess-eval"
    execution_id = "exec-eval"
    _seed_ready_session(resource, tenant_id, session_id)
    _seed_execution(
        resource,
        tenant_id=tenant_id,
        session_id=session_id,
        execution_id=execution_id,
    )
    _seed_evaluation(
        resource,
        tenant_id=tenant_id,
        session_id=session_id,
        execution_id=execution_id,
    )

    client = _build_client(tenant_id, resource)
    response = client.get(f"/v1/executions/{execution_id}/evaluation")

    assert response.status_code == 200
    data = response.json()
    assert data["evaluation_id"] == "eval-123"
    assert data["execution_id"] == execution_id
    assert data["baseline_status"] == "SKIPPED"
    assert data["baseline_skip_reason"] == "MISSING_PARSED_TEXT"


def test_evaluation_api_missing_returns_404() -> None:
    resource = _FakeDdbResource()
    tenant_id = "tenant-missing"
    session_id = "sess-missing"
    execution_id = "exec-missing"
    _seed_ready_session(resource, tenant_id, session_id)
    _seed_execution(
        resource,
        tenant_id=tenant_id,
        session_id=session_id,
        execution_id=execution_id,
    )

    client = _build_client(tenant_id, resource)
    response = client.get(f"/v1/executions/{execution_id}/evaluation")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == ErrorCode.EXECUTION_NOT_FOUND


def test_evaluation_api_runtime_returns_validation_error() -> None:
    resource = _FakeDdbResource()
    tenant_id = "tenant-runtime"
    session_id = "sess-runtime"
    execution_id = "exec-runtime"
    _seed_ready_session(resource, tenant_id, session_id)
    _seed_execution(
        resource,
        tenant_id=tenant_id,
        session_id=session_id,
        execution_id=execution_id,
        mode="RUNTIME",
    )

    client = _build_client(tenant_id, resource)
    response = client.get(f"/v1/executions/{execution_id}/evaluation")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == ErrorCode.VALIDATION_ERROR


def test_evaluation_api_forbidden_for_other_tenant() -> None:
    resource = _FakeDdbResource()
    tenant_id = "tenant-owner"
    session_id = "sess-owner"
    execution_id = "exec-owner"
    _seed_ready_session(resource, tenant_id, session_id)
    _seed_execution(
        resource,
        tenant_id=tenant_id,
        session_id=session_id,
        execution_id=execution_id,
    )
    _seed_evaluation(
        resource,
        tenant_id=tenant_id,
        session_id=session_id,
        execution_id=execution_id,
    )

    client = _build_client("tenant-other", resource)
    response = client.get(f"/v1/executions/{execution_id}/evaluation")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == ErrorCode.FORBIDDEN


def test_get_and_wait_execution_enforce_tenant_and_poll(monkeypatch: Any) -> None:
    resource = _FakeDdbResource()
    tenant_id = "tenant-a"
    session_id = "sess-456"
    _seed_ready_session(resource, tenant_id, session_id)

    tables = _table_names()
    executions_table = resource.Table(tables.executions)
    ddb.create_execution(
        executions_table,
        tenant_id=tenant_id,
        session_id=session_id,
        execution_id="exec-123",
        status="RUNNING",
        mode="ANSWERER",
        question="Q",
    )

    client = _build_client(tenant_id, resource)
    get_response = client.get("/v1/executions/exec-123")
    assert get_response.status_code == 200
    assert get_response.json()["status"] == "RUNNING"

    foreign_client = _build_client("tenant-b", resource)
    forbidden = foreign_client.get("/v1/executions/exec-123")
    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == ErrorCode.FORBIDDEN

    def fake_monotonic() -> float:
        return 0.0

    def fake_sleep(_: float) -> None:
        ddb.update_execution_status(
            executions_table,
            session_id=session_id,
            execution_id="exec-123",
            expected_status="RUNNING",
            new_status="COMPLETED",
            answer="done",
        )

    monkeypatch.setattr("rlm_rs.api.executions.time.monotonic", fake_monotonic)
    monkeypatch.setattr("rlm_rs.api.executions.time.sleep", fake_sleep)

    wait_response = client.post(
        "/v1/executions/exec-123/wait",
        json={"timeout_seconds": 5},
    )
    assert wait_response.status_code == 200
    assert wait_response.json()["status"] == "COMPLETED"


def test_cancel_execution_updates_status() -> None:
    resource = _FakeDdbResource()
    tenant_id = "tenant-a"
    session_id = "sess-cancel"
    execution_id = "exec-cancel"
    _seed_execution(
        resource,
        tenant_id=tenant_id,
        session_id=session_id,
        execution_id=execution_id,
        status="RUNNING",
    )

    client = _build_client(tenant_id, resource)
    response = client.post(f"/v1/executions/{execution_id}/cancel")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "CANCELLED"

    execution_item = next(iter(resource.tables["executions"].items.values()))
    assert execution_item["status"] == "CANCELLED"
    assert execution_item.get("completed_at")


def test_cancel_execution_idempotent_when_not_running() -> None:
    resource = _FakeDdbResource()
    tenant_id = "tenant-a"
    session_id = "sess-cancelled"
    execution_id = "exec-done"
    _seed_execution(
        resource,
        tenant_id=tenant_id,
        session_id=session_id,
        execution_id=execution_id,
        status="COMPLETED",
    )

    client = _build_client(tenant_id, resource)
    response = client.post(f"/v1/executions/{execution_id}/cancel")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "COMPLETED"

    execution_item = next(iter(resource.tables["executions"].items.values()))
    assert execution_item["status"] == "COMPLETED"


def test_list_executions_filters_and_pagination() -> None:
    resource = _FakeDdbResource()
    tenant_id = "tenant-a"
    other_tenant = "tenant-b"

    _seed_execution(
        resource,
        tenant_id=tenant_id,
        session_id="sess-alpha",
        execution_id="exec-alpha",
        status="RUNNING",
        mode="ANSWERER",
    )
    _seed_execution(
        resource,
        tenant_id=tenant_id,
        session_id="sess-alpha",
        execution_id="exec-bravo",
        status="COMPLETED",
        mode="RUNTIME",
    )
    _seed_execution(
        resource,
        tenant_id=tenant_id,
        session_id="sess-beta",
        execution_id="exec-charlie",
        status="COMPLETED",
        mode="ANSWERER",
    )
    _seed_execution(
        resource,
        tenant_id=other_tenant,
        session_id="sess-alpha",
        execution_id="exec-foreign",
        status="COMPLETED",
        mode="ANSWERER",
    )

    client = _build_client(tenant_id, resource)

    response = client.get("/v1/executions", params={"status": "COMPLETED"})
    assert response.status_code == 200
    data = response.json()
    assert {item["execution_id"] for item in data["executions"]} == {
        "exec-bravo",
        "exec-charlie",
    }

    response = client.get("/v1/executions", params={"session_id": "sess-alpha"})
    assert response.status_code == 200
    data = response.json()
    assert {item["execution_id"] for item in data["executions"]} == {
        "exec-alpha",
        "exec-bravo",
    }

    collected: list[str] = []
    cursor: str | None = None
    while True:
        params = {"limit": 1}
        if cursor:
            params["cursor"] = cursor
        response = client.get("/v1/executions", params=params)
        assert response.status_code == 200
        payload = response.json()
        collected.extend([item["execution_id"] for item in payload["executions"]])
        cursor = payload["next_cursor"]
        if not cursor:
            break

    assert set(collected) == {"exec-alpha", "exec-bravo", "exec-charlie"}
    assert len(collected) == 3


def test_list_executions_validates_cursor_and_limit() -> None:
    resource = _FakeDdbResource()
    tenant_id = "tenant-a"
    _seed_execution(
        resource,
        tenant_id=tenant_id,
        session_id="sess-alpha",
        execution_id="exec-alpha",
        status="RUNNING",
        mode="ANSWERER",
    )

    client = _build_client(tenant_id, resource)

    response = client.get("/v1/executions", params={"cursor": "not-a-cursor"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == ErrorCode.VALIDATION_ERROR

    response = client.get("/v1/executions", params={"limit": 1001})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == ErrorCode.VALIDATION_ERROR
