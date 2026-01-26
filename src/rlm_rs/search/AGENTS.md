# AGENTS

This file is directory-local guidance for coding agents working in `src/rlm_rs/search/`.

## What lives here

This package contains the **optional search subsystem**, used for retrieval over ingested corpora when enabled.

Key files (by name):

- `backends.py`: search backend interfaces/implementations (pluggable).
- `indexing.py`: indexing helpers used during ingestion.
- `__init__.py`: package marker.

## How it connects to the rest of the repo

- **Ingestion** (`src/rlm_rs/ingestion/**`) may call indexing helpers to populate a search backend after parsing.
- **Orchestrator** (`src/rlm_rs/orchestrator/**`) may issue search queries as part of tool resolution.
- **Settings** (`src/rlm_rs/settings.py`) and `compose.yaml` control whether search is enabled by default (`ENABLE_SEARCH_DEFAULT`).
- The sandbox must never call search directly; search requests must be mediated by the orchestrator.

## Safe change guidelines

- **Keep the backend interface stable and minimal.**
  - It should be easy to swap implementations (or disable entirely) without affecting core execution/citation logic.
- **Cache-aware design**:
  - Orchestrator caches search results in S3; changes to query normalization or keying can affect reproducibility and cost.
- **Treat search as “optional enhancement.”**
  - The system must remain correct without search enabled.

## Useful commands

- Run search-related unit tests:
  - `uv run pytest -q tests/unit/test_search_backend.py`

