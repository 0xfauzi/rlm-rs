# AGENTS

This file is directory-local guidance for coding agents working in `ui/` (the Next.js frontend).

## What lives here

This is a **Next.js App Router** application that provides a developer-facing UI for inspecting and driving RLM-RS:

- `src/app/`: route tree (pages for sessions, executions, runtime stepping, citations, debug, etc.).
- `src/components/`: reusable UI components (layout, modals, primitives like `CodeBlock`).
- `src/contexts/`, `src/hooks/`: React state management and shared hooks.
- `tests/e2e/`: Playwright E2E tests + fixtures.

Key config:

- `package.json`: scripts for dev/build/lint/typecheck/test/e2e.
- `next.config.js`: reverse-proxy rewrites for backend API and LocalStack.
- `playwright.config.ts`, `vitest.config.ts`, `tsconfig.json`: test/tooling configuration.

## How it connects to the rest of the repo

- The UI is a client of the HTTP API in `src/rlm_rs/api/**`.
- `next.config.js` rewrites:
  - `/v1/*` and `/health/*` → backend API (`API_PROXY_TARGET`, default `http://localhost:8080`)
  - `/localstack/*` → LocalStack (`LOCALSTACK_PROXY_TARGET`, default `http://localhost:4566`)
  This lets the UI call “same-origin” paths in dev/production without hardcoding backend URLs.
- Docker wiring lives in `compose.yaml` and `docker/ui.Dockerfile`:
  - `compose.yaml` builds `rlm-ui` with build args matching the proxy targets.
- UI test flows correspond to backend objects:
  - sessions/doc readiness (ingestion + parser)
  - executions (answerer/runtime)
  - spans/citations verification

## Safe change guidelines

- **Respect Next.js App Router conventions**:
  - `src/app/**/page.tsx` defines routes.
  - Keep server/client component boundaries clear.
- **Important UI invariant from repo root**:
  - Pages that call `useSearchParams` must wrap the client component in a `Suspense` boundary to avoid build-time prerender errors.
- **Prefer calling same-origin paths** (`/v1/...`, `/health/...`, `/localstack/...`) so rewrites handle environment differences.
- **Treat API response shapes as contracts**:
  - If you change what the UI sends/reads, update backend tests (`tests/unit/test_api_*.py`) and UI E2E scripts (`scripts/e2e/` and/or `ui/tests/e2e/`).

## Useful commands

From the `ui/` directory:

- Install deps:
  - `npm ci`
- Dev server:
  - `npm run dev`
- Production build:
  - `npm run build`
- Lint and typecheck:
  - `npm run lint`
  - `npm run typecheck`
- Unit tests (Vitest):
  - `npm test`
- E2E tests (Playwright):
  - `npm run test:e2e`

