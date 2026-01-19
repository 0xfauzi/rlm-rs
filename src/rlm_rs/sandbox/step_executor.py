from __future__ import annotations

import builtins
import contextlib
import io
import signal
import sys
import threading
import time
from collections.abc import Iterator

from botocore.client import BaseClient
from pydantic import JsonValue

from rlm_rs.errors import ErrorCode
from rlm_rs.models import (
    LimitsSnapshot,
    StepError,
    StepEvent,
    StepFinal,
    StepResult,
    ToolRequestsEnvelope,
)
from rlm_rs.sandbox.ast_policy import ALLOWED_BUILTINS, AstPolicyError, validate_source
from rlm_rs.sandbox.context import ContextView
from rlm_rs.sandbox.tool_api import ToolAPI, ToolFinal, ToolRequestLimitError, ToolYield
from rlm_rs.storage.state import (
    StateValidationError,
    canonical_state_bytes,
    validate_state_payload,
)


class StepTimeoutError(Exception):
    pass


def _timeout_handler(signum: int, frame: object) -> None:
    del signum, frame
    raise StepTimeoutError("Step exceeded max_step_seconds")


def _can_use_signal_timeout() -> bool:
    if threading.current_thread() is not threading.main_thread():
        return False
    return hasattr(signal, "SIGALRM") and hasattr(signal, "setitimer")


@contextlib.contextmanager
def _trace_timeout(limit: int) -> Iterator[None]:
    deadline = time.monotonic() + limit
    previous_trace = sys.gettrace()

    def _wrap_trace(prev_trace: object | None):
        def _trace(frame: object, event: str, arg: object) -> object | None:
            if time.monotonic() >= deadline:
                raise StepTimeoutError("Step exceeded max_step_seconds")
            if prev_trace is None:
                return _trace
            next_trace = prev_trace(frame, event, arg)
            if next_trace is None:
                return _trace
            return _wrap_trace(next_trace)

        return _trace

    sys.settrace(_wrap_trace(previous_trace))
    try:
        yield
    finally:
        sys.settrace(previous_trace)


@contextlib.contextmanager
def _step_timeout(limit: int | None) -> Iterator[None]:
    if limit is None:
        yield
        return
    if limit <= 0:
        raise StepTimeoutError("Step exceeded max_step_seconds")
    if _can_use_signal_timeout():
        previous_handler = signal.getsignal(signal.SIGALRM)
        previous_timer = signal.getitimer(signal.ITIMER_REAL)
        try:
            signal.signal(signal.SIGALRM, _timeout_handler)
            signal.setitimer(signal.ITIMER_REAL, limit)
        except ValueError:
            signal.signal(signal.SIGALRM, previous_handler)
            signal.setitimer(signal.ITIMER_REAL, *previous_timer)
        else:
            try:
                yield
            finally:
                signal.setitimer(signal.ITIMER_REAL, *previous_timer)
                signal.signal(signal.SIGALRM, previous_handler)
            return
    with _trace_timeout(limit):
        yield


def _allowed_builtins() -> dict[str, object]:
    return {
        name: getattr(builtins, name)
        for name in ALLOWED_BUILTINS
        if hasattr(builtins, name)
    }


def _build_error(
    code: ErrorCode | str,
    message: str,
    details: dict[str, JsonValue] | None = None,
) -> StepError:
    if isinstance(code, ErrorCode):
        code_value = code.value
    else:
        code_value = str(code)
    return StepError(code=code_value, message=message, details=details)


def _truncate_stdout(stdout: str, limit: int | None) -> str:
    if limit is None:
        return stdout
    return stdout[:limit]


def _normalize_tool_requests(tool: ToolAPI) -> ToolRequestsEnvelope | None:
    envelope = tool.tool_requests
    if not envelope.llm and not envelope.search:
        return None
    return envelope


def _span_limit_error(
    span_count: int,
    limits: LimitsSnapshot | None,
) -> StepError | None:
    if not limits or limits.max_spans_per_step is None:
        return None
    limit = limits.max_spans_per_step
    if span_count <= limit:
        return None
    return _build_error(
        ErrorCode.BUDGET_EXCEEDED,
        f"Span limit exceeded: {limit}",
        details={"limit": limit, "observed": span_count},
    )


def _tool_limit_error(
    tool: ToolAPI,
    limits: LimitsSnapshot | None,
) -> StepError | None:
    if not limits or limits.max_tool_requests_per_step is None:
        return None
    limit = limits.max_tool_requests_per_step
    envelope = tool.tool_requests
    observed = len(envelope.llm) + len(envelope.search)
    if observed <= limit:
        return None
    return _build_error(
        ErrorCode.BUDGET_EXCEEDED,
        f"Tool request limit exceeded: {limit}",
        details={"limit": limit, "observed": observed},
    )


