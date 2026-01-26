# AGENTS

This file is directory-local guidance for coding agents working in `ui/src/components/layout/`.

## What lives here

App-shell and navigation components:

- `AppShell.tsx`: top-level layout wrapper for the UI.
- `Sidebar.tsx`: navigation/sidebar.
- `TopBar.tsx`: top navigation/status/actions.
- `AutoSeed.tsx`: development helper for seeding data (typically for local/demo workflows).

## How it connects to the rest of the repo

- These components are composed by routes in `ui/src/app/**`.
- Auto-seeding/dev helpers often depend on backend readiness:
  - health endpoints (`/health/ready`)
  - LocalStack proxy (`/localstack/*`) for dev-only introspection
  - session/execution endpoints (`/v1/*`)

## Safe change guidelines

- Keep navigation predictable and stable; this UI is used to debug backend state.
- Be careful with “AutoSeed” behaviors:
  - avoid writing to production-like backends accidentally
  - make it explicit when the UI is creating sessions/executions
- Avoid tying layout components to a specific page; keep them generic.

## Useful commands

- Run UI and verify shell behavior:
  - `npm run dev`

