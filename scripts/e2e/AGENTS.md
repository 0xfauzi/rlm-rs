# AGENTS

This file is directory-local guidance for coding agents working in `scripts/e2e/`.

## What lives here

This directory contains **markdown-based UI E2E test scripts/checklists** intended to be run with a Playwright-driven workflow (often via an IDE/MCP automation agent):

- `run-all-tests.md`: The ordered checklist of E2E scripts to run.
- `test-*.md`: Step-by-step UI flows (sessions, answerer mode, runtime mode, debug pages, responsiveness, etc.).

The `.md` files are deliberately explicit and human-readable so failures are diagnosable.

## How it connects to the rest of the repo

- The UI under test is `ui/` (Next.js app).
- The backend services must be running (API + parser + workers + LocalStack):
  - typically via `scripts/ui_smoke_test.sh` or `docker compose up --build`
- The flows referenced here map to API concepts implemented in:
  - `src/rlm_rs/api/**` (HTTP endpoints)
  - `src/rlm_rs/orchestrator/**` (answerer loop + tool resolution)
  - `src/rlm_rs/storage/**` (sessions/executions persistence)

## Safe change guidelines

- **Update these scripts when you change the UI or the API shape.**
  - Example: if you rename fields in session/execution JSON, update the UI and also adjust the checklist expectations.
- **Keep the test ordering meaningful.**
  - `run-all-tests.md` should roughly progress from “app shell loads” → “sessions” → “answerer/runtime flows” → “debug/edge cases”.
- **Prefer stable selectors and deterministic UI states** in the UI; these scripts should not rely on flaky timing assumptions.

## Useful commands

- Bring up the full stack (including UI) for local E2E:
  - `docker compose up --build`
- Run the readiness smoke for UI (if available in your workflow):
  - `scripts/ui_smoke_test.sh`

