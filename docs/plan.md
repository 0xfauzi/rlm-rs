# RLM-RS Monorepo Implementation Plan (uv + Docker)

This plan implements `rls_spec.md` (Recursive Language Model Runtime Service, v1.3) as a practical, production-minded runtime for the "Recursive Language Models" paper (`rls_paper_pdf.pdf`, Khattab et al., 2025).

Core paper idea preserved: treat long prompts and corpora as an external environment the model can inspect and transform with code, and use recursive LLM calls only when semantic judgment is needed.

Core production corrections preserved (from the spec):
- Sandbox step execution is short-lived and has no provider secrets or direct network access.
- State is JSON-only; large blobs are stored in S3 via pointers.
- Citations are produced from runtime span logging, not by parsing generated code.

## Paper alignment checklist (RLM paper -> RLM-RS)

This plan implements the paper’s core RLM abstraction, with production-oriented adaptations from the spec:
- **Paper: prompt lives in a REPL environment**: RLM-RS exposes `context` as a `ContextView` of `DocView`s backed by canonical parsed text in S3 (lazy reads, not loaded into the LLM context window).
- **Paper: recursive calls via `llm_query` inside the REPL**: RLM-RS uses `tool.queue_llm(...)` to emit a tool request; the orchestrator resolves it out-of-sandbox and writes results into `state["_tool_results"]`.
- **Paper: iterative interaction with environment**: RLM-RS replaces a long-lived REPL with step-based execution (Lambda-compatible) plus persisted JSON state between steps.
- **Paper: heavy-tail trajectory costs**: RLM-RS treats budgets, strict limits, and caching as mandatory, not optional.
- **Paper: subcalls are helpful but can explode**: RLM-RS enforces per-step and total caps; prompts encourage batching and selective subcalls.

## Scope and deliverables

By following this plan, you end up with:
- A single Python monorepo managed with `uv`, runnable locally and in Docker.
- Services:
  - HTTP API (FastAPI) implementing the spec’s `/v1/...` endpoints.
  - Orchestrator worker for Answerer mode (managed loop).
  - Ingestion worker to parse documents via a parser service contract.
  - Sandbox step runtime (AWS Lambda compatible), plus a local equivalent.
  - Optional MCP server wrapping the HTTP API.
- Local dev environment using Docker Compose with LocalStack for AWS primitives (S3 + DynamoDB, optionally Lambda/SQS).
- A minimal local parser service (so the full pipeline can run end-to-end without external dependencies).
- Unit + integration tests that verify safety boundaries, determinism, and core flows (including citations).

Non-goals for v1:
- Multi-tenant adversarial isolation (the spec explicitly calls out a stronger boundary if tenants are adversarial).
- A UI.
- Full search integration (optional and gated behind an interface and feature flag).

## Key decisions (make once, early)

1) **Local AWS emulation**
- Recommended: LocalStack (S3 + DynamoDB; optionally Lambda/SQS).
- Alternatives: MinIO (S3) + DynamoDB Local (DDB) + direct in-process sandbox runner.

2) **LLM provider integration**
- Recommended initial: OpenAI SDK (fits the spec’s `gpt-5` / `gpt-5-mini` examples).
- Alternative: LiteLLM as a provider-agnostic shim (good if you plan to swap providers often).

3) **MCP server**
- If you already have an MCP client workflow: implement MCP server now.
- If not: keep MCP as optional and ship HTTP first.
- If unsure what library to use: choose one and lock it early (see Step 16).

## Default SDKs and libraries (v1)

These are the default choices this plan assumes, documented again in the specific steps where they are used.

