import json
from decimal import Decimal

import pytest

from rlm_rs.storage import s3
from rlm_rs.storage.state import (
    StateValidationError,
    build_state_s3_key,
    canonical_state_bytes,
    normalize_json_value,
    persist_state_payload,
    validate_state_payload,
)


class FakeS3Client:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def put_object(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(kwargs)
        return {"ETag": "fake"}


def test_state_json_validation_rejects_invalid_types() -> None:
    with pytest.raises(StateValidationError):
        validate_state_payload({"bad": b"bytes"})

    with pytest.raises(StateValidationError):
        validate_state_payload({"bad": {"nested": {1: "nope"}}})

    with pytest.raises(StateValidationError):
        validate_state_payload({"bad": float("nan")})

    with pytest.raises(StateValidationError):
        validate_state_payload(["not", "object"])


def test_state_json_validation_inline_and_offload() -> None:
    payload = {"work": {"step": 1, "notes": "ok"}}
    state_bytes = canonical_state_bytes(payload)

    inline_limit = len(state_bytes) + 1
    inline_result = persist_state_payload(
        state=payload,
        tenant_id="tenant-1",
        execution_id="exec-1",
        turn_index=2,
        max_inline_bytes=inline_limit,
    )

    assert inline_result.state_json == payload
    assert inline_result.state_s3_uri is None
    assert inline_result.summary["byte_length"] == len(state_bytes)

    offload_limit = max(1, len(state_bytes) - 1)
    client = FakeS3Client()
    offload_result = persist_state_payload(
        state=payload,
        tenant_id="tenant-1",
        execution_id="exec-1",
        turn_index=2,
        max_inline_bytes=offload_limit,
        s3_client=client,
        bucket="state-bucket",
    )

    key = build_state_s3_key(tenant_id="tenant-1", execution_id="exec-1", turn_index=2)

    assert offload_result.state_json is None
    assert offload_result.state_s3_uri == f"s3://state-bucket/{key}"
    assert offload_result.checksum.startswith("sha256:")
    assert offload_result.summary["byte_length"] == len(state_bytes)
    assert len(client.calls) == 1

    call = client.calls[0]
    assert call["Bucket"] == "state-bucket"
    assert call["Key"] == key
    assert call["ContentEncoding"] == "gzip"
    assert call["ContentType"] == "application/json"

    restored = json.loads(s3.gunzip_bytes(call["Body"]))
    assert restored == payload


def test_normalize_json_value_coerces_decimals() -> None:
    payload = {"work": {"meta": {"doc_count": Decimal("2"), "ratio": Decimal("1.5")}}}
    normalized = normalize_json_value(payload)

    assert normalized == {"work": {"meta": {"doc_count": 2, "ratio": 1.5}}}
