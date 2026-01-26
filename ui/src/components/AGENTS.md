# AGENTS

This file is directory-local guidance for coding agents working in `ui/src/components/`.

## What lives here

Shared React components used across the route tree:

- `layout/`: app shell (sidebar/topbar), navigation, and auto-seeding helpers.
- `modals/`: modal flows for starting answerer/runtime executions.
- `ui/`: UI primitives (code blocks, empty states, skeletons, toast utilities, etc.).
- `ErrorBoundary.tsx`: component-level error boundary.

## How it connects to the rest of the repo

- Used by pages under `ui/src/app/**`.
- Many components render data returned by the backend API (`src/rlm_rs/api/**`) via the UI’s proxy paths (`/v1/*`).
- Some components (e.g. “start execution” modals) map directly onto backend actions:
  - session → create execution
  - runtime → submit step / resolve tools

## Safe change guidelines

- Keep components reusable; avoid embedding route-specific assumptions in shared primitives.
- Centralize error handling and toasts so failures are obvious (this UI is for debugging a complex system).
- Prefer stable styling primitives and avoid introducing heavy dependencies unless needed.

## Useful commands

- Run UI unit tests:
  - `npm test`
- Lint/typecheck:
  - `npm run lint`
  - `npm run typecheck`

