# AGENTS

This file is directory-local guidance for coding agents working in `src/rlm_rs/orchestrator/`.

## What lives here

This package is the **managed control plane** for running executions:

- Answerer mode: the orchestrator runs the root-model loop, invokes sandbox steps, resolves tool requests, persists state, and decides when to stop.
- Tool resolution: resolves sandbox-enqueued tools (LLM subcalls, optional search), with caching and budget enforcement.
- Citation assembly: turns sandbox span logs into verifiable citation references (checksums over canonical parsed text).

Key files (by name):

- `worker.py`: orchestrator worker loop and execution scheduling (poll/run).
- `providers.py`: provider integrations (e.g. OpenAI, Fake provider) and provider-facing abstractions.
- `root_prompt.py`: prompt shape/instructions for root model (including the strict `repl`-block contract).
- `citations.py`: citation derivation/verification logic and SpanRef assembly.
- `baseline.py`: baseline behaviors/helpers for execution logic (used in tests and reference flows).
- `eval_judge.py`: optional evaluation judging (gated by settings/flags).

## How it connects to the rest of the repo

- **Sandbox**: `src/rlm_rs/sandbox/**`
  - Sandbox runs isolated steps; orchestrator must treat sandbox as untrusted and only exchange JSON state/tool requests.
- **API**: `src/rlm_rs/api/**`
  - API creates executions and stores metadata/state pointers; orchestrator advances them.
- **Storage**: `src/rlm_rs/storage/**`
  - Orchestrator reads/writes DynamoDB execution metadata and stores large blobs/caches/traces in S3.
- **Models/settings**: `src/rlm_rs/models.py`, `src/rlm_rs/settings.py`
  - Define schemas and budgets/limits enforced by the orchestrator.
- **Search (optional)**: `src/rlm_rs/search/**`
  - Orchestrator may call search backend for retrieval (never from sandbox).
- **Tests**:
  - unit: provider caching, citations, budgets, orchestrator logic (`tests/unit/test_*`)
  - integration: end-to-end Answerer behavior (`tests/integration/test_orchestrator_answerer.py`)

## Non-negotiable invariants

- **Root model output must be exactly one ` ```repl ` code block** with no surrounding text.
  - This contract is assumed by parsing and by safety logic around sandbox execution.
- **Sandbox does not call providers/search.**
  - The orchestrator is the only component that holds provider credentials and performs external calls.
- **Citations are not “model claims.”**
  - They are derived from span logs + canonical text checksums, so changing span logging or parsing affects verifiability.

## Safe change guidelines

- **Budget enforcement and caching are correctness features**, not just optimizations.
  - When changing cache keys, persistence format, or budgets, update tests and validate the smoke test.
- **Tool resolution runs with bounded concurrency.**
  - Keep `TOOL_RESOLUTION_MAX_CONCURRENCY` in mind when adjusting subcall/search flows.
- **Keep provider interfaces narrow and testable.**
  - Providers should be swappable (Fake vs OpenAI) without changing core orchestration logic.
- **Be explicit about retry semantics** (timeouts, backoff) for provider calls and storage writes.
- **Avoid importing UI/test-only helpers**; this package should remain production-safe.

## Useful commands

- Run orchestrator worker locally:
  - `WORKER_MODE=orchestrator uv run python -m rlm_rs.worker_entrypoint`
- Run orchestrator-focused tests:
  - `uv run pytest -q tests/integration/test_orchestrator_answerer.py`
  - `uv run pytest -q tests/unit/test_openai_provider_cache.py tests/unit/test_citations.py`
- Validate end-to-end:
  - `scripts/smoke_test.sh`