- Packaging and environments: `uv` (Steps 01, 15)
- Schemas and validation: `pydantic` v2 (Step 02)
- HTTP services: `FastAPI` + `uvicorn` (Steps 06, 10)
- Settings: `pydantic-settings` (Step 03)
- AWS primitives: `boto3` (Steps 04, 05, 11, 12, 18)
- Local AWS: `LocalStack` + `docker compose` (Step 04)
- HTTP clients: `httpx` (Steps 06, 16)
- Retries/backoff: `tenacity` (Steps 06, 12)
- Structured logs: `structlog` (Step 03)
- Metrics and tracing: `prometheus-client` + `opentelemetry-sdk` (Steps 03, 17)
- Tests: `pytest` (+ `pytest-asyncio` when needed), LocalStack-backed integration tests; optional `moto` for pure unit tests (Steps 01, 14)
- LLM provider: official `openai` Python SDK behind a provider interface; optional future adapter for `litellm` if multi-provider becomes important (Step 12)
- Local dev cache (optional): `diskcache`, while keeping S3 as the authoritative cache store (Step 12)
- Parsing: `pdfminer.six` (PDF) + `trafilatura` (HTML) + `python-docx` (DOCX) (Step 06)
- Optional search: OpenSearch + `opensearch-py` (recommended if you need a production-aligned local story); alternative lightweight local-only option is SQLite FTS5 (Step 18)

## Monorepo structure (recommended)

Single `uv` project with a single importable package, multiple entrypoints:

```
.
├─ pyproject.toml
├─ uv.lock
├─ src/rlm_rs/
│  ├─ api/                 # FastAPI app + routers
│  ├─ orchestrator/        # Answerer loop + tool resolution
│  ├─ ingestion/           # ingestion worker logic
│  ├─ sandbox/             # AST policy + ContextView/DocView + step runner
│  ├─ storage/             # DynamoDB + S3 + state offload
│  ├─ llm/                 # provider adapters + caching helpers
│  ├─ parser/              # parser client + local parser service
│  ├─ mcp/                 # optional MCP server wrapper
│  ├─ models.py            # Pydantic models (requests/responses + internal schemas)
│  ├─ errors.py            # error codes + exception mapping
│  ├─ settings.py          # env-driven config
│  └─ logging.py           # structured logging + tracing setup
├─ docker/
│  ├─ api.Dockerfile
│  ├─ worker.Dockerfile
│  └─ parser.Dockerfile
├─ compose.yaml
├─ scripts/
│  ├─ localstack_init.sh
│  └─ smoke_test.sh
└─ tests/
   ├─ unit/
   └─ integration/
```

Why single-package: it keeps `uv` setup and Docker builds simple while still supporting multiple deployable processes.

## Detailed build plan (each step includes verification)

### Step 01: Bootstrap the uv project and tooling

**Goal**
- Create a reproducible Python 3.11+ project using `uv` and establish baseline tooling (lint, format, tests).

**Work**
- Initialize `pyproject.toml` for a `src/` layout package named `rlm_rs`.
- Add dev tooling:
  - `ruff` (lint + format)
  - `pytest` (+ `pytest-asyncio` if using async tests)
  - `mypy` (optional but recommended for a service with many schemas)
- Rationale:
  - `ruff` replaces multiple tools (fast linting + formatting).
  - `pytest` is the most widely used Python test runner and integrates well with async and HTTP testing.
  - `mypy` is the simplest way to keep service boundaries and schemas from drifting as the codebase grows.
- Add basic CI-friendly scripts (as `pyproject.toml` scripts or `make` targets).

**Verification**
- `uv --version`
- `uv sync`
- `uv run python -c "import rlm_rs; print('ok')"`
- `uv run ruff check .`
- `uv run pytest -q`

---

### Step 02: Define core schemas and error model from the spec

**Goal**
- Implement the spec’s contracts as Pydantic models and shared types, before writing services.

**Work**
- Create `pydantic` v2 models for:
  - Budgets and limits snapshot (`Budgets`, `LimitsSnapshot`)
  - Tool requests (`LLMToolRequest`, `SearchToolRequest`, `ToolRequestsEnvelope`)
  - Step input/output (`StepEvent`, `StepResult`)
  - Span log entries (`SpanLogEntry`)
  - SpanRef (`SpanRef`) + citation verify request/response
  - HTTP API request/response bodies for sessions/executions/runtime
- Implement the spec’s error envelope and standard error codes in `rlm_rs/errors.py`.
- Implement `raise_http_error(code, message, details)` helper to ensure consistent responses.
- Rationale:
  - `pydantic` gives strict runtime validation at service boundaries and can generate JSON schema for docs and debugging.

