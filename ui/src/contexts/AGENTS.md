# AGENTS

This file is directory-local guidance for coding agents working in `ui/src/contexts/`.

## What lives here

React Context providers and hooks for sharing app-wide state:

- `AppContext.tsx`: app-level configuration/state shared across routes.
- `ToastContext.tsx`: toast state/dispatch for notifications.

## How it connects to the rest of the repo

- Context state is consumed by components under `ui/src/app/**` and `ui/src/components/**`.
- Toast/error context should surface backend API failures clearly (backend is in `src/rlm_rs/api/**`).

## Safe change guidelines

- Keep context values stable and well-typed; avoid “god contexts” that accumulate unrelated state.
- Prefer colocating state with routes/components unless truly global.
- Avoid storing secrets in client-side context.

## Useful commands

- Typecheck after changing context types:
  - `npm run typecheck`

