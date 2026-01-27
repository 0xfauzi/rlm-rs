# RLM-RS

RLM-RS is a reference implementation of the Recursive Language Model Runtime Service. It turns very large corpora into an external environment that a model can inspect with Python, while keeping provider calls and budgets in a managed orchestrator loop.

The design follows the Recursive Language Models paper and the consolidated spec in `docs/rls_spec.md`.

## Goals

- Execute model-written Python steps over corpora that exceed any single model context window.
- Keep sandbox execution isolated: no provider secrets, no direct network egress, JSON-only state.
- Support recursive subcalls through tool requests resolved by the orchestrator.
- Produce verifiable citations from runtime span logging.
- Provide predictable cost controls via budgets, limits, and caching.

## Non-goals

- A long-lived REPL server for arbitrary code.
- Untrusted multi-tenant sandboxing against adversarial code.
- A polished end-user UI (the UI in `ui/` is developer-facing).

## Provider configuration

RLM-RS supports OpenAI and Azure OpenAI through the same provider path.

- OpenAI: `LLM_PROVIDER=openai` and `OPENAI_API_KEY`.
- Azure OpenAI: `LLM_PROVIDER=azure_openai`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`, and `OPENAI_API_VERSION`.
- Model names should be deployment names when using Azure OpenAI.

## Architecture at a glance

Component diagram:

```mermaid
%%{init: {"flowchart": {"nodeSpacing": 70, "rankSpacing": 70}, "themeVariables": {"fontSize": "18px"}}}%%
flowchart LR
  %% Clients
  subgraph Clients[Clients]
    C1["Client / App"]
    C2["MCP Client (Claude/IDE)"]
  end

  %% Edge / entry
  subgraph Edge[Edge]
    ALB["ALB / API Gateway"]
  end

  %% Core service
  subgraph Core["RLM-RS Core (ECS)"]
    API["HTTP API"]
    ORCH["Orchestrator\n(Answerer loop + Tool resolver)"]
    ING["Ingestion Worker\n(Parse + Optional Index)"]
    MCP["MCP Server\n(HTTP wrapper)"]
  end

  %% Execution sandbox
  subgraph Sandbox["Sandbox Execution"]
    LAMBDA["Lambda: rlm-sandbox-step\nPython step executor"]
  end

  %% Data plane
  subgraph Storage["Storage"]
    DDB["(DynamoDB\nsessions/docs/executions/state/audit)"]
    S3["(S3\nparsed text/meta/offsets\nstate blobs\ntrace\ncache)"]
  end

  %% External deps
  subgraph External["External Services"]
    PARSER["Parser Service"]
    LLM["LLM Provider(s)"]
    SEARCH["Search Backend\n(optional)"]
    OBS["Observability\n(metrics/logs/traces)"]
  end

  %% Main flows
  C1 --> ALB --> API
  C2 --> MCP --> API
  API <--> DDB
  API --> ORCH
  ORCH <--> DDB
  ORCH <--> S3
  ORCH --> LLM
  ORCH --> SEARCH

  ING <--> DDB
  ING --> PARSER
  ING --> S3
  ING --> SEARCH

  ORCH --> LAMBDA
  LAMBDA --> S3
  LAMBDA --> ORCH

  API --> OBS
  ORCH --> OBS
  ING --> OBS
  LAMBDA --> OBS

  %% Security boundary notes
  NOTE_LAMBDA["Sandbox has NO provider secrets.\nNo direct internet egress.\nReads parsed corpus from S3 only.\nEmits tool requests; never calls LLM/Search."]
  NOTE_ORCH["Orchestrator holds provider credentials,\nenforces budgets, caching, retries,\nand persists trace/citations."]
  NOTE_LAMBDA -.-> LAMBDA
  NOTE_ORCH -.-> ORCH
