# AGENTS

This file is directory-local guidance for coding agents working in `src/rlm_rs.egg-info/`.

## What lives here

This directory contains **setuptools-generated packaging metadata** (e.g. `PKG-INFO`, `SOURCES.txt`, `requires.txt`).

In many Python repos, `*.egg-info` directories are build artifacts and are not checked into source control. In this repo, we keep only `rlm_rs.egg-info/AGENTS.md` tracked as agent guidance; generated metadata files should remain untracked (see `.gitignore`).

## How it connects to the rest of the repo

- The source of truth for packaging is `pyproject.toml` (project name/version/dependencies) and the packages under `src/`.
- When the project metadata changes, these files may be regenerated/updated by packaging operations.

## Safe change guidelines

- **Do not hand-edit files here** unless you have a very specific reason and you understand the packaging implications.
- **Prefer changing `pyproject.toml`** (and Python package code) and letting tooling regenerate metadata.
- If you are doing repository hygiene work, avoid committing generated `*.egg-info` metadata. Prefer changing `pyproject.toml` and letting tooling regenerate locally.

## Useful commands

Most work should happen outside this folder:

- Sync deps and run code/tests:
  - `uv sync`
  - `uv run pytest -q`
