# AGENTS

This file is directory-local guidance for coding agents working in `src/rlm_rs/ingestion/`.

## What lives here

This package implements the **ingestion worker**: it takes raw document references from sessions, parses them via the parser service, and writes canonical parsed artifacts for the sandbox to consume.

Key files:

- `worker.py`: worker construction and the “run once / poll” ingestion loop.
- `__init__.py`: package marker.

## How it connects to the rest of the repo

Ingestion is the bridge between “raw docs” and “canonical parsed corpus”:

- Reads session/document state from DynamoDB via `src/rlm_rs/storage/ddb.py`.
- Calls the parser service via `src/rlm_rs/parser/client.py` (HTTP).
- Writes parsed artifacts to S3 via `src/rlm_rs/storage/s3.py`:
  - canonical text
  - metadata
  - offsets / indexing helpers (used for deterministic slicing + citations)
- Updates document/session readiness status so:
  - the API can report session readiness (`src/rlm_rs/api/sessions.py`)
  - the orchestrator/runtime can execute steps against documents (`src/rlm_rs/orchestrator/**`, `src/rlm_rs/sandbox/**`)
- If search is enabled, ingestion may also trigger indexing via `src/rlm_rs/search/**`.

## Safe change guidelines

- **Idempotency is critical.** The worker may retry; changes should not corrupt or duplicate artifacts.
- **Determinism is a hard requirement.**
  - Parsed text/offsets must remain stable for citations/checksums.
  - Any change to parsing outputs usually requires updating tests and validating checksum semantics.
- **Respect readiness semantics.**
  - Session/document statuses are consumed by API and UI; keep status transitions consistent with `src/rlm_rs/models.py`.
- **Avoid pulling in orchestrator/provider logic.**
  - Ingestion should not depend on LLM providers; it should focus on parsing and storage/indexing.

## Useful commands

- Run the ingestion worker locally:
  - `WORKER_MODE=ingestion uv run python -m rlm_rs.worker_entrypoint`
- Or run the same loop pattern used in `scripts/smoke_test.sh` (direct import in a small runner).
- Run ingestion-related tests:
  - `uv run pytest -q tests/unit/test_ingestion_worker.py`
  - `uv run pytest -q tests/integration/` (requires LocalStack)

