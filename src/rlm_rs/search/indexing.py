from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlparse

from botocore.client import BaseClient
from pydantic import JsonValue

from rlm_rs.storage import s3

DEFAULT_CHUNK_SIZE_CHARS = 1000
DEFAULT_CHUNK_OVERLAP_CHARS = 200
DEFAULT_INDEX_PREFIX = "search-index"


@dataclass(frozen=True)
class SearchIndexConfig:
    chunk_size_chars: int = DEFAULT_CHUNK_SIZE_CHARS
    chunk_overlap_chars: int = DEFAULT_CHUNK_OVERLAP_CHARS
    index_prefix: str = DEFAULT_INDEX_PREFIX


def _read_int(config: Mapping[str, Any], key: str, default: int) -> int:
    value = config.get(key, default)
    if isinstance(value, bool):
        raise ValueError(f"{key} must be an integer")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be an integer") from exc


def _read_str(config: Mapping[str, Any], key: str, default: str) -> str:
    value = config.get(key, default)
    if value is None:
        return default
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    cleaned = value.strip().strip("/")
    if not cleaned:
        raise ValueError(f"{key} must be a non-empty string")
    return cleaned


def load_search_index_config(config: Mapping[str, Any] | None) -> SearchIndexConfig:
    payload = config if isinstance(config, Mapping) else {}
    chunk_size_chars = _read_int(payload, "chunk_size_chars", DEFAULT_CHUNK_SIZE_CHARS)
    chunk_overlap_chars = _read_int(
        payload, "chunk_overlap_chars", DEFAULT_CHUNK_OVERLAP_CHARS
    )
    index_prefix = _read_str(payload, "index_prefix", DEFAULT_INDEX_PREFIX)

    if chunk_size_chars <= 0:
        raise ValueError("chunk_size_chars must be positive")
    if chunk_overlap_chars < 0:
        raise ValueError("chunk_overlap_chars must be non-negative")
    if chunk_overlap_chars >= chunk_size_chars:
        raise ValueError("chunk_overlap_chars must be smaller than chunk_size_chars")

    return SearchIndexConfig(
        chunk_size_chars=chunk_size_chars,
        chunk_overlap_chars=chunk_overlap_chars,
        index_prefix=index_prefix,
    )


def _split_s3_uri(uri: str) -> tuple[str, str]:
    parsed = urlparse(uri)
    if parsed.scheme != "s3" or not parsed.netloc:
        raise ValueError(f"Invalid S3 URI: {uri}")
    return parsed.netloc, parsed.path.lstrip("/")


def _chunk_text(
    text: str,
    *,
    chunk_size_chars: int,
    chunk_overlap_chars: int,
) -> list[tuple[int, int, str]]:
    if not text:
        return []
    step = chunk_size_chars - chunk_overlap_chars
    chunks: list[tuple[int, int, str]] = []
    for start in range(0, len(text), step):
        end = min(start + chunk_size_chars, len(text))
        if start >= end:
            break
        chunks.append((start, end, text[start:end]))
        if end == len(text):
            break
    return chunks


def build_index_key(
    *,
    prefix: str,
    tenant_id: str,
    session_id: str,
    doc_id: str,
) -> str:
    cleaned = prefix.strip().strip("/") or DEFAULT_INDEX_PREFIX
    return f"{cleaned}/{tenant_id}/{session_id}/{doc_id}/index.json"


def build_index_payload(
    *,
    tenant_id: str,
    session_id: str,
    doc_id: str,
    doc_index: int,
    config: SearchIndexConfig,
    text: str,
) -> dict[str, JsonValue]:
    chunks = _chunk_text(
        text,
        chunk_size_chars=config.chunk_size_chars,
        chunk_overlap_chars=config.chunk_overlap_chars,
    )
    chunk_records: list[dict[str, JsonValue]] = []
    for start, end, chunk_text in chunks:
        chunk_records.append(
            {
                "doc_index": doc_index,
                "start_char": start,
                "end_char": end,
                "chunk_text": chunk_text,
            }
        )
    return {
        "tenant_id": tenant_id,
        "session_id": session_id,
        "doc_id": doc_id,
        "doc_index": doc_index,
        "chunk_size_chars": config.chunk_size_chars,
        "chunk_overlap_chars": config.chunk_overlap_chars,
        "chunks": chunk_records,
    }


def index_document(
    *,
    s3_client: BaseClient,
    bucket: str,
    tenant_id: str,
    session_id: str,
    doc_id: str,
    doc_index: int,
    text_s3_uri: str,
    config: SearchIndexConfig,
) -> tuple[str, int]:
    text_bucket, text_key = _split_s3_uri(text_s3_uri)
    payload = s3.get_bytes(s3_client, text_bucket, text_key)
    text = payload.decode("utf-8")
    index_payload = build_index_payload(
        tenant_id=tenant_id,
        session_id=session_id,
        doc_id=doc_id,
        doc_index=doc_index,
        config=config,
        text=text,
    )
    key = build_index_key(
        prefix=config.index_prefix,
        tenant_id=tenant_id,
        session_id=session_id,
        doc_id=doc_id,
    )
    s3.put_json(s3_client, bucket, key, index_payload)
    return f"s3://{bucket}/{key}", len(index_payload["chunks"])
