# AGENTS

This file is directory-local guidance for coding agents working in `ui/tests/e2e/fixtures/`.

## What lives here

Small, stable fixtures used by UI E2E tests (for example, documents uploaded/ingested during flows).

## How it connects to the rest of the repo

- Fixtures are typically uploaded to S3 (via the backend/API) and then ingested by:
  - ingestion worker (`src/rlm_rs/ingestion/**`)
  - parser service (`src/rlm_rs/parser/**`)
  and later read by the sandbox during executions.

## Safe change guidelines

- Keep fixtures small and deterministic; avoid binary files unless necessary.
- If a fixture’s content changes, update any tests that assert on derived outputs (summaries, spans, citations).

