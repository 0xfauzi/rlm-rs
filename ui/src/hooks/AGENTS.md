# AGENTS

This file is directory-local guidance for coding agents working in `ui/src/hooks/`.

## What lives here

Custom React hooks used by the UI:

- `useAutoSeed.ts`: hook supporting local/dev auto-seeding flows.

## How it connects to the rest of the repo

- Hooks may call backend endpoints (via `/v1/*` and `/health/*`) and therefore depend on:
  - API routes in `src/rlm_rs/api/**`
  - stack readiness driven by `compose.yaml` services

## Safe change guidelines

- Keep hooks deterministic and easy to test; avoid hidden global side-effects.
- Be explicit about when a hook performs writes (e.g. seeding sessions/executions).
- Avoid tight polling loops; use backoff/deadlines.

## Useful commands

- Typecheck and unit test after changes:
  - `npm run typecheck`
  - `npm test`