**Verification**
- `uv run python -c "from rlm_rs.models import StepEvent, StepResult; print(StepEvent.model_json_schema()['title'])"`
- `uv run pytest -q tests/unit -k models`

---

### Step 03: Implement settings and structured logging

**Goal**
- Centralize configuration and make logs usable in Docker and production.

**Work**
- Add `rlm_rs/settings.py` using `pydantic-settings`:
  - AWS region, DDB prefix, S3 bucket, LocalStack endpoint override
  - Parser service URL
  - LLM provider selection and secrets wiring (orchestrator-only)
  - Default budgets/models JSON blobs
  - Feature flags: enable_search, enable_mcp, enable_trace_redaction
- Add `rlm_rs/logging.py` with:
  - JSON logs using `structlog`, with `request_id`, `tenant_id`, `session_id`, `execution_id`
  - Optional tracing via `opentelemetry-sdk` (safe to no-op if not configured)
- Rationale:
  - `pydantic-settings` prevents config drift across local, Docker, and production.
  - `structlog` makes structured JSON logging consistent across processes.
  - OpenTelemetry keeps you provider-agnostic for traces (and can be disabled cleanly).

**Verification**
- `uv run python -c "from rlm_rs.settings import Settings; s=Settings(); print('loaded', bool(s.aws_region))"`
- Start any service and confirm logs are JSON and include request IDs (after Step 09).

---

### Step 04: Local infrastructure with Docker Compose (LocalStack)

**Goal**
- Provide a local environment that matches production primitives: S3 + DynamoDB (and optionally Lambda).

**Work**
- Add `compose.yaml` with:
  - `localstack` configured for `s3,dynamodb` (and optionally `lambda,sqs` later)
  - `rlm-api`, `rlm-orchestrator-worker`, `rlm-ingestion-worker`, `rlm-parser`
- Add `scripts/localstack_init.sh` to:
  - Create the S3 bucket
  - Create DynamoDB tables from the spec (`rlm_sessions`, `rlm_documents`, `rlm_executions`, `rlm_execution_state`, `rlm_api_keys`, `rlm_audit_log`)
- Ensure services can use LocalStack endpoints via env vars.
- Rationale:
  - `LocalStack` is the least-effort way to run S3 + DynamoDB locally with AWS-compatible APIs (no bespoke mocks for core flows).
  - Using `awslocal` keeps the init scripts close to real AWS CLI usage.

**Verification**
- `docker compose up -d localstack`
- `docker compose exec -T localstack awslocal s3 ls`
- `docker compose exec -T localstack awslocal dynamodb list-tables`
- `docker compose run --rm localstack-init` (if you implement init as a one-shot container)
- Re-run `list-tables` and confirm required tables exist.

---

### Step 05: Storage layer (DynamoDB + S3) and state offload policy

**Goal**
- Implement the spec’s storage model and the JSON-only state policy (inline vs S3 blob).

**Work**
- Create `rlm_rs/storage/ddb.py` using `boto3` (thin typed wrappers, no ORM):
  - CRUD for sessions, documents, executions, execution_state
  - Conditional update helpers for status transitions
  - A simple lease/lock mechanism for workers (to avoid two orchestrators advancing the same execution)
- Create `rlm_rs/storage/s3.py` using `boto3`:
  - Get/put helpers, range reads, gzip put/get utilities
  - Deterministic JSON serialization helper for checksums
- Create `rlm_rs/storage/state.py` implementing:
  - JSON validation (reject non-JSON types) per spec 10.1
  - Inline storage cutoff and S3 offload (spec 10.2)
  - Stored checksum and summary
- Rationale:
  - `boto3` is the most compatible choice for LocalStack and AWS.
  - A thin wrapper keeps DynamoDB key design and conditional writes explicit (and easier to audit than an ORM).

**Verification**
- Unit:
  - `uv run pytest -q tests/unit -k state_json_validation`
- Integration (with LocalStack running):
  - `uv run pytest -q tests/integration -k ddb_s3_roundtrip`

