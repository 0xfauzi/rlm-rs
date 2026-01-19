import os
from uuid import uuid4

import pytest
from botocore.exceptions import EndpointConnectionError, NoCredentialsError
from fastapi.testclient import TestClient

from rlm_rs.api import auth
from rlm_rs.api import dependencies as deps
from rlm_rs.api.app import create_app
from rlm_rs.api.auth import ApiKeyContext
from rlm_rs.storage import ddb


def _localstack_config() -> tuple[str, str]:
    region = os.getenv("AWS_REGION", "us-east-1")
    endpoint_url = os.getenv(
        "LOCALSTACK_ENDPOINT_URL",
        os.getenv("AWS_ENDPOINT_URL", "http://localhost:4566"),
    )
    return region, endpoint_url


def _ensure_localstack_clients() -> tuple[object, object, str]:
    region, endpoint_url = _localstack_config()
    os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
    os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
    os.environ.setdefault("AWS_DEFAULT_REGION", region)

    ddb_client = ddb.build_ddb_client(region=region, endpoint_url=endpoint_url)
    ddb_resource = ddb.build_ddb_resource(region=region, endpoint_url=endpoint_url)

    try:
        ddb_client.list_tables()
    except (EndpointConnectionError, NoCredentialsError) as exc:
        pytest.skip(f"LocalStack not available: {exc}")

    return ddb_client, ddb_resource, endpoint_url


def _ensure_tables(ddb_client: object, prefix: str) -> ddb.DdbTableNames:
    tables = ddb.build_table_names(prefix)
    for name in tables.__dict__.values():
        ddb.ensure_table(ddb_client, name)
    return tables


def _build_client(
    tenant_id: str,
    ddb_resource: object,
    tables: ddb.DdbTableNames,
) -> TestClient:
    app = create_app()
    app.dependency_overrides[deps.get_ddb_resource] = lambda: ddb_resource
    app.dependency_overrides[deps.get_table_names] = lambda: tables
    app.dependency_overrides[auth.require_api_key] = lambda: ApiKeyContext(tenant_id=tenant_id)
    return TestClient(app)


def test_list_executions_filters_and_pagination_localstack() -> None:
    previous_prefix = os.environ.get("DDB_TABLE_PREFIX")
    prefix = f"rlm_test_{uuid4().hex[:8]}"
    os.environ["DDB_TABLE_PREFIX"] = prefix

    try:
        ddb_client, ddb_resource, _ = _ensure_localstack_clients()
        tables = _ensure_tables(ddb_client, prefix)
        executions_table = ddb_resource.Table(tables.executions)

        tenant_id = "tenant-list"
        other_tenant = "tenant-other"

        ddb.create_execution(
            executions_table,
            tenant_id=tenant_id,
            session_id="sess-alpha",
            execution_id="exec-alpha",
            status="RUNNING",
            mode="ANSWERER",
            started_at="2026-01-01T00:00:00Z",
        )
        ddb.create_execution(
            executions_table,
            tenant_id=tenant_id,
            session_id="sess-alpha",
            execution_id="exec-bravo",
            status="COMPLETED",
            mode="RUNTIME",
            started_at="2026-01-01T00:01:00Z",
        )
        ddb.create_execution(
            executions_table,
            tenant_id=tenant_id,
            session_id="sess-beta",
            execution_id="exec-charlie",
            status="COMPLETED",
            mode="ANSWERER",
            started_at="2026-01-01T00:02:00Z",
        )
        ddb.create_execution(
            executions_table,
            tenant_id=other_tenant,
            session_id="sess-alpha",
            execution_id="exec-foreign",
            status="COMPLETED",
            mode="ANSWERER",
            started_at="2026-01-01T00:03:00Z",
        )

        client = _build_client(tenant_id, ddb_resource, tables)

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
    finally:
        if previous_prefix is None:
            os.environ.pop("DDB_TABLE_PREFIX", None)
        else:
            os.environ["DDB_TABLE_PREFIX"] = previous_prefix
