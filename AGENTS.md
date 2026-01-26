# AGENTS

This file is the local guide for coding agents working on RLM-RS.

## Project overview
- RLM-RS implements the Recursive Language Model Runtime Service with API, orchestrator, ingestion, sandbox, parser, and storage components.
- Core code lives under `src/rlm_rs`; tests are in `tests`; design references are in `docs`.
- The developer-facing UI lives under `ui/`.

## Start here
- Read the contract: `docs/rls_spec.md`, `docs/sequence.md`, `docs/runtime_sequence.md`.
- Validate changes end-to-end with `scripts/smoke_test.sh` before doing load tests.

## Guiding principles (and deliberate divergences from the RLM paper)

The RLM paper (`docs/rls_paper_pdf.pdf`) frames the core mechanism as loading the prompt into a **Python REPL environment** and letting the model write code against it. This repo keeps the same *idea* (treat the corpus as an external environment) but implements it with enterprise-safe boundaries:

- **No persistent REPL and no arbitrary code execution**
  - We do **not** run a long-lived, general REPL as suggested in the paper; instead we execute **single, sandboxed steps** with strict limits and an AST safety policy.
  - This architecture is tailored for enterprise environments where arbitrarily executing LLM-generated code is disallowed; sandboxing and explicit boundaries are non-negotiable.
- **Least-privilege security boundary**
  - The sandbox has **no provider secrets** and **no outbound network**, and it accepts **JSON-only state**.
  - Any external work (LLM subcalls, search, etc.) is emitted as **tool requests** and resolved by the orchestrator, never inside the sandbox.
- **Auditability and reproducibility**
  - State crossing boundaries is JSON; large blobs live in S3 with checksums/summaries.
  - We persist traces/logs and derive citations from span logs + canonical text checksums so results are inspectable and verifiable.
- **Operational control**
  - Budgets, caching, retries, and provider credentials are centralized in the orchestrator to enforce cost and reliability controls consistently.

## Architecture invariants
- Sandbox execution has no provider secrets and no outbound network, and it only accepts JSON state.
- Sandbox steps should run through the configured SandboxRunner (local for dev, Lambda for prod).
- Tool requests are queued in the sandbox and resolved by the orchestrator, never inside the sandbox.
- Root model output must be a single fenced code block labeled `repl` with no surrounding text.
- Citations are derived from span logs and checksums, not from model output.
- Parsed text in S3 is canonical, so changes must preserve determinism.

## Environment and tooling
- Use `uv` for installs and all Python commands. Do not add requirements.txt.
- Local AWS primitives use LocalStack. Use `.env.example` and `compose.yaml` as the source of defaults.
- Settings are defined in `src/rlm_rs/settings.py` and should stay in sync with `.env.example`.

## UI patterns
- Next.js pages that call `useSearchParams` must wrap the client component in a `Suspense` boundary to avoid build-time prerender errors.

## Common commands
- `uv sync`
- `uv run pytest -q`
- `uv run ruff check .`
- `docker compose up --build`
- `scripts/smoke_test.sh`
- `scripts/load_test.sh --iterations N`

## LocalStack and init
- `scripts/localstack_init.sh` creates the S3 bucket and DynamoDB tables.
- Integration tests skip when LocalStack is unavailable. Start it before running them.
- If LocalStack state persists between runs, set `DDB_TABLE_PREFIX` to a unique value to avoid conditional write collisions in integration tests.

## Change checklists
- Adding env vars: update `src/rlm_rs/settings.py`, `.env.example`, and `compose.yaml`.
- Changing API contracts: update relevant tests and any spec references in `docs`.
- Modifying parsing or span logic: ensure canonical text, offsets, and checksums remain stable.

## Scaling and measurement
- Validate the full pipeline with `scripts/smoke_test.sh` before load tests.
- Measure performance or budget changes instead of estimating.
