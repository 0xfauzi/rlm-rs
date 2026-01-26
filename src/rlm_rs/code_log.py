from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Sequence

from pydantic import JsonValue
from structlog.stdlib import BoundLogger

from rlm_rs.models import ToolRequestsEnvelope, ToolResultsEnvelope
from rlm_rs.settings import Settings
from rlm_rs.storage import ddb

_REPL_BLOCK_RE = re.compile(r"```repl[ \t]*\n(.*?)\n?```", re.DOTALL)
_LOG_FIELDS = (
    "execution_id",
    "sequence",
    "created_at",
    "source",
    "kind",
    "model_name",
    "tool_type",
    "content",
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _format_timestamp(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def extract_repl_code(output: str) -> str | None:
    normalized = output.replace("\r\n", "\n").replace("\r", "\n")
    matches = list(_REPL_BLOCK_RE.finditer(normalized))
    if len(matches) != 1:
        return None
    return matches[0].group(1)


def redact_value(value: JsonValue) -> JsonValue:
    if value is None:
        return None
    if isinstance(value, dict):
        return {key: redact_value(val) for key, val in value.items()}
    if isinstance(value, list):
        return [redact_value(val) for val in value]
    return "[REDACTED]"


def build_repl_entry(
    *,
    source: str,
    model_name: str | None,
    content: str,
) -> dict[str, JsonValue]:
    return {
        "source": source,
        "kind": "REPL",
        "model_name": model_name,
        "content": content,
    }


def build_repl_parse_error_entry(
    *,
    model_name: str | None,
    error: str,
    output: str,
) -> dict[str, JsonValue]:
    return {
        "source": "ROOT",
        "kind": "REPL_PARSE_ERROR",
        "model_name": model_name,
        "content": {"error": error, "output": output},
    }


def build_tool_request_entries(
    envelope: ToolRequestsEnvelope,
) -> list[dict[str, JsonValue]]:
    entries: list[dict[str, JsonValue]] = []
    for request in envelope.llm:
        entries.append(
            {
                "source": "TOOL",
                "kind": "TOOL_REQUEST",
                "tool_type": "llm",
                "content": request.model_dump(exclude_none=True),
            }
        )
    for request in envelope.search:
        entries.append(
            {
                "source": "TOOL",
                "kind": "TOOL_REQUEST",
                "tool_type": "search",
                "content": request.model_dump(exclude_none=True),
            }
        )
    return entries


def build_tool_result_entries(
    results: ToolResultsEnvelope,
    statuses: dict[str, str],
) -> list[dict[str, JsonValue]]:
    entries: list[dict[str, JsonValue]] = []
    for key, result in results.llm.items():
        entries.append(
            {
                "source": "TOOL",
                "kind": "TOOL_RESULT",
                "tool_type": "llm",
                "content": {
                    "key": key,
                    "status": statuses.get(key),
                    "result": result.model_dump(exclude_none=True),
                },
            }
        )
    for key, result in results.search.items():
        entries.append(
            {
                "source": "TOOL",
                "kind": "TOOL_RESULT",
                "tool_type": "search",
                "content": {
                    "key": key,
                    "status": statuses.get(key),
                    "result": result.model_dump(exclude_none=True),
                },
            }
        )
    return entries


@dataclass
class CodeLogWriter:
    table: Any
    execution_id: str
    settings: Settings
    logger: BoundLogger | None = None

    def write(self, entries: Sequence[dict[str, JsonValue]]) -> list[dict[str, Any]]:
        if not entries:
            return []
        created_at = _format_timestamp(_utc_now())
        normalized: list[dict[str, Any]] = []
        for entry in entries:
            content = entry.get("content")
            if self.settings.enable_trace_redaction:
                content = redact_value(content)
            normalized.append({**entry, "content": content, "created_at": created_at})
        items = ddb.put_code_log_entries(
            self.table,
            execution_id=self.execution_id,
            entries=normalized,
        )
        if self.logger is not None:
            for item in items:
                payload = {key: item.get(key) for key in _LOG_FIELDS}
                self.logger.info("code_log", **payload)
        return items
