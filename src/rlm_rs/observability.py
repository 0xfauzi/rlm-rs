from __future__ import annotations

import os
import time

from fastapi import APIRouter, FastAPI, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.types import ASGIApp

from rlm_rs.settings import Settings

try:
    from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

    _PROMETHEUS_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency
    _PROMETHEUS_AVAILABLE = False
    CONTENT_TYPE_LATEST = "text/plain"
    Counter = None  # type: ignore[assignment]
    Histogram = None  # type: ignore[assignment]
    generate_latest = None  # type: ignore[assignment]


if _PROMETHEUS_AVAILABLE:
    _REQUEST_COUNT = Counter(
        "rlm_api_requests_total",
        "Total API requests",
        ("method", "endpoint", "status"),
    )
    _REQUEST_LATENCY = Histogram(
        "rlm_api_request_latency_seconds",
        "API request latency in seconds",
        ("method", "endpoint"),
    )

    class MetricsMiddleware(BaseHTTPMiddleware):
        def __init__(self, app: ASGIApp) -> None:
            super().__init__(app)

        async def dispatch(
            self,
            request: Request,
            call_next: RequestResponseEndpoint,
        ) -> Response:
            start = time.perf_counter()
            try:
                response = await call_next(request)
            except Exception:
                elapsed = time.perf_counter() - start
                endpoint = _route_label(request)
                _REQUEST_COUNT.labels(
                    method=request.method,
                    endpoint=endpoint,
                    status="500",
                ).inc()
                _REQUEST_LATENCY.labels(
                    method=request.method,
                    endpoint=endpoint,
                ).observe(elapsed)
                raise

            elapsed = time.perf_counter() - start
            endpoint = _route_label(request)
            _REQUEST_COUNT.labels(
                method=request.method,
                endpoint=endpoint,
                status=str(response.status_code),
            ).inc()
            _REQUEST_LATENCY.labels(
                method=request.method,
                endpoint=endpoint,
            ).observe(elapsed)
            return response

    _metrics_router = APIRouter()

    @_metrics_router.get("/metrics")
    def metrics() -> Response:
        payload = generate_latest() if generate_latest is not None else b""
        return Response(content=payload, media_type=CONTENT_TYPE_LATEST)


def _route_label(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    if isinstance(path, str):
        return path
    return request.url.path


def _configure_metrics(app: FastAPI) -> None:
    if not _PROMETHEUS_AVAILABLE:
        return
    app.add_middleware(MetricsMiddleware)
    app.include_router(_metrics_router)


def _configure_tracing(app: FastAPI) -> None:
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except Exception:  # pragma: no cover - optional dependency
        return

    resource = None
    service_name = os.getenv("OTEL_SERVICE_NAME")
    if service_name:
        resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource) if resource is not None else TracerProvider()
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app)


def configure_observability(app: FastAPI, settings: Settings) -> None:
    if settings.enable_metrics:
        _configure_metrics(app)
    if settings.enable_otel_tracing:
        _configure_tracing(app)
