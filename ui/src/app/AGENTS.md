# AGENTS

This file is directory-local guidance for coding agents working in `ui/src/app/` (Next.js App Router routes).

## What lives here

This directory defines the UI’s route tree and global app scaffolding:

- `layout.tsx`: global layout wrapper for all routes.
- `globals.css`: global styles (Tailwind v4 is used via PostCSS config).
- `page.tsx`: the home/landing route.
- `error.tsx`: global error boundary route.
- `not-found.tsx`: 404 route.
- Feature routes (subdirectories):
  - `sessions/`: session list + session detail routes.
  - `executions/`: execution list + execution detail + runtime view routes.
  - `citations/`: citation inspection/verification routes.
  - `debug/`: debugging utilities/views.

## How it connects to the rest of the repo

- All routes ultimately visualize or drive data coming from the backend API (`src/rlm_rs/api/**`), accessed via same-origin paths:
  - `/v1/...` (API)
  - `/health/...` (health checks)
  - `/localstack/...` (LocalStack proxy; mainly for debugging)
  configured in `ui/next.config.js`.
- The route structure roughly mirrors backend resources:
  - sessions → documents + readiness (ingestion/parser)
  - executions → answerer/runtime runs (orchestrator/sandbox)
  - citations/spans → verification (orchestrator + canonical parsed text)

## Safe change guidelines

- **Server vs client components**:
  - Keep data-fetching patterns consistent and avoid leaking secrets to the client.
- **Suspense requirement**:
  - If a route uses `useSearchParams`, wrap the consuming client component in `Suspense` to avoid prerender/build issues.
- **Error handling**:
  - Prefer surfacing backend errors clearly (HTTP status + message) since this UI is used for debugging system behavior.

## Useful commands

- Run the UI while iterating on routes:
  - `npm run dev`
- Validate production build behavior:
  - `npm run build`

