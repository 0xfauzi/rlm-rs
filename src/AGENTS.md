# AGENTS

This file is directory-local guidance for coding agents working in `src/`.

## What lives here

This directory is the **Python package root** (configured by `pyproject.toml` as `package-dir = {"" = "src"}`):

- `rlm_rs/`: The actual runtime service implementation package.
- `rlm_rs.egg-info/`: Packaging metadata directory. Only `rlm_rs.egg-info/AGENTS.md` is tracked as agent guidance; generated metadata files should remain untracked (see `.gitignore`).

## How it connects to the rest of the repo

- `pyproject.toml` declares dependencies and points setuptools at this directory.
- The entrypoints/services are implemented under `src/rlm_rs/**` and run via:
  - `uv run uvicorn rlm_rs.api.app:app` (API)
  - `uv run uvicorn rlm_rs.parser.service:app` (parser service)
  - `uv run python -m rlm_rs.worker_entrypoint` (workers)
  - `uv run python -m rlm_rs.mcp` (MCP server wrapper)
- Tests import from this package; keep public API stable where reasonable.

## Safe change guidelines

- **Use `uv` for all Python commands** in this repo; do not introduce `requirements.txt`.
- **Prefer adding new modules under `src/rlm_rs/`** rather than top-level scripts.
- **If you change project dependencies or Python requirements**, update `pyproject.toml` and then re-run `uv sync`.
- **Be careful with generated artifacts**:
  - `__pycache__/` and `*.pyc` should not be committed.
  - `rlm_rs.egg-info/` is generated; avoid hand-editing and avoid committing metadata files.

## Useful commands

- Sync dependencies:
  - `uv sync`
- Run tests:
  - `uv run pytest -q`
- Lint:
  - `uv run ruff check .`
