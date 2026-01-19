from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from rlm_rs.api.executions import router as executions_router
from rlm_rs.api.health import router as health_router
from rlm_rs.api.request_limits import RequestSizeLimitMiddleware
from rlm_rs.api.rate_limits import attach_rate_limiter
from rlm_rs.api.sessions import router as sessions_router
from rlm_rs.api.spans import router as spans_router
from rlm_rs.errors import ErrorCode, ErrorEnvelope, ErrorInfo, RLMHTTPError
from rlm_rs.logging import configure_logging, get_logger
from rlm_rs.observability import configure_observability
from rlm_rs.settings import Settings


def _handle_rlm_http_error(_: Request, exc: RLMHTTPError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.error.model_dump())


def _handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    logger = get_logger("rlm_rs.api")
    logger.exception("api.unhandled_error", path=str(request.url.path))
    envelope = ErrorEnvelope(
        error=ErrorInfo(code=ErrorCode.INTERNAL_ERROR, message="Internal server error")
    )
    return JSONResponse(status_code=500, content=envelope.model_dump())


def create_app() -> FastAPI:
    configure_logging()
    settings = Settings()
    app = FastAPI(title="RLM API")
    app.state.request_size_limit_bytes = settings.request_size_limit_bytes
    attach_rate_limiter(app, settings)
    app.add_middleware(
        RequestSizeLimitMiddleware,
        default_limit=settings.request_size_limit_bytes,
    )
    configure_observability(app, settings)
    app.add_exception_handler(RLMHTTPError, _handle_rlm_http_error)
    app.add_exception_handler(Exception, _handle_unexpected_error)
    app.include_router(health_router)
    app.include_router(executions_router)
    app.include_router(sessions_router)
    app.include_router(spans_router)
    return app


app = create_app()
