from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlparse

from pydantic import JsonValue

from rlm_rs import code_log
from rlm_rs.models import SpanLogEntry, ToolRequestsEnvelope, ToolResultsEnvelope
from rlm_rs.orchestrator.citations import merge_span_log
from rlm_rs.orchestrator.root_prompt import build_root_prompt, root_prompt_version
from rlm_rs.settings import Settings
from rlm_rs.storage import s3, state as state_store

TRACE_SCHEMA_VERSION = "rlm_trace_v1"
SCAN_TAG_PREFIX = "scan:"
DEFAULT_TRACE_S3_PREFIX = "traces"


def _is_scan_span(span: SpanLogEntry) -> bool:
    tag = span.tag or ""
    return tag.startswith(SCAN_TAG_PREFIX)


def _split_s3_uri(uri: str) -> tuple[str, str]:
    parsed = urlparse(uri)
    if parsed.scheme != "s3" or not parsed.netloc:
        raise ValueError(f"Invalid S3 URI: {uri}")
    return parsed.netloc, parsed.path.lstrip("/")


def build_trace_s3_key(
    *,
    tenant_id: str,
    execution_id: str,
    prefix: str = DEFAULT_TRACE_S3_PREFIX,
) -> str:
    return f"{prefix}/{tenant_id}/{execution_id}/trace.json.gz"


def persist_trace_artifact(
    *,
    s3_client: Any,
    bucket: str,
    tenant_id: str,
    execution_id: str,
    artifact: Mapping[str, JsonValue],
    prefix: str = DEFAULT_TRACE_S3_PREFIX,
) -> str:
    key = build_trace_s3_key(
        tenant_id=tenant_id,
        execution_id=execution_id,
        prefix=prefix,
    )
    s3.put_gzip_json(s3_client, bucket, key, artifact)
    return f"s3://{bucket}/{key}"


def load_trace_artifact(*, s3_client: Any, trace_s3_uri: str) -> Mapping[str, JsonValue]:
    bucket, key = _split_s3_uri(trace_s3_uri)
    payload = s3.get_gzip_json(s3_client, bucket, key)
    if not isinstance(payload, dict):
        raise ValueError("Trace payload must be a JSON object.")
    return payload


def load_state_payload(
    state_item: Mapping[str, Any],
    *,
    s3_client: Any,
) -> JsonValue | None:
    state_json = state_item.get("state_json")
    state_s3_uri = state_item.get("state_s3_uri")
    if state_s3_uri:
        bucket, key = _split_s3_uri(str(state_s3_uri))
        state_json = s3.get_gzip_json(s3_client, bucket, key)
    state_json = state_store.normalize_json_value(state_json)
    state_store.validate_state_payload(state_json)
    return state_json


def _span_metrics(spans: Iterable[SpanLogEntry]) -> dict[str, int]:
    spans_list = list(spans)
    merged = merge_span_log(spans_list)
    total_chars = sum(max(0, span.end_char - span.start_char) for span in spans_list)
    unique_chars = sum(max(0, span.end_char - span.start_char) for span in merged)
    docs_touched = len({span.doc_index for span in merged})
    max_span = 0
    for span in spans_list:
        max_span = max(max_span, max(0, span.end_char - span.start_char))
    return {
        "total_chars": total_chars,
        "unique_chars": unique_chars,
        "docs_touched": docs_touched,
        "max_span_chars": max_span,
    }


def compute_span_metrics(span_log: Iterable[SpanLogEntry]) -> dict[str, int]:
    spans = list(span_log)
    read_spans = [span for span in spans if not _is_scan_span(span)]
    scan_spans = [span for span in spans if _is_scan_span(span)]
    read_metrics = _span_metrics(read_spans)
    scan_metrics = _span_metrics(scan_spans)
    return {
        "span_chars": read_metrics["total_chars"],
        "unique_span_chars": read_metrics["unique_chars"],
        "docs_touched": read_metrics["docs_touched"],
        "max_span_chars": read_metrics["max_span_chars"],
        "scan_span_chars": scan_metrics["total_chars"],
        "scan_unique_span_chars": scan_metrics["unique_chars"],
        "scan_docs_touched": scan_metrics["docs_touched"],
        "scan_max_span_chars": scan_metrics["max_span_chars"],
    }


