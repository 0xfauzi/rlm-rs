# AGENTS

This file is directory-local guidance for coding agents working in `ui/public/`.

## What lives here

Static assets served directly by Next.js (favicons, SVGs, etc.).

## How it connects to the rest of the repo

- Used by the UI route tree under `ui/src/app/**` (e.g. icons/images referenced in components).
- Does not interact with backend code directly.

## Safe change guidelines

- Prefer adding small, optimized assets.
- If you replace an asset, keep filenames stable unless you also update all references.

## Useful commands

- Validate the UI still builds after asset changes:
  - `npm run build`

