# AGENTS

This file is directory-local guidance for coding agents working in `ui/src/components/ui/`.

## What lives here

UI primitives and shared presentation components used across pages:

- `CodeBlock.tsx`: code formatting/highlighting component (has a unit test `CodeBlock.test.tsx`).
- `EmptyState.tsx`: “no data yet” state UI.
- `ErrorPanel.tsx`: consistent error display surface.
- `Skeleton.tsx`: loading placeholders.
- `toast.tsx`: toast UI helpers/utilities.

## How it connects to the rest of the repo

- Used heavily by pages under `ui/src/app/**` and higher-level components under `ui/src/components/**`.
- Error display and toast behavior should align with data-fetching patterns and error shapes returned by the backend API (`/v1/*`).

## Safe change guidelines

- Keep primitives generic and reusable; avoid baking in route-specific assumptions.
- Prefer accessible markup (keyboard focus, ARIA where appropriate) and predictable loading states.
- Maintain tests for primitives when behavior is non-trivial:
  - update `CodeBlock.test.tsx` when changing formatting logic

## Useful commands

- Run UI unit tests (includes primitives):
  - `npm test`

