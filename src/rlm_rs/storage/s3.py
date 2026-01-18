from __future__ import annotations

import gzip
import hashlib
import json
from typing import Any

import boto3
from botocore.client import BaseClient
from pydantic import JsonValue


def build_s3_client(
    *,
    region: str | None = None,
    endpoint_url: str | None = None,
) -> BaseClient:
    return boto3.client("s3", region_name=region, endpoint_url=endpoint_url)


def deterministic_json_bytes(payload: JsonValue) -> bytes:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return encoded.encode("utf-8")


def deterministic_json_checksum(payload: JsonValue) -> str:
    digest = hashlib.sha256(deterministic_json_bytes(payload)).hexdigest()
    return digest


def gzip_bytes(payload: bytes) -> bytes:
    return gzip.compress(payload)


def gunzip_bytes(payload: bytes) -> bytes:
    return gzip.decompress(payload)


def put_bytes(
    client: BaseClient,
    bucket: str,
    key: str,
    payload: bytes,
    *,
    content_type: str | None = None,
    content_encoding: str | None = None,
) -> dict[str, Any]:
    extra: dict[str, Any] = {}
    if content_type:
        extra["ContentType"] = content_type
    if content_encoding:
        extra["ContentEncoding"] = content_encoding

    return client.put_object(Bucket=bucket, Key=key, Body=payload, **extra)


def get_bytes(
    client: BaseClient,
    bucket: str,
    key: str,
    *,
    version_id: str | None = None,
) -> bytes:
    params: dict[str, Any] = {"Bucket": bucket, "Key": key}
    if version_id:
        params["VersionId"] = version_id
    response = client.get_object(**params)
    return response["Body"].read()


def get_range_bytes(
    client: BaseClient,
    bucket: str,
    key: str,
    start: int,
    end: int | None = None,
    *,
    version_id: str | None = None,
) -> bytes:
    if start < 0 or (end is not None and end < start):
        raise ValueError("Invalid byte range")
    range_header = f"bytes={start}-" if end is None else f"bytes={start}-{end}"
    params: dict[str, Any] = {"Bucket": bucket, "Key": key, "Range": range_header}
    if version_id:
        params["VersionId"] = version_id
    response = client.get_object(**params)
    return response["Body"].read()


def put_json(
    client: BaseClient,
    bucket: str,
    key: str,
    payload: JsonValue,
    *,
    content_type: str = "application/json",
) -> dict[str, Any]:
    body = deterministic_json_bytes(payload)
    return put_bytes(client, bucket, key, body, content_type=content_type)


def get_json(
    client: BaseClient,
    bucket: str,
    key: str,
    *,
    version_id: str | None = None,
) -> JsonValue:
    body = get_bytes(client, bucket, key, version_id=version_id)
    return json.loads(body)


def put_gzip_bytes(
    client: BaseClient,
    bucket: str,
    key: str,
    payload: bytes,
    *,
    content_type: str | None = None,
) -> dict[str, Any]:
    return put_bytes(
        client,
        bucket,
        key,
        gzip_bytes(payload),
        content_type=content_type,
        content_encoding="gzip",
    )


def get_gzip_bytes(
    client: BaseClient,
    bucket: str,
    key: str,
    *,
    version_id: str | None = None,
) -> bytes:
    body = get_bytes(client, bucket, key, version_id=version_id)
    return gunzip_bytes(body)


def put_gzip_json(
    client: BaseClient,
    bucket: str,
    key: str,
    payload: JsonValue,
    *,
    content_type: str = "application/json",
) -> dict[str, Any]:
    body = deterministic_json_bytes(payload)
    return put_gzip_bytes(client, bucket, key, body, content_type=content_type)


def get_gzip_json(
    client: BaseClient,
    bucket: str,
    key: str,
    *,
    version_id: str | None = None,
) -> JsonValue:
    body = get_gzip_bytes(client, bucket, key, version_id=version_id)
    return json.loads(body)
