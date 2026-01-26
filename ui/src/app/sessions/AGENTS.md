# AGENTS

This file is directory-local guidance for coding agents working in `ui/src/app/sessions/`.

## What lives here

Routes for listing and inspecting sessions:

- `page.tsx`: sessions list route.
- `[session_id]/`: dynamic route for a specific session’s detail page.

Sessions represent corpora/doc collections and their ingestion readiness.

## How it connects to the rest of the repo

- Backed by session/document endpoints in `src/rlm_rs/api/sessions.py`.
- Session readiness depends on ingestion + parsing:
  - ingestion worker: `src/rlm_rs/ingestion/**`
  - parser service: `src/rlm_rs/parser/**`
  - persisted in DDB/S3: `src/rlm_rs/storage/**`

## Safe change guidelines

- Keep the UI explicit about session status transitions (CREATING → READY/FAILED).
- Prefer polling with a deadline/backoff rather than tight loops.
- When changing session models, update backend tests (`tests/unit/test_api_sessions.py`) and UI behavior together.

## Useful commands

- Run UI:
  - `npm run dev`
- Validate end-to-end session creation/ingestion:
  - `scripts/smoke_test.sh`

