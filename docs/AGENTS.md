# AGENTS

This file is directory-local guidance for coding agents working in `docs/`.

## What lives here

This directory contains **design references** and **architecture documentation**. It is the best place to understand intent before changing code:

- `rls_spec.md`: Consolidated spec for the runtime protocol and architecture.
- `component.md`: Component diagram and system boundaries.
- `sequence.md` / `runtime_sequence.md`: Step-by-step sequences for Answerer and Runtime modes.
- `runtime_examples.md`: Example flows and “what good looks like”.
- `plan.md`: Implementation plan and library choices.
- `ui_spec.md`: UI expectations and constraints.
- `fine_tuning_rlm_policy.md`: Policy and guidance for finetuning/evaluation data.
- `rls_paper_pdf.pdf`: Research reference (paper).

## How it connects to the rest of the repo

- `src/rlm_rs/**` should implement the behaviors described here:
  - API semantics: `src/rlm_rs/api/**`
  - Orchestrator behavior: `src/rlm_rs/orchestrator/**`
  - Sandbox constraints: `src/rlm_rs/sandbox/**`
  - Parser determinism: `src/rlm_rs/parser/**`
  - Storage model: `src/rlm_rs/storage/**` + `src/rlm_rs/models.py`
- The UI (`ui/`) should reflect routes and objects described in the sequences (sessions, executions, spans/citations).
- If you change runtime sequences, budgets, or invariants, **update these docs** alongside tests so the repo stays self-explanatory.

## Safe change guidelines

- **Treat `rls_spec.md` as the “contract.”** If code diverges, either fix the code or explicitly amend the spec.
- **Document “why,” not just “what.”** When changing a mechanism (e.g. citation verification, caching keys, state persistence), include the rationale and the invariants that must hold.
- **Keep diagrams in sync** with real endpoints and worker behavior. The repo has both Answerer and Runtime mode flows: update the correct one.

## Useful commands

Docs are static, but you typically edit them alongside code changes. Common companion commands:

- Run tests after behavior changes:
  - `uv run pytest -q`
- Lint Python after refactors:
  - `uv run ruff check .`
- Validate end-to-end behavior after changing contracts:
  - `scripts/smoke_test.sh`
