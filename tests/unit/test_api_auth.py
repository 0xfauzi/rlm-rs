import hmac

import pytest

from rlm_rs.api import auth
from rlm_rs.errors import ErrorCode, RLMHTTPError
from rlm_rs.settings import Settings
from rlm_rs.storage.ddb import DdbTableNames


class _FakeTable:
    def __init__(self, item: dict[str, object] | None) -> None:
        self._item = item
        self.last_key: dict[str, str] | None = None

    def get_item(self, *, Key: dict[str, str]) -> dict[str, object]:
        self.last_key = Key
        if self._item is None:
            return {}
        return {"Item": self._item}


class _FakeDdbResource:
    def __init__(self, item: dict[str, object] | None) -> None:
        self.table = _FakeTable(item)

    def Table(self, _: str) -> _FakeTable:  # noqa: N802 - boto3 uses this casing
        return self.table


def _table_names() -> DdbTableNames:
    return DdbTableNames(
        sessions="sessions",
        documents="documents",
        executions="executions",
        execution_state="execution_state",
        api_keys="api_keys",
        audit_log="audit_log",
    )


def test_require_api_key_missing_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_KEY_PEPPER", "pepper")
    with pytest.raises(RLMHTTPError) as exc:
        auth.require_api_key(
            authorization=None,
            settings=Settings(),
            ddb_resource=_FakeDdbResource(None),
            table_names=_table_names(),
        )

    assert exc.value.status_code == 401
    assert exc.value.error.error.code == ErrorCode.UNAUTHORIZED


def test_require_api_key_invalid_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_KEY_PEPPER", "pepper")
    with pytest.raises(RLMHTTPError) as exc:
        auth.require_api_key(
            authorization="Bearer not-a-real-key",
            settings=Settings(),
            ddb_resource=_FakeDdbResource(None),
            table_names=_table_names(),
        )

    assert exc.value.status_code == 401
    assert exc.value.error.error.code == ErrorCode.UNAUTHORIZED


def test_require_api_key_valid_key_uses_compare_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    api_key = "rlm_key_test"
    pepper = "pepper"
    key_hash = auth.hash_api_key(api_key, pepper)
    key = auth.build_api_key_key(key_hash)
    item = {
        "PK": key["PK"],
        "SK": key["SK"],
        "tenant_id": "tenant-123",
        "key_prefix": "rlm_key_",
        "scopes": ["sessions:read"],
        "revoked": False,
    }
    monkeypatch.setenv("API_KEY_PEPPER", pepper)
    resource = _FakeDdbResource(item)
    settings = Settings()

    called: dict[str, tuple[str, str]] = {}
    real_compare = hmac.compare_digest

    def _compare_digest(left: str, right: str) -> bool:
        called["args"] = (left, right)
        return real_compare(left, right)

    monkeypatch.setattr(auth.hmac, "compare_digest", _compare_digest)

    context = auth.require_api_key(
        authorization=f"Bearer {api_key}",
        settings=settings,
        ddb_resource=resource,
        table_names=_table_names(),
    )

    assert context.tenant_id == "tenant-123"
    assert called["args"] == (key_hash, key_hash)
    assert resource.table.last_key == key


def test_require_api_key_revoked(monkeypatch: pytest.MonkeyPatch) -> None:
    api_key = "rlm_key_test"
    pepper = "pepper"
    monkeypatch.setenv("API_KEY_PEPPER", pepper)
    key_hash = auth.hash_api_key(api_key, pepper)
    key = auth.build_api_key_key(key_hash)
    item = {"PK": key["PK"], "SK": key["SK"], "tenant_id": "tenant-123", "revoked": True}

    with pytest.raises(RLMHTTPError) as exc:
        auth.require_api_key(
            authorization=f"Bearer {api_key}",
            settings=Settings(),
            ddb_resource=_FakeDdbResource(item),
            table_names=_table_names(),
        )

    assert exc.value.status_code == 401
    assert exc.value.error.error.code == ErrorCode.UNAUTHORIZED
