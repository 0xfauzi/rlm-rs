from __future__ import annotations

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.types import ASGIApp

from rlm_rs.errors import ErrorCode, ErrorEnvelope, ErrorInfo


def _too_large_response(limit: int, observed: int) -> JSONResponse:
    envelope = ErrorEnvelope(
        error=ErrorInfo(
            code=ErrorCode.REQUEST_TOO_LARGE,
            message="Request body too large",
            details={"limit_bytes": limit, "observed_bytes": observed},
        )
    )
    return JSONResponse(status_code=413, content=envelope.model_dump())


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, *, default_limit: int | None = None) -> None:
        super().__init__(app)
        self._default_limit = default_limit

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        limit = getattr(request.app.state, "request_size_limit_bytes", None)
        if limit is None:
            limit = self._default_limit
        if limit is None or limit <= 0:
            return await call_next(request)

        content_length = request.headers.get("content-length")
        if content_length:
            try:
                declared = int(content_length)
            except ValueError:
                declared = None
            if declared is not None and declared > limit:
                return _too_large_response(limit, declared)

        body = await request.body()
        if len(body) > limit:
            return _too_large_response(limit, len(body))

        return await call_next(request)
