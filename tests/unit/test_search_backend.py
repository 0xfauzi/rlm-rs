import time
from typing import Any

from botocore.exceptions import ClientError

from rlm_rs.models import SearchHit, SearchToolRequest, ToolRequestsEnvelope
from rlm_rs.orchestrator.providers import FakeLLMProvider
from rlm_rs.orchestrator.worker import BudgetTracker, _resolve_tool_requests
from rlm_rs.search.backends import (
    CachedSearchBackend,
    FakeSearchBackend,
    S3SearchCache,
    SEARCH_DISABLED_MESSAGE,
)


def test_fake_search_backend_returns_deterministic_hits() -> None:
    backend = FakeSearchBackend()
    request = SearchToolRequest(key="s1", query="alpha beta", k=3, filters=None)
    doc_indexes = [0, 1]
    doc_lengths = [20, 40]

    first = backend.search(
        tenant_id="tenant-1",
        session_id="session-1",
        request=request,
        doc_indexes=doc_indexes,
        doc_lengths=doc_lengths,
    )
    second = backend.search(
        tenant_id="tenant-1",
        session_id="session-1",
        request=request,
        doc_indexes=doc_indexes,
        doc_lengths=doc_lengths,
    )

    assert [hit.model_dump() for hit in first] == [hit.model_dump() for hit in second]
    assert len(first) == 3

    lengths = dict(zip(doc_indexes, doc_lengths))
    for hit in first:
        assert isinstance(hit.doc_index, int)
        assert isinstance(hit.start_char, int)
        assert isinstance(hit.end_char, int)
        assert hit.doc_index in lengths
        assert 0 <= hit.start_char <= hit.end_char <= lengths[hit.doc_index]


def test_search_disabled_rejects_requests_with_error_meta() -> None:
    requests = ToolRequestsEnvelope(
        search=[SearchToolRequest(key="s1", query="term", k=2, filters=None)]
    )
    tracker = BudgetTracker(budgets=None, start_time=time.monotonic())
    results, statuses = _resolve_tool_requests(
        requests,
        tenant_id="tenant-1",
        session_id="session-1",
        provider=FakeLLMProvider(),
        tracker=tracker,
        model=None,
        enable_search=False,
        search_backend=FakeSearchBackend(),
        doc_indexes=[0],
        doc_lengths=[10],
    )

    assert statuses["s1"] == "error"
    meta = results.search["s1"].meta
    assert meta is not None
    error = meta.get("error")
    assert isinstance(error, dict)
    assert error.get("code") == "VALIDATION_ERROR"
    assert error.get("message") == SEARCH_DISABLED_MESSAGE


class _FakeS3Body:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload


class _FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}

    def put_object(self, *, Bucket: str, Key: str, Body: Any, **kwargs: Any) -> None:
        payload = Body if isinstance(Body, bytes) else str(Body).encode("utf-8")
        self.objects[(Bucket, Key)] = payload

    def get_object(self, *, Bucket: str, Key: str, **kwargs: Any) -> dict[str, Any]:
        payload = self.objects.get((Bucket, Key))
        if payload is None:
            raise _not_found_error("GetObject")
        return {"Body": _FakeS3Body(payload)}


def _not_found_error(operation: str) -> ClientError:
    return ClientError(
        {
            "Error": {
                "Code": "NoSuchKey",
                "Message": "Not found",
            }
        },
        operation,
    )


class _CountingBackend:
    def __init__(self) -> None:
        self.calls = 0

    def search(
        self,
        *,
        tenant_id: str,
        session_id: str,
        request: SearchToolRequest,
        doc_indexes: list[int],
        doc_lengths: list[int] | None = None,
    ) -> list[SearchHit]:
        self.calls += 1
        _ = tenant_id, session_id, request, doc_lengths
        return [
            SearchHit(
                doc_index=doc_indexes[0],
                start_char=1,
                end_char=3,
                score=0.5,
                preview="hit",
            )
        ]


def test_search_cache_hits_reuse_s3_record() -> None:
    backend = _CountingBackend()
    s3_client = _FakeS3Client()
    cache = S3SearchCache(s3_client, "bucket")
    cached = CachedSearchBackend(backend=backend, cache=cache, backend_name="fake")
    request = SearchToolRequest(key="k1", query="term", k=1, filters=None)

    first = cached.search(
        tenant_id="tenant-1",
        session_id="session-1",
        request=request,
        doc_indexes=[0],
        doc_lengths=[10],
    )
    second = cached.search(
        tenant_id="tenant-1",
        session_id="session-1",
        request=request,
        doc_indexes=[0],
        doc_lengths=[10],
    )

    assert backend.calls == 1
    assert [hit.model_dump() for hit in first] == [hit.model_dump() for hit in second]