```

Answerer mode sequence:

```mermaid
sequenceDiagram
  autonumber
  actor Client as Client / App
  participant API as RLM-RS API (ECS)
  participant Orch as Orchestrator (ECS)
  participant Ingest as Ingestion Worker (ECS)
  participant Parser as Parser Service
  participant DDB as DynamoDB
  participant S3 as S3 (Parsed + State + Trace + Cache)
  participant Lambda as Sandbox Step (Lambda)
  participant LLM as LLM Provider
  participant Search as Search Backend (optional)

  %% --- Session creation + ingestion ---
  Client->>API: POST /v1/sessions (docs: raw_s3_uri, options)
  API->>DDB: Create Session (CREATING) + Document records (REGISTERED)
  API-->>Client: 202 {session_id, status=CREATING}

  par Ingestion pipeline
    Ingest->>DDB: Poll sessions/docs needing parse
    Ingest->>Parser: POST /parse (raw_s3_uri, output s3_prefix)
    Parser-->>Ingest: {text_s3_uri, meta_s3_uri, offsets_s3_uri, stats, checksums}
    Ingest->>S3: Write parsed outputs (text/meta/offsets)
    Ingest->>DDB: Update doc ingest_status=PARSED (+ pointers/checksums)
    opt If search enabled
      Ingest->>Search: Index chunks (doc_id, start/end, embeddings)
      Search-->>Ingest: Indexed
      Ingest->>DDB: Update doc ingest_status=INDEXED
    end
    Ingest->>DDB: Set session READY when readiness condition met
  end

  Client->>API: GET /v1/sessions/{session_id}
  API->>DDB: Read session + doc statuses
  API-->>Client: 200 {status=READY,...}

  %% --- Answerer execution loop ---
  Client->>API: POST /v1/sessions/{session_id}/executions (question, budgets, models)
  API->>DDB: Create execution (RUNNING)
  API-->>Client: 202 {execution_id, status=RUNNING}

  loop Until FINAL or budgets exceeded
    Orch->>DDB: Load execution state + budgets snapshot
    Orch->>LLM: Call ROOT model (system prompt + question + state summary + last stdout/error)
    LLM-->>Orch: Root output (single ```repl code block)

    Orch->>Lambda: Invoke step(code, state, context_manifest, tool_results, limits)

    %% Sandbox uses doc slices; logs spans; queues tools; returns state updates
    Lambda->>S3: Lazy range-read parsed text (via offsets) as needed
    S3-->>Lambda: Text slices
    Lambda-->>Orch: {stdout, state, span_log, tool_requests, final?}

    Orch->>DDB: Persist step record + execution state pointer (inline or S3)
    opt Large state
      Orch->>S3: Write state blob state_{turn}.json.gz
      Orch->>DDB: Store state_s3_uri + checksum + summary
    end
    Orch->>S3: Append trace (or write per-turn trace)
    Orch->>DDB: Accumulate span_log stats (counts, last turn)

    alt final.is_final == true
      Orch->>S3: Load canonical text spans for checksum
      Orch->>DDB: Read doc pointers + ids
      Orch->>Orch: Merge spans + compute SpanRefs (checksums)
      Orch->>DDB: Mark execution COMPLETED + store answer + citations + trace pointer
    else tool_requests present
      opt LLM subcalls
        Orch->>S3: Check LLM cache (hash(provider,model,temp,max_tokens,prompt))
        alt cache hit
          S3-->>Orch: Cached result
        else cache miss
          Orch->>LLM: Call SUB model (temperature=0 default)
          LLM-->>Orch: Subcall result
          Orch->>S3: Write cache entry
        end
        Orch->>DDB: Update execution state _tool_results.llm[key] + _tool_status[key]
      end

      opt Search requests (if enabled)
        Orch->>S3: Check search cache
        alt cache hit
          S3-->>Orch: Cached hits
        else cache miss
          Orch->>Search: Query index (filters, k)
          Search-->>Orch: Hits (doc_index,start,end,preview)
          Orch->>S3: Write cache entry
        end
        Orch->>DDB: Update execution state _tool_results.search[key] + _tool_status[key]
      end
    else No tool requests
      Orch->>DDB: Continue loop (next root turn)
    end

  end

  Client->>API: GET /v1/executions/{execution_id}
  API->>DDB: Read execution summary
  API-->>Client: 200 {status, answer, citations(SpanRefs), trace_s3_uri}
```

Runtime mode sequence:

```mermaid
sequenceDiagram
  autonumber
  actor Client as Client / App
  participant API as RLM-RS API (ECS)
  participant Orch as Tool Resolver (ECS)
  participant DDB as DynamoDB
  participant S3 as S3 (Parsed + State + Trace + Cache)
  participant Lambda as Sandbox Step (Lambda)
  participant LLM as LLM Provider
  participant Search as Search Backend (optional)

  %% --- Precondition: Session is READY (docs parsed; search optionally indexed) ---
  Note over Client,API: Session already created + ingested. Runtime mode is client-driven stepping.

  %% --- Create runtime execution ---
  Client->>API: POST /v1/sessions/{session_id}/executions/runtime
  API->>DDB: Create execution (RUNNING, mode=RUNTIME)
  API->>DDB: Create initial execution_state (empty JSON) + budgets snapshot
  API-->>Client: 201 {execution_id, status=RUNNING}

  %% --- Client submits a step (code + optional state) ---
  loop Client drives each turn
    Client->>API: POST /v1/executions/{execution_id}/steps {code, state?, options(resolve_tools=false)}
    API->>DDB: Load latest execution_state + session manifest
    API->>Lambda: Invoke step(code, state_in, context_manifest, tool_results, limits)

    %% Sandbox reads corpus lazily
    Lambda->>S3: Lazy range-read parsed text (via offsets) as needed
    S3-->>Lambda: Text slices
    Lambda-->>API: step_out {stdout, state_out, span_log, tool_requests, final?, error?}

    API->>DDB: Persist turn record + state pointer (inline or S3)
    opt Large state_out
      API->>S3: Write state blob state_{turn}.json.gz
      API->>DDB: Store state_s3_uri + checksum + summary
    end
    API->>S3: Append trace (or write per-turn trace)

    alt final.is_final == true
      API->>S3: Load canonical text spans for checksum
      API->>DDB: Read doc ids/pointers
      API->>API: Merge spans + compute SpanRefs (checksums)
      API->>DDB: Mark execution COMPLETED + store answer + citations + trace pointer
      API-->>Client: 200 {status=COMPLETED, answer, citations, trace_s3_uri}
    else error returned
      API-->>Client: 200 {success=false, error, stdout, state, span_log}
      Note over Client: Client decides whether to retry, modify code, or cancel.
    else no final
      API-->>Client: 200 {success=true, stdout, state, span_log, tool_requests}
    end

    %% --- Client optionally asks service to resolve tools ---
    opt tool_requests present AND client wants managed resolution
      Client->>API: POST /v1/executions/{execution_id}/tools/resolve {tool_requests, models(sub_model)}
      API->>DDB: Load execution_state + budgets consumed
      API->>Orch: Validate budgets + normalize requests + compute cache keys

      opt LLM subcalls
        Orch->>S3: Check LLM cache (hash(provider,model,temp,max_tokens,prompt))
        alt cache hit
          S3-->>Orch: Cached result
        else cache miss
          Orch->>LLM: Call SUB model (temperature=0 default)
          LLM-->>Orch: Subcall result
          Orch->>S3: Write cache entry
        end
        Orch->>DDB: Persist _tool_results.llm[key] + _tool_status[key]
      end

      opt Search requests (if enabled)
        Orch->>S3: Check search cache
        alt cache hit
          S3-->>Orch: Cached hits
        else cache miss
          Orch->>Search: Query index (filters, k)
          Search-->>Orch: Hits (doc_index,start,end,preview)
          Orch->>S3: Write cache entry
        end
        Orch->>DDB: Persist _tool_results.search[key] + _tool_status[key]
      end

      API-->>Client: 200 {tool_results, statuses}
      Note over Client: Client includes tool_results in next /steps call (or reads from state if server merged).
    end

    %% --- Client can poll execution state anytime ---
    opt polling
      Client->>API: GET /v1/executions/{execution_id}
      API->>DDB: Read execution summary + pointers
      API-->>Client: 200 {status, budgets_consumed, last_turn, trace_s3_uri?}
    end
  end

  %% --- Optional: Cancel ---
  opt client cancels
    Client->>API: POST /v1/executions/{execution_id}/cancel
    API->>DDB: Mark execution CANCELLED
    API-->>Client: 200 {status=CANCELLED}
  end

```

Core services:

- API service (FastAPI): HTTP endpoints for sessions, executions, spans, and citations.
- Orchestrator worker: Answerer mode loop, tool resolution, caching, and trace persistence.
- Ingestion worker: parses documents via the parser service and writes parsed outputs.
- Sandbox step runtime: AWS Lambda compatible step executor with strict limits.
- Storage: DynamoDB for metadata and S3 for parsed text, state blobs, traces, and caches.
- UI (Next.js): developer-facing UI for sessions, executions, and citations.
- Optional services: MCP server wrapper and a search backend.

Security boundaries:

- The sandbox never calls LLM providers or search backends.
- Provider credentials live in the orchestrator process.
- State is JSON-only, with large payloads stored in S3 and referenced by pointer.

## Execution modes

Answerer mode is a managed loop:

1. Root model proposes a Python step.
2. Sandbox executes the step against a lazy ContextView of the corpus.
3. The step can enqueue tool requests.
4. Orchestrator resolves tool requests, persists state and trace, and repeats.
5. Final answer returns with SpanRef citations derived from logged spans.

Runtime mode is client-driven:

- Clients submit steps and optionally request tool resolution, while the service handles sandbox execution, state persistence, and citation verification.

## Key concepts

- Session: corpus + configuration, backed by parsed outputs in S3.
- Execution: a run against a session in Answerer or Runtime mode.
- Step: a single sandbox invocation with JSON state in and out.
- ContextView and DocView: lazy S3 range reads with span logging for citations.
- SpanRef: verifiable citation containing document id, offsets, and checksum.

## Repository layout

- `src/rlm_rs/api`: HTTP API routes and dependencies.
- `src/rlm_rs/orchestrator`: Answerer loop, provider integration, citations.
- `src/rlm_rs/ingestion`: ingestion worker for parsing and optional indexing.
- `src/rlm_rs/sandbox`: step executor, AST policy, context access.
- `src/rlm_rs/storage`: DynamoDB and S3 helpers.
- `src/rlm_rs/parser`: parser service and client.
- `src/rlm_rs/mcp`: MCP server wrapper for the HTTP API.
- `src/rlm_rs/finetune`: trace export and dataset prep helpers.
- `scripts/`: operational scripts and evaluation utilities.
- `ui/`: developer-facing UI for sessions/executions/citations.
- `docs/`: spec, architecture, and sequence diagrams.

## Local development

Prerequisites:

- Python 3.11+
- `uv`
- Docker with Docker Compose

### Option A: Docker Compose

This runs LocalStack plus API, parser, workers, and the UI.

```bash
export LLM_PROVIDER=fake
export DEFAULT_ROOT_MODEL=fake-root
docker compose up --build
```

OpenAI provider:

```bash
export LLM_PROVIDER=openai
export OPENAI_API_KEY=YOUR_OPENAI_API_KEY
export DEFAULT_ROOT_MODEL=YOUR_ROOT_MODEL
export DEFAULT_SUB_MODEL=YOUR_SUB_MODEL
# Optional: export OPENAI_BASE_URL=YOUR_BASE_URL
# Optional: export OPENAI_TIMEOUT_SECONDS=SECONDS
# Optional: export OPENAI_MAX_RETRIES=COUNT
docker compose up --build
```

You can set these in a `.env` file instead of exporting them in your shell.

Check health:

```bash
curl -fsS http://localhost:8080/health/ready
```

Open the UI at `http://localhost:3000`.

### Option B: Run services locally with uv

Use LocalStack for AWS primitives and run services with `uv`.

```bash
docker compose up -d localstack localstack-init
export AWS_REGION=us-east-1
export AWS_ACCESS_KEY_ID=test
export AWS_SECRET_ACCESS_KEY=test
export AWS_SESSION_TOKEN=test
export LOCALSTACK_ENDPOINT_URL=http://localhost:4566
export AWS_ENDPOINT_URL=$LOCALSTACK_ENDPOINT_URL
export S3_BUCKET=rlm-local
export DDB_TABLE_PREFIX=rlm
export API_KEY_PEPPER=local-pepper
export PARSER_SERVICE_URL=http://127.0.0.1:8081
```

Fake provider:

```bash
export LLM_PROVIDER=fake
export DEFAULT_ROOT_MODEL=fake-root
```

OpenAI provider:

```bash
export LLM_PROVIDER=openai
export OPENAI_API_KEY=YOUR_OPENAI_API_KEY
export DEFAULT_ROOT_MODEL=YOUR_ROOT_MODEL
export DEFAULT_SUB_MODEL=YOUR_SUB_MODEL
# Optional: export OPENAI_BASE_URL=YOUR_BASE_URL
# Optional: export OPENAI_TIMEOUT_SECONDS=SECONDS
# Optional: export OPENAI_MAX_RETRIES=COUNT
```

Run services:

```bash
uv sync
uv run uvicorn rlm_rs.parser.service:app --host 0.0.0.0 --port 8081
uv run uvicorn rlm_rs.api.app:app --host 0.0.0.0 --port 8080
```

Workers:

```bash
WORKER_MODE=ingestion uv run python -m rlm_rs.worker_entrypoint
WORKER_MODE=orchestrator uv run python -m rlm_rs.worker_entrypoint
```

### End-to-end smoke test

```bash
./scripts/smoke_test.sh
```

### Manual end-to-end (LocalStack + OpenAI): fast path

This is the minimal “real user” flow against the running stack using OpenAI. It assumes `LLM_PROVIDER=openai`, `OPENAI_API_KEY` set in `.env`, and `docker compose up --build` is running.

1) Seed an API key (matches API_KEY_PEPPER in `.env`):
```bash
API_KEY=rlm_key_local API_KEY_PEPPER=smoke-pepper TENANT_ID=tenant_local \
API_KEY_HASH=$(UV_CACHE_DIR=/tmp/uv-cache API_KEY="$API_KEY" API_KEY_PEPPER="$API_KEY_PEPPER" uv run python - <<'PY'
import hashlib, hmac, os
api_key=os.environ["API_KEY"]; pepper=os.environ["API_KEY_PEPPER"]
print(hmac.new(pepper.encode(), api_key.encode(), hashlib.sha256).hexdigest())
PY
) \
&& docker compose exec -T localstack awslocal dynamodb put-item \
  --table-name rlm_api_keys \
  --item "{\"PK\":{\"S\":\"KEY#${API_KEY_HASH}\"},\"SK\":{\"S\":\"KEY#${API_KEY_HASH}\"},\"tenant_id\":{\"S\":\"${TENANT_ID}\"}}"
```

