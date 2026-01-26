# AGENTS

This file is directory-local guidance for coding agents working in `ui/src/app/sessions/[session_id]/`.

## What lives here

Dynamic session detail routes:

- `page.tsx`: detail view for a specific `session_id` (documents, readiness, and actions like starting executions).

## How it connects to the rest of the repo

- Reads session + document readiness from `src/rlm_rs/api/sessions.py`.
- Starting executions from a session uses `src/rlm_rs/api/executions.py`, and then:
  - Answerer mode runs via `src/rlm_rs/orchestrator/**`
  - Runtime mode steps run via `src/rlm_rs/sandbox/**` through API endpoints

## Safe change guidelines

- Keep `session_id` handling strict and transparent (no client-side “guessing”).
- If you add query-string behavior (`useSearchParams`), wrap the client component in `Suspense` to avoid prerender issues.
- Make sure actions are disabled until the session is READY to avoid confusing backend errors.

## Useful commands

- Run UI:
  - `npm run dev`

