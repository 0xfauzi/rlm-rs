# AGENTS

This file is directory-local guidance for coding agents working in `ui/src/components/modals/`.

## What lives here

Modal flows that initiate backend actions:

- `StartAnswererModal.tsx`: creates an Answerer-mode execution for a session.
- `StartRuntimeModal.tsx`: creates a Runtime-mode execution for a session (client-driven stepping).

## How it connects to the rest of the repo

- These components call backend endpoints implemented in:
  - `src/rlm_rs/api/executions.py` (execution creation; runtime execution creation)
  - `src/rlm_rs/api/sessions.py` (session readiness checks)
- The created executions are progressed by:
  - orchestrator worker for Answerer mode (`src/rlm_rs/orchestrator/**`)
  - sandbox step execution via API for Runtime mode (`src/rlm_rs/sandbox/**`)

## Safe change guidelines

- Validate session readiness before enabling “start” actions; failures should be user-readable.
- Keep request payloads aligned with backend request models; update backend tests + UI together when changing fields.
- Avoid silently defaulting important parameters (budgets/models); make defaults visible where possible.

## Useful commands

- Run UI and validate modal flows:
  - `npm run dev`
- End-to-end backend flow validation:
  - `scripts/smoke_test.sh`

