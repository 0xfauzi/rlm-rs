from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from typing import Protocol, Sequence

from botocore.client import BaseClient
from botocore.exceptions import ClientError
from pydantic import JsonValue, ValidationError

from rlm_rs.errors import ErrorCode
from rlm_rs.models import SearchHit, SearchToolRequest
from rlm_rs.storage import s3

SEARCH_DISABLED_MESSAGE = "Search is disabled"
DEFAULT_SEARCH_CACHE_PREFIX = "cache"


def build_error_meta(
    code: ErrorCode | str,
    message: str,
    details: dict[str, JsonValue] | None = None,
) -> dict[str, JsonValue]:
    error_code = ErrorCode(code)
    payload: dict[str, JsonValue] = {
        "code": error_code.value,
        "message": message,
    }
    if details is not None:
        payload["details"] = details
    return {"error": payload}


def search_disabled_error_meta() -> dict[str, JsonValue]:
    return build_error_meta(
        ErrorCode.VALIDATION_ERROR,
        SEARCH_DISABLED_MESSAGE,
        details={"reason": "search_disabled"},
    )


class SearchBackend(Protocol):
    def search(
        self,
        *,
        tenant_id: str,
        session_id: str,
        request: SearchToolRequest,
        doc_indexes: Sequence[int],
        doc_lengths: Sequence[int] | None = None,
    ) -> list[SearchHit]:
        ...


