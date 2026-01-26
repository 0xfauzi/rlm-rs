# AGENTS

This file is directory-local guidance for coding agents working in `tests/unit/__pycache__/`.

## What lives here

Generated Python bytecode caches (`*.pyc`) created when running unit tests.

In this repo, we keep `__pycache__/AGENTS.md` tracked so agents have clear guidance if a tool or workflow navigates into a cache directory. The cache files themselves should remain untracked (see `.gitignore`).

## Safe change guidelines

- **Do not edit anything here.**
- Safe to delete at any time.
- If you see cache files (for example `*.pyc`) in a git diff, treat it as accidental and remove them.