def compute_tool_metrics(turns: Sequence[Mapping[str, JsonValue]]) -> dict[str, int]:
    llm_requests = 0
    search_requests = 0
    total_prompt_chars = 0
    for turn in turns:
        tool_requests = turn.get("tool_requests")
        if tool_requests is None:
            continue
        try:
            envelope = ToolRequestsEnvelope.model_validate(tool_requests)
        except Exception:  # noqa: BLE001
            continue
        llm_requests += len(envelope.llm)
        search_requests += len(envelope.search)
        for request in envelope.llm:
            total_prompt_chars += len(request.prompt or "")
    return {
        "llm_subcalls": llm_requests,
        "search_requests": search_requests,
        "total_subcall_prompt_chars": total_prompt_chars,
    }


def compute_trace_metrics(
    *,
    turns: Sequence[Mapping[str, JsonValue]],
    parse_errors: Sequence[Mapping[str, JsonValue]],
) -> dict[str, JsonValue]:
    span_log: list[SpanLogEntry] = []
    step_errors = 0
    for turn in turns:
        for raw in turn.get("span_log", []) or []:
            try:
                span_log.append(SpanLogEntry.model_validate(raw))
            except Exception:  # noqa: BLE001
                continue
        error_payload = (turn.get("step") or {}).get("error")
        if error_payload:
            step_errors += 1
    span_metrics = compute_span_metrics(span_log)
    tool_metrics = compute_tool_metrics(turns)
    return {
        "turns": len(turns),
        "parse_errors": len(parse_errors),
        "step_errors": step_errors,
        **span_metrics,
        **tool_metrics,
    }


def _redact(value: JsonValue, settings: Settings) -> JsonValue:
    if not settings.enable_trace_redaction:
        return value
    return code_log.redact_value(value)


