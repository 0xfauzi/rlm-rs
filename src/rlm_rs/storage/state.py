from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass

from botocore.client import BaseClient
from pydantic import JsonValue

from rlm_rs.storage import s3


DEFAULT_INLINE_MAX_BYTES = 350 * 1024
DEFAULT_STATE_S3_PREFIX = "state"
CHECKSUM_PREFIX = "sha256:"


class StateValidationError(ValueError):
    pass


class StateOffloadError(RuntimeError):
    pass


@dataclass(frozen=True)
class StatePayloadRecord:
    state_json: JsonValue | None
    state_s3_uri: str | None
    checksum: str
    summary: dict[str, JsonValue]


def _validate_json_value(value: object, path: str) -> None:
    if value is None:
        return
    if isinstance(value, bool):
        return
    if isinstance(value, int):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise StateValidationError(f"Invalid JSON number at {path}")
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
                raise StateValidationError(f"Invalid JSON object key at {path}: {key!r}")
            next_path = f"{path}.{key}"
            _validate_json_value(item, next_path)
        return

    raise StateValidationError(f"Invalid JSON value at {path}: {type(value).__name__}")


def validate_state_payload(state: JsonValue | None) -> None:
    if state is None:
        return
    if isinstance(state, str):
        return
    if not isinstance(state, dict):
        raise StateValidationError("State must be a JSON object or string.")
    _validate_json_value(state, "$")


def canonical_state_bytes(state: JsonValue | None) -> bytes:
    return s3.deterministic_json_bytes(state)


def build_state_summary(state_bytes: bytes) -> dict[str, JsonValue]:
    state_text = state_bytes.decode("utf-8")
    return {
        "byte_length": len(state_bytes),
        "char_length": len(state_text),
    }


def build_state_s3_key(
    *,
    tenant_id: str,
    execution_id: str,
    turn_index: int,
    prefix: str = DEFAULT_STATE_S3_PREFIX,
) -> str:
    return f"{prefix}/{tenant_id}/{execution_id}/state_{turn_index}.json.gz"


def persist_state_payload(
    *,
    state: JsonValue | None,
    tenant_id: str,
    execution_id: str,
    turn_index: int,
    max_inline_bytes: int = DEFAULT_INLINE_MAX_BYTES,
    s3_client: BaseClient | None = None,
    bucket: str | None = None,
    s3_prefix: str = DEFAULT_STATE_S3_PREFIX,
) -> StatePayloadRecord:
    validate_state_payload(state)

    state_bytes = canonical_state_bytes(state)
    checksum = f"{CHECKSUM_PREFIX}{hashlib.sha256(state_bytes).hexdigest()}"
    summary = build_state_summary(state_bytes)

    if len(state_bytes) <= max_inline_bytes:
        return StatePayloadRecord(
            state_json=state,
            state_s3_uri=None,
            checksum=checksum,
            summary=summary,
        )

    if s3_client is None or bucket is None:
        raise StateOffloadError("S3 client and bucket required for offloaded state.")

    key = build_state_s3_key(
        tenant_id=tenant_id,
        execution_id=execution_id,
        turn_index=turn_index,
        prefix=s3_prefix,
    )
    s3.put_gzip_json(s3_client, bucket, key, state)
    state_s3_uri = f"s3://{bucket}/{key}"

    return StatePayloadRecord(
        state_json=None,
        state_s3_uri=state_s3_uri,
        checksum=checksum,
        summary=summary,
    )
