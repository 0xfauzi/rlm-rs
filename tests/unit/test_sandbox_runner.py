from __future__ import annotations

import io
import json

from rlm_rs.models import ContextManifest, StepEvent, StepResult
from rlm_rs.sandbox.runner import SandboxRunner


class _FakeLambdaClient:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def invoke(self, **kwargs: object) -> dict[str, object]:
        _ = kwargs
        body = json.dumps(self._payload).encode("utf-8")
        return {"Payload": io.BytesIO(body)}


def _build_event(code: str, state: dict[str, object] | None = None) -> StepEvent:
    return StepEvent(
        tenant_id="tenant-1",
        session_id="session-1",
        execution_id="exec-1",
        turn_index=0,
        code=code,
        state=state or {},
        context_manifest=ContextManifest(docs=[]),
        tool_results=None,
        limits=None,
    )


def test_local_runner_executes_step() -> None:
    runner = SandboxRunner(mode="local")
    event = _build_event('print("hello")')

    result = runner.run(event)

    assert result.success is True
    assert "hello" in result.stdout


def test_lambda_runner_parses_step_result_payload() -> None:
    event = _build_event('print("hello")')
    local_result = SandboxRunner(mode="local").run(event)
    payload = {
        "statusCode": 200,
        "body": local_result.model_dump_json(),
    }
    runner = SandboxRunner(
        mode="lambda",
        lambda_function_name="rlm-sandbox-step",
        lambda_client=_FakeLambdaClient(payload),
    )

    lambda_result = runner.run(event)

    assert lambda_result.model_dump() == local_result.model_dump()


def test_state_persists_between_steps() -> None:
    runner = SandboxRunner(mode="local")
    first = _build_event('state["work"] = {"count": 1}')
    first_result = runner.run(first)

    second = _build_event('state["work"]["count"] += 1\nprint(state["work"]["count"])', first_result.state)
    second_result = runner.run(second)

    assert second_result.state == {"work": {"count": 2}}
    assert "2" in second_result.stdout