---

### Step 06: Minimal parser service + parser client

**Goal**
- Make the system runnable end-to-end locally by implementing the parser service contract from spec §14.

**Work**
- Implement `rlm_rs/parser/service.py` (FastAPI + `uvicorn`) with `POST /parse`:
  - Read raw object from S3 (LocalStack in dev)
  - Produce canonical UTF-8 `text.txt` with deterministic line endings
  - Produce `meta.json` with page spans where possible
  - Produce `offsets.json` checkpoints for mapping char offsets to byte offsets
  - Compute and return `text_checksum`
- Implement `rlm_rs/parser/client.py` using `httpx` (+ `tenacity` for retries/timeouts) for ingestion worker calls.

**Recommended libraries (v1)**
- PDF: `pdfminer.six` (more controllable extraction, tends to be more reproducible for canonical text)
  - Alternative: `pypdf` (simpler, but extraction quality varies more by document)
  - Alternative: `PyMuPDF` (often high quality, but adds another native dependency)
- DOCX: `python-docx`
- HTML: `trafilatura`
- OCR: keep optional behind a flag; local option is `pytesseract`, production option is AWS Textract (only if you decide you need OCR coverage)

Rationale: the parser is the source of truth for canonical text, so we prefer libraries that are easy to run deterministically and consistently across local and Docker environments.

**Verification**
- Start parser service: `docker compose up -d rlm-parser`
- Upload a small PDF to LocalStack S3, then call:
  - `curl -sS -X POST http://localhost:<parser_port>/parse -d '<json>' | jq .status`
- Confirm S3 now contains `parsed/.../text.txt` and that repeated parses for the same input are byte-identical.

---

### Step 07: Sandbox runtime primitives (AST policy, ContextView/DocView, ToolAPI)

**Goal**
- Implement the paper’s REPL-like environment safely, matching the spec’s security and execution model.

**Work**
- Implement `rlm_rs/sandbox/ast_policy.py` using stdlib `ast`, per Appendix B:
  - Reject imports, globals/nonlocal, dunder attribute access, banned names/modules
- Implement `rlm_rs/sandbox/context.py`:
  - `ContextView` exposing `len(context)` and `context[i] -> DocView`
  - `DocView.__getitem__(slice)` and `DocView.slice(a,b,tag)`:
    - range-read from canonical `text.txt` using `offsets.json`
    - log span entries (`doc_index`, `start_char`, `end_char`, `tag`)
  - Optional helpers `find` and `regex` implemented without exposing `re` imports to model code (helpers live in sandbox implementation and can use stdlib `re`)
- Implement `rlm_rs/sandbox/tool_api.py`:
  - `queue_llm`, `queue_search`, `YIELD`, `FINAL`
  - Enforce max tool requests per step and normalized request schemas
- Rationale:
  - The spec’s AST policy is specific; implementing it directly with stdlib `ast` keeps it auditable and predictable.

**Verification**
- Unit:
  - `uv run pytest -q tests/unit -k ast_policy`
  - `uv run pytest -q tests/unit -k span_logging`
- Quick local smoke:
  - Run a sandbox step against a parsed doc and confirm `doc[0:50]` returns text and produces a span log entry.

---

### Step 08: Sandbox step executor (Lambda-compatible handler)

**Goal**
- Implement the spec’s step contract (§6.1) including output truncation and limits enforcement.

**Work**
- Implement `rlm_rs/sandbox/step_executor.py`:
  - Parse and validate the `code` field (enforce the single ```repl ...``` block rule for Answerer-mode root outputs)
  - Apply AST policy before execution
  - Execute with restricted globals:
    - allowed builtins only (no `open`, no `__import__`, no `eval`, no `exec`)
    - injected `context`, `state`, `tool`
  - Capture stdout (for example via `io.StringIO` + `contextlib.redirect_stdout`), enforce truncation, enforce span/tool/state limits
  - Return `StepResult` with `success/stdout/state/span_log/tool_requests/final/error`
