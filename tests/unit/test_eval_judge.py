from __future__ import annotations

from typing import Any

from rlm_rs.models import EvaluationJudgeMetrics, EvaluationJudgeScores, ModelsConfig, SpanLogEntry
from rlm_rs.orchestrator import baseline as baseline_eval
from rlm_rs.orchestrator import eval_judge
from rlm_rs.orchestrator.citations import DocumentText
from rlm_rs.orchestrator.providers import FakeLLMProvider
from rlm_rs.orchestrator.worker import OrchestratorWorker
from rlm_rs.settings import Settings
from rlm_rs.storage import ddb


class _FakeTable:
    def __init__(self) -> None:
        self.items: dict[tuple[str, str], dict[str, Any]] = {}

    def put_item(self, *, Item: dict[str, Any], ConditionExpression: str | None = None) -> None:
        key = (Item["PK"], Item["SK"])
        self.items[key] = dict(Item)

    def update_item(
        self,
        *,
        Key: dict[str, str],
        UpdateExpression: str,
        ExpressionAttributeNames: dict[str, str] | None = None,
        ExpressionAttributeValues: dict[str, Any] | None = None,
        ConditionExpression: str | None = None,
    ) -> None:
        item = self.items.get((Key["PK"], Key["SK"]))
        if item is None:
            item = {"PK": Key["PK"], "SK": Key["SK"]}
            self.items[(Key["PK"], Key["SK"])] = item
        updates = UpdateExpression.removeprefix("SET ").split(",")
        names = ExpressionAttributeNames or {}
        values = ExpressionAttributeValues or {}
        for update in updates:
            left, right = update.split("=", 1)
            attr = names.get(left.strip(), left.strip())
            item[attr] = values[right.strip()]


def test_eval_judge_metrics_persisted(monkeypatch: Any) -> None:
    monkeypatch.setenv("S3_BUCKET", "eval-bucket")
    monkeypatch.setenv("ENABLE_EVAL_JUDGE", "true")
    monkeypatch.setenv("EVAL_JUDGE_MODEL", "gpt-5")
    monkeypatch.setenv("EVAL_JUDGE_PROVIDER", "openai")
    settings = Settings()

    def fake_prepare_baseline_prompt(**_: Any) -> baseline_eval.BaselineCheckResult:
        return baseline_eval.BaselineCheckResult(
            prompt="baseline",
            input_tokens=12,
            context_window=128,
            skip_reason=None,
        )

    def fake_evaluate_judge(
        *,
        question: str,
        answer: str | None,
        answerer_contexts: list[str],
        baseline_answer: str | None,
        baseline_contexts: list[str],
        settings: Settings,
        logger: Any | None = None,
    ) -> EvaluationJudgeMetrics:
        assert question == "What is it?"
        assert answer == "Alpha"
        assert baseline_answer == "fake:baseline"
        assert answerer_contexts == ["Alpha"]
        assert baseline_contexts == ["Alpha beta"]
        return EvaluationJudgeMetrics(
            answerer=EvaluationJudgeScores(answer_relevancy=0.7, faithfulness=0.8),
            baseline=EvaluationJudgeScores(answer_relevancy=0.4, faithfulness=0.5),
        )

    monkeypatch.setattr(baseline_eval, "prepare_baseline_prompt", fake_prepare_baseline_prompt)
    monkeypatch.setattr(eval_judge, "evaluate_judge", fake_evaluate_judge)

    table = _FakeTable()
    worker = OrchestratorWorker(
        settings=settings,
        ddb_resource=object(),
        table_names=ddb.build_table_names(None),
        s3_client=object(),
        provider=FakeLLMProvider(),
    )

    worker._create_evaluation_record(
        table,
        execution_item={"mode": "ANSWERER"},
        session_id="sess-1",
        execution_id="exec-1",
        tenant_id="tenant-1",
        question="What is it?",
        answer="Alpha",
        models=ModelsConfig(root_model="gpt-5", sub_model=None),
        documents=[
            {
                "doc_id": "doc-1",
                "doc_index": 0,
                "ingest_status": "PARSED",
                "text_s3_uri": "s3://eval-bucket/doc-1.txt",
            }
        ],
        span_log=[SpanLogEntry(doc_index=0, start_char=0, end_char=5)],
        documents_text=[DocumentText(doc_id="doc-1", doc_index=0, text="Alpha beta")],
    )

    item = table.items[("EXEC#exec-1", "EVAL")]
    metrics = item["judge_metrics"]
    assert float(metrics["answerer"]["answer_relevancy"]) == 0.7
    assert float(metrics["answerer"]["faithfulness"]) == 0.8
    assert float(metrics["baseline"]["answer_relevancy"]) == 0.4
    assert float(metrics["baseline"]["faithfulness"]) == 0.5


def test_eval_judge_faithfulness_skipped_on_context_window_exceeded(monkeypatch: Any) -> None:
    def fake_evaluate(*_: Any, **kwargs: Any) -> Any:
        metric = kwargs.get("metrics", [None])[0]
        if isinstance(metric, eval_judge.Faithfulness):
            raise RuntimeError(
                "This model's maximum context length is 8192 tokens. However, you requested 9000 tokens."
            )

        class FakeResult:
            scores = [{"answer_relevancy": 0.9}]

        return FakeResult()

    monkeypatch.setattr(eval_judge, "evaluate", fake_evaluate)

    scores = eval_judge._score_answer(
        question="What is it?",
        answer="Alpha",
        contexts=["Alpha beta gamma" * 1000],
        llm=object(),
        embeddings=object(),
        logger=None,
        label="baseline",
    )
    assert scores is not None
    assert scores.answer_relevancy == 0.9
    assert scores.faithfulness is None
    assert scores.faithfulness_skip_reason == "CONTEXT_WINDOW_EXCEEDED"
