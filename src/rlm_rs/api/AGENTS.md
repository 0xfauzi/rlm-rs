# AGENTS

This file is directory-local guidance for coding agents working in `src/rlm_rs/api/`.

## What lives here

This package defines the **FastAPI HTTP API** for RLM-RS:

- `app.py`: FastAPI app construction, router wiring, and middleware setup.
- `auth.py`: API key authentication (Bearer token) and related helpers.
- `dependencies.py`: Dependency injection helpers (clients, settings, storage, etc.).
- Route modules (non-exhaustive, based on filenames):
  - `sessions.py`: session creation/status (and document readiness).
  - `executions.py`: create/poll executions; runtime stepping endpoints.
  - `spans.py` / `citations`-related routes: fetching spans and verifying citations.
  - `health.py`: liveness/readiness.
  - `rate_limits.py`, `request_limits.py`: request-level and tenant-level safeguards.

## How it connects to the rest of the repo

- API routes call into:
  - `src/rlm_rs/models.py` (shared request/response models)
  - `src/rlm_rs/settings.py` (configuration)
  - `src/rlm_rs/storage/**` (DynamoDB + S3 persistence)
  - `src/rlm_rs/orchestrator/**` (for managed Answerer/runtime tool resolution flows)
  - `src/rlm_rs/observability.py` / `logging.py` (metrics/tracing/logging)
- The UI (`ui/`) and MCP server (`src/rlm_rs/mcp/`) are primary API clients.
- Authentication is coupled to:
  - `API_KEY_PEPPER` (env var)
  - DynamoDB `api_keys` table (with `DDB_TABLE_PREFIX`), created by `scripts/localstack_init.sh` / `compose.yaml`.

## Safe change guidelines

- **Treat response shapes as contracts.**
  - If you change a request/response model, update:
    - relevant unit tests (`tests/unit/test_api_*.py`)
    - UI consumers (`ui/src/**`) and/or MCP wrapper if impacted
    - docs if the change is user-visible (`docs/`)
- **Preserve the security model**:
  - The sandbox must never be reachable for arbitrary execution; routes should continue to enforce auth and limits.
  - Avoid logging secrets (API keys, provider keys).
- **Keep settings and defaults aligned**:
  - Adding/changing env vars requires updates to `src/rlm_rs/settings.py`, `.env.example`, and `compose.yaml`.
- **Prefer explicit, testable error handling** (stable HTTP status codes and error schemas).

## Useful commands

- Run the API locally (expects LocalStack + env vars):
  - `uv run uvicorn rlm_rs.api.app:app --host 0.0.0.0 --port 8080`
- Run API unit tests:
  - `uv run pytest -q tests/unit/test_api_*.py`
- End-to-end validation that exercises API routes:
  - `scripts/smoke_test.sh`

