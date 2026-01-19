# RLM-RS Minimal UI Spec (LocalStack, Dev Key)

This spec defines a minimal but complete UI for developers and researchers. It targets LocalStack only, uses a fixed dev key, and supports file upload to S3, Answerer mode, Runtime multi-step, and citation inspection.

## Scope

- LocalStack only. No cloud or multi-account switching.
- Fixed dev key `rlm_key_local` with auto-seeding.
- File upload to LocalStack S3.
- Answerer and Runtime execution flows.
- Full debugging surfaces: state, spans, raw API, errors.

## Global Configuration

Defaults (editable in a hidden Debug panel):

- API base URL: `http://localhost:8080`
- LocalStack endpoint: `http://localhost:4566`
- S3 bucket: `rlm-local`
- DynamoDB table prefix: `rlm`
- Tenant: `tenant_local`
- Dev key: `rlm_key_local`
- API key pepper: `smoke-pepper`

## App Layout

### Top Bar

Display:
- API health: `API: Online` or `API: Offline`
- LocalStack health: `LocalStack: Online` or `LocalStack: Offline`
- Tenant: `tenant_local`
- Dev key badge: `Using dev key: rlm_key_local`

Behavior:
- Health checks refresh every 10 seconds.
- Clicking a health badge opens Debug with latest health payloads.

### Sidebar Navigation

Items:
- Sessions
- Executions
- Debug

Persistent indicators:
- Count of RUNNING executions.
- LocalStack status dot.

## Auto-seed Behavior

Trigger:
- On app load if `localStorage.seeded != "true"`.

Flow:
1) Compute HMAC-SHA256 of `rlm_key_local` with `API_KEY_PEPPER`.
2) Put item into `rlm_api_keys`:
   - PK: `KEY#{hash}`
   - SK: `KEY#{hash}`
   - tenant_id: `tenant_local`
3) Set `localStorage.seeded = "true"`.

UI:
- Toast success: `Seeded API key in LocalStack`
- Toast failure: `Failed to seed API key. Check LocalStack status.`

## Screen: Sessions

### Upload and Create Session Card

Inputs:
- File drop zone with browse button.
- Source name (prefill from filename, editable).
- MIME type (prefill, editable).
- TTL minutes (default `60`).
- Optional session options:
  - Enable search (checkbox).
  - Readiness mode (LAX/STRICT).

Primary action:
- `Upload and Create`

Behavior:
- Uploads file to `s3://rlm-local/raw/tenant_local/<uuid>/<filename>`.
- Creates session with docs metadata and TTL.
- Shows progress and errors inline.

### Sessions Table

Columns:
- Session ID
- Status
- Readiness (Parsed/Search)
- Doc count
- Created at
- Action: `Open`

Row actions:
- `Open` navigates to Session Detail.

## Screen: Session Detail

Header:
- `Session <id>`
- Status pill
- TTL countdown

Docs list:
- Doc ID
- Source name
- Ingest status
- Parsed URIs (text/meta/offsets)
- Action: `View parsed text`

Actions:
- `Start Answerer`
- `Start Runtime`

Behavior:
- `View parsed text` opens preview drawer with range selection.

## Screen: Answerer Execution

### Start Answerer Modal

Inputs:
- Question textarea
- Root model (default from API settings)
- Sub model (default from API settings)
- Budgets JSON editor (optional)
- Options:
  - Return trace (checkbox)
  - Redact trace (checkbox)

Actions:
- `Start Execution`
- `Cancel`

### Execution View

Left panel:
- Execution ID
- Status
- Budgets consumed
- Turn count
- Total seconds
- LLM subcalls

Right panel:
- Answer box
- Citations list
  - `Doc <index> · <start>-<end> · <checksum>`
  - Button: `Inspect`
- Trace pointer (when enabled)
- Step history panel
  - Turn timeline with status badges
  - Stdout, state, span log, tool requests, final, and error payloads

Behavior:
- Poll every 2 seconds while RUNNING.
- Stop on terminal status.

## Screen: Runtime Execution (Multi-step)

### Multi-step Script Editor

Features:
- Step list with add, remove, reorder.
- Each step is a code cell.
- Run controls:
  - `Run Step`
  - `Run All`
  - `Reset State`

Behavior:
- `Run Step` posts `/v1/executions/{id}/steps`.
- `Run All` executes sequentially, halting on error.
- `Reset State` creates a new runtime execution.

### Output Inspector

Tabs:
- stdout
- state (JSON tree)
- span log
- tool requests

Displays:
- Step result summary: success, error, timing.
- Tool requests list with resolve status.

## Screen: Citation Viewer

Metadata:
- Tenant ID
- Session ID
- Doc ID
- Doc index
- Start, End
- Checksum

Excerpt:
- Text preview with highlighted span.
- Range selector for surrounding context.

Actions:
- `Verify citation` calls `/v1/citations/verify`.

## Screen: Executions

Table:
- Execution ID
- Session ID
- Mode (Answerer/Runtime)
- Status
- Started/Completed timestamps
- Actions: `Open`, `Cancel` (when RUNNING)

Filters:
- Status filter
- Mode filter
- Session filter

## Screen: Debug

Panels:
- API health (last response)
- LocalStack health (bucket and table checks)
- Recent requests (last 20 with latency and status)
- Errors (stack traces when available)

Actions:
- `Refresh` health checks
- `Clear request log`
- `Re-seed dev key`

## Data Flow and Endpoints

Session:
- `POST /v1/sessions`
- `GET /v1/sessions/{session_id}`

Answerer:
- `POST /v1/sessions/{session_id}/executions`
- `GET /v1/executions/{execution_id}`
- `GET /v1/executions/{execution_id}/steps`
- `POST /v1/executions/{execution_id}/cancel`

Runtime:
- `POST /v1/sessions/{session_id}/executions/runtime`
- `POST /v1/executions/{execution_id}/steps`
- `POST /v1/executions/{execution_id}/cancel`

Citations:
- `POST /v1/spans/get`
- `POST /v1/citations/verify`

Health:
- `GET /health/ready`
- `GET /health/live`

LocalStack:
- S3 upload via AWS SDK to `http://localhost:4566`
- DynamoDB seed via AWS SDK to `http://localhost:4566`

## Error Handling Copy

- `Invalid API key. Seed the dev key or check API_KEY_PEPPER.`
- `LocalStack not reachable at http://localhost:4566`
- `API not reachable at http://localhost:8080`
- `Session not ready yet. Try again in a few seconds.`
- `Execution failed. Open Debug for details.`

## Non-functional Requirements

- UI loads on mobile and desktop.
- No provider secrets in browser storage.
- Log all requests and responses in Debug.
- Keep UI responsive when polling.
