# AGENTS

This file is directory-local guidance for coding agents working in `src/rlm_rs/finetune/`.

## What lives here

Helpers for finetuning and evaluation workflows:

- `traces.py`: utilities for exporting execution traces and preparing finetune-ready artifacts.
- `__init__.py`: package marker.

## How it connects to the rest of the repo

- Trace exports rely on execution state and logs stored via `src/rlm_rs/storage/**`.
- Code and tool logs referenced here are written by `src/rlm_rs/code_log.py`.
- Evaluation scripts in `scripts/` use these helpers to build datasets and run analysis.

## Safe change guidelines

- Keep export formats stable; downstream eval/finetune scripts assume specific fields.
- Avoid adding runtime dependencies here unless they are required for evaluation tooling.
- Prefer deterministic outputs so dataset regeneration is reproducible.

## Useful commands

- See the finetune scripts under `scripts/` for how these helpers are invoked.

