from __future__ import annotations

from typing import Any

import httpx
from tenacity import Retrying, retry_if_exception, stop_after_attempt, wait_exponential

from .models import ParseFailure, ParseRequest, ParseResponse, ParseSuccess


class ParserRetryableError(Exception):
    def __init__(self, response: httpx.Response) -> None:
        super().__init__(f"Retryable status {response.status_code}")
        self.response = response


class ParserClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 30.0,
        max_retries: int = 3,
        auth_token: str | None = None,
    ) -> None:
        headers: dict[str, str] = {}
        if auth_token:
            headers["Authorization"] = f"Bearer {auth_token}"
        self._client = httpx.Client(
            base_url=base_url,
            timeout=httpx.Timeout(timeout_seconds),
            headers=headers,
        )
        self._max_retries = max_retries

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "ParserClient":
        return self

    def __exit__(self, exc_type: Any, exc: Any, exc_tb: Any) -> None:
        self.close()

    def parse(self, request: ParseRequest) -> ParseResponse:
        payload = request.model_dump(mode="json", exclude_none=True)
        response = self._send_request("/parse", payload)
        data = response.json()
        return _parse_response(data)

    def _send_request(self, path: str, payload: dict[str, Any]) -> httpx.Response:
        retryer = Retrying(
            retry=retry_if_exception(_should_retry),
            wait=wait_exponential(multiplier=0.5, min=0.5, max=5),
            stop=stop_after_attempt(self._max_retries),
            reraise=True,
        )
        for attempt in retryer:
            with attempt:
                response = self._client.post(path, json=payload)
                if response.status_code >= 500 or response.status_code == 429:
                    raise ParserRetryableError(response)
                response.raise_for_status()
                return response
        raise RuntimeError("Retry loop exited without response")


def _should_retry(exc: BaseException) -> bool:
    if isinstance(exc, ParserRetryableError):
        return True
    return isinstance(exc, httpx.RequestError)


def _parse_response(payload: dict[str, Any]) -> ParseResponse:
    status = payload.get("status")
    if status == "success":
        return ParseSuccess.model_validate(payload)
    if status == "failed":
        return ParseFailure.model_validate(payload)
    raise ValueError("Unexpected parser response status")