@dataclass
class TraceCollector:
    settings: Settings
    turns: dict[int, dict[str, JsonValue]] = field(default_factory=dict)
    parse_errors: list[dict[str, JsonValue]] = field(default_factory=list)

    def start_turn(
        self,
        *,
        turn_index: int,
        root_prompt: str,
        root_prompt_version: str,
        root_prompt_inputs: Mapping[str, JsonValue],
        budget_snapshot: JsonValue | None,
    ) -> None:
        self.turns[turn_index] = {
            "turn_index": turn_index,
            "root_prompt": _redact(root_prompt, self.settings),
            "root_prompt_version": root_prompt_version,
            "root_prompt_inputs": _redact(dict(root_prompt_inputs), self.settings),
            "budget_snapshot": _redact(budget_snapshot, self.settings),
        }

    def record_parse_error(
        self,
        *,
        turn_index: int,
        error: str,
        output: str,
        root_prompt: str,
        root_prompt_version: str,
        root_prompt_inputs: Mapping[str, JsonValue],
        timings: Mapping[str, JsonValue] | None = None,
    ) -> None:
        self.parse_errors.append(
            {
                "turn_index": turn_index,
                "error": _redact(error, self.settings),
                "output": _redact(output, self.settings),
                "root_prompt": _redact(root_prompt, self.settings),
                "root_prompt_version": root_prompt_version,
                "root_prompt_inputs": _redact(dict(root_prompt_inputs), self.settings),
                "timings": _redact(dict(timings or {}), self.settings),
            }
        )

    def record_repl_code(self, *, turn_index: int, repl_code: str) -> None:
        entry = self.turns.setdefault(turn_index, {"turn_index": turn_index})
        entry["repl_code"] = _redact(repl_code, self.settings)

    def record_step_result(
        self,
        *,
        turn_index: int,
        result: Any,
        state_summary: Mapping[str, JsonValue] | None,
        checksum: str | None,
        timings: Mapping[str, JsonValue] | None = None,
    ) -> None:
        entry = self.turns.setdefault(turn_index, {"turn_index": turn_index})
        tool_requests = (
            result.tool_requests.model_dump(exclude_none=True)
            if result.tool_requests
            else None
        )
        span_log = [span.model_dump(exclude_none=True) for span in result.span_log]
        final_payload = result.final.model_dump(exclude_none=True) if result.final else None
        error_payload = result.error.model_dump(exclude_none=True) if result.error else None
        entry["step"] = {
            "success": result.success,
            "stdout": _redact(result.stdout, self.settings),
            "tool_requests": _redact(tool_requests, self.settings),
            "final": _redact(final_payload, self.settings),
            "error": _redact(error_payload, self.settings),
        }
        entry["span_log"] = span_log
        entry["tool_requests"] = _redact(tool_requests, self.settings)
        entry["state_summary"] = _redact(dict(state_summary or {}), self.settings)
        entry["state_checksum"] = checksum
        entry["timings"] = _redact(dict(timings or {}), self.settings)

    def record_tool_results(
        self,
        *,
        turn_index: int,
        tool_results: ToolResultsEnvelope | None,
        tool_status: Mapping[str, JsonValue] | None,
    ) -> None:
        entry = self.turns.setdefault(turn_index, {"turn_index": turn_index})
        payload = tool_results.model_dump(exclude_none=True) if tool_results else None
        entry["tool_results"] = _redact(payload, self.settings)
        entry["tool_status"] = _redact(dict(tool_status or {}), self.settings)

    def build_artifact(
        self,
        *,
        execution: Mapping[str, JsonValue],
        session: Mapping[str, JsonValue],
        documents: Sequence[Mapping[str, JsonValue]],
        evaluation: Mapping[str, JsonValue] | None,
    ) -> dict[str, JsonValue]:
        turns = [self.turns[index] for index in sorted(self.turns)]
        metrics = compute_trace_metrics(turns=turns, parse_errors=self.parse_errors)
        return {
            "schema_version": TRACE_SCHEMA_VERSION,
            "execution": dict(execution),
            "session": dict(session),
            "documents": [dict(doc) for doc in documents],
            "evaluation": dict(evaluation) if evaluation else None,
            "turns": turns,
            "parse_errors": list(self.parse_errors),
            "metrics": metrics,
        }


def _format_step_error(error: Mapping[str, JsonValue] | None) -> str | None:
    if not error:
        return None
    code = error.get("code")
    message = error.get("message")
    if code and message:
        return f"{code}: {message}"
    return str(error)


def _budget_snapshot_from_counts(
    *,
    budgets: Mapping[str, JsonValue] | None,
    turns: int,
    llm_subcalls: int,
) -> dict[str, JsonValue] | None:
    if budgets is None:
        return None
    limits = dict(budgets)
    consumed: dict[str, JsonValue] = {
        "turns": turns,
        "llm_subcalls": llm_subcalls,
    }
    remaining: dict[str, JsonValue] = {}
    max_turns = limits.get("max_turns")
    if isinstance(max_turns, int):
        remaining["turns"] = max(max_turns - turns, 0)
    max_subcalls = limits.get("max_llm_subcalls")
    if isinstance(max_subcalls, int):
        remaining["llm_subcalls"] = max(max_subcalls - llm_subcalls, 0)
    return {"limits": limits, "consumed": consumed, "remaining": remaining}


