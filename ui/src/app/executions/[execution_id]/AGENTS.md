# AGENTS

This file is directory-local guidance for coding agents working in `ui/src/app/executions/[execution_id]/`.

## What lives here

Dynamic execution detail routes:

- `page.tsx`: execution detail view for a specific `execution_id`.
- `runtime/`: nested route(s) for runtime-step interaction/inspection for a specific execution.

## How it connects to the rest of the repo

- Reads execution status, answer, citations, and trace pointers from backend endpoints in `src/rlm_rs/api/executions.py`.
- Execution details reflect orchestration and sandbox behavior:
  - answerer loop (`src/rlm_rs/orchestrator/**`)
  - sandbox steps (`src/rlm_rs/sandbox/**`)
  - citation verification (`src/rlm_rs/orchestrator/citations.py`)

## Safe change guidelines

- Keep URL params and backend identifiers aligned:
  - `execution_id` should match backend IDs exactly (no client-side reformatting).
- Avoid exposing raw trace/state blobs directly without redaction if trace redaction is enabled server-side.
- If you add query-string driven behavior via `useSearchParams`, wrap the client component in `Suspense` (repo-wide Next.js constraint).

## Useful commands

- Run UI:
  - `npm run dev`

