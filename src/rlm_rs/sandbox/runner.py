from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

import boto3
from botocore.client import BaseClient
from botocore.config import Config
from structlog.stdlib import BoundLogger

from rlm_rs.logging import get_logger
from rlm_rs.models import StepEvent, StepResult
from rlm_rs.settings import Settings
from rlm_rs.sandbox.step_executor import execute_step

SandboxRunnerMode = Literal["local", "lambda"]


@dataclass
class SandboxRunner:
    mode: SandboxRunnerMode
    lambda_function_name: str | None = None
    lambda_timeout_seconds: float | None = None
    lambda_client: BaseClient | None = None
    logger: BoundLogger | None = None

    def run(
        self,
        event: StepEvent,
        *,
        s3_client: BaseClient | None = None,
        region: str | None = None,
        endpoint_url: str | None = None,
    ) -> StepResult:
        if self.logger is None:
            self.logger = get_logger("rlm_rs.sandbox.runner")

        if self.mode == "local":
            return execute_step(
                event,
                s3_client=s3_client,
                region=region,
                endpoint_url=endpoint_url,
            )

        if self.mode != "lambda":
            raise ValueError(f"Unknown sandbox runner mode: {self.mode}")
        if not self.lambda_function_name:
            raise ValueError("sandbox lambda function name is required")

        if self.lambda_client is None:
            self.lambda_client = _build_lambda_client(
                region=region,
                endpoint_url=endpoint_url,
                timeout_seconds=self.lambda_timeout_seconds,
            )

        payload = json.dumps(event.model_dump(exclude_none=True)).encode("utf-8")
        response = self.lambda_client.invoke(
            FunctionName=self.lambda_function_name,
            InvocationType="RequestResponse",
            Payload=payload,
        )
        raw = response.get("Payload")
        if raw is None:
            raise RuntimeError("Lambda response payload missing")
        response_payload = raw.read()
        if isinstance(response_payload, bytes):
            response_payload = response_payload.decode("utf-8")
        payload_obj = json.loads(response_payload)
        if isinstance(payload_obj, dict) and "statusCode" in payload_obj:
            status = int(payload_obj.get("statusCode") or 0)
            body = payload_obj.get("body")
            if status != 200:
                raise RuntimeError(f"Lambda error status {status}: {body}")
            if isinstance(body, str):
                return StepResult.model_validate_json(body)
            return StepResult.model_validate(body)
        return StepResult.model_validate(payload_obj)


def build_sandbox_runner(
    settings: Settings,
    *,
    logger: BoundLogger | None = None,
    lambda_client: BaseClient | None = None,
) -> SandboxRunner:
    mode = (settings.sandbox_runner or "local").lower()
    if mode not in ("local", "lambda"):
        raise ValueError(f"Unsupported sandbox runner mode: {mode}")
    return SandboxRunner(
        mode=mode,  # type: ignore[arg-type]
        lambda_function_name=settings.sandbox_lambda_function_name,
        lambda_timeout_seconds=settings.sandbox_lambda_timeout_seconds,
        lambda_client=lambda_client,
        logger=logger,
    )


def _build_lambda_client(
    *,
    region: str | None,
    endpoint_url: str | None,
    timeout_seconds: float | None = None,
) -> BaseClient:
    if timeout_seconds is None:
        config = Config()
    else:
        config = Config(
            connect_timeout=timeout_seconds,
            read_timeout=timeout_seconds,
        )
    return boto3.client(
        "lambda",
        region_name=region,
        endpoint_url=endpoint_url,
        config=config,
    )
