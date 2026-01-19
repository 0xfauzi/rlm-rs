from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Any, Mapping

from boto3.resources.base import ServiceResource
from fastapi import Depends, Header

from rlm_rs.api.dependencies import get_ddb_resource, get_settings, get_table_names
from rlm_rs.errors import ErrorCode, raise_http_error
from rlm_rs.settings import Settings
from rlm_rs.storage.ddb import DdbTableNames


API_KEY_PREFIX = "rlm_key_"
_API_KEY_RECORD_PREFIX = "KEY#"


@dataclass(frozen=True)
class ApiKeyContext:
    tenant_id: str
    key_prefix: str | None = None
    scopes: list[str] | None = None


def hash_api_key(api_key: str, pepper: str) -> str:
    digest = hmac.new(pepper.encode("utf-8"), api_key.encode("utf-8"), hashlib.sha256)
    return digest.hexdigest()


def build_api_key_key(key_hash: str) -> dict[str, str]:
    key = f"{_API_KEY_RECORD_PREFIX}{key_hash}"
    return {"PK": key, "SK": key}


def _extract_key_hash(item: Mapping[str, Any]) -> str | None:
    pk = item.get("PK")
    if not isinstance(pk, str) or not pk.startswith(_API_KEY_RECORD_PREFIX):
        return None
    return pk.removeprefix(_API_KEY_RECORD_PREFIX)


def _parse_authorization_header(authorization: str | None) -> str:
    if not authorization:
        raise_http_error(ErrorCode.UNAUTHORIZED, "Missing Authorization header")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise_http_error(ErrorCode.UNAUTHORIZED, "Invalid Authorization header")
    if not token.startswith(API_KEY_PREFIX):
        raise_http_error(ErrorCode.UNAUTHORIZED, "Invalid API key")
    return token


def require_api_key(
    authorization: str | None = Header(default=None, alias="Authorization"),
    settings: Settings = Depends(get_settings),
    ddb_resource: ServiceResource = Depends(get_ddb_resource),
    table_names: DdbTableNames = Depends(get_table_names),
) -> ApiKeyContext:
    api_key = _parse_authorization_header(authorization)
    if not settings.api_key_pepper:
        raise_http_error(ErrorCode.INTERNAL_ERROR, "API key pepper is not configured")

    key_hash = hash_api_key(api_key, settings.api_key_pepper)
    table = ddb_resource.Table(table_names.api_keys)
    response = table.get_item(Key=build_api_key_key(key_hash))
    item = response.get("Item")
    if not item:
        raise_http_error(ErrorCode.UNAUTHORIZED, "Invalid API key")

    stored_hash = _extract_key_hash(item)
    if stored_hash is None or not hmac.compare_digest(stored_hash, key_hash):
        raise_http_error(ErrorCode.UNAUTHORIZED, "Invalid API key")

    if item.get("revoked"):
        raise_http_error(ErrorCode.UNAUTHORIZED, "API key revoked")

    tenant_id = item.get("tenant_id")
    if not tenant_id:
        raise_http_error(ErrorCode.UNAUTHORIZED, "Invalid API key")

    scopes = item.get("scopes")
    if scopes is not None and not isinstance(scopes, list):
        raise_http_error(ErrorCode.UNAUTHORIZED, "Invalid API key scopes")

    key_prefix = item.get("key_prefix")
    if key_prefix is not None and not isinstance(key_prefix, str):
        raise_http_error(ErrorCode.UNAUTHORIZED, "Invalid API key prefix")

    return ApiKeyContext(
        tenant_id=tenant_id,
        key_prefix=key_prefix,
        scopes=scopes if isinstance(scopes, list) else None,
    )


def ensure_tenant_access(item: Mapping[str, Any], tenant_id: str) -> None:
    if item.get("tenant_id") != tenant_id:
        raise_http_error(ErrorCode.FORBIDDEN, "Forbidden")
