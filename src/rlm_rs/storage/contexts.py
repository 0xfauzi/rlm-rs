from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from botocore.client import BaseClient
from pydantic import JsonValue

from rlm_rs.models import ContextItem
from rlm_rs.storage import s3, state as state_store

DEFAULT_CONTEXTS_S3_PREFIX = "contexts"


class ContextsValidationError(ValueError):
    pass


class ContextsOffloadError(RuntimeError):
    pass


@dataclass(frozen=True)
class ContextsPayloadRecord:
    contexts_json: list[JsonValue] | None
    contexts_s3_uri: str | None
    byte_length: int


def _validate_json_value(value: object, path: str) -> None:
    if value is None:
        return
    if isinstance(value, bool):
        return
    if isinstance(value, int):
        return
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ContextsValidationError(f"Invalid JSON number at {path}")
        return
    if isinstance(value, str):
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json_value(item, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ContextsValidationError(
                    f"Invalid JSON object key at {path}: {key!r}"
                )
            _validate_json_value(item, f"{path}.{key}")
        return
    raise ContextsValidationError(f"Invalid JSON value at {path}: {type(value).__name__}")


def validate_contexts_payload(contexts: list[JsonValue]) -> None:
    if not isinstance(contexts, list):
        raise ContextsValidationError("Contexts payload must be a list.")
    for index, item in enumerate(contexts):
        try:
            ContextItem.model_validate(item)
        except Exception as exc:  # noqa: BLE001
            raise ContextsValidationError(
                f"Invalid context item at index {index}: {exc}"
            ) from exc
        _validate_json_value(item, f"$[{index}]")


def canonical_contexts_bytes(contexts: list[JsonValue]) -> bytes:
    return s3.deterministic_json_bytes(contexts)


def _split_s3_uri(uri: str) -> tuple[str, str]:
    parsed = urlparse(uri)
    if parsed.scheme != "s3" or not parsed.netloc:
        raise ValueError(f"Invalid S3 URI: {uri}")
    return parsed.netloc, parsed.path.lstrip("/")


def build_contexts_s3_key(
    *,
    tenant_id: str,
    execution_id: str,
    prefix: str = DEFAULT_CONTEXTS_S3_PREFIX,
) -> str:
    return f"{prefix}/{tenant_id}/{execution_id}/contexts.json.gz"


def persist_contexts_payload(
    *,
    contexts: list[JsonValue],
    tenant_id: str,
    execution_id: str,
    max_inline_bytes: int = state_store.DEFAULT_INLINE_MAX_BYTES,
    s3_client: BaseClient | None = None,
    bucket: str | None = None,
    s3_prefix: str = DEFAULT_CONTEXTS_S3_PREFIX,
) -> ContextsPayloadRecord:
    validate_contexts_payload(contexts)

    contexts_bytes = canonical_contexts_bytes(contexts)
    byte_length = len(contexts_bytes)

    if byte_length <= max_inline_bytes:
        return ContextsPayloadRecord(
            contexts_json=contexts,
            contexts_s3_uri=None,
            byte_length=byte_length,
        )

    if s3_client is None or bucket is None:
        raise ContextsOffloadError("S3 client and bucket required for offloaded contexts.")

    key = build_contexts_s3_key(
        tenant_id=tenant_id,
        execution_id=execution_id,
        prefix=s3_prefix,
    )
    s3.put_gzip_json(s3_client, bucket, key, contexts)
    contexts_s3_uri = f"s3://{bucket}/{key}"

    return ContextsPayloadRecord(
        contexts_json=None,
        contexts_s3_uri=contexts_s3_uri,
        byte_length=byte_length,
    )


def load_contexts_payload(
    *,
    s3_client: BaseClient,
    contexts_s3_uri: str,
) -> list[JsonValue]:
    bucket, key = _split_s3_uri(contexts_s3_uri)
    payload = s3.get_gzip_json(s3_client, bucket, key)
    if not isinstance(payload, list):
        raise ContextsValidationError("Contexts payload must be a list.")
    validate_contexts_payload(payload)
    return payload
