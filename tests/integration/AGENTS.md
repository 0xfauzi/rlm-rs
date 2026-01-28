# AGENTS

This file is directory-local guidance for coding agents working in `tests/integration/`.

## What lives here

These are **integration tests** that exercise multiple components together, typically against **LocalStack** (S3 + DynamoDB):

- Storage roundtrips (`test_ddb_s3_roundtrip.py`)
- API integration flows (e.g. listing executions)
- Orchestrator end-to-end behavior (`test_orchestrator_answerer.py`)
- Evaluation/eval-judge flows (when enabled)

## How it connects to the rest of the repo

- Requires LocalStack and storage wiring from:
  - `compose.yaml`
  - `scripts/localstack_init.sh`
  - `src/rlm_rs/storage/**`
- Exercises real flows across:
  - ingestion (`src/rlm_rs/ingestion/**`)
  - parser service (`src/rlm_rs/parser/**`)
  - API (`src/rlm_rs/api/**`)
  - orchestrator (`src/rlm_rs/orchestrator/**`)

## Safe change guidelines

- **Keep tests hermetic and idempotent.**
  - LocalStack state can persist across runs; if you see conditional write collisions, set `DDB_TABLE_PREFIX` to a unique value per run.
- **Minimize timing flakiness.**
  - Prefer polling with deadlines over fixed sleeps.
- **Normalize DynamoDB numerics before JSON.**
  - DDB returns `Decimal` values - use `state_store.normalize_json_value` before sending JSON payloads in tests.
- **Fail with actionable error messages.**
  - Integration failures can be hard to debug; make assertions descriptive.

## Useful commands

- Start LocalStack + init:
  - `docker compose up -d localstack localstack-init`
- Run integration tests:
  - `uv run pytest -q tests/integration`
