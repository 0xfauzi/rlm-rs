# AGENTS

This file is directory-local guidance for coding agents working in `src/rlm_rs/storage/`.

## What lives here

This package contains the storage layer for RLM-RS:

- DynamoDB helpers for metadata/state pointers (sessions, documents, executions, audit logs).
- S3 helpers for large blobs and canonical artifacts (parsed text/meta/offsets, state blobs, traces, caches).

Key files (by name):

- `ddb.py`: DynamoDB table naming, key structure, and read/write helpers.
- `s3.py`: S3 client helpers (including LocalStack-friendly configuration).
- `state.py`: execution state persistence conventions (inline vs S3 blobs, checksums/summaries).
- `__init__.py`: package marker.

## How it connects to the rest of the repo

- **API** (`src/rlm_rs/api/**`) reads/writes sessions and executions through this layer.
- **Ingestion** (`src/rlm_rs/ingestion/**`) writes parsed artifacts to S3 and updates DDB statuses.
- **Orchestrator** (`src/rlm_rs/orchestrator/**`) persists execution turns, caches provider/search results, and stores traces/citation material.
- **Sandbox** (`src/rlm_rs/sandbox/**`) reads canonical parsed artifacts from S3 (range reads / offset-driven slicing).
- **LocalStack**:
  - `compose.yaml` runs LocalStack and `localstack-init`
  - `scripts/localstack_init.sh` ensures bucket/tables exist
  - tests/integration rely on LocalStack availability

## Safe change guidelines

- **Table naming must remain consistent**:
  - `DDB_TABLE_PREFIX` affects table names; scripts and code must agree.
- **Be careful with conditional writes and idempotency**:
  - ingestion and orchestrator may retry; storage operations should be safe under repetition.
- **State blobs and checksums are part of correctness**:
  - large state may be stored in S3; pointers/checksums in DDB must accurately reflect the blob.
- **CORS / endpoints**:
  - UI sometimes talks to LocalStack for debugging; ensure any changes to endpoints remain consistent with `compose.yaml` proxying and CORS settings.

## Useful commands

- Start LocalStack + init:
  - `docker compose up -d localstack localstack-init`
- Run storage-focused tests:
  - `uv run pytest -q tests/unit/test_state.py tests/unit/test_s3.py`
- Integration roundtrip (LocalStack required):
  - `uv run pytest -q tests/integration/test_ddb_s3_roundtrip.py`

