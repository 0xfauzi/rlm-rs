# AGENTS

This file is directory-local guidance for coding agents working in `ui/src/`.

## What lives here

All frontend source code for the Next.js UI:

- `app/`: Next.js App Router route tree.
- `components/`: shared components (layout shell, modals, UI primitives).
- `contexts/`: React contexts (global app state, toasts).
- `hooks/`: custom hooks (e.g. auto-seeding).

## How it connects to the rest of the repo

- The UI calls backend endpoints exposed by `src/rlm_rs/api/**` using the proxy rewrites configured in `ui/next.config.js`.
- UI pages reflect backend concepts:
  - sessions + document readiness (ingestion + parser)
  - executions (answerer/runtime)
  - spans/citations verification

## Safe change guidelines

- Prefer keeping API calls and JSON parsing centralized and consistent across pages (avoid duplicating response-shape assumptions).
- Keep route segments under `app/` small and composable; push reusable UI into `components/`.
- When adding `useSearchParams` to a page, ensure the client component is wrapped in `Suspense` (repo-wide invariant).

## Useful commands

- Typecheck the whole UI:
  - `npm run typecheck`
- Run unit tests:
  - `npm test`

