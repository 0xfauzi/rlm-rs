# AGENTS

This file is directory-local guidance for coding agents working in `ui/src/app/executions/[execution_id]/runtime/`.

## What lives here

UI routes for interacting with or inspecting **Runtime mode** executions:

- `page.tsx`: runtime execution view for a specific `execution_id`.

Runtime mode is client-driven stepping: the UI submits code/state for a turn and renders stdout/state/tool requests.

## How it connects to the rest of the repo

- Backend endpoints are implemented in `src/rlm_rs/api/executions.py` (runtime execution creation, `/steps`, and optionally tool resolution).
- The sandbox runs the actual step code:
  - `src/rlm_rs/sandbox/step_executor.py` and friends
- Tool requests are resolved outside the sandbox:
  - `src/rlm_rs/orchestrator/**` (managed tool resolution, caching, budgets)

## Safe change guidelines

- Maintain the security boundary:
  - the UI should only submit code via the runtime endpoints; it should not introduce “direct” execution pathways.
- Render tool requests/results transparently:
  - tool requests should be shown as queued work, not “already executed” actions.
- Keep the UI resilient to partial results:
  - sandbox may return stdout + tool requests without final answers.

## Useful commands

- Run UI and manually validate runtime stepping:
  - `npm run dev`

