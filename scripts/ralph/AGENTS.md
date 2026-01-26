# AGENTS

This file is directory-local guidance for coding agents working in `scripts/ralph/`.

## What lives here

This directory contains **product and planning artifacts** used to guide development. These files are not imported by the runtime code paths:

- `prd.json` / `rebuild_prd.json`: Structured product requirements / rebuild snapshots.
- `prd_prompt.txt`, `prompt.md`, `understand_prompt.md`: Prompting scaffolds for generating or refining plans/specs.
- `progress.txt`: Running notes / progress tracking.
- `codebase_map.md`: A human-oriented map of the repository.

## How it connects to the rest of the repo

- These artifacts should reflect (and drive) changes in:
  - `docs/` (spec, sequences, UI spec)
  - `src/rlm_rs/**` (implementation)
  - `ui/` (frontend)
  - `tests/` (verification)
- When requirements change here, they should usually be propagated into:
  - `docs/rls_spec.md` (behavioral contract)
  - executable tests (`tests/**`) and/or `scripts/smoke_test.sh`

## Safe change guidelines

- **Prefer editing these files freely**, but keep them consistent with the implemented system.
- **Avoid “drift”:** if you update the plan/PRD, follow through by updating docs and tests (or explicitly note what remains hypothetical).
- **Do not treat these as runtime configuration.** Real configuration belongs in `.env.example`, `compose.yaml`, and `src/rlm_rs/settings.py`.

## Useful commands

There are no direct runtime commands for this folder, but common companions are:

- Run the stack to validate plan vs reality:
  - `scripts/smoke_test.sh`
- Run tests after implementing PRD changes:
  - `uv run pytest -q`