2) Upload a sample doc to S3 (adjust content if desired):
```bash
RUN_ID=$(UV_CACHE_DIR=/tmp/uv-cache uv run python - <<'PY'
import uuid; print(uuid.uuid4().hex)
PY
)
RAW_URI="s3://rlm-local/raw/tenant_local/${RUN_ID}/sample.txt"
printf 'Answerer flow regression test.' | docker compose exec -T localstack awslocal s3 cp - "$RAW_URI" --content-type text/plain
```

3) Create a session and wait until READY:
```bash
SESSION_ID=$(curl -s -X POST http://localhost:8080/v1/sessions \
  -H "Authorization: Bearer rlm_key_local" \
  -H "Content-Type: application/json" \
  -d "{\"ttl_minutes\":60,\"docs\":[{\"source_name\":\"sample.txt\",\"mime_type\":\"text/plain\",\"raw_s3_uri\":\"${RAW_URI}\"}]}" \
  | uv run python - <<'PY'
import sys, json; print(json.load(sys.stdin)["session_id"])
PY
)
curl -s -H "Authorization: Bearer rlm_key_local" http://localhost:8080/v1/sessions/${SESSION_ID}
# expect status READY
```

4) Start an Answerer execution and poll for completion:
```bash
EXEC_ID=$(curl -s -X POST http://localhost:8080/v1/sessions/${SESSION_ID}/executions \
  -H "Authorization: Bearer rlm_key_local" \
  -H "Content-Type: application/json" \
  -d '{"question":"Summarize the document in one short sentence."}' \
  | uv run python - <<'PY'
import sys, json; print(json.load(sys.stdin)["execution_id"])
PY
)
for i in {1..30}; do
  resp=$(curl -s -H "Authorization: Bearer rlm_key_local" http://localhost:8080/v1/executions/${EXEC_ID})
  echo "$resp"
  echo "$resp" | grep '"status":"RUNNING"' >/dev/null || break
  sleep 2
done
```