- Implement `rlm_rs/sandbox/lambda_handler.py` that converts Lambda event <-> `StepEvent/StepResult`.
- Implement a local adapter that calls the same executor directly for dev (so local runs do not depend on Lambda).
- Rationale:
  - Keeping one step executor and two adapters (Lambda and local) avoids duplicating sandbox behavior across environments.

**Verification**
- Unit:
  - `uv run pytest -q tests/unit -k step_executor_limits`
- Integration:
  - With LocalStack + parsed doc, execute a simple step that:
    - reads a span
    - queues one LLM request
    - yields
  - Confirm `tool_requests.llm[0].key` is present and `final.is_final` is false.

---

### Step 09: Citation system (SpanRefs, checksums, verify)

**Goal**
- Produce stable, verifiable citations from span logs per spec §9.

**Work**
- Implement `rlm_rs/orchestrator/citations.py`:
  - span merging and deduplication
  - checksum computation using stdlib `unicodedata.normalize("NFC", ...)` + UTF-8 encode + `hashlib.sha256`
- Implement `POST /v1/spans/get` and `POST /v1/citations/verify` behaviors (wired in Step 10).

**Verification**
- Unit:
  - `uv run pytest -q tests/unit -k checksum_determinism`
- Integration:
  - Slice a span, compute SpanRef, verify it through the verify function and confirm `valid=true`.

---

### Step 10: HTTP API service (FastAPI) implementing spec §16

**Goal**
- Implement the public API exactly as specified, including runtime mode endpoints.

**Work**
- Add FastAPI app (run with `uvicorn`) with routers, using the `pydantic` request/response models from Step 02:
  - Sessions: `POST /v1/sessions`, `GET /v1/sessions/{session_id}`, `DELETE /v1/sessions/{session_id}`
  - Executions (Answerer): `POST /v1/sessions/{session_id}/executions`, `GET /v1/executions/{execution_id}`, `POST /v1/executions/{execution_id}/wait`
  - Runtime mode: `POST /v1/sessions/{session_id}/executions/runtime`, `POST /v1/executions/{execution_id}/steps`, `POST /v1/executions/{execution_id}/tools/resolve`
  - Spans/citations: `POST /v1/spans/get`, `POST /v1/citations/verify`
  - Health: `/health/live`, `/health/ready`
- Implement auth:
  - `Authorization: Bearer rlm_key_...`
  - Map API key to `tenant_id` via `rlm_api_keys` table
  - Store only a keyed hash of the API key (HMAC-SHA256 with a server-side pepper) and use `hmac.compare_digest` for constant-time comparison
- Ensure all storage access checks tenant ownership (spec §18.1).
- Rationale:
  - `FastAPI` + `pydantic` keeps the HTTP surface strongly typed and close to the spec’s schemas.
  - HMAC-hashed API keys avoid storing raw secrets while staying simple for local and production.

**Verification**
- Run API: `docker compose up -d rlm-api`
- `curl -sS http://localhost:<api_port>/health/live | jq -r .status` returns `ok`
- Unauthorized requests return 401 with the spec error envelope.

---

### Step 11: Ingestion worker (parse pipeline)

**Goal**
- Implement session readiness gating by parsing registered docs into canonical text + offsets + meta.

**Work**
- Implement ingestion worker as a long-running process (container) using:
  - `boto3` for DynamoDB polling and S3 reads/writes
  - `httpx` for calling the parser service
  - `tenacity` for retry/backoff on transient parser or AWS errors
- Implement ingestion worker loop:
  - Poll for documents with `ingest_status=REGISTERED|PARSING` and sessions in `CREATING`
  - Call parser service for each doc
  - Write pointers and checksums to `rlm_documents`
  - Set `ingest_status=PARSED` (and `INDEXED` later if search enabled)
  - Mark session `READY` based on readiness_mode (STRICT/LAX)
- Ensure idempotency (retries do not corrupt state).
- Rationale:
  - A simple poller is enough for v1 and is easy to run locally; if you later want a queue, swap the polling source to SQS (still via `boto3` and LocalStack).

**Verification**
- Local smoke:
  - Upload one small doc to raw S3.
  - `POST /v1/sessions` referencing that S3 URI.
  - Wait until `GET /v1/sessions/{id}` returns `READY` and doc has `text_s3_uri`.

