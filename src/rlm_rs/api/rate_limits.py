from __future__ import annotations

import time
from dataclasses import dataclass

from fastapi import Depends, FastAPI, Request
from pydantic import BaseModel, Field, JsonValue, ValidationError

from rlm_rs.api.auth import ApiKeyContext, require_api_key
from rlm_rs.errors import ErrorCode, raise_http_error
from rlm_rs.settings import Settings


class RateLimitSpec(BaseModel):
    max_requests: int = Field(gt=0)
    window_seconds: int = Field(gt=0)


class RateLimitsConfig(BaseModel):
    default: RateLimitSpec
    tenants: dict[str, RateLimitSpec] = Field(default_factory=dict)


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    limit: int
    remaining: int
    reset_seconds: int
    window_seconds: int

    def to_details(self) -> dict[str, int | bool]:
        return {
            "allowed": self.allowed,
            "limit": self.limit,
            "remaining": self.remaining,
            "reset_seconds": self.reset_seconds,
            "window_seconds": self.window_seconds,
        }


class RateLimiter:
    def __init__(self, config: RateLimitsConfig) -> None:
        self._config = config
        self._state: dict[str, tuple[float, int]] = {}

    def check(self, tenant_id: str) -> RateLimitDecision:
        spec = self._config.tenants.get(tenant_id, self._config.default)
        now = time.monotonic()
        window_start, count = self._state.get(tenant_id, (now, 0))
        if now - window_start >= spec.window_seconds:
            window_start = now
            count = 0
        count += 1
        self._state[tenant_id] = (window_start, count)
        remaining = max(spec.max_requests - count, 0)
        reset_seconds = max(int(spec.window_seconds - (now - window_start)), 0)
        return RateLimitDecision(
            allowed=count <= spec.max_requests,
            limit=spec.max_requests,
            remaining=remaining,
            reset_seconds=reset_seconds,
            window_seconds=spec.window_seconds,
        )


def _parse_rate_limits(raw: JsonValue | None) -> RateLimitsConfig | None:
    if raw is None:
        return None
    if isinstance(raw, dict) and "max_requests" in raw:
        return RateLimitsConfig.model_validate({"default": raw})
    return RateLimitsConfig.model_validate(raw)


def build_rate_limiter(settings: Settings) -> RateLimiter | None:
    try:
        config = _parse_rate_limits(settings.rate_limits_json)
    except ValidationError as exc:
        raise ValueError(f"Invalid rate limits config: {exc}") from exc
    if config is None:
        return None
    return RateLimiter(config)


def attach_rate_limiter(app: FastAPI, settings: Settings) -> None:
    app.state.rate_limiter = build_rate_limiter(settings)


def enforce_rate_limit(
    request: Request,
    context: ApiKeyContext = Depends(require_api_key),
) -> None:
    limiter = getattr(request.app.state, "rate_limiter", None)
    if limiter is None:
        return
    decision = limiter.check(context.tenant_id)
    if not decision.allowed:
        raise_http_error(
            ErrorCode.RATE_LIMITED,
            "Rate limit exceeded",
            details=decision.to_details(),
        )
