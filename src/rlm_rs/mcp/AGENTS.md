# AGENTS

This file is directory-local guidance for coding agents working in `src/rlm_rs/mcp/`.

## What lives here

This package provides an **MCP server wrapper** around the RLM-RS HTTP API, so MCP-capable clients (e.g. IDE agents) can interact with the service through the MCP protocol.

Key files:

- `__main__.py`: module entrypoint (`python -m rlm_rs.mcp`).
- `server.py`: MCP server implementation and tool definitions.
- `__init__.py`: package marker.

## How it connects to the rest of the repo

- The MCP server is an HTTP client of the backend API in `src/rlm_rs/api/**`.
- It typically needs:
  - `RLM_BASE_URL` (e.g. `http://localhost:8080`)
  - `RLM_API_KEY` (Bearer token value)
- It should not contain “business logic” that diverges from the API; it should map MCP tools onto API endpoints and return structured results.

## Safe change guidelines

- **Keep MCP tools thin**:
  - Prefer adding new capabilities by adding/changing HTTP endpoints first, then exposing them via MCP.
- **Be explicit about auth and base URL configuration**.
- **Do not leak secrets** (provider keys, bearer tokens) through logs or tool outputs.
- **Stability matters**: MCP tool names/arguments are a contract with clients; avoid breaking changes unless necessary.

## Useful commands

- Run the MCP server locally (API must already be running):
  - `export RLM_BASE_URL=http://localhost:8080`
  - `export RLM_API_KEY=rlm_key_local`
  - `uv run python -m rlm_rs.mcp`

