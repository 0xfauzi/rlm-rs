# AGENTS

This file is directory-local guidance for coding agents working in `ui/src/app/executions/`.

## What lives here

Routes for listing and inspecting executions:

- `page.tsx`: route entrypoint for the executions list.
- `ExecutionsPageClient.tsx`: client component for listing/filtering executions.
- `[execution_id]/`: dynamic route segment for execution detail pages.

## How it connects to the rest of the repo

- Primary backend dependency is the executions API in `src/rlm_rs/api/executions.py`.
- Execution objects and state are persisted via `src/rlm_rs/storage/**`.
- Answerer mode execution progression is driven by `src/rlm_rs/orchestrator/**`.
- Runtime mode stepping is backed by the sandbox (`src/rlm_rs/sandbox/**`) plus API/orchestrator endpoints.

## Safe change guidelines

- Treat execution status values as a shared contract with the backend models (`src/rlm_rs/models.py`).
- Prefer polling with backoff (or server-sent updates if added later) rather than tight loops in the UI.
- Keep “execution detail” concerns in `[execution_id]/` to avoid bloating the list page.

## Useful commands

- Run UI and validate execution list/filters:
  - `npm run dev`
- Exercise end-to-end execution creation and listing:
  - `scripts/smoke_test.sh`