---

### Step 12: Orchestrator worker (Answerer mode loop + tool resolver + caching)

**Goal**
- Implement the managed RLM loop (Appendix A) with budgets, caching, and strict root protocol parsing.

**Work**
- Implement:
  - Root prompt builder using spec Appendix D (subcalls enabled/disabled toggle)
  - Root output parser:
    - accept only one ` ```repl ... ``` ` block
    - reject any output outside the block
  - Step invocation via sandbox adapter (local) or AWS Lambda (production config)
  - Tool resolution:
    - LLM subcalls resolved in orchestrator, not sandbox
    - implement an LLM provider interface with an `OpenAIProvider` backed by the official `openai` Python SDK
    - keep the provider interface narrow so adding a `LiteLLMProvider` later is a small change
    - use `tenacity` to apply consistent retry and timeout policies for provider calls
    - caching in S3 using stable hash keys (spec §12.3)
    - optional local-only cache: `diskcache` to speed up development while keeping S3 as the source of truth
  - Budgets enforcement:
    - per-step limits enforced by sandbox step executor
    - total limits enforced by orchestrator (turns, time, total subcalls, total prompt chars)
- Provide an LLM provider abstraction and a `FakeLLMProvider` for tests.
- Rationale:
  - Using the official provider SDK reduces protocol and auth edge cases.
  - `tenacity` avoids hand-rolled retry logic and makes tail behavior easier to tune and test.
  - S3-backed caching is simple, portable, and matches the spec; `diskcache` is only a dev acceleration.

**Verification**
- Unit:
  - `uv run pytest -q tests/unit -k root_output_parser`
- Integration (no external LLM required):
  - Use `FakeLLMProvider` to return deterministic ` ```repl ...``` ` code.
  - Execute a full Answerer run that terminates with `tool.FINAL(...)`.
  - Confirm execution record is `COMPLETED` with citations present.

---

### Step 13: End-to-end local smoke test script

**Goal**
- Provide a single command that proves the full system works locally on a tiny corpus before scaling.

**Work**
- Add `scripts/smoke_test.sh` that:
  1. Starts Compose dependencies (LocalStack + parser + API + workers)
  2. Uploads a small fixture doc to raw S3
  3. Creates a session and waits for READY
  4. Starts an execution in Answerer mode using `FakeLLMProvider` (or real provider if configured)
  5. Polls until completion and verifies citations via `/v1/citations/verify`

**Verification**
- `bash scripts/smoke_test.sh`
- Script exits 0 and prints the final answer and `valid=true` citation checks.

---

### Step 14: Test suite hardening (safety, determinism, robustness)

**Goal**
- Prevent regressions in the safety boundary and citation correctness.

**Work**
- Add tests for:
  - AST rejection: imports, dunder access, banned builtins, banned names
  - Infinite loops hit the sandbox line/instruction limit
  - Stdout truncation and max spans per step enforced
  - State JSON validation rejects non-JSON types and oversize states follow offload policy
  - Citation checksum mismatch detection
- Test tooling choices:
  - Use `pytest` as the primary runner.
  - Prefer LocalStack-backed integration tests for real DynamoDB/S3 behavior.
  - Optional: use `moto` for fast unit tests where full LocalStack is unnecessary.

**Verification**
- `uv run pytest -q`

---

### Step 15: Docker images and runtime configuration

**Goal**
- Build production-like images for each process and make local config explicit.

**Work**
- Add Dockerfiles:
  - `docker/api.Dockerfile` (FastAPI)
  - `docker/worker.Dockerfile` (orchestrator + ingestion, selectable by env)
  - `docker/parser.Dockerfile` (minimal parser service)
- Use `uv sync --frozen` in Docker builds.
- Add `.env.example` documenting all env vars and defaults.

**Verification**
- `docker compose build`
- `docker compose up -d`
- `curl -sS http://localhost:<api_port>/health/ready | jq -r .status` returns `ok`

---

### Step 16: Optional MCP server wrapper

