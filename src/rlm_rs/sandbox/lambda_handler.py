from __future__ import annotations

import json
from typing import Any

from rlm_rs.models import StepEvent, StepResult
from rlm_rs.sandbox.step_executor import execute_step


def _extract_payload(event: dict[str, Any]) -> dict[str, Any]:
    body = event.get("body")
    if body is None:
        return event
    if isinstance(body, str):
        return json.loads(body)
    if isinstance(body, dict):
        return body
    raise TypeError("Unsupported Lambda body payload.")


def lambda_handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    del context
    payload = _extract_payload(event)
    step_event = StepEvent.model_validate(payload)
    result = execute_step(step_event)
    return {"statusCode": 200, "body": result.model_dump_json()}


def run_local(payload: StepEvent | dict[str, Any]) -> StepResult:
    if isinstance(payload, StepEvent):
        step_event = payload
    else:
        step_event = StepEvent.model_validate(payload)
    return execute_step(step_event)
