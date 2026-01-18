from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from rlm_rs.api import auth
from rlm_rs.api import dependencies as deps
from rlm_rs.api.app import create_app
from rlm_rs.api.auth import ApiKeyContext
from rlm_rs.api.rate_limits import RateLimiter, RateLimitSpec, RateLimitsConfig
from rlm_rs.errors import ErrorCode
from rlm_rs.settings import Settings
from rlm_rs.storage.ddb import DdbTableNames


class _FakeTable:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    def put_item(self, *, Item: dict[str, Any], ConditionExpression: str | None = None) -> None:
        del ConditionExpression
        self.items.append(dict(Item))


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
        api_keys="api_keys",
        audit_log="audit_log",
    )


def _build_client(
    *,
    rate_limiter: RateLimiter | None = None,
    request_size_limit_bytes: int | None = None,
) -> TestClient:
    app = create_app()
    if rate_limiter is not None:
        app.state.rate_limiter = rate_limiter
    if request_size_limit_bytes is not None:
        app.state.request_size_limit_bytes = request_size_limit_bytes

    resource = _FakeDdbResource()
    app.dependency_overrides[deps.get_ddb_resource] = lambda: resource
    app.dependency_overrides[deps.get_table_names] = _table_names
    app.dependency_overrides[deps.get_settings] = lambda: Settings()
    app.dependency_overrides[auth.require_api_key] = lambda: ApiKeyContext(
        tenant_id="tenant-a"
    )
    return TestClient(app)


def test_rate_limit_returns_429() -> None:
    limiter = RateLimiter(
        RateLimitsConfig(
            default=RateLimitSpec(max_requests=1, window_seconds=60),
        )
    )
    client = _build_client(rate_limiter=limiter)
    payload = {
        "ttl_minutes": 60,
        "docs": [
            {
                "source_name": "contract.pdf",
                "mime_type": "application/pdf",
                "raw_s3_uri": "s3://bucket/raw/contract.pdf",
            }
        ],
    }

    first = client.post("/v1/sessions", json=payload)
    assert first.status_code == 200

    second = client.post("/v1/sessions", json=payload)
    assert second.status_code == 429
    assert second.json()["error"]["code"] == ErrorCode.RATE_LIMITED


def test_request_size_limit_returns_413() -> None:
    client = _build_client(request_size_limit_bytes=100)
    payload = {
        "ttl_minutes": 60,
        "docs": [
            {
                "source_name": "x" * 300,
                "mime_type": "application/pdf",
                "raw_s3_uri": "s3://bucket/raw/contract.pdf",
            }
        ],
    }

    response = client.post("/v1/sessions", json=payload)
    assert response.status_code == 413
    assert response.json()["error"]["code"] == ErrorCode.REQUEST_TOO_LARGE
