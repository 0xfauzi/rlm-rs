# AGENTS

This file is directory-local guidance for coding agents working in `scripts/`.

## What lives here

This directory contains **operational scripts** for running, validating, and load-testing the stack:

- `smoke_test.sh`: End-to-end validation (LocalStack + parser + API + workers) using a Fake provider by default.
- `ui_smoke_test.sh`: Brings up the stack and checks the UI can load (used before UI E2E).
- `load_test.sh`: Load testing harness (run only after smoke passes).
- `localstack_init.sh`: Creates the S3 bucket and DynamoDB tables (also wired in `compose.yaml` as `localstack-init`).
- `build_finetune_datasets.py`: Builds finetune datasets from stored traces/logs.
- `export_finetune_traces.py`: Exports execution traces for finetuning/evaluation.
- `evaluate_finetuned_policy.py`: Evaluates finetuned policies against stored traces.
- `recompute_evaluation.py`: Recomputes evaluation outputs from stored artifacts.
- `e2e/`: Markdown test scripts/checklists for UI Playwright-based verification.
- `ralph/`: Product/planning artifacts (not runtime-critical).

## How it connects to the rest of the repo

- `compose.yaml` mounts and executes `scripts/localstack_init.sh` via the `localstack-init` service.
- `smoke_test.sh` runs:
  - `uv run uvicorn rlm_rs.parser.service:app` (parser service)
  - `uv run uvicorn rlm_rs.api.app:app` (API)
  - `rlm_rs.ingestion.worker` (ingestion worker loop)
  - `rlm_rs.orchestrator.worker` with `FakeLLMProvider` (orchestrator loop)
  and then exercises the API to create a session, ingest a fixture, run an execution, and verify citations.
- UI E2E scripts rely on `ui/` and the running services started by `compose.yaml` / `ui_smoke_test.sh`.

## Safe change guidelines

- **Treat `smoke_test.sh` as the “golden path” for correctness.**
  - If you change API contracts, state persistence, ingestion behavior, citations, or budgets, update the smoke test accordingly.
- **Keep LocalStack table/bucket creation aligned** with `src/rlm_rs/storage/ddb.py` and `src/rlm_rs/settings.py`.
  - Table naming depends on `DDB_TABLE_PREFIX`; scripts should use the same prefix logic.
- **Avoid making scripts “too smart.”** Prefer calling into real code paths (workers/services) so the scripts remain representative of production behavior.

## Useful commands

- Full end-to-end sanity check:
  - `scripts/smoke_test.sh`
- Start LocalStack + init only (useful before running integration tests):
  - `docker compose up -d localstack localstack-init`
- Load testing (only after smoke passes):
  - `scripts/load_test.sh --iterations N`

