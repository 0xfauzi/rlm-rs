from __future__ import annotations

import copy

from pydantic import JsonValue

from rlm_rs.models import (
    LLMToolRequest,
    LimitsSnapshot,
    SearchToolRequest,
    ToolRequestsEnvelope,
)


class ToolAPIError(Exception):
    pass


class ToolPreconditionError(ToolAPIError):
    def __init__(self, *, key: str, missing_llm_keys: list[str]) -> None:
        message = (
            "queue_llm blocked: missing required tool results for keys: "
            + ", ".join(missing_llm_keys)
        )
        super().__init__(message)
        self.key = key
        self.missing_llm_keys = missing_llm_keys


TOOL_SCHEMA_VERSION = "v1"

_TOOL_SPECS: list[dict[str, JsonValue]] = [
    {
        "name": "queue_llm",
        "signature": (
            "tool.queue_llm(key, prompt, *, model_hint=\"sub\", max_tokens=None, "
            "max_output_tokens=None, max_output_chars=None, temperature=0, metadata=None)"
        ),
        "description": "Queue a sub-LLM call for the orchestrator to resolve.",
        "params": [
            {"name": "key", "kind": "positional", "type": "string", "required": True},
            {"name": "prompt", "kind": "positional", "type": "string", "required": True},
            {
                "name": "model_hint",
                "kind": "keyword",
                "type": ["string", "null"],
                "default": "sub",
            },
            {
                "name": "max_tokens",
                "kind": "keyword",
                "type": ["integer", "null"],
                "default": None,
            },
            {
                "name": "max_output_tokens",
                "kind": "keyword",
                "type": ["integer", "null"],
                "default": None,
            },
            {
                "name": "max_output_chars",
                "kind": "keyword",
                "type": ["integer", "null"],
                "default": None,
            },
            {
                "name": "temperature",
                "kind": "keyword",
                "type": ["number", "null"],
                "default": 0,
            },
            {
                "name": "metadata",
                "kind": "keyword",
                "type": ["object", "null"],
                "default": None,
            },
        ],
        "aliases": {
            "max_output_tokens": "max_tokens",
            "max_output_chars": "max_tokens",
        },
        "constraints": {
            "exactly_one_of": ["max_tokens", "max_output_tokens", "max_output_chars"]
        },
        "returns": "None",
    },
    {
        "name": "queue_search",
        "signature": "tool.queue_search(key, query, *, k=10, filters=None)",
        "description": "Queue a search request for the orchestrator to resolve.",
        "params": [
            {"name": "key", "kind": "positional", "type": "string", "required": True},
            {"name": "query", "kind": "positional", "type": "string", "required": True},
            {"name": "k", "kind": "keyword", "type": "integer", "default": 10},
            {
                "name": "filters",
                "kind": "keyword",
                "type": ["object", "null"],
                "default": None,
            },
        ],
        "returns": "None",
    },
    {
        "name": "YIELD",
        "signature": "tool.YIELD(reason=None)",
        "description": "End the step so queued tools can be resolved.",
        "params": [
            {
                "name": "reason",
                "kind": "positional",
                "type": ["string", "null"],
                "default": None,
            }
        ],
        "returns": "Raises ToolYield",
    },
    {
        "name": "FINAL",
        "signature": "tool.FINAL(answer)",
        "description": "Finalize the execution with an answer.",
        "params": [
            {"name": "answer", "kind": "positional", "type": "string", "required": True}
        ],
        "returns": "Raises ToolFinal",
    },
]


def _render_tool_signatures(specs: list[dict[str, JsonValue]]) -> str:
    lines = ["Tool signatures"]
    for spec in specs:
        signature = spec.get("signature")
        if isinstance(signature, str) and signature:
            lines.append(f"- {signature}")
    return "\n".join(lines)


TOOL_SIGNATURE_TEXT = _render_tool_signatures(_TOOL_SPECS)

TOOL_SCHEMA_BASE: dict[str, JsonValue] = {
    "version": TOOL_SCHEMA_VERSION,
    "signature_text": TOOL_SIGNATURE_TEXT,
    "tools": _TOOL_SPECS,
}


def tool_schema_base() -> dict[str, JsonValue]:
    return copy.deepcopy(TOOL_SCHEMA_BASE)


def _apply_availability(
    schema: dict[str, JsonValue],
    *,
    name: str,
    enabled: bool,
    disabled_reason: str | None,
) -> None:
    tools = schema.get("tools")
    if not isinstance(tools, list):
        return
    for spec in tools:
        if not isinstance(spec, dict):
            continue
        if spec.get("name") != name:
            continue
        availability: dict[str, JsonValue] = {"enabled": enabled}
        if not enabled and disabled_reason:
            availability["disabled_reason"] = disabled_reason
        spec["availability"] = availability
        return


