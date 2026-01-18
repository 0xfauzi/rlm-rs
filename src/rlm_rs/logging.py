from __future__ import annotations

import logging

import structlog

try:
    from opentelemetry import trace
except Exception:  # pragma: no cover - optional dependency
    trace = None

_LOG_CONTEXT_KEYS = ("request_id", "tenant_id", "session_id", "execution_id")


def _add_trace_context(
    logger: structlog.BoundLogger,
    method_name: str,
    event_dict: dict[str, object],
) -> dict[str, object]:
    if trace is None:
        return event_dict
    span = trace.get_current_span()
    if span is None:
        return event_dict
    span_context = span.get_span_context()
    if not span_context or not span_context.is_valid:
        return event_dict
    event_dict["trace_id"] = f"{span_context.trace_id:032x}"
    event_dict["span_id"] = f"{span_context.span_id:016x}"
    return event_dict


def configure_logging(log_level: str = "INFO") -> None:
    logging.basicConfig(level=log_level, format="%(message)s")
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            _add_trace_context,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def bind_log_context(**kwargs: str | None) -> None:
    context = {
        key: value
        for key, value in kwargs.items()
        if key in _LOG_CONTEXT_KEYS and value is not None
    }
    if context:
        structlog.contextvars.bind_contextvars(**context)


def clear_log_context() -> None:
    structlog.contextvars.clear_contextvars()


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
