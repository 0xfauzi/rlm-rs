# AGENTS

This file is directory-local guidance for coding agents working in `tests/`.

## What lives here

This directory contains the automated test suite for RLM-RS:

- `unit/`: fast unit tests that mock or stub external systems.
- `integration/`: slower tests that exercise real storage (LocalStack) and multi-component flows.
- `test_smoke.py`: top-level smoke-style tests that validate core wiring.

## How it connects to the rest of the repo

- Tests primarily validate behavior implemented in `src/rlm_rs/**`.
- Integration tests rely on the LocalStack configuration and table/bucket naming defined by:
  - `compose.yaml`
  - `scripts/localstack_init.sh`
  - `src/rlm_rs/storage/**`
- Many API tests cover the HTTP surface in `src/rlm_rs/api/**` and therefore act as a contract for the UI (`ui/`) and MCP server (`src/rlm_rs/mcp/`).

## Safe change guidelines

- **Prefer writing tests that enforce invariants** (sandbox isolation, repl-only root output, citation verification) rather than re-testing implementation details.
- **When you change a contract**, update tests in the same change:
  - API schema changes → update `tests/unit/test_api_*.py`
  - parsing/citation changes → update relevant unit + integration tests and run smoke
  - worker behavior changes → update ingestion/orchestrator tests
- **Keep integration tests idempotent**:
  - LocalStack state can persist; if collisions occur, set `DDB_TABLE_PREFIX` to something unique per run.

## Useful commands

- Run all tests:
  - `uv run pytest -q`
- Run unit tests only:
  - `uv run pytest -q tests/unit`
- Run integration tests (start LocalStack first):
  - `docker compose up -d localstack localstack-init`
  - `uv run pytest -q tests/integration`