def build_trace_from_storage(
    *,
    execution_item: Mapping[str, Any],
    session_item: Mapping[str, Any],
    documents: Sequence[Mapping[str, Any]],
    steps: Sequence[Mapping[str, Any]],
    code_log_entries: Sequence[Mapping[str, Any]],
    evaluation_item: Mapping[str, Any] | None,
    s3_client: Any,
) -> dict[str, JsonValue]:
    steps_sorted = sorted(steps, key=lambda item: int(item.get("turn_index", 0)))
    root_repls = [
        entry.get("content")
        for entry in code_log_entries
        if entry.get("source") == "ROOT" and entry.get("kind") == "REPL"
    ]
    parse_errors = [
        entry.get("content")
        for entry in code_log_entries
        if entry.get("source") == "ROOT" and entry.get("kind") == "REPL_PARSE_ERROR"
    ]
    doc_lengths = [
        int(item.get("char_length") or 0) for item in sorted(documents, key=lambda d: d.get("doc_index", 0))
    ]
    subcalls_enabled = bool(
        ((execution_item.get("models") or {}).get("sub_model"))
        or ((session_item.get("models_default") or {}).get("sub_model"))
    )
    prompt_version = root_prompt_version(subcalls_enabled=subcalls_enabled)
    last_stdout: str | None = None
    last_error: str | None = None
    llm_subcalls = 0
    turns: list[dict[str, JsonValue]] = []
    for idx, step in enumerate(steps_sorted):
        tool_requests = step.get("tool_requests")
        if tool_requests is not None:
            try:
                envelope = ToolRequestsEnvelope.model_validate(tool_requests)
                llm_subcalls += len(envelope.llm)
            except Exception:  # noqa: BLE001
                pass
        budget_snapshot = _budget_snapshot_from_counts(
            budgets=execution_item.get("budgets_requested"),
            turns=idx,
            llm_subcalls=llm_subcalls,
        )
        prompt_inputs = {
            "question": execution_item.get("question"),
            "doc_count": len(documents),
            "doc_lengths_chars": doc_lengths,
            "budget_snapshot": budget_snapshot,
            "last_stdout": last_stdout,
            "last_error": last_error,
            "state_summary": None,
            "subcalls_enabled": subcalls_enabled,
        }
        root_prompt = build_root_prompt(
            question=str(execution_item.get("question") or ""),
            doc_count=len(documents),
            doc_lengths_chars=doc_lengths,
            budget_snapshot=budget_snapshot,
            last_stdout=last_stdout,
            last_error=last_error,
            state_summary=None,
            subcalls_enabled=subcalls_enabled,
        )
        state_payload = load_state_payload(step, s3_client=s3_client)
        tool_results = None
        tool_status = None
        if isinstance(state_payload, dict):
            tool_results = state_payload.get("_tool_results")
            tool_status = state_payload.get("_tool_status")
        turn_record: dict[str, JsonValue] = {
            "turn_index": int(step.get("turn_index", idx)),
            "root_prompt": root_prompt,
            "root_prompt_version": prompt_version,
            "root_prompt_inputs": prompt_inputs,
            "budget_snapshot": budget_snapshot,
            "repl_code": root_repls[idx] if idx < len(root_repls) else None,
            "step": {
                "success": step.get("success"),
                "stdout": step.get("stdout"),
                "tool_requests": tool_requests,
                "final": step.get("final"),
                "error": step.get("error"),
            },
            "span_log": step.get("span_log") or [],
            "tool_requests": tool_requests,
            "tool_results": tool_results,
            "tool_status": tool_status,
            "state_summary": step.get("summary"),
            "state_checksum": step.get("checksum"),
            "timings": state_store.normalize_json_value(step.get("timings")),
        }
        turns.append(turn_record)
        last_stdout = step.get("stdout")
        last_error = _format_step_error(step.get("error"))
    execution_payload = state_store.normalize_json_value(dict(execution_item))
    session_payload = state_store.normalize_json_value(dict(session_item))
    documents_payload = [state_store.normalize_json_value(dict(doc)) for doc in documents]
    evaluation_payload = (
        state_store.normalize_json_value(dict(evaluation_item))
        if evaluation_item
        else None
    )
    metrics = compute_trace_metrics(turns=turns, parse_errors=parse_errors)
    return {
        "schema_version": TRACE_SCHEMA_VERSION,
        "execution": execution_payload,
        "session": session_payload,
        "documents": documents_payload,
        "evaluation": evaluation_payload,
        "turns": turns,
        "parse_errors": parse_errors,
        "metrics": metrics,
    }