def _state_limit_error(
    state: JsonValue | None,
    limits: LimitsSnapshot | None,
) -> StepError | None:
    try:
        validate_state_payload(state)
    except StateValidationError as exc:
        return _build_error(ErrorCode.STATE_INVALID_TYPE, str(exc))
    if not limits or limits.max_state_chars is None:
        return None
    state_bytes = canonical_state_bytes(state)
    state_text = state_bytes.decode("utf-8")
    state_length = len(state_text)
    if state_length <= limits.max_state_chars:
        return None
    return _build_error(
        ErrorCode.STATE_TOO_LARGE,
        f"State size exceeded: {limits.max_state_chars}",
        details={"limit": limits.max_state_chars, "observed": state_length},
    )


def _result(
    *,
    success: bool,
    stdout: str,
    state: JsonValue | None,
    span_log: list,
    tool_requests: ToolRequestsEnvelope | None,
    final: StepFinal | None,
    error: StepError | None,
) -> StepResult:
    return StepResult(
        success=success,
        stdout=stdout,
        state=state,
        span_log=span_log,
        tool_requests=tool_requests,
        final=final,
        error=error,
    )


def _ast_error_details(error: AstPolicyError) -> dict[str, JsonValue]:
    violations = [
        {
            "rule": violation.rule,
            "message": violation.message,
            "line": violation.line,
            "col": violation.col,
        }
        for violation in error.violations
    ]
    return {"violations": violations}


def execute_step(
    event: StepEvent,
    *,
    s3_client: BaseClient | None = None,
    region: str | None = None,
    endpoint_url: str | None = None,
) -> StepResult:
    try:
        validate_source(event.code)
    except SyntaxError as exc:
        details = {"line": exc.lineno, "col": exc.offset}
        return _result(
            success=False,
            stdout="",
            state=event.state,
            span_log=[],
            tool_requests=None,
            final=None,
            error=_build_error(ErrorCode.VALIDATION_ERROR, str(exc), details=details),
        )
    except AstPolicyError as exc:
        return _result(
            success=False,
            stdout="",
            state=event.state,
            span_log=[],
            tool_requests=None,
            final=None,
            error=_build_error(
                ErrorCode.SANDBOX_AST_REJECTED,
                str(exc),
                details=_ast_error_details(exc),
            ),
        )

    context_view = ContextView(
        event.context_manifest,
        s3_client=s3_client,
        region=region,
        endpoint_url=endpoint_url,
    )
    tool = ToolAPI(limits=event.limits)
    max_step_seconds = event.limits.max_step_seconds if event.limits else None
    sandbox_globals: dict[str, object] = {
        "__builtins__": _allowed_builtins(),
        "context": context_view,
        "state": event.state,
        "tool": tool,
    }

    stdout_buffer = io.StringIO()
    final: StepFinal | None = None
    error: StepError | None = None
    with contextlib.redirect_stdout(stdout_buffer):
        try:
            with _step_timeout(max_step_seconds):
                exec(event.code, sandbox_globals)
        except StepTimeoutError as exc:
            error = _build_error(
                ErrorCode.STEP_TIMEOUT,
                str(exc),
                details={"limit": max_step_seconds},
            )
        except ToolYield as exc:
            final = StepFinal(is_final=False, answer=exc.reason)
        except ToolFinal as exc:
            final = StepFinal(is_final=True, answer=exc.answer)
        except ToolRequestLimitError as exc:
            error = _build_error(
                ErrorCode.BUDGET_EXCEEDED,
                str(exc),
                details={"limit": exc.limit},
            )
        except Exception as exc:  # noqa: BLE001
            error = _build_error(
                ErrorCode.INTERNAL_ERROR,
                str(exc),
                details={"type": type(exc).__name__},
            )

    stdout = _truncate_stdout(
        stdout_buffer.getvalue(),
        event.limits.max_stdout_chars if event.limits else None,
    )
    span_log = context_view.span_log
    tool_requests = _normalize_tool_requests(tool)
    state_value = sandbox_globals.get("state", event.state)

    if error is not None:
        return _result(
            success=False,
            stdout=stdout,
            state=event.state,
            span_log=span_log,
            tool_requests=tool_requests,
            final=None,
            error=error,
        )

    state_error = _state_limit_error(state_value, event.limits)
    if state_error is not None:
        return _result(
            success=False,
            stdout=stdout,
            state=event.state,
            span_log=span_log,
            tool_requests=tool_requests,
            final=None,
            error=state_error,
        )

    tool_error = _tool_limit_error(tool, event.limits)
    if tool_error is not None:
        return _result(
            success=False,
            stdout=stdout,
            state=state_value,
            span_log=span_log,
            tool_requests=tool_requests,
            final=None,
            error=tool_error,
        )

    span_error = _span_limit_error(len(span_log), event.limits)
    if span_error is not None:
        return _result(
            success=False,
            stdout=stdout,
            state=state_value,
            span_log=span_log,
            tool_requests=tool_requests,
            final=None,
            error=span_error,
        )

    return _result(
        success=True,
        stdout=stdout,
        state=state_value,
        span_log=span_log,
        tool_requests=tool_requests,
        final=final,
        error=None,
    )
