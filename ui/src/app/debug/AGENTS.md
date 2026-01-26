# AGENTS

This file is directory-local guidance for coding agents working in `ui/src/app/debug/`.

## What lives here

Debugging-focused routes:

- `page.tsx`: debug page entrypoint.

The debug UI is meant to help inspect the running system (API, storage, execution artifacts) during development.

## How it connects to the rest of the repo

- Typically calls backend endpoints under `/health/*` and `/v1/*` (proxied by `ui/next.config.js`).
- May also use the `/localstack/*` proxy for inspecting LocalStack state in dev (when enabled by CORS/proxy wiring in `compose.yaml`).

## Safe change guidelines

- Prefer making debug features **read-only** by default (avoid accidental writes to LocalStack or production backends).
- Do not expose secrets (API keys, provider keys) in the UI.
- Keep debug endpoints aligned with actual backend capabilities; don’t hardcode assumptions that diverge from `src/rlm_rs/api/**`.

## Useful commands

- Run the stack and UI:
  - `docker compose up --build`

