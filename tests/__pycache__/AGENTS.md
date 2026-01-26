# AGENTS

This file is directory-local guidance for coding agents working in `tests/__pycache__/`.

## What lives here

This directory normally contains **generated Python bytecode caches** (`*.pyc`) created when running tests.

In this repo, we keep `__pycache__/AGENTS.md` tracked so agents have clear guidance if a tool or workflow navigates into a cache directory. The cache files themselves should remain untracked (see `.gitignore`).

## Safe change guidelines

- **Do not edit anything here.**
- It is always safe to delete `__pycache__/` directories.
- If you see cache files (for example `*.pyc`) in a git diff, treat it as accidental and remove them.
