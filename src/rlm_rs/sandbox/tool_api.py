from __future__ import annotations

from pydantic import JsonValue

from rlm_rs.models import (
    LLMToolRequest,
    LimitsSnapshot,
    SearchToolRequest,
    ToolRequestsEnvelope,
)


class ToolAPIError(Exception):
    pass


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
    def __init__(self, limits: LimitsSnapshot | None = None) -> None:
        self._limits = limits
        self._llm: list[LLMToolRequest] = []
        self._search: list[SearchToolRequest] = []

    def queue_llm(
        self,
        key: str,
        prompt: str,
        *,
        model_hint: str | None = "sub",
        max_tokens: int,
        temperature: float | None = 0,
        metadata: dict[str, JsonValue] | None = None,
    ) -> None:
        self._ensure_capacity()
        request = LLMToolRequest(
            key=key,
            prompt=prompt,
            model_hint=model_hint,
            max_tokens=max_tokens,
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