5) Runtime mode quick check (optional):
```bash
RUNTIME_EXEC=$(curl -s -X POST http://localhost:8080/v1/sessions/${SESSION_ID}/executions/runtime \
  -H "Authorization: Bearer rlm_key_local" \
  -H "Content-Type: application/json" | uv run python - <<'PY'
import sys, json; print(json.load(sys.stdin)["execution_id"])
PY
)
curl -s -X POST http://localhost:8080/v1/executions/${RUNTIME_EXEC}/steps \
  -H "Authorization: Bearer rlm_key_local" \
  -H "Content-Type: application/json" \
  -d '{"code":"snippet = context[0][0:20]\ntool.FINAL(snippet)"}'
```

Debug/inspection helpers:
- `docker compose logs <service>` for API/parser/worker errors.
- `docker compose exec -T localstack awslocal s3 ls s3://rlm-local/parsed/...` and `awslocal dynamodb scan --table-name rlm_executions` to inspect artifacts.

## Finetuning and evaluation

- `docs/fine_tuning_rlm_policy.md` captures policy and data-shaping guidance.
- `scripts/export_finetune_traces.py` exports execution traces for analysis or dataset generation.
- `scripts/build_finetune_datasets.py` prepares datasets from stored traces/logs.
- `scripts/evaluate_finetuned_policy.py` evaluates finetuned policies against stored traces.
- `scripts/recompute_evaluation.py` recomputes evaluation outputs from stored artifacts.

