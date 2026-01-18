from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import httpx
from mcp.server.fastmcp import Context, FastMCP
from pydantic import AliasChoices, Field, HttpUrl, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from rlm_rs.models import (
    CitationVerifyRequest,
    CitationVerifyResponse,
    CreateExecutionRequest,
    CreateExecutionResponse,
    CreateRuntimeExecutionResponse,
    CreateSessionRequest,
    CreateSessionResponse,
    DeleteSessionResponse,
    ExecutionStatusResponse,
    ExecutionWaitRequest,
    GetSessionResponse,
    SpanGetRequest,
    SpanGetResponse,
    StepRequest,
    StepResult,
    ToolResolveRequest,
    ToolResolveResponse,
)


class MCPSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", case_sensitive=False, extra="ignore")

    base_url: HttpUrl = Field(validation_alias=AliasChoices("RLM_BASE_URL"))
    api_key: str = Field(validation_alias=AliasChoices("RLM_API_KEY"))

    @field_validator("api_key")
    @classmethod
    def _require_api_key(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("RLM_API_KEY must be set")
        return value


def _format_error(response: httpx.Response) -> str:
    base = f"HTTP {response.status_code}"
    try:
        payload = response.json()
    except ValueError:
        text = response.text.strip()
        return f"{base}: {text}" if text else base

    if isinstance(payload, dict) and "error" in payload:
        error = payload.get("error") or {}
        code = error.get("code")
        message = error.get("message")
        details = error.get("details")
        if code or message:
            base = f"{base} {code}: {message}".strip()
        if details:
            return f"{base} details={details}"
        return base

    return f"{base}: {payload}"


class RLMApiClient:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def request_json(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        if payload is not None:
            kwargs["json"] = payload
        response = await self._client.request(method, path, **kwargs)
        if response.status_code >= 400:
            raise RuntimeError(_format_error(response))
        try:
            data = response.json()
        except ValueError as exc:
            raise RuntimeError(f"Invalid JSON response for {method} {path}") from exc
        if not isinstance(data, dict):
            raise RuntimeError(f"Unexpected response payload for {method} {path}")
        return data

    async def get(self, path: str) -> dict[str, Any]:
        return await self.request_json("GET", path)

    async def post(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self.request_json("POST", path, payload)

    async def delete(self, path: str) -> dict[str, Any]:
        return await self.request_json("DELETE", path)


def _client_from_context(context: Context) -> RLMApiClient:
    client = context.request_context.lifespan_context
    if not isinstance(client, RLMApiClient):
        raise RuntimeError("MCP client is not initialized")
    return client


async def rlm_create_session(
    request: CreateSessionRequest,
    context: Context,
) -> CreateSessionResponse:
    """Create a session via POST /v1/sessions."""
    client = _client_from_context(context)
    payload = request.model_dump(exclude_none=True)
    data = await client.post("/v1/sessions", payload)
    return CreateSessionResponse.model_validate(data)


async def rlm_get_session(
    session_id: str,
    context: Context,
) -> GetSessionResponse:
    """Fetch session details via GET /v1/sessions/{session_id}."""
    client = _client_from_context(context)
    data = await client.get(f"/v1/sessions/{session_id}")
    return GetSessionResponse.model_validate(data)


async def rlm_delete_session(
    session_id: str,
    context: Context,
) -> DeleteSessionResponse:
    """Delete a session via DELETE /v1/sessions/{session_id}."""
    client = _client_from_context(context)
    data = await client.delete(f"/v1/sessions/{session_id}")
    return DeleteSessionResponse.model_validate(data)


async def rlm_start_execution(
    session_id: str,
    request: CreateExecutionRequest,
    context: Context,
) -> CreateExecutionResponse:
    """Create an execution via POST /v1/sessions/{session_id}/executions."""
    client = _client_from_context(context)
    payload = request.model_dump(exclude_none=True)
    data = await client.post(f"/v1/sessions/{session_id}/executions", payload)
    return CreateExecutionResponse.model_validate(data)


async def rlm_get_execution(
    execution_id: str,
    context: Context,
) -> ExecutionStatusResponse:
    """Fetch execution status via GET /v1/executions/{execution_id}."""
    client = _client_from_context(context)
    data = await client.get(f"/v1/executions/{execution_id}")
    return ExecutionStatusResponse.model_validate(data)


async def rlm_wait_execution(
    execution_id: str,
    request: ExecutionWaitRequest,
    context: Context,
) -> ExecutionStatusResponse:
    """Wait for execution completion via POST /v1/executions/{execution_id}/wait."""
    client = _client_from_context(context)
    payload = request.model_dump(exclude_none=True)
    data = await client.post(f"/v1/executions/{execution_id}/wait", payload)
    return ExecutionStatusResponse.model_validate(data)


async def rlm_runtime_create_execution(
    session_id: str,
    context: Context,
) -> CreateRuntimeExecutionResponse:
    """Create a runtime execution via POST /v1/sessions/{session_id}/executions/runtime."""
    client = _client_from_context(context)
    data = await client.post(f"/v1/sessions/{session_id}/executions/runtime")
    return CreateRuntimeExecutionResponse.model_validate(data)


async def rlm_runtime_step(
    execution_id: str,
    request: StepRequest,
    context: Context,
) -> StepResult:
    """Run a runtime step via POST /v1/executions/{execution_id}/steps."""
    client = _client_from_context(context)
    payload = request.model_dump(exclude_none=True)
    data = await client.post(f"/v1/executions/{execution_id}/steps", payload)
    return StepResult.model_validate(data)


async def rlm_resolve_tools(
    execution_id: str,
    request: ToolResolveRequest,
    context: Context,
) -> ToolResolveResponse:
    """Resolve runtime tools via POST /v1/executions/{execution_id}/tools/resolve."""
    client = _client_from_context(context)
    payload = request.model_dump(exclude_none=True)
    data = await client.post(f"/v1/executions/{execution_id}/tools/resolve", payload)
    return ToolResolveResponse.model_validate(data)


async def rlm_get_span(
    request: SpanGetRequest,
    context: Context,
) -> SpanGetResponse:
    """Fetch span text via POST /v1/spans/get."""
    client = _client_from_context(context)
    payload = request.model_dump(exclude_none=True)
    data = await client.post("/v1/spans/get", payload)
    return SpanGetResponse.model_validate(data)


async def rlm_verify_citation(
    request: CitationVerifyRequest,
    context: Context,
) -> CitationVerifyResponse:
    """Verify citations via POST /v1/citations/verify."""
    client = _client_from_context(context)
    payload = request.model_dump(exclude_none=True)
    data = await client.post("/v1/citations/verify", payload)
    return CitationVerifyResponse.model_validate(data)


def _register_tools(server: FastMCP) -> None:
    server.add_tool(rlm_create_session)
    server.add_tool(rlm_get_session)
    server.add_tool(rlm_delete_session)
    server.add_tool(rlm_start_execution)
    server.add_tool(rlm_get_execution)
    server.add_tool(rlm_wait_execution)
    server.add_tool(rlm_runtime_create_execution)
    server.add_tool(rlm_runtime_step)
    server.add_tool(rlm_resolve_tools)
    server.add_tool(rlm_get_span)
    server.add_tool(rlm_verify_citation)


def build_server(settings: MCPSettings | None = None) -> FastMCP:
    settings = settings or MCPSettings()
    base_url = str(settings.base_url).rstrip("/")
    headers = {"Authorization": f"Bearer {settings.api_key}"}

    @asynccontextmanager
    async def lifespan(_: FastMCP) -> AsyncIterator[RLMApiClient]:
        async with httpx.AsyncClient(base_url=base_url, headers=headers) as client:
            yield RLMApiClient(client)

    server = FastMCP(
        name="RLM MCP Server",
        instructions=(
            "Set RLM_BASE_URL and RLM_API_KEY to point this MCP server at the RLM HTTP API."
        ),
        lifespan=lifespan,
    )
    _register_tools(server)
    return server


def main() -> None:
    server = build_server()
    server.run()


if __name__ == "__main__":
    main()
