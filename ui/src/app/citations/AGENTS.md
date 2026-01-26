# AGENTS

This file is directory-local guidance for coding agents working in `ui/src/app/citations/`.

## What lives here

Routes and client components for working with citations/spans:

- `page.tsx`: route entrypoint.
- `CitationsPageClient.tsx`: client-side UI for citation lookup/verification.

## How it connects to the rest of the repo

- Uses backend citation/span endpoints implemented in:
  - `src/rlm_rs/api/spans.py`
  - `src/rlm_rs/api/...` citation verification routes
- The underlying correctness is implemented in:
  - `src/rlm_rs/orchestrator/citations.py` (SpanRef derivation/verification)
  - `src/rlm_rs/sandbox/context.py` (span logging)
  - `src/rlm_rs/parser/**` (canonical parsed text + offsets)

## Safe change guidelines

- Keep the UI terminology aligned with the backend:
  - “SpanRef” is verifiable and should display doc id, offsets, and checksum-derived status.
- Do not invent citations in the UI; treat citations as artifacts emitted/verified by the backend.
- Prefer same-origin API calls (`/v1/...`) so `next.config.js` rewrites work in all environments.

## Useful commands

- Run UI and validate citation pages manually:
  - `npm run dev`
- Validate citation verification end-to-end:
  - `scripts/smoke_test.sh`

