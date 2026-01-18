# AGENTS

This file is the local guide for coding agents working on rlm-rs.

## Project overview
- RLM-RS implements the Recursive Language Model Runtime Service with API, orchestrator, ingestion, sandbox, parser, and storage components.
- Core code lives under `src/rlm_rs`; tests are in `tests`; design references are in `docs`.

## Architecture invariants
- Sandbox execution has no provider secrets and no outbound network, and it only accepts JSON state.
- Tool requests are queued in the sandbox and resolved by the orchestrator, never inside the sandbox.
- Root model output must be a single fenced code block labeled `repl` with no surrounding text.
- Citations are derived from span logs and checksums, not from model output.
- Parsed text in S3 is canonical, so changes must preserve determinism.

## Environment and tooling
- Use `uv` for installs and all Python commands. Do not add requirements.txt.
- Local AWS primitives use LocalStack. Use `.env.example` and `compose.yaml` as the source of defaults.
- Settings are defined in `src/rlm_rs/settings.py` and should stay in sync with `.env.example`.

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

## Change checklists
- Adding env vars: update `src/rlm_rs/settings.py`, `.env.example`, and `compose.yaml`.
- Changing API contracts: update relevant tests and any spec references in `docs`.
- Modifying parsing or span logic: ensure canonical text, offsets, and checksums remain stable.

## Scaling and measurement
- Validate the full pipeline with `scripts/smoke_test.sh` before load tests.
- Measure performance or budget changes instead of estimating.