def build_tool_schema(
    *,
    subcalls_enabled: bool | None = None,
    search_enabled: bool | None = None,
) -> dict[str, JsonValue]:
    schema = tool_schema_base()
    if subcalls_enabled is not None:
        _apply_availability(
            schema,
            name="queue_llm",
            enabled=bool(subcalls_enabled),
            disabled_reason="subcalls disabled",
        )
    if search_enabled is not None:
        _apply_availability(
            schema,
            name="queue_search",
            enabled=bool(search_enabled),
            disabled_reason="search disabled",
        )
    return schema


class ToolRequestLimitError(ToolAPIError):
    def __init__(self, limit: int) -> None:
        super().__init__(f"Tool request limit exceeded: {limit}")
        self.limit = limit


class ToolYield(BaseException):
    def __init__(self, reason: str | None = None) -> None:
        super().__init__(reason or "")
        self.reason = reason


class ToolFinal(BaseException):
    def __init__(self, answer: str) -> None:
        super().__init__(answer)
        self.answer = answer


class ToolAPI:
    def __init__(
        self,
        limits: LimitsSnapshot | None = None,
        *,
        state: JsonValue | None = None,
    ) -> None:
        self._limits = limits
        self._state = state
        self._llm: list[LLMToolRequest] = []
        self._search: list[SearchToolRequest] = []

    def queue_llm(
        self,
        key: str,
        prompt: str,
        *,
        model_hint: str | None = "sub",
        max_tokens: int | None = None,
        max_output_tokens: int | None = None,
        max_output_chars: int | None = None,
        temperature: float | None = 0,
        metadata: dict[str, JsonValue] | None = None,
    ) -> None:
        missing_keys = self._missing_required_llm_keys(metadata)
        if missing_keys:
            raise ToolPreconditionError(key=key, missing_llm_keys=missing_keys)
        provided_count = sum(
            value is not None
            for value in (max_tokens, max_output_tokens, max_output_chars)
        )
        if provided_count != 1:
            raise ToolAPIError(
                "queue_llm requires exactly one of max_tokens, max_output_tokens, max_output_chars"
            )
        if max_tokens is not None:
            resolved_max_tokens = max_tokens
        elif max_output_tokens is not None:
            resolved_max_tokens = max_output_tokens
        else:
            resolved_max_tokens = max_output_chars
        self._ensure_capacity()
        request = LLMToolRequest(
            key=key,
            prompt=prompt,
            model_hint=model_hint,
            max_tokens=resolved_max_tokens,
            temperature=temperature,
            metadata=metadata,
        )
        self._llm.append(request)

    def queue_search(
        self,
        key: str,
        query: str,
        *,
        k: int = 10,
        filters: dict[str, JsonValue] | None = None,
    ) -> None:
        self._ensure_capacity()
        request = SearchToolRequest(key=key, query=query, k=k, filters=filters)
        self._search.append(request)

    def YIELD(self, reason: str | None = None) -> None:
        raise ToolYield(reason)

    def FINAL(self, answer: str) -> None:
        raise ToolFinal(answer)

    def schema(self) -> dict[str, JsonValue]:
        return tool_schema_base()

    @property
    def tool_requests(self) -> ToolRequestsEnvelope:
        return ToolRequestsEnvelope(llm=list(self._llm), search=list(self._search))

    def _ensure_capacity(self) -> None:
        if not self._limits:
            return
        limit = self._limits.max_tool_requests_per_step
        if limit is None:
            return
        if len(self._llm) + len(self._search) >= limit:
            raise ToolRequestLimitError(limit)

    def _missing_required_llm_keys(
        self,
        metadata: dict[str, JsonValue] | None,
    ) -> list[str]:
        if not metadata or not isinstance(metadata, dict):
            return []
        required = metadata.get("requires_llm_keys")
        if not isinstance(required, list):
            return []
        required_keys = [key for key in required if isinstance(key, str) and key.strip()]
        if not required_keys:
            return []
        llm_bucket = self._llm_results_bucket()
        missing: list[str] = []
        for req_key in required_keys:
            entry = llm_bucket.get(req_key)
            if not isinstance(entry, dict):
                missing.append(req_key)
                continue
            text = entry.get("text")
            if not isinstance(text, str) or not text.strip():
                missing.append(req_key)
        return missing

    def _llm_results_bucket(self) -> dict[str, JsonValue]:
        if not isinstance(self._state, dict):
            return {}
        tool_results = self._state.get("_tool_results")
        if not isinstance(tool_results, dict):
            return {}
        llm_bucket = tool_results.get("llm")
        if not isinstance(llm_bucket, dict):
            return {}
        return llm_bucket
