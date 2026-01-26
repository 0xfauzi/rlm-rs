# AGENTS

This file is directory-local guidance for coding agents working in `tests/unit/`.

## What lives here

These are **unit tests** for individual modules and contracts in `src/rlm_rs/**`.

Common areas covered (based on filenames in this repo):

- API behavior: `test_api_*.py` (auth, sessions, executions, runtime, limits, spans)
- Sandbox safety and limits: `test_ast_policy.py`, `test_step_executor_limits.py`, `test_tool_api.py`
- Orchestrator behavior/citations/providers: `test_baseline.py`, `test_citations.py`, `test_eval_judge.py`, `test_root_output_parser.py`, `test_openai_provider_cache.py`
- Storage helpers: `test_state.py`, `test_s3.py`, `test_search_backend.py`
- Models/settings/errors/logging: `test_models.py`, `test_settings.py`, `test_errors.py`, `test_metrics.py`, `test_code_log.py`
- Evaluation flows: `test_evaluations.py`

## How it connects to the rest of the repo

- Unit tests are the first line of defense for:
  - API contracts consumed by `ui/` and `src/rlm_rs/mcp/`
  - sandbox/orchestrator boundaries (no network in sandbox; tool resolution outside sandbox)
  - citation determinism (spans + checksums)
- They should run quickly and not require LocalStack unless explicitly testing storage integration (those belong in `tests/integration/`).

## Safe change guidelines

- **When changing behavior, update unit tests first** (or alongside) to encode the new contract.
- **Prefer stable, deterministic fakes** over “real” network calls.
  - Many tests use fake providers rather than OpenAI.
- **Keep fixtures small and explicit.**
  - Large fixtures should live in `ui/tests/e2e/fixtures/` (UI) or be generated in-test.

## Useful commands

- Run all unit tests:
  - `uv run pytest -q tests/unit`
- Run a focused subset (example):
  - `uv run pytest -q tests/unit/test_api_executions.py`

