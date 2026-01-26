# AGENTS

This file is directory-local guidance for coding agents working in `ui/tests/`.

## What lives here

Automated tests for the UI, primarily end-to-end (E2E) tests using Playwright:

- `e2e/`: Playwright test suite, fixtures, and screenshots.

## How it connects to the rest of the repo

- UI E2E tests require the backend stack to be running:
  - API (`src/rlm_rs/api/**`)
  - parser (`src/rlm_rs/parser/**`)
  - workers (`src/rlm_rs/ingestion/**`, `src/rlm_rs/orchestrator/**`)
  - LocalStack (S3 + DynamoDB)
- High-level E2E workflows are also documented in `scripts/e2e/*.md` checklists.

## Safe change guidelines

- Keep tests stable and deterministic:
  - prefer stable selectors and predictable UI states
  - avoid brittle timing assumptions
- When you change backend contracts, update UI tests accordingly (and vice versa).

## Useful commands

- Run UI E2E tests:
  - `npm run test:e2e`

