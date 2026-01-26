# AGENTS

This file is directory-local guidance for coding agents working in `src/rlm_rs/__pycache__/`.

## What lives here

This directory normally contains **generated Python bytecode caches** (`*.pyc`), created automatically when Python imports modules.

In this repo, we keep `__pycache__/AGENTS.md` tracked so agents have clear guidance if a tool or workflow navigates into a cache directory. The cache files themselves should remain untracked (see `.gitignore`).

## How it connects to the rest of the repo

- It is derived from the real source code under `src/rlm_rs/**`.
- It should not contain hand-authored logic, and changes here do not represent meaningful code changes.

## Safe change guidelines

- **Do not edit anything in this directory.**
- It is safe to delete `__pycache__/` directories at any time; Python will recreate them.
- If you see cache files (for example `*.pyc`) in a git diff, treat it as accidental and remove them.