## Authentication and API keys

The API expects a bearer token with the `rlm_key_` prefix and checks it against the `api_keys` DynamoDB table. The smoke test script seeds a local key in LocalStack and shows the expected format.

Local API key setup:

```bash
export API_KEY=rlm_key_local
export API_KEY_PEPPER=local-pepper
export TENANT_ID=tenant_local
API_KEY_HASH="$(uv run python - <<'PY'
import hashlib
import hmac
import os

api_key = os.environ["API_KEY"]
pepper = os.environ["API_KEY_PEPPER"]
digest = hmac.new(pepper.encode("utf-8"), api_key.encode("utf-8"), hashlib.sha256)
print(digest.hexdigest())
PY
)"
awslocal dynamodb put-item --table-name rlm_api_keys --item \
  "{\"PK\":{\"S\":\"KEY#${API_KEY_HASH}\"},\"SK\":{\"S\":\"KEY#${API_KEY_HASH}\"},\"tenant_id\":{\"S\":\"${TENANT_ID}\"}}"
```

If you are not using `awslocal`, use `aws --endpoint-url "$LOCALSTACK_ENDPOINT_URL"`. If you change `DDB_TABLE_PREFIX`, update the table name accordingly.

## API overview

- `POST /v1/sessions` create a session with document references.
- `GET /v1/sessions/{session_id}` session status and document readiness.
- `POST /v1/sessions/{session_id}/executions` start Answerer mode.
- `GET /v1/executions/{execution_id}` execution status and results.
- `POST /v1/sessions/{session_id}/executions/runtime` start Runtime mode.
- `POST /v1/executions/{execution_id}/steps` run a runtime step.
- `POST /v1/executions/{execution_id}/tools/resolve` resolve tool requests.
- `POST /v1/spans/get` retrieve a span and SpanRef.
- `POST /v1/citations/verify` verify a SpanRef checksum.
- `GET /health/live` and `GET /health/ready` health checks.