**Goal**
- Provide MCP tools that map 1:1 to the HTTP API (spec §17).

**Work**
- Choose an MCP implementation approach:
  - Recommended: the official MCP Python SDK (if it meets your needs), because it avoids re-implementing the protocol surface.
  - Fallback: a minimal MCP-compatible server that forwards to the HTTP API using `httpx` (keeps MCP as a thin wrapper, per spec §17).
- Implement tools:
  - `rlm_create_session`, `rlm_get_session`, `rlm_delete_session`
  - `rlm_start_execution`, `rlm_get_execution`, `rlm_wait_execution`
  - `rlm_runtime_create_execution`, `rlm_runtime_step`, `rlm_resolve_tools`
  - `rlm_get_span`, `rlm_verify_citation`
Rationale: MCP should remain a transport adapter. The authoritative logic stays in the HTTP API and orchestrator.

**Verification**
- Run MCP server locally and call at least:
  - create session
  - get execution
  - verify citation
  using your MCP client (IDE/Claude) against the local API.

---

### Step 17: Production readiness checklist (practical, not theoretical)

**Goal**
- Prepare for real deployments while preserving the spec’s security boundary.

**Work**
- Add rate limiting (per-tenant) and request size limits:
  - Rate limiting: use `limits` (and optionally `slowapi` for FastAPI integration). Use an in-memory limiter for local dev; for production, switch the storage backend to Redis or enforce limits at the edge (API Gateway/WAF) and keep app limits as a backstop.
  - Request size limits: add a small FastAPI/Starlette middleware (no extra dependency required).
- Add trace redaction controls (`return_trace` and `redact_trace`).
- Confirm secrets boundaries:
  - API and workers have no sandbox secrets
  - Orchestrator is the only component that can reach LLM providers
- Add S3 lifecycle + DynamoDB TTL guidance to docs (what keys expire, when).
- Observability wiring:
  - Metrics: `prometheus-client` (for example expose `/metrics` on internal ports).
  - Tracing: `opentelemetry-sdk` (exporter configured by env, safe to disable).
- Add a load-test harness that runs the smoke test repeatedly and records:
  - budgets consumed distribution
  - cache hit rates
  - execution duration
  (Do not pick final budget defaults without measurement.)

**Verification**
- `uv run pytest -q`
- `docker compose up -d` then run `scripts/smoke_test.sh` in a loop (small scale first).

---

### Step 18: Optional search integration (accelerator, not truth source)

**Goal**
- Implement spec §15 so `tool.queue_search(...)` can accelerate candidate-finding, without changing the citation truth model (truth still comes from span slicing + checksums).

**Work**
- Define a `SearchBackend` interface used by:
  - Ingestion worker for indexing
  - Orchestrator tool resolver for `search` requests
- Pick a backend (local + production story):
  - Recommended (production-aligned): OpenSearch + `opensearch-py` (runs in Docker locally, deployable in AWS)
  - Lightweight local-only alternative: SQLite FTS5 for lexical search (fast to ship, but not a great prod match)
  - Vector option: Qdrant (Docker-friendly, production-capable) plus an embedding provider
- Implement chunking (spec suggests ~1k-4k chars + overlap) and store `doc_index/start_char/end_char` with each chunk.
- Wire readiness gating:
  - `readiness_mode=LAX`: READY after PARSED
  - `readiness_mode=STRICT`: READY after INDEXED
- Implement search caching in S3 (spec §12.3) and ensure results include candidate spans.

**Verification**
- With search enabled in config:
  - Create a session with `options.enable_search=true`.
  - Confirm docs transition `PARSED -> INDEXING -> INDEXED` and session becomes READY per readiness_mode.
  - Execute a runtime step that calls `tool.queue_search` and verify returned hits are plausible, then re-slice cited spans and verify checksums.

---

## Scaling guidance (aligns with the paper’s heavy-tail behavior)

1) Validate the entire pipeline on a single tiny document first (Step 13).
2) Scale only one parameter at a time (number of docs, doc size, max turns, subcall budget).
3) Track tail latencies and costs. Do not set “final” budget defaults without measuring real trajectories for your workloads.
