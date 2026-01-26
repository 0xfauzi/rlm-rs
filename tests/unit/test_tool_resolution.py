from __future__ import annotations

import threading
import time

from rlm_rs.models import LLMToolRequest, ToolRequestsEnvelope
from rlm_rs.orchestrator.worker import BudgetTracker, _resolve_tool_requests
from rlm_rs.search.backends import FakeSearchBackend


class _BarrierProvider:
    def __init__(self) -> None:
        self._barrier = threading.Barrier(2)

    def complete_subcall(
        self,
        prompt: str,
        model: str | None,
        max_tokens: int | None,
        temperature: float | None,
        *,
        tenant_id: str,
    ) -> str:
        _ = prompt, model, max_tokens, temperature, tenant_id
        try:
            self._barrier.wait(timeout=1.0)
        except threading.BrokenBarrierError as exc:
            raise RuntimeError("LLM subcalls were not concurrent") from exc
        return "ok"


def test_tool_resolution_runs_llm_requests_concurrently() -> None:
    requests = ToolRequestsEnvelope(
        llm=[
            LLMToolRequest(key="a", prompt="p1", max_tokens=10, temperature=0),
            LLMToolRequest(key="b", prompt="p2", max_tokens=10, temperature=0),
        ]
    )
    tracker = BudgetTracker(budgets=None, start_time=time.monotonic())

    results, statuses = _resolve_tool_requests(
        requests,
        tenant_id="tenant-1",
        session_id="session-1",
        provider=_BarrierProvider(),
        tracker=tracker,
        model="sub",
        enable_search=False,
        search_backend=FakeSearchBackend(),
        doc_indexes=[0],
        doc_lengths=[10],
        max_concurrency=2,
    )

    assert statuses["a"] == "resolved"
    assert statuses["b"] == "resolved"
    assert results.llm["a"].text == "ok"
    assert results.llm["b"].text == "ok"
