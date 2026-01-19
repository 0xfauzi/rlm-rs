from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Iterable, Protocol

from botocore.client import BaseClient
from botocore.exceptions import ClientError
from openai import (
    APIConnectionError,
    APITimeoutError,
    APIStatusError,
    OpenAI,
    RateLimitError,
)
from structlog.stdlib import BoundLogger
from tenacity import Retrying, retry_if_exception, stop_after_attempt, wait_exponential

from rlm_rs.logging import get_logger
from rlm_rs.storage import s3


@dataclass(frozen=True)
class LLMCall:
    prompt: str
    model: str | None
    max_tokens: int | None
    temperature: float | None


class LLMProvider(Protocol):
    def complete_root(
        self,
        prompt: str,
        model: str | None,
        *,
        tenant_id: str | None = None,
    ) -> str:
        ...

    def complete_subcall(
        self,
        prompt: str,
        model: str | None,
        max_tokens: int,
        temperature: float | None,
        *,
        tenant_id: str | None = None,
    ) -> str:
        ...


class FakeLLMProvider:
    def __init__(
        self,
        *,
        root_outputs: Iterable[str] | None = None,
        default_root_output: str | None = None,
    ) -> None:
        self._root_outputs = deque(root_outputs or [])
        self._default_root_output = (
            default_root_output or "```repl\ntool.FINAL(\"ok\")\n```"
        )
        self.calls: list[LLMCall] = []

    def complete_root(
        self,
        prompt: str,
        model: str | None,
        *,
        tenant_id: str | None = None,
    ) -> str:
        self.calls.append(
            LLMCall(
                prompt=prompt,
                model=model,
                max_tokens=None,
                temperature=None,
            )
        )
        if self._root_outputs:
            return self._root_outputs.popleft()
        return self._default_root_output

    def complete_subcall(
        self,
        prompt: str,
        model: str | None,
        max_tokens: int,
        temperature: float | None,
        *,
        tenant_id: str | None = None,
    ) -> str:
        self.calls.append(
            LLMCall(
                prompt=prompt,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
            )
        )
        return f"fake:{prompt}"


