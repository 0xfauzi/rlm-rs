# AGENTS

This file is directory-local guidance for coding agents working in `src/rlm_rs/parser/`.

## What lives here

This package implements the **parser service** and its client/models. The parser converts raw documents (e.g. PDFs, text files) into a **canonical parsed representation** stored in S3.

Key files:

- `service.py`: the parser HTTP service (FastAPI app).
- `client.py`: client used by ingestion to call the parser service.
- `models.py`: request/response models for parsing.

## How it connects to the rest of the repo

- **Ingestion** (`src/rlm_rs/ingestion/worker.py`) calls the parser service to produce:
  - canonical text
  - metadata
  - offsets/index structures
- **Sandbox** (`src/rlm_rs/sandbox/context.py`) reads canonical parsed artifacts from S3 for deterministic slicing and span logging.
- **Citations** (`src/rlm_rs/orchestrator/citations.py`) rely on canonical text + offsets to compute checksums and verify SpanRefs.
- **Storage** (`src/rlm_rs/storage/s3.py`) provides S3 access patterns used by ingestion and sandbox.

## Determinism requirements (very important)

The parsed output is part of the system’s “ground truth.” Changes here can invalidate citations and break reproducibility:

- Output text must be stable for the same input.
- Offsets must correspond exactly to the output text.
- Any checksum or normalization logic must be consistent across environments.

If you change parsing behavior, expect to update tests and validate end-to-end flows.

## Safe change guidelines

- **Keep parsing pure and side-effect-free** (beyond writing outputs): avoid depending on external services.
- **Be explicit about MIME types and supported formats.**
- **Treat `models.py` as an API contract** between ingestion and parser; update both ends together.

## Useful commands

- Run the parser service locally:
  - `uv run uvicorn rlm_rs.parser.service:app --host 0.0.0.0 --port 8081`
- Run parser-related tests:
  - `uv run pytest -q tests/unit/test_parser_service.py`

