from rlm_rs.storage.s3 import (
    deterministic_json_bytes,
    deterministic_json_checksum,
    gunzip_bytes,
    gzip_bytes,
)


def test_deterministic_json_bytes_and_checksum_are_stable() -> None:
    payload_a = {"b": 1, "a": [1, {"c": "x", "b": True}]}
    payload_b = {"a": [1, {"b": True, "c": "x"}], "b": 1}

    bytes_a = deterministic_json_bytes(payload_a)
    bytes_b = deterministic_json_bytes(payload_b)

    assert bytes_a == bytes_b
    assert deterministic_json_checksum(payload_a) == deterministic_json_checksum(payload_b)


def test_gzip_roundtrip() -> None:
    payload = b"rlm-rs gzip test"

    compressed = gzip_bytes(payload)
    assert gunzip_bytes(compressed) == payload
