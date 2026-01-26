# AGENTS

This file is directory-local guidance for coding agents working in `ui/tests/e2e/`.

## What lives here

Playwright end-to-end test suite for the Next.js UI:

- `fixtures/`: input files and stable artifacts used during tests.
- `screenshots/`: screenshot output directory (often kept with `.gitkeep`).

The Playwright configuration is at `ui/playwright.config.ts`.

## How it connects to the rest of the repo

- Tests drive the UI in a browser and therefore indirectly exercise backend endpoints in `src/rlm_rs/api/**`.
- Many tests assume the proxy rewrites in `ui/next.config.js` are working so `/v1/*` routes reach the backend.
- For scripted/manual E2E flows, also see `scripts/e2e/*.md`.

## Safe change guidelines

- Keep fixtures minimal and deterministic.
- If you update routes or UI structure, update tests and any screenshots expectations together.
- Avoid asserting on unstable text that can change due to backend timing; assert on status transitions and visible state changes.

## Useful commands

- Run Playwright E2E:
  - `npm run test:e2e`

