# AGENTS

This file is directory-local guidance for coding agents working in `src/rlm_rs/sandbox/`.

## What lives here

This package implements the **sandbox step executor**: it executes model-written Python steps in a constrained environment against a lazy view of the corpus, producing:

- stdout/stderr (captured)
- JSON-only state updates
- span logs (for citations)
- tool requests (to be resolved by the orchestrator)

Key files (by name):

- `step_executor.py`: main step execution loop and result shaping.
- `ast_policy.py`: AST restrictions/safety policy for user/model-provided code.
- `context.py`: ContextView/DocView abstractions for reading canonical text from S3 with span logging.
- `tool_api.py`: `tool.*` API exposed to sandbox code (e.g. `tool.FINAL(...)`), and tool request queueing.
- `lambda_handler.py`: AWS Lambda entrypoint wrapper.
- `runner.py`: SandboxRunner abstraction for local vs Lambda execution.

## How it connects to the rest of the repo

- The sandbox reads **canonical parsed artifacts** written by ingestion:
  - produced by `src/rlm_rs/ingestion/**` and `src/rlm_rs/parser/**`
  - stored in S3 via `src/rlm_rs/storage/s3.py`
- It writes span logs and state that the orchestrator/API persist and interpret:
  - orchestrator: `src/rlm_rs/orchestrator/**`
  - API runtime stepping: `src/rlm_rs/api/executions.py`
- The sandbox must never call providers or search backends; tool requests are resolved outside the sandbox.

## Non-negotiable invariants

- **No provider secrets in the sandbox.**
- **No outbound network** (and do not add code paths that assume network access).
- **JSON-only state** crossing the boundary.
- **Tool requests are queued** (never executed directly in the sandbox).
- **Citations are derived from span logs** emitted here + canonical text checksums.

## Safe change guidelines

- **Security first**:
  - Expand AST allowances cautiously; update tests (`tests/unit/test_ast_policy.py`) and consider bypasses.
  - Avoid adding “convenient” Python capabilities that enable filesystem/network access.
- **Preserve determinism**:
  - Context slicing and offsets must stay stable; span boundaries must be consistent.
- **Keep the `tool` API stable**:
  - Root prompt and many tests assume certain tool behaviors; breaking changes ripple through orchestrator and UI.

## Useful commands

- Run sandbox-related unit tests:
  - `uv run pytest -q tests/unit/test_ast_policy.py tests/unit/test_step_executor_limits.py`
- End-to-end validation (runs real sandbox steps via the API/orchestrator):
  - `scripts/smoke_test.sh`

