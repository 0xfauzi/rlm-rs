import pytest

from rlm_rs.errors import ErrorCode
from rlm_rs.models import ContextDocument, ContextManifest, LimitsSnapshot, StepEvent
from rlm_rs.sandbox import context as sandbox_context
from rlm_rs.sandbox import step_executor
from rlm_rs.sandbox.step_executor import execute_step
from rlm_rs.storage.state import canonical_state_bytes


def _make_event(
    *,
    code: str,
    limits: LimitsSnapshot | None = None,
    state: dict | str | None = None,
    manifest: ContextManifest | None = None,
) -> StepEvent:
    if manifest is None:
        manifest = ContextManifest(docs=[])
    return StepEvent(
        tenant_id="tenant",
        session_id="session",
        execution_id="execution",
        turn_index=0,
        code=code,
        state=state,
        context_manifest=manifest,
        limits=limits,
    )


def test_step_executor_limits_tool_requests() -> None:
    code = "tool.queue_llm('k1', 'prompt', max_tokens=1)\n"
    code += "tool.queue_search('k2', 'query')"
    event = _make_event(
        code=code,
        limits=LimitsSnapshot(max_tool_requests_per_step=1),
    )

    result = execute_step(event)

    assert not result.success
    assert result.error is not None
    assert result.error.code == ErrorCode.BUDGET_EXCEEDED.value


def test_step_executor_blocks_synthesis_without_required_notes() -> None:
    code = (
        "tool.queue_llm("
        "'synth', "
        "'Combine notes', "
        "max_tokens=10, "
        "metadata={'requires_llm_keys': ['note_1', 'note_2']}"
        ")\n"
    )
    event = _make_event(
        code=code,
        state={"_tool_results": {"llm": {"note_1": {"text": "ok"}}, "search": {}}},
    )

    result = execute_step(event)

    assert not result.success
    assert result.error is not None
    assert result.error.code == ErrorCode.VALIDATION_ERROR.value
    assert result.error.details is not None
    assert result.error.details["missing_llm_keys"] == ["note_2"]


def test_step_executor_limits_spans(monkeypatch: pytest.MonkeyPatch) -> None:
    text = "hello world"
    text_bytes = text.encode("utf-8")
    offsets = {
        "char_length": len(text),
        "byte_length": len(text_bytes),
        "checkpoints": [
            {"char": 0, "byte": 0},
            {"char": len(text), "byte": len(text_bytes)},
        ],
    }

    monkeypatch.setattr(
        sandbox_context, "build_s3_client", lambda **kwargs: object()
    )
    monkeypatch.setattr(
        sandbox_context, "get_json", lambda client, bucket, key: offsets
    )

    def _fake_get_range_bytes(
        client: object,
        bucket: str,
        key: str,
        start: int,
        end: int | None = None,
        *,
        version_id: str | None = None,
    ) -> bytes:
        del client, bucket, key, version_id
        payload = text_bytes
        if end is None:
            return payload[start:]
        return payload[start : end + 1]

    monkeypatch.setattr(sandbox_context, "get_range_bytes", _fake_get_range_bytes)

    manifest = ContextManifest(
        docs=[
            ContextDocument(
                doc_id="doc-1",
                doc_index=0,
                text_s3_uri="s3://bucket/text.txt",
                offsets_s3_uri="s3://bucket/offsets.json",
            )
        ]
    )
    code = "for _ in range(3):\n    context[0].slice(0, 1, tag='unit')"
    event = _make_event(
        code=code,
        limits=LimitsSnapshot(max_spans_per_step=2),
        manifest=manifest,
    )

    result = execute_step(event)

    assert not result.success
    assert result.error is not None
    assert result.error.code == ErrorCode.BUDGET_EXCEEDED.value


def test_step_executor_limits_state_size() -> None:
    payload = {"data": "x" * 16}
    payload_bytes = canonical_state_bytes(payload)
    payload_length = len(payload_bytes.decode("utf-8"))
    event = _make_event(
        code="state = {'data': 'x' * 16}",
        limits=LimitsSnapshot(max_state_chars=payload_length - 1),
    )

    result = execute_step(event)

    assert not result.success
    assert result.error is not None
    assert result.error.code == ErrorCode.STATE_TOO_LARGE.value


def test_step_executor_limits_step_timeout() -> None:
    event = _make_event(
        code="while True:\n    pass",
        limits=LimitsSnapshot(max_step_seconds=1),
    )

    result = execute_step(event)

    assert not result.success
    assert result.error is not None
    assert result.error.code == ErrorCode.STEP_TIMEOUT.value
    assert result.error.details == {"limit": 1}


def test_step_executor_limits_step_timeout_trace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(step_executor, "_can_use_signal_timeout", lambda: False)
    event = _make_event(
        code="while True:\n    pass",
        limits=LimitsSnapshot(max_step_seconds=1),
    )

    result = execute_step(event)

    assert not result.success
    assert result.error is not None
    assert result.error.code == ErrorCode.STEP_TIMEOUT.value
    assert result.error.details == {"limit": 1}


def test_step_executor_limits_stdout_truncation() -> None:
    output = "x" * 8
    expected = f"{output}\n"
    limit = len(expected) - 1
    event = _make_event(
        code=f"print('{output}')",
        limits=LimitsSnapshot(max_stdout_chars=limit),
    )

    result = execute_step(event)

    assert result.success
    assert result.stdout == expected[:limit]


def test_step_executor_rejects_provider_imports() -> None:
    event = _make_event(code="import openai")

    result = execute_step(event)

    assert not result.success
    assert result.error is not None
    assert result.error.code == ErrorCode.SANDBOX_AST_REJECTED.value
