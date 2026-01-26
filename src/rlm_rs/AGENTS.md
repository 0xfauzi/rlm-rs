# AGENTS

This file is directory-local guidance for coding agents working in `src/rlm_rs/` (the main Python package).

## What lives here

This package implements the RLM-RS runtime service and its supporting components:

- **API**: `api/` (FastAPI routes, auth, request/rate limits, sessions/executions/spans endpoints)
- **Orchestrator**: `orchestrator/` (Answerer loop, tool resolution, provider integration, citations, optional eval judge)
- **Ingestion**: `ingestion/` (parses documents via the parser service, writes canonical parsed artifacts, optional indexing)
- **Sandbox runtime**: `sandbox/` (step executor, AST policy, JSON-only state, tool request queueing)
- **Parser service**: `parser/` (HTTP service + client + models for parsing raw docs into canonical text/offsets)
- **Storage**: `storage/` (DynamoDB and S3 helpers; state persistence; table naming conventions)
- **Search**: `search/` (optional indexing/query backends)
- **MCP wrapper**: `mcp/` (MCP server that wraps the HTTP API)
- **Finetune utilities**: `finetune/` (trace exports and dataset prep helpers used in evaluation/finetuning)

Cross-cutting modules in this package include (non-exhaustive):

- `settings.py`: environment-driven settings; must stay aligned with `.env.example` and `compose.yaml`
- `models.py`: core Pydantic models shared across components
- `observability.py` / `logging.py`: tracing/logging/metrics wiring
- `worker_entrypoint.py`: selects and runs ingestion/orchestrator worker loops
- `errors.py`: shared error types and API error shaping
- `code_log.py`: execution-level code/tool logging persisted via storage (used in evaluations)

## Architecture invariants (do not break)

These are system boundaries that many tests and docs assume:

- **Sandbox isolation**: no provider secrets, no outbound network, JSON-only state in/out.
- **Tool flow**: sandbox enqueues tool requests; the orchestrator resolves them (LLM subcalls, search, etc.).
- **Root output shape**: the root model output must be a single fenced code block labeled `repl` with no surrounding text.
- **Citations**: derived from span logs + checksums over canonical parsed text (not from model output).
- **Canonical parsing**: parsed text/offsets in S3 must be deterministic; changes must preserve checksum semantics.

## How it connects to the rest of the repo

- `docs/` describes the intended behavior and execution sequences; update docs when contracts change.
- `tests/` exercises these modules:
  - unit tests validate API behavior, sandbox limits, provider caching, citations, etc.
  - integration tests validate LocalStack-backed persistence and ingestion/execution roundtrips
- `compose.yaml` wires services and workers to these modules (API, parser, workers).
- `ui/` calls the HTTP API exposed by `api/` and displays sessions/executions/spans/citations.

## Safe change guidelines

- **If you add env vars**: update `settings.py`, `.env.example`, and `compose.yaml` together.
- **If you change an API response or request schema**: update `models.py` (or route-local models) and the relevant tests under `tests/unit/test_api_*.py` (and UI code if it consumes the fields).
- **If you touch parsing, spans, or citations**: validate determinism and checksum stability; run the smoke test and relevant unit tests.
- **Prefer smaller, explicit interfaces** between components; avoid importing “upwards” (e.g. sandbox should not import orchestrator/provider code).

## Useful commands

- Run API locally:
  - `uv run uvicorn rlm_rs.api.app:app --host 0.0.0.0 --port 8080`
- Run parser locally:
  - `uv run uvicorn rlm_rs.parser.service:app --host 0.0.0.0 --port 8081`
- Run workers:
  - `WORKER_MODE=ingestion uv run python -m rlm_rs.worker_entrypoint`
  - `WORKER_MODE=orchestrator uv run python -m rlm_rs.worker_entrypoint`
- Run tests + lint:
  - `uv run pytest -q`
  - `uv run ruff check .`