def _stable_seed(query: str) -> int:
    digest = hashlib.sha256(query.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big")


def _span_length(query: str) -> int:
    return max(1, min(200, len(query)))


def _doc_info(
    doc_indexes: Sequence[int],
    doc_lengths: Sequence[int] | None,
) -> list[tuple[int, int]]:
    lengths = list(doc_lengths or [])
    info: list[tuple[int, int]] = []
    for index, doc_index in enumerate(doc_indexes):
        length = 0
        if index < len(lengths):
            length = max(0, int(lengths[index]))
        info.append((int(doc_index), length))
    return info


class FakeSearchBackend:
    def search(
        self,
        *,
        tenant_id: str,
        session_id: str,
        request: SearchToolRequest,
        doc_indexes: Sequence[int],
        doc_lengths: Sequence[int] | None = None,
    ) -> list[SearchHit]:
        _ = tenant_id, session_id
        k = max(int(request.k), 0)
        if k <= 0:
            return []
        info = _doc_info(doc_indexes, doc_lengths)
        if not info:
            return []
        seed = _stable_seed(request.query)
        span_length = _span_length(request.query)
        hits: list[SearchHit] = []
        for index in range(k):
            doc_index, doc_length = info[(seed + index) % len(info)]
            if doc_length <= 0:
                start_char = 0
                end_char = 0
            else:
                start_char = (seed + index * 97) % doc_length
                end_char = min(start_char + span_length, doc_length)
                if end_char == start_char:
                    end_char = min(start_char + 1, doc_length)
            hits.append(
                SearchHit(
                    doc_index=doc_index,
                    start_char=start_char,
                    end_char=end_char,
                )
            )
        return hits


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _format_timestamp(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_search_cache_key(
    *,
    tenant_id: str,
    session_id: str,
    request: SearchToolRequest,
    doc_indexes: Sequence[int],
    doc_lengths: Sequence[int] | None = None,
    prefix: str = DEFAULT_SEARCH_CACHE_PREFIX,
) -> str:
    key_payload: dict[str, JsonValue] = {
        "session_id": session_id,
        "query": request.query,
        "k": int(request.k),
        "filters": request.filters,
        "doc_indexes": [int(doc_index) for doc_index in doc_indexes],
    }
    if doc_lengths is not None:
        key_payload["doc_lengths"] = [int(length) for length in doc_lengths]
    digest = hashlib.sha256(s3.deterministic_json_bytes(key_payload)).hexdigest()
    cleaned = prefix.strip().strip("/") or DEFAULT_SEARCH_CACHE_PREFIX
    return f"{cleaned}/{tenant_id}/search/{digest}.json"


def _is_cache_miss(exc: BaseException) -> bool:
    if isinstance(exc, KeyError):
        return True
    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code")
        return code in {"NoSuchKey", "404", "NotFound"}
    return False


class S3SearchCache:
    def __init__(
        self,
        s3_client: BaseClient,
        bucket: str,
        *,
        prefix: str = DEFAULT_SEARCH_CACHE_PREFIX,
    ) -> None:
        self._s3_client = s3_client
        self._bucket = bucket
        self._prefix = prefix

    def get_hits(
        self,
        *,
        tenant_id: str,
        session_id: str,
        request: SearchToolRequest,
        doc_indexes: Sequence[int],
        doc_lengths: Sequence[int] | None = None,
    ) -> list[SearchHit] | None:
        key = build_search_cache_key(
            tenant_id=tenant_id,
            session_id=session_id,
            request=request,
            doc_indexes=doc_indexes,
            doc_lengths=doc_lengths,
            prefix=self._prefix,
        )
        try:
            payload = s3.get_json(self._s3_client, self._bucket, key)
        except Exception as exc:  # noqa: BLE001
            if _is_cache_miss(exc):
                return None
            return None
        if not isinstance(payload, dict):
            return None
        response = payload.get("response")
        if not isinstance(response, dict):
            return None
        hits_payload = response.get("hits")
        if not isinstance(hits_payload, list):
            return None
        hits: list[SearchHit] = []
        for item in hits_payload:
            if not isinstance(item, dict):
                return None
            try:
                hits.append(SearchHit.model_validate(item))
            except ValidationError:
                return None
        return hits

    def put_hits(
        self,
        *,
        tenant_id: str,
        session_id: str,
        request: SearchToolRequest,
        doc_indexes: Sequence[int],
        doc_lengths: Sequence[int] | None,
        hits: Sequence[SearchHit],
        backend: str,
    ) -> str:
        key = build_search_cache_key(
            tenant_id=tenant_id,
            session_id=session_id,
            request=request,
            doc_indexes=doc_indexes,
            doc_lengths=doc_lengths,
            prefix=self._prefix,
        )
        record = {
            "created_at": _format_timestamp(_utc_now()),
            "backend": backend,
            "request": {
                "query": request.query,
                "k": int(request.k),
                "filters": request.filters,
                "doc_indexes": [int(doc_index) for doc_index in doc_indexes],
                "doc_lengths": (
                    [int(length) for length in doc_lengths]
                    if doc_lengths is not None
                    else None
                ),
            },
            "response": {
                "hits": [hit.model_dump(exclude_none=True) for hit in hits],
            },
        }
        s3.put_json(self._s3_client, self._bucket, key, record)
        return key


@dataclass
class CachedSearchBackend:
    backend: SearchBackend
    cache: S3SearchCache
    backend_name: str

    def search(
        self,
        *,
        tenant_id: str,
        session_id: str,
        request: SearchToolRequest,
        doc_indexes: Sequence[int],
        doc_lengths: Sequence[int] | None = None,
    ) -> list[SearchHit]:
        cached = self.cache.get_hits(
            tenant_id=tenant_id,
            session_id=session_id,
            request=request,
            doc_indexes=doc_indexes,
            doc_lengths=doc_lengths,
        )
        if cached is not None:
            return cached
        hits = self.backend.search(
            tenant_id=tenant_id,
            session_id=session_id,
            request=request,
            doc_indexes=doc_indexes,
            doc_lengths=doc_lengths,
        )
        self.cache.put_hits(
            tenant_id=tenant_id,
            session_id=session_id,
            request=request,
            doc_indexes=doc_indexes,
            doc_lengths=doc_lengths,
            hits=hits,
            backend=self.backend_name,
        )
        return hits