## MCP server

The MCP server wraps the HTTP API.

```bash
export RLM_BASE_URL=http://localhost:8080
export RLM_API_KEY=rlm_key_local
uv run python -m rlm_rs.mcp
```

## Configuration

Common environment variables:

- `AWS_REGION`, `S3_BUCKET`, `DDB_TABLE_PREFIX`
- `LOCALSTACK_ENDPOINT_URL` or `AWS_ENDPOINT_URL`
- `PARSER_SERVICE_URL`
- `API_KEY_PEPPER` for API key hashing
- `LLM_PROVIDER` with `OPENAI_API_KEY` for OpenAI or `LLM_PROVIDER=fake` for local runs
- `DEFAULT_ROOT_MODEL`, `DEFAULT_SUB_MODEL`
- `DEFAULT_BUDGETS_JSON`, `DEFAULT_MODELS_JSON`
- `SANDBOX_RUNNER`, `SANDBOX_LAMBDA_FUNCTION_NAME`, `SANDBOX_LAMBDA_TIMEOUT_SECONDS`
- `ENABLE_ROOT_STATE_SUMMARY`
- `TOOL_RESOLUTION_MAX_CONCURRENCY`

See `src/rlm_rs/settings.py` and `compose.yaml` for the full list.

## Enterprise deployment notes

- Run the sandbox in Lambda by setting `SANDBOX_RUNNER=lambda` and wiring `SANDBOX_LAMBDA_FUNCTION_NAME`.
- Keep the Lambda in a VPC with no NAT and only an S3 gateway endpoint; do not grant provider secrets.
- Deploy per region (or per tenant) to ensure S3/DDB/Lambda/ECS and the LLM provider remain in-region.
- Use `ENABLE_ROOT_STATE_SUMMARY` if you want the root prompt to receive key/count-only state summaries.
- Tune `TOOL_RESOLUTION_MAX_CONCURRENCY` to bound parallel tool resolution.

## Docs

- `docs/rls_spec.md`: consolidated spec and protocol details.
- `docs/component.md`: component diagram.
- `docs/sequence.md`: Answerer mode sequence.
- `docs/runtime_sequence.md`: Runtime mode sequence.
- `docs/plan.md`: implementation plan and library choices.
- `docs/fine_tuning_rlm_policy.md`: finetuning policy and data guidance.

## Status

This repository is a reference implementation and is under active development.
