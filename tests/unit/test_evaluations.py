from typing import Any

from rlm_rs.models import EvaluationRecord
from rlm_rs.storage import ddb


class _FakeTable:
    def __init__(self) -> None:
        self.items: dict[tuple[str, str], dict[str, Any]] = {}

    def put_item(self, *, Item: dict[str, Any], ConditionExpression: str | None = None) -> None:
        key = (Item["PK"], Item["SK"])
        if ConditionExpression and "attribute_not_exists" in ConditionExpression:
            if key in self.items:
                raise ValueError("Condition failed")
        self.items[key] = dict(Item)

    def get_item(self, *, Key: dict[str, str]) -> dict[str, Any]:
        item = self.items.get((Key["PK"], Key["SK"]))
        if item is None:
            return {}
        return {"Item": dict(item)}


def test_evaluation_record_roundtrip() -> None:
    payload = {
        "evaluation_id": "eval-123",
        "tenant_id": "tenant-1",
        "session_id": "sess-1",
        "execution_id": "exec-1",
        "mode": "ANSWERER",
        "question": "What is the policy?",
        "answer": "Here is the policy.",
        "baseline_status": "SKIPPED",
        "baseline_skip_reason": "RUNTIME_MODE",
        "baseline_answer": None,
        "baseline_input_tokens": None,
        "baseline_context_window": None,
        "created_at": "2026-01-01T00:00:00Z",
    }

    record = EvaluationRecord.model_validate(payload)

    assert record.evaluation_id == "eval-123"
    assert record.baseline_status == "SKIPPED"
    assert record.baseline_skip_reason == "RUNTIME_MODE"
    assert record.baseline_answer is None


def test_evaluation_ddb_helpers_roundtrip() -> None:
    table = _FakeTable()

    item = ddb.create_evaluation(
        table,
        evaluation_id="eval-456",
        tenant_id="tenant-2",
        session_id="sess-2",
        execution_id="exec-2",
        mode="ANSWERER",
        question="What is the plan?",
        answer="Here is the plan.",
        baseline_status="COMPLETED",
        baseline_skip_reason=None,
        baseline_answer="Baseline answer.",
        baseline_input_tokens=120,
        baseline_context_window=400000,
        created_at="2026-01-02T00:00:00Z",
    )

    assert item["PK"] == "EXEC#exec-2"
    assert item["SK"] == "EVAL"
    assert item["evaluation_id"] == "eval-456"
    assert item["baseline_context_window"] == 400000

    fetched = ddb.get_evaluation(table, execution_id="exec-2")
    assert fetched is not None
    assert fetched["evaluation_id"] == "eval-456"
    assert fetched["baseline_answer"] == "Baseline answer."