DEFAULT_LLM_CACHE_PREFIX = "cache"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_OPENAI_TIMEOUT_SECONDS = 30.0
DEFAULT_OPENAI_MAX_RETRIES = 3
OPENAI_PROVIDER_NAME = "openai"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _format_timestamp(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _prompt_sha256(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def build_llm_cache_key(
    *,
    tenant_id: str,
    provider: str,
    model: str | None,
    max_tokens: int,
    temperature: float | None,
    prompt: str,
    prefix: str = DEFAULT_LLM_CACHE_PREFIX,
) -> str:
    key_payload = {
        "provider": provider,
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "prompt_sha256": _prompt_sha256(prompt),
    }
    digest = hashlib.sha256(s3.deterministic_json_bytes(key_payload)).hexdigest()
    return f"{prefix}/{tenant_id}/llm/{digest}.json"


def _cache_record(
    *,
    provider: str,
    model: str | None,
    prompt: str,
    max_tokens: int,
    temperature: float | None,
    text: str,
    raw: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "created_at": _format_timestamp(_utc_now()),
        "provider": provider,
        "model": model,
        "request": {
            "prompt_sha256": _prompt_sha256(prompt),
            "max_tokens": max_tokens,
            "temperature": temperature,
        },
        "response": {
            "text": text,
            "raw": raw or {},
        },
    }


def _is_cache_miss(exc: BaseException) -> bool:
    if isinstance(exc, KeyError):
        return True
    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code")
        return code in {"NoSuchKey", "404", "NotFound"}
    return False


class S3LLMCache:
    def __init__(
        self,
        s3_client: BaseClient,
        bucket: str,
        *,
        prefix: str = DEFAULT_LLM_CACHE_PREFIX,
    ) -> None:
        self._s3_client = s3_client
        self._bucket = bucket
        self._prefix = prefix

    def get_text(
        self,
        *,
        tenant_id: str,
        provider: str,
        model: str | None,
        max_tokens: int,
        temperature: float | None,
        prompt: str,
    ) -> str | None:
        key = build_llm_cache_key(
            tenant_id=tenant_id,
            provider=provider,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            prompt=prompt,
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
        text = response.get("text")
        if not isinstance(text, str):
            return None
        return text

    def put_text(
        self,
        *,
        tenant_id: str,
        provider: str,
        model: str | None,
        max_tokens: int,
        temperature: float | None,
        prompt: str,
        text: str,
        raw: dict[str, Any] | None = None,
    ) -> str:
        key = build_llm_cache_key(
            tenant_id=tenant_id,
            provider=provider,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            prompt=prompt,
            prefix=self._prefix,
        )
        record = _cache_record(
            provider=provider,
            model=model,
            prompt=prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            text=text,
            raw=raw,
        )
        s3.put_json(self._s3_client, self._bucket, key, record)
        return key


def _should_retry_openai(exc: BaseException) -> bool:
    if isinstance(exc, (APIConnectionError, APITimeoutError, RateLimitError)):
        return True
    if isinstance(exc, APIStatusError):
        status = getattr(exc, "status_code", None)
        if status is not None and status >= 500:
            return True
        if status == 429:
            return True
    return False


def _uses_max_completion_tokens(model: str | None) -> bool:
    if not model:
        return False
    normalized = model.lower()
    if normalized.startswith("gpt-5"):
        return True
    if normalized.startswith("o") and len(normalized) > 1 and normalized[1].isdigit():
        return True
    return False


def _openai_error_payload(exc: BaseException) -> dict[str, Any] | None:
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        return body
    if isinstance(body, (str, bytes)):
        try:
            parsed = json.loads(body)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, dict):
            return parsed
    response = getattr(exc, "response", None)
    if response is None:
        return None
    json_method = getattr(response, "json", None)
    if not callable(json_method):
        return None
    try:
        payload = json_method()
    except Exception:  # noqa: BLE001
        return None
    if isinstance(payload, dict):
        return payload
    return None


def _wants_max_completion_tokens(exc: BaseException) -> bool:
    payload = _openai_error_payload(exc)
    if payload:
        error = payload.get("error")
        if isinstance(error, dict):
            param = error.get("param")
            code = error.get("code")
            message = error.get("message")
            if param == "max_tokens" and code == "unsupported_parameter":
                return True
            if isinstance(message, str) and "max_completion_tokens" in message:
                return True
    text = str(exc)
    return "max_completion_tokens" in text and "max_tokens" in text


def _wants_default_temperature(exc: BaseException) -> bool:
    payload = _openai_error_payload(exc)
    if payload:
        error = payload.get("error")
        if isinstance(error, dict):
            param = error.get("param")
            code = error.get("code")
            message = error.get("message")
            if param == "temperature" and code in {
                "unsupported_value",
                "invalid_value",
                "unsupported_parameter",
            }:
                return True
            if isinstance(message, str) and "temperature" in message.lower():
                if "only the default" in message.lower():
                    return True
    text = str(exc).lower()
    return "temperature" in text and "only the default" in text


class OpenAIProvider:
    def __init__(
        self,
        *,
        client: Any | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float | None = DEFAULT_OPENAI_TIMEOUT_SECONDS,
        max_retries: int | None = DEFAULT_OPENAI_MAX_RETRIES,
        s3_client: BaseClient | None = None,
        s3_bucket: str | None = None,
        cache_prefix: str = DEFAULT_LLM_CACHE_PREFIX,
        logger: BoundLogger | None = None,
    ) -> None:
        if timeout_seconds is None:
            timeout_seconds = DEFAULT_OPENAI_TIMEOUT_SECONDS
        if max_retries is None:
            max_retries = DEFAULT_OPENAI_MAX_RETRIES
        resolved_base_url = (
            base_url.strip()
            if isinstance(base_url, str) and base_url.strip()
            else DEFAULT_OPENAI_BASE_URL
        )
        if client is None:
            client = OpenAI(
                api_key=api_key,
                base_url=resolved_base_url,
                timeout=timeout_seconds,
                max_retries=0,
            )
        self._client = client
        self._max_retries = max_retries
        self._logger = logger or get_logger("rlm_rs.llm")
        self._cache = None
        if s3_client is not None and s3_bucket is not None:
            self._cache = S3LLMCache(s3_client, s3_bucket, prefix=cache_prefix)

    def complete_root(
        self,
        prompt: str,
        model: str | None,
        *,
        tenant_id: str | None = None,
    ) -> str:
        text, raw = self._chat_completion_with_meta(
            prompt,
            model,
            max_tokens=None,
            temperature=None,
        )
        self._log_completion(
            call_kind="root",
            model=model,
            text=text,
            raw=raw,
            tenant_id=tenant_id,
        )
        return text

    def complete_subcall(
        self,
        prompt: str,
        model: str | None,
        max_tokens: int,
        temperature: float | None,
        *,
        tenant_id: str | None = None,
    ) -> str:
        effective_temperature = 0.0 if temperature is None else temperature
        if tenant_id and self._cache is not None:
            cached = self._cache.get_text(
                tenant_id=tenant_id,
                provider=OPENAI_PROVIDER_NAME,
                model=model,
                max_tokens=max_tokens,
                temperature=effective_temperature,
                prompt=prompt,
            )
            if cached is not None:
                return cached
        text, raw = self._chat_completion_with_meta(
            prompt,
            model,
            max_tokens=max_tokens,
            temperature=effective_temperature,
        )
        if tenant_id and self._cache is not None:
            self._cache.put_text(
                tenant_id=tenant_id,
                provider=OPENAI_PROVIDER_NAME,
                model=model,
                max_tokens=max_tokens,
                temperature=effective_temperature,
                prompt=prompt,
                text=text,
                raw=raw,
            )
        return text

    def _log_completion(
        self,
        *,
        call_kind: str,
        model: str | None,
        text: str,
        raw: dict[str, Any],
        tenant_id: str | None,
    ) -> None:
        if self._logger is None:
            return
        payload: dict[str, Any] = {
            "call_kind": call_kind,
            "model": model,
            "output_chars": len(text),
        }
        finish_reason = raw.get("finish_reason")
        if isinstance(finish_reason, str) and finish_reason:
            payload["finish_reason"] = finish_reason
        usage = raw.get("usage")
        if isinstance(usage, dict):
            completion_tokens = usage.get("completion_tokens")
            if completion_tokens is not None:
                payload["completion_tokens"] = completion_tokens
        if tenant_id:
            payload["tenant_id"] = tenant_id
        self._logger.info("llm_completion", **payload)

    def _chat_completion(
        self,
        prompt: str,
        model: str | None,
        *,
        max_tokens: int | None,
        temperature: float | None,
    ) -> str:
        text, _ = self._chat_completion_with_meta(
            prompt,
            model,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return text

    def _chat_completion_with_meta(
        self,
        prompt: str,
        model: str | None,
        *,
        max_tokens: int | None,
        temperature: float | None,
    ) -> tuple[str, dict[str, Any]]:
        if not model:
            raise ValueError("model is required for OpenAI provider")

        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
        }
        if max_tokens is not None:
            if _uses_max_completion_tokens(model):
                payload["max_completion_tokens"] = max_tokens
            else:
                payload["max_tokens"] = max_tokens
        if temperature is not None:
            payload["temperature"] = temperature

        def _call(request_payload: dict[str, Any]) -> Any:
            return self._client.chat.completions.create(**request_payload)

        try:
            response = self._with_retries(lambda: _call(payload))
        except APIStatusError as exc:
            retry_payload = dict(payload)
            retry = False
            if max_tokens is not None and _wants_max_completion_tokens(exc):
                if "max_tokens" in retry_payload:
                    retry_payload.pop("max_tokens", None)
                    retry_payload["max_completion_tokens"] = max_tokens
                    retry = True
            if _wants_default_temperature(exc) and "temperature" in retry_payload:
                retry_payload.pop("temperature", None)
                retry = True
            if not retry:
                raise
            response = self._with_retries(lambda: _call(retry_payload))
        text = ""
        finish_reason: str | None = None
        choices = getattr(response, "choices", None)
        if choices:
            choice = choices[0]
            finish_reason = getattr(choice, "finish_reason", None)
            message = getattr(choice, "message", None)
            content = getattr(message, "content", None)
            if isinstance(content, str):
                text = content
        raw: dict[str, Any] = {}
        response_id = getattr(response, "id", None)
        if response_id:
            raw["id"] = response_id
        if isinstance(finish_reason, str) and finish_reason:
            raw["finish_reason"] = finish_reason
        usage = getattr(response, "usage", None)
        if usage is not None:
            usage_payload: dict[str, Any] = {}
            for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
                value = getattr(usage, field, None)
                if value is not None:
                    usage_payload[field] = value
            if usage_payload:
                raw["usage"] = usage_payload
        return text, raw

    def _with_retries(self, func: Any) -> Any:
        retryer = Retrying(
            retry=retry_if_exception(_should_retry_openai),
            wait=wait_exponential(multiplier=0.5, min=0.5, max=5),
            stop=stop_after_attempt(self._max_retries),
            reraise=True,
        )
        for attempt in retryer:
            with attempt:
                return func()
        raise RuntimeError("Retry loop exited without response")
