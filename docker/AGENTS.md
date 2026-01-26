# AGENTS

This file is directory-local guidance for coding agents working in `docker/`.

## What lives here

This directory contains the Docker build definitions for the services orchestrated by `compose.yaml`:

- `api.Dockerfile`: Image for the FastAPI HTTP API (`src/rlm_rs/api`).
- `worker.Dockerfile`: Image for background workers (`src/rlm_rs/ingestion`, `src/rlm_rs/orchestrator`), selected via `WORKER_MODE`.
- `parser.Dockerfile`: Image for the parser service (`src/rlm_rs/parser/service.py`).
- `ui.Dockerfile`: Image for the Next.js UI (`ui/`).

## How it connects to the rest of the repo

- `compose.yaml` chooses which Dockerfile to build for each service and wires up ports and environment variables.
- Python images should stay aligned with `pyproject.toml` (dependencies) and `src/rlm_rs` code.
- The UI image uses `ui/package.json` and `ui/next.config.js`; `compose.yaml` passes build args (e.g. API proxy targets) that must match how the UI proxies API/LocalStack traffic.

## Safe change guidelines

- **Prefer changing application behavior in code first** (`src/rlm_rs/**` or `ui/**`), and only adjust Dockerfiles when you truly need to.
- **When you add a new env var used by a service**, update the triangle:
  - `src/rlm_rs/settings.py`
  - `.env.example`
  - `compose.yaml`
  Dockerfiles usually do not need changes unless you change runtime expectations.
- **Keep the sandbox boundary intact**: the sandbox step executor is *not* built/run from these Dockerfiles; avoid “helpful” additions that would blur the orchestrator vs sandbox separation.
- **Avoid pinning OS packages unless required**; prefer Python/Node dependencies managed via `pyproject.toml` and `ui/package-lock.json`.

## Useful commands

- Build and run the full stack (LocalStack + API + parser + workers + UI):
  - `docker compose up --build`
- Rebuild a single service after Dockerfile changes:
  - `docker compose build rlm-api` (or `rlm-parser`, `rlm-orchestrator-worker`, `rlm-ingestion-worker`, `rlm-ui`)
- Inspect failures:
  - `docker compose logs -f rlm-api` (or the relevant service)

