# Recursive Language Model Runtime Service (RLM-RS)

Specification v1.3 (Consolidated)
Status: Implementable
Last updated: 2026-01-16

This spec consolidates and reconciles:
- The Recursive Language Models paper ("RLM paper").
- The original spec (rls_old.md).
- The corrected but sparser spec (rls.md).

It keeps the paper's core idea: treat very long prompts and document corpora as an external environment the model can inspect and transform with Python, and use recursive sub-LLM calls only when semantic judgment is needed.

It also keeps the production-critical corrections from rls.md:
- Lambda is a step executor, not a long-running REPL.
- The sandbox never calls external providers; it only queues tool requests.
- State is JSON-only; large blobs go to S3 via pointers.
- Citations are produced from runtime span logging (not brittle code parsing).

---

## Table of contents

0. Summary
1. Alignment with the RLM paper
2. Goals, non-goals, assumptions
3. Key design decisions
4. Terminology
5. High-level architecture
6. Execution model
7. Root-model protocol (Answerer mode)
8. Context model (ContextView / DocView)
9. Citation system (SpanRefs)
10. State model (JSON-only)
11. Tool requests and provider resolution
12. Budgets, limits, and caching
13. Storage model (DynamoDB + S3)
14. Parser service contract (required formats)
15. Optional search integration (index + retrieve)
16. HTTP API specification
17. MCP integration
18. Security model
19. Observability and operations
20. Deployment and configuration
21. Testing and validation
22. Error handling
Appendix A. Orchestrator loop pseudocode
Appendix B. Sandbox AST policy (minimum viable)
Appendix C. Trace schema
Appendix D. System prompt (copy-paste)

---

## 0. Summary

RLM-RS is a standalone service for running Recursive Language Model (RLM) style executions over corpora far beyond any model context window.

Core loop:
1. Root model proposes a Python step.
2. Sandbox runs the step (Lambda) against an instrumented environment exposing the corpus.
3. The step may emit tool requests (LLM subcalls, optional search). The sandbox never calls external providers.
4. Orchestrator (ECS) resolves tool requests, persists state/trace, and repeats.
5. Completion returns a final answer plus verifiable citations (SpanRefs) derived from logged span accesses.

Modes:
- Answerer mode (managed): RLM-RS runs the full loop and returns the answer.
- Runtime mode (client-driven): the caller drives the loop and uses RLM-RS only as a step runtime.

---

## 1. Alignment with the RLM paper

The RLM paper defines an RLM as an interface-compatible wrapper around an LLM that:
- Loads the long prompt into a programming environment (REPL) as context.
- Lets the model write code to inspect and decompose context.
- Encourages recursive calls to a sub-LLM (llm_query) over programmatic snippets.

RLM-RS preserves the abstraction but replaces a long-lived REPL with:
- short-lived sandbox steps (Lambda)
- explicit yields between steps for tool resolution
- JSON-only persisted state between steps

This is not cosmetic: it is what makes the system deployable and operable.

Paper-driven concerns that are first-class here:
- Heavy tails: RLM trajectories can explode in time and subcalls. Budgets and caching are mandatory.
- REPL-only benefits: running code against the environment can help even without subcalls.

---

## 2. Goals, non-goals, assumptions

### 2.1 Goals
- Handle corpora far exceeding any model context window (millions+ tokens).
- Expose the RLM runtime over HTTP and MCP.
- Allow model-written Python to inspect/slice/aggregate documents.
- Support recursion via orchestrator-resolved sub-LLM calls.
- Produce stable, verifiable citations (SpanRefs) via runtime span logging.
- Predictable cost via budgets, hard caps, caching, and strict limits.
- Tenant isolation with audit logging and rate limiting.

### 2.2 Non-goals (v1.x)
- A continuously running Python REPL server with in-memory state.
- Arbitrary untrusted user code execution.
- An end-user UI.

### 2.3 Assumptions
- Raw files already exist in S3.
- A parser service exists that can produce canonical UTF-8 text, structure metadata, and offset mapping.
- Orchestrator can reach LLM providers (keys and egress live in ECS, not Lambda).
- Authentication maps requests to tenant_id.

---

## 3. Key design decisions

### 3.1 Lambda is a step executor, not the runtime
- Lambda runs a single step with strict limits.
- ECS orchestrator can run long jobs by chaining steps.

### 3.2 Sandbox never calls external providers
Killed: calling providers from Lambda with Secrets Manager.
Reason: secrets + egress + throttling + retries + caching do not belong in the sandbox.

Kept: sandbox only queues tool requests; orchestrator resolves them.

### 3.3 State is JSON-only
Killed: dill/pickle state persistence.
Reason: unsafe, opaque, brittle, impossible to debug reliably at scale.

Kept: JSON-only state; large blobs are offloaded to S3.

### 3.4 Citations come from runtime span logging
Killed: parsing generated code to infer citations.
Reason: brittle and fails silently.

Kept: ContextView/DocView logs every slice; orchestrator converts spans to SpanRefs with checksums.

### 3.5 Honest threat model
This sandbox is appropriate for controlled model-generated code with strict policies.
If you expect adversarial tenants, you need a stronger isolation boundary than Lambda.

---

## 4. Terminology

- Tenant: isolation boundary for data, budgets, and rate limits.
- Session: a corpus + configuration with TTL; references documents and parsed outputs.
- Document: raw file pointer + parsed outputs (text/meta/offsets).
- Execution: a run against a session (Answerer or Runtime).
- Step: one Lambda invocation executing Python.
- Root model: the LLM that writes steps and decides strategy.
- Subcall: an LLM call requested from code (semantic judgment), executed by orchestrator.
- Tool request: a queued request emitted by sandbox to be resolved outside.
- Span log: runtime record of slices accessed (doc_index/start/end/tag).
- SpanRef: verifiable citation record including checksum.

---

## 5. High-level architecture

### 5.1 Components

- RLM-RS API + Orchestrator (ECS)
  - HTTP API, AuthN/Z, rate limiting
  - Session lifecycle and readiness gating
  - Answerer-mode orchestration loop
  - Tool resolution (LLM/search) + caching
  - SpanRef generation + trace persistence

- Ingestion worker (ECS)
  - Calls parser service for registered docs
  - Writes parsed output pointers/status in DynamoDB
  - Optional search indexing lifecycle

- Lambda step sandbox
  - Executes Python with strict restrictions
  - Lazy-loads parsed text from S3
  - Records span accesses
  - Emits tool requests
  - Returns stdout/state/spans/tool_requests/final/error

- Storage
  - DynamoDB: sessions, documents, executions, execution_state, api_keys, audit_log
  - S3: parsed text/meta/offsets, large state blobs, traces, caches

### 5.2 Data flow
1. Client creates session (docs reference raw S3 URIs).
2. Ingestion worker parses docs and records outputs.
3. Session becomes READY when required ingest statuses are met.
4. Client starts an execution.
5. Orchestrator loops: root step -> Lambda -> tool resolution -> persist -> repeat.
6. Completion returns answer + citations (+ trace pointer if requested).


---

## 6. Execution model

### 6.1 Step contract

Lambda input (event):
- tenant_id, session_id, execution_id, turn_index
- code (Python source)
- state (JSON object or JSON string)
- context_manifest (docs list with S3 URIs for parsed outputs)
- tool_results (resolved since last step)
- limits snapshot (read-only)

Lambda output:
- success (bool)
- stdout (string, truncated)
- state (JSON object or string)
- span_log (list)
- tool_requests (object: llm[] and search[])
- final (optional: is_final, answer)
- error (optional: code, message, details)

### 6.2 Tool request protocol

The sandbox can emit tool requests of type:
- llm: semantic subcalls executed by orchestrator
- search: optional retrieval acceleration executed by orchestrator

Tool requests are queued, not executed in-sandbox.

### 6.3 Reserved state namespaces

Orchestrator-owned keys (sandbox must treat as read-only):
- state["_tool_results"]["llm"][key] = {"text": "...", "meta": {...}}
- state["_tool_results"]["search"][key] = {"hits": [...], "meta": {...}}
- state["_tool_status"][key] = "pending" | "resolved" | "error"
- state["_budgets"] = { ...remaining snapshot... }
- state["_trace"] = optional small trace summary (not full trace)

Model-owned keys:
- state["work"] (recommended workspace)
- state["notes"], state["answer_draft"], etc.

### 6.4 Execution statuses
- RUNNING
- COMPLETED
- FAILED
- TIMEOUT
- BUDGET_EXCEEDED
- MAX_TURNS_EXCEEDED

---

## 7. Root-model protocol (Answerer mode)

### 7.1 Output rule (hard)
The root model MUST output exactly one fenced code block per turn:

```python
```repl
# code here
```
```

No prose, no markdown outside the block.

### 7.2 Finalization rule
The only way to complete is:

```python
tool.FINAL("final answer text")
```

### 7.3 Subcall rule
To request a subcall:

```python
tool.queue_llm("key", prompt, model_hint="sub", max_tokens=..., temperature=0)
tool.YIELD("waiting for subcall key")
```

Then, next turn, read:

```python
state["_tool_results"]["llm"]["key"]["text"]
```

### 7.4 Citation rule
Citations are produced from runtime span logging. Before asserting a fact, the model should read supporting text via doc slicing so the runtime logs the span.

### 7.5 Contexts output mode (output_mode=CONTEXTS)
Answerer executions accept options.output_mode with values ANSWER or CONTEXTS.
When output_mode=CONTEXTS:
- The orchestrator appends contexts-mode instructions to the root prompt.
- The model marks returnable spans by calling doc.slice(start, end, tag="context") or doc.slice(start, end, tag="context:<suffix>").
- Only spans whose tag is exactly context or starts with context: are returned as contexts.
- tool.FINAL(...) still ends the execution, but its answer text is ignored.
- Responses include contexts inline or a contexts_s3_uri pointer. The answer field is null and citations are derived 1:1 from the same spans.

---

## 8. Context model (ContextView / DocView)

### 8.1 Context exposure
The sandbox does not receive giant strings in the Lambda payload. It receives a manifest and loads text lazily from S3.

Sandbox global context is a ContextView:
- len(context) = number of documents
- context[i] returns a DocView

### 8.2 DocView behavior
- doc[a:b] returns the slice and logs the span access
- doc.slice(a, b, tag=None) same
- Optional helpers (if meta exists):
- doc.find(substr, *, start=0, end=None, max_hits=20)
- doc.regex(pattern, *, start=0, end=None, max_hits=20)
  - doc.sections() returns structured spans from meta
  - doc.page_spans() returns page-level spans from meta

All helpers that return text MUST log the underlying spans.

### 8.3 Span log entry schema
Minimum:

```json
{
  "doc_index": 0,
  "start_char": 45000,
  "end_char": 52000,
  "tag": "candidate_clause"
}
```

---

## 9. Citation system (SpanRefs)

### 9.1 SpanRef schema

```json
{
  "tenant_id": "t_...",
  "session_id": "s_...",
  "doc_id": "d_...",
  "doc_index": 0,
  "start_char": 45000,
  "end_char": 52000,
  "checksum": "sha256:..."
}
```

### 9.2 Checksum computation
- Load exact text[start_char:end_char] from canonical parsed text.
- Unicode normalize to NFC.
- UTF-8 encode.
- SHA-256 hash.
- Prefix with sha256:.

### 9.3 Deduplication / merging
To avoid citation spam:
- sort spans per doc by start_char
- merge overlaps
- optionally merge spans with gaps <= merge_gap_chars
- recompute checksum on merged span

### 9.4 Verification endpoint
- Reload referenced span
- Recompute checksum
- If mismatch: citation invalid

---

## 10. State model (JSON-only)

### 10.1 Persistent state rules
State MUST be JSON-serializable.

Orchestrator validates:
- size limit
- reserved key enforcement
- JSON type validity (no bytes, no datetime, no custom objects)

If invalid -> STATE_INVALID_TYPE.

### 10.2 Large state handling
Policy:
- Inline in DynamoDB if <= 350KB.
- Otherwise gzip canonical JSON and write to S3:
  - state/{tenant_id}/{execution_id}/state_{turn}.json.gz
  - store pointer + checksum + summary in DynamoDB

### 10.3 State cleanup
- Execution state TTL aligns to session TTL (or separate execution TTL).
- S3 lifecycle rules delete state blobs and traces after TTL.


---

## 11. Tool requests and provider resolution

### 11.1 LLM tool request schema
Sandbox emits entries like:

```json
{
  "type": "llm",
  "key": "extract_termination",
  "prompt": "...",
  "model_hint": "sub",
  "max_tokens": 1200,
  "temperature": 0,
  "metadata": {"purpose": "extract"}
}
```

### 11.2 Search tool request schema (optional)

```json
{
  "type": "search",
  "key": "term_notice_hits",
  "query": "termination notice period",
  "k": 10,
  "filters": {"doc_ids": ["doc_..."]}
}
```

### 11.3 Tool resolution rules (orchestrator)
- Validate budgets (max_subcalls, max_prompt_chars, etc.).
- Apply caching if enabled.
- Call provider.
- Write results into state["_tool_results"].
- Set state["_tool_status"][key].

### 11.4 Failure handling
If a provider fails:
- set _tool_status[key] = "error"
- store error metadata in _tool_results (truncated)
- the root model should recover (retry once if budget allows, or adapt strategy)

---

## 12. Budgets, limits, and caching

### 12.1 Budget schema (recommended)

```json
{
  "max_turns": 20,
  "max_total_seconds": 180,
  "max_step_seconds": 30,

  "max_spans_total": 2000,
  "max_spans_per_step": 200,

  "max_tool_requests_per_step": 25,

  "max_llm_subcalls": 50,
  "max_llm_prompt_chars": 200000,
  "max_total_llm_prompt_chars": 2000000,

  "max_stdout_chars": 15000,
  "max_state_chars": 500000
}
```

### 12.2 Enforcement
- Orchestrator enforces totals (turns, wall time, subcalls totals).
- Lambda enforces per-step limits (timeout, stdout, spans, tool request counts, state size).
- On exceed: terminate with BUDGET_EXCEEDED / MAX_TURNS_EXCEEDED / STEP_TIMEOUT.

### 12.3 Caching strategy
Caching subcalls is worth it only if outputs are stable.
Default policy:
- subcalls use temperature=0 (or provider equivalent)
- cache key includes provider, model, temperature, max_tokens, and prompt hash

Suggested S3 layout:
- cache/{tenant_id}/llm/{sha256}.json
- cache/{tenant_id}/search/{sha256}.json

Cache record schema:

```json
{
  "created_at": "...",
  "provider": "...",
  "model": "...",
  "request": {"prompt_sha256": "...", "max_tokens": 900, "temperature": 0},
  "response": {"text": "...", "raw": {}}
}
```

---

## 13. Storage model (DynamoDB + S3)

### 13.1 DynamoDB tables

#### rlm_sessions
PK: TENANT#{tenant_id}
SK: SESSION#{session_id}

Attributes:
- status: CREATING | READY | FAILED | EXPIRED | DELETING
- created_at, expires_at, ttl_epoch
- doc_count, total_chars
- options: enable_search, readiness_mode
- defaults: models_default, budgets_default

Recommended GSIs:
- session lookup by session_id
- tenant status listing

#### rlm_documents
PK: SESSION#{session_id}
SK: DOC#{doc_id}

Attributes:
- tenant_id, session_id, doc_id, doc_index
- source_name, mime_type
- raw_s3_uri, raw_s3_version_id (optional), raw_s3_etag (optional)
- text_s3_uri
- meta_s3_uri (optional)
- offsets_s3_uri (optional)
- char_length, byte_length, page_count (optional)
- parser_version
- text_checksum (sha256 of full canonical text; recommended)
- ingest_status: REGISTERED | PARSING | PARSED | INDEXING | INDEXED | FAILED
- failure_reason (optional)

#### rlm_executions
PK: SESSION#{session_id}
SK: EXEC#{execution_id}

Attributes:
- tenant_id, session_id, execution_id
- status, mode
- question (optional)
- answer (optional)
- citations (optional)
- trace_s3_uri (optional)
- budgets_requested, budgets_consumed
- started_at, completed_at, duration_ms

#### rlm_execution_state
PK: EXEC#{execution_id}
SK: STATE

Attributes:
- turn_index
- state_json (optional)
- state_s3_uri (optional)
- checksum, summary
- updated_at
- ttl_epoch

#### rlm_api_keys
PK/SK: KEY#{key_hash}

Attributes:
- tenant_id, key_prefix, name, scopes
- created_at, expires_at, revoked, last_used_at

#### rlm_audit_log
PK: TENANT#{tenant_id}
SK: TS#{timestamp}#REQ#{request_id}

Attributes:
- action, actor_id, status_code, latency_ms
- session_id (optional), execution_id (optional), error_code (optional)

### 13.2 S3 layout (recommended)
- parsed/{tenant_id}/{session_id}/{doc_id}/text.txt
- parsed/{tenant_id}/{session_id}/{doc_id}/meta.json
- parsed/{tenant_id}/{session_id}/{doc_id}/offsets.json
- state/{tenant_id}/{execution_id}/state_{turn}.json.gz
- traces/{tenant_id}/{session_id}/{execution_id}.json.gz
- cache/{tenant_id}/llm/{hash}.json
- cache/{tenant_id}/search/{hash}.json

Objects should be immutable after write (except lifecycle deletion).

---

## 14. Parser service contract (required formats)

This section restores the missing concrete formats from rls_old, but aligns them with rls.md's citation stability requirements.

### 14.1 Parse request (example)

POST /parse

```json
{
  "request_id": "uuid",
  "source": {
    "s3_uri": "s3://bucket/path/file.pdf",
    "s3_version_id": "optional",
    "s3_etag": "optional"
  },
  "output": {
    "s3_prefix": "s3://bucket/parsed/{tenant_id}/{session_id}/{doc_id}/"
  },
  "options": {
    "extract_structure": true,
    "ocr_enabled": true,
    "language_hint": "en",
    "timeout_seconds": 300
  }
}
```

### 14.2 Parse response (success example)

```json
{
  "request_id": "uuid",
  "status": "success",
  "outputs": {
    "text_s3_uri": "s3://bucket/parsed/.../text.txt",
    "meta_s3_uri": "s3://bucket/parsed/.../meta.json",
    "offsets_s3_uri": "s3://bucket/parsed/.../offsets.json"
  },
  "stats": {
    "char_length": 345678,
    "byte_length": 356123,
    "page_count": 47,
    "parse_duration_ms": 12340
  },
  "parser_version": "parser-2.3.1",
  "text_checksum": "sha256:...",
  "warnings": ["Page 23: image-only content, OCR applied"]
}
```

### 14.3 Parse response (error example)

```json
{
  "request_id": "uuid",
  "status": "failed",
  "error": {
    "code": "UNSUPPORTED_FORMAT",
    "message": "Cannot parse encrypted PDF",
    "details": {}
  },
  "parser_version": "parser-2.3.1"
}
```

### 14.4 Canonical text requirements (non-negotiable)
Parser MUST output a canonical UTF-8 text representation that is:
- deterministic for the same raw input object version + parser_version
- stable with declared line-ending policy
- free of nondeterministic transformations (timestamps, random IDs)

### 14.5 Structure metadata format (meta.json)
Minimum viable format (page spans + optional hierarchical structure):

```json
{
  "version": "1.0",
  "doc_id": "uuid",
  "parser_version": "parser-2.3.1",
  "structure": {
    "type": "document",
    "title": "Contract Agreement",
    "children": [
      {
        "type": "section",
        "title": "1. Definitions",
        "level": 1,
        "start_char": 234,
        "end_char": 5678,
        "page": 1,
        "children": []
      }
    ]
  },
  "pages": [
    {"page_num": 1, "start_char": 0, "end_char": 4523},
    {"page_num": 2, "start_char": 4524, "end_char": 9102}
  ],
  "tables": [
    {"start_char": 12345, "end_char": 13456, "page": 3, "rows": 5, "cols": 4}
  ]
}
```

### 14.6 Offset mapping format (offsets.json)
Used for ranged S3 reads and stable mapping:

```json
{
  "version": "1.0",
  "doc_id": "uuid",
  "char_length": 345678,
  "byte_length": 356123,
  "encoding": "utf-8",
  "checkpoints": [
    {"char": 0, "byte": 0},
    {"char": 10000, "byte": 10234},
    {"char": 20000, "byte": 20512}
  ],
  "checkpoint_interval": 10000
}
```

### 14.7 Determinism requirements
Parser MUST:
1. Produce identical output for identical raw input (same object version).
2. Include parser_version and raw object identity fields (etag/version_id).
3. Never silently change canonicalization rules without bumping parser_version.

---

## 15. Optional search integration (index + retrieve)

Search is an accelerator, not a truth source. Truth comes from span slicing + checksums.

### 15.1 Indexing lifecycle
If enabled:
- Ingestion worker chunkifies canonical text (e.g., 1k-4k chars with overlap).
- Index records include:
  - tenant_id, session_id, doc_id, doc_index
  - chunk_start_char, chunk_end_char
  - chunk_text (or embeddings)

Doc ingest_status transitions:
- PARSED -> INDEXING -> INDEXED (or FAILED)

Session readiness_mode:
- STRICT: READY only if all docs are INDEXED
- LAX: READY when all docs are PARSED

### 15.2 Search result schema
Search results must return candidate spans:

```json
{
  "hits": [
    {
      "score": 12.3,
      "doc_index": 0,
      "start_char": 120044,
      "end_char": 120412,
      "preview": "Either party may terminate..."
    }
  ]
}
```

---

## 16. HTTP API specification

Base path: /v1
Auth: Authorization: Bearer rlm_key_...

### 16.1 Error envelope (all non-2xx)

```json
{
  "error": {
    "code": "ERROR_CODE",
    "message": "Human readable",
    "request_id": "req_...",
    "details": {}
  }
}
```

### 16.2 Sessions

POST /v1/sessions

Request:

```json
{
  "ttl_minutes": 120,
  "docs": [
    {
      "source_name": "contract.pdf",
      "mime_type": "application/pdf",
      "raw_s3_uri": "s3://bucket/raw/contract.pdf",
      "raw_s3_version_id": "optional",
      "raw_s3_etag": "optional"
    }
  ],
  "options": {"enable_search": false, "readiness_mode": "LAX"},
  "models_default": {"root_model": "gpt-5", "sub_model": "gpt-5-mini"},
  "budgets_default": {"max_turns": 20, "max_total_seconds": 180, "max_llm_subcalls": 50}
}
```

Response:

```json
{
  "session_id": "sess_...",
  "status": "CREATING",
  "created_at": "...",
  "expires_at": "...",
  "docs": [
    {"doc_id": "doc_...", "doc_index": 0, "ingest_status": "REGISTERED"}
  ]
}
```

GET /v1/sessions/{session_id}

Response:

```json
{
  "session_id": "sess_...",
  "status": "READY",
  "readiness": {"parsed_ready": true, "search_ready": false, "ready": true},
  "docs": [
    {
      "doc_id": "doc_...",
      "doc_index": 0,
      "ingest_status": "PARSED",
      "text_s3_uri": "s3://...",
      "meta_s3_uri": "s3://...",
      "offsets_s3_uri": "s3://..."
    }
  ]
}
```

DELETE /v1/sessions/{session_id}

Response:

```json
{"status":"DELETING"}
```

### 16.3 Executions (Answerer mode)

POST /v1/sessions/{session_id}/executions

Request:

```json
{
  "question": "What are the termination conditions and notice periods?",
  "models": {"root_model": "gpt-5", "sub_model": "gpt-5-mini"},
  "budgets": {"max_total_seconds": 180, "max_llm_subcalls": 40},
  "options": {
    "return_trace": false,
    "redact_trace": false,
    "synchronous": false,
    "synchronous_timeout_seconds": 30,
    "output_mode": "ANSWER"
  }
}
```

Response:

```json
{"execution_id":"exec_...","status":"RUNNING"}
```

GET /v1/executions/{execution_id}

Response (completed):

```json
{
  "execution_id": "exec_...",
  "output_mode": "ANSWER",
  "status": "COMPLETED",
  "answer": "...",
  "citations": [
    {
      "tenant_id": "...",
      "session_id": "...",
      "doc_id": "...",
      "doc_index": 0,
      "start_char": 1,
      "end_char": 2,
      "checksum": "sha256:..."
    }
  ],
  "budgets_consumed": {"turns": 7, "llm_subcalls": 12, "total_seconds": 42},
  "trace_s3_uri": "s3://.../traces/.../exec_....json.gz"
}
```

Response (completed, output_mode=CONTEXTS):

```json
{
  "execution_id": "exec_...",
  "output_mode": "CONTEXTS",
  "status": "COMPLETED",
  "answer": null,
  "contexts": [
    {
      "sequence_index": 0,
      "turn_index": 1,
      "span_index": 0,
      "tag": "context:summary",
      "text": "example text",
      "text_char_length": 12,
      "source_name": "contract.pdf",
      "mime_type": "application/pdf",
      "ref": {
        "tenant_id": "...",
        "session_id": "...",
        "doc_id": "...",
        "doc_index": 0,
        "start_char": 10,
        "end_char": 22,
        "checksum": "sha256:..."
      }
    }
  ],
  "contexts_s3_uri": null,
  "citations": [
    {
      "tenant_id": "...",
      "session_id": "...",
      "doc_id": "...",
      "doc_index": 0,
      "start_char": 10,
      "end_char": 22,
      "checksum": "sha256:..."
    }
  ],
  "budgets_consumed": {"turns": 1, "llm_subcalls": 0, "total_seconds": 1},
  "trace_s3_uri": "s3://.../traces/.../exec_....json.gz"
}
```

### 16.3.1 Context item schema (contexts output)

Each context item is derived from a span_log entry tagged for context return.

```json
{
  "sequence_index": 0,
  "turn_index": 1,
  "span_index": 0,
  "tag": "context",
  "text": "example text",
  "text_char_length": 12,
  "source_name": "contract.pdf",
  "mime_type": "application/pdf",
  "ref": {
    "tenant_id": "...",
    "session_id": "...",
    "doc_id": "...",
    "doc_index": 0,
    "start_char": 10,
    "end_char": 22,
    "checksum": "sha256:..."
  }
}
```

Notes:
- sequence_index is the global discovery order across all turns and is contiguous starting at 0.
- turn_index is the step index where the span was logged.
- span_index is the in-turn span order for that step.
- tag is the span tag, for example context or context:<suffix>.

GET /v1/executions/{execution_id}/steps

Response:

```json
{
  "steps": [
    {
      "turn_index": 0,
      "updated_at": "2025-01-01T00:00:00Z",
      "success": true,
      "stdout": "1\\n",
      "state": {},
      "span_log": [],
      "tool_requests": {"llm": [], "search": []},
      "final": {"is_final": false, "answer": null},
      "error": null,
      "checksum": "sha256:...",
      "summary": {"byte_length": 0, "char_length": 0}
    }
  ]
}
```

POST /v1/executions/{execution_id}/wait

Request:

```json
{"timeout_seconds": 30}
```

POST /v1/executions/{execution_id}/cancel

Notes:
- Idempotent. If the execution is not RUNNING, returns the current status.

Response:

```json
{
  "execution_id": "exec_...",
  "status": "CANCELLED",
  "completed_at": "2025-01-01T00:00:00Z"
}
```

### 16.4 Runtime mode (client-driven)

POST /v1/sessions/{session_id}/executions/runtime

Response:

```json
{"execution_id":"exec_...","status":"RUNNING"}
```

POST /v1/executions/{execution_id}/steps

Request:

```json
{
  "code": "```repl\nprint(len(context))\n```",
  "state": null,
  "options": {"resolve_tools": false}
}
```

Response:

```json
{
  "success": true,
  "stdout": "1\n",
  "state": {},
  "span_log": [],
  "tool_requests": {"llm": [], "search": []},
  "final": {"is_final": false, "answer": null},
  "error": null
}
```

POST /v1/executions/{execution_id}/tools/resolve

Request:

```json
{
  "tool_requests": {
    "llm": [
      {"key": "k", "prompt": "...", "model_hint": "sub", "max_tokens": 600, "temperature": 0}
    ],
    "search": []
  },
  "models": {"sub_model": "gpt-5-mini"}
}
```

Response:

```json
{
  "tool_results": {
    "llm": {"k": {"text": "...", "meta": {"model": "gpt-5-mini"}}},
    "search": {}
  },
  "statuses": {"k": "resolved"}
}
```

### 16.5 Spans and citations

POST /v1/spans/get

Request:

```json
{"session_id":"sess_...","doc_id":"doc_...","start_char":100,"end_char":200}
```

Response:

```json
{
  "text": "...",
  "ref": {
    "tenant_id": "...",
    "session_id": "...",
    "doc_id": "...",
    "doc_index": 0,
    "start_char": 100,
    "end_char": 200,
    "checksum": "sha256:..."
  }
}
```

POST /v1/citations/verify

Request:

```json
{
  "ref": {
    "tenant_id": "...",
    "session_id": "...",
    "doc_id": "...",
    "doc_index": 0,
    "start_char": 100,
    "end_char": 200,
    "checksum": "sha256:..."
  }
}
```

Response:

```json
{
  "valid": true,
  "text": "...",
  "source_name": "contract.pdf",
  "char_range": {"start_char": 100, "end_char": 200}
}
```

### 16.6 Health
- GET /health/live -> {"status":"ok"}
- GET /health/ready -> checks DDB/S3/Lambda and (optionally) provider connectivity

---

## 17. MCP integration

RLM-RS provides an MCP server as a thin wrapper over HTTP.

Minimum tools (map 1:1 to HTTP):
- rlm_create_session
- rlm_get_session
- rlm_delete_session
- rlm_start_execution
- rlm_get_execution
- rlm_wait_execution
- rlm_runtime_create_execution
- rlm_runtime_step
- rlm_resolve_tools
- rlm_get_span
- rlm_verify_citation

Environment variables:
- RLM_BASE_URL
- RLM_API_KEY

---

## 18. Security model

### 18.1 Tenant isolation
- API keys map to tenant_id.
- All storage reads validate tenant ownership.
- S3 keys partitioned by tenant_id.

### 18.2 Secrets handling
- Provider API keys live only in the ECS orchestrator (Secrets Manager).
- Lambda has no access to provider secrets.

### 18.3 Sandbox hardening (minimum viable)
- Reject imports and OS-level modules (os/sys/subprocess/socket).
- Builtins allowlist only.
- No network egress for Lambda (VPC, no NAT, only S3 via gateway endpoint).
- Instruction/line limits.
- Strict output/state limits.

### 18.4 Threat model
This is suitable when code is produced by your orchestrated models under strict rules and tenants are not adversarial.
If tenants are adversarial, use a stronger isolation boundary than Lambda.

---

## 19. Observability and operations

### 19.1 Metrics (recommended)
API:
- rlm.api.requests.total (endpoint, status)
- rlm.api.latency.seconds (endpoint)

Executions:
- rlm.execution.total (status, mode)
- rlm.execution.duration.seconds
- rlm.execution.turns
- rlm.execution.subcalls
- rlm.execution.spans.total

Lambda:
- rlm.lambda.invocations.total (status)
- rlm.lambda.duration.seconds

LLM:
- rlm.llm.requests.total (model, cache_hit)
- rlm.llm.tokens.total (model, type=input|output)
- rlm.llm.latency.seconds (model)
- rlm.llm.errors.total

### 19.2 Structured logging
Log at least:
- request_id, tenant_id, session_id, execution_id
- turn_index, code_length, state_size
- budgets consumed snapshots
- tool request counts, cache hits
- error codes + truncated details

### 19.3 Tracing
- Store full trace in S3 and reference from the execution record.
- Support redaction controls for prompts and model outputs if required.

---

## 20. Deployment and configuration

ECS services:
- rlm-api (includes orchestrator loop)
- rlm-ingestion-worker
Optional:
- rlm-mcp-server

Lambda:
- rlm-sandbox-step (Python 3.11)
- timeout: max_step_seconds + overhead

Networking:
- ECS behind ALB in private subnets
- Lambda in VPC with S3 gateway endpoint and no internet egress
- ECS has controlled egress to LLM provider endpoints

Core environment variables:
- AWS_REGION
- DDB_TABLE_PREFIX
- S3_BUCKET
- LAMBDA_FUNCTION_NAME
- PARSER_SERVICE_URL
- PARSER_SERVICE_AUTH_SECRET_ARN
- LLM_PROVIDER
- LLM_PROVIDER_SECRET_ARN
- DEFAULT_ROOT_MODEL
- DEFAULT_SUB_MODEL
- DEFAULT_BUDGETS_JSON
- RATE_LIMITS_JSON
- ENABLE_SEARCH_DEFAULT
- SEARCH_BACKEND_CONFIG (optional)

---

## 21. Testing and validation

Unit tests:
- AST validator rejects imports/dunder/banned calls.
- Builtins allowlist contains no __import__, open, eval, exec, compile.
- JSON validator rejects non-JSON types.
- Span logging correctness (slices produce expected span entries).
- Checksum determinism tests.

Integration tests:
- Full Answerer loop with no subcalls.
- Subcalls + yields.
- Tool errors and recovery.
- Citation verification roundtrip.
- Large state offload to S3.

Robustness tests:
- Runaway loops hit instruction/line limits.
- Excessive tool requests capped.
- Huge stdout capped.
- Oversized state rejected/offloaded per policy.

---

## 22. Error handling

### 22.1 Standard error envelope

```json
{
  "error": {
    "code": "ERROR_CODE",
    "message": "Human readable",
    "request_id": "req_...",
    "details": {}
  }
}
```

### 22.2 Core error codes
- UNAUTHORIZED (401)
- FORBIDDEN (403)
- SESSION_NOT_FOUND (404)
- EXECUTION_NOT_FOUND (404)
- SESSION_NOT_READY (409)
- SESSION_EXPIRED (410)
- VALIDATION_ERROR (422)
- RATE_LIMITED (429)
- BUDGET_EXCEEDED (400)
- MAX_TURNS_EXCEEDED (400)
- STEP_TIMEOUT (400)
- SANDBOX_AST_REJECTED (400)
- SANDBOX_LINE_LIMIT (400)
- STATE_INVALID_TYPE (400)
- STATE_TOO_LARGE (400)
- CHECKSUM_MISMATCH (400)
- S3_READ_ERROR (502)
- PARSER_ERROR (502)
- LLM_PROVIDER_ERROR (502)
- LAMBDA_ERROR (500)
- INTERNAL_ERROR (500)


---

# Appendix A. Orchestrator loop pseudocode

```python
def run_answerer_execution(session, question, budgets, models):
    exec_rec = create_execution_record(...)

    state = {
        "work": {},
        "_tool_results": {"llm": {}, "search": {}},
        "_tool_status": {},
        "_budgets": budgets_snapshot(...),
    }

    last_stdout = ""

    for turn in range(budgets["max_turns"]):
        if total_time_exceeded(budgets):
            return finalize(exec_rec, status="BUDGET_EXCEEDED")

        prompt = build_root_prompt(
            session=session,
            question=question,
            state=state,
            last_stdout=last_stdout,
            budgets_snapshot=budgets_snapshot(...),
        )

        code = call_root_model(prompt, models["root_model"])

        step_out = invoke_lambda_step(
            tenant_id=session.tenant_id,
            session_id=session.session_id,
            execution_id=exec_rec.execution_id,
            turn_index=turn,
            code=code,
            state=state,
            context_manifest=session.context_manifest,
            tool_results=state.get("_tool_results", {}),
            limits=per_step_limits(budgets),
        )

        persist_turn_trace(exec_rec, turn, code, step_out)

        if step_out.get("error"):
            # policy choice: ask model to repair or fail fast
            state["work"]["last_error"] = step_out["error"]
            last_stdout = step_out.get("stdout", "")
            continue

        last_stdout = step_out.get("stdout", "")

        state = validate_and_persist_state(exec_rec, turn, step_out.get("state"))

        add_spans(exec_rec, step_out.get("span_log", []))

        final_obj = step_out.get("final") or {}
        if final_obj.get("is_final"):
            citations = make_spanrefs(exec_rec.spans, session.docs)
            return complete_execution(exec_rec, answer=final_obj.get("answer", ""), citations=citations)

        tool_requests = step_out.get("tool_requests") or {"llm": [], "search": []}
        tool_results = resolve_tools(tool_requests, budgets, models)
        inject_tool_results(state, tool_results)

    return finalize(exec_rec, status="MAX_TURNS_EXCEEDED")
```

---

# Appendix B. Sandbox AST policy (minimum viable)

Reject AST nodes:
- Import, ImportFrom
- Global, Nonlocal
- Any attribute access containing "__" (dunder)

Reject any Name in the banned set:
- eval, exec, compile, open, input, __import__, globals, locals, vars, dir, help

Reject any attempted module identifiers (even if imports are disallowed):
- os, sys, subprocess, socket, pathlib, shutil, urllib, requests, http

Builtins allowlist only:
- len, range, enumerate, zip, map, filter, sorted, reversed
- min, max, sum, abs, round
- int, float, str, bool, list, dict, set, tuple
- isinstance
- print (captured)

Enforce:
- max executed lines or instructions
- max stdout chars
- max spans per step
- max tool requests per step
- max state bytes

---

# Appendix C. Trace schema

Stored in S3 as gzipped JSON.

```json
{
  "version": "1.1",
  "tenant_id": "t_...",
  "session_id": "sess_...",
  "execution_id": "exec_...",
  "mode": "ANSWERER",
  "question": "...",
  "started_at": "...",
  "completed_at": "...",
  "status": "COMPLETED",
  "budgets_requested": {},
  "budgets_consumed": {},
  "turns": [
    {
      "turn_index": 0,
      "root_model": {"name": "..."},
      "root_output_raw": "...",
      "code": "...",
      "lambda": {
        "success": true,
        "stdout": "...",
        "span_log": [],
        "tool_requests": {},
        "final": {"is_final": false, "answer": null},
        "error": null,
        "duration_ms": 1234
      },
      "tool_resolution": {
        "requests": {},
        "results": {},
        "duration_ms": 456
      }
    }
  ],
  "final": {
    "answer": "...",
    "citations": [],
    "completed_at": "..."
  }
}
```

---

# Appendix D. System prompt (copy-paste, production-ready)

This appendix defines a strict system prompt for the root model in Answerer mode.
The orchestrator should substitute ALL_CAPS placeholders.

## Appendix D1: Root model system prompt (subcalls enabled)

You are the root model operating inside RLM-RS (Recursive Language Model Runtime Service).

Your job: answer the QUESTION using a document corpus that you cannot see directly in your model context window. Instead, you must write Python code to inspect and transform the corpus through the sandbox environment.

Environment you can use (inside the sandbox step)
You will write Python inside a fenced code block labelled `repl`. The sandbox provides these globals:

- context: a list-like ContextView of documents.
  - len(context) = number of documents
  - doc = context[i] returns a DocView
  - doc[a:b] returns a text slice and automatically logs a citation span
  - optional helpers (may exist): doc.find(...), doc.regex(...), doc.sections(), doc.page_spans()

- state: a JSON-serializable dict persisted between steps.
  - Use state["work"] as your workspace (create it if missing).
  - Tool results appear in state["_tool_results"].
  - Tool schema is in state["_tool_schema"] (JSON) and tool.schema() (static spec).

- tool: a ToolAPI for queuing external operations (the sandbox has no network).
  - Use state["_tool_schema"] for exact parameters, aliases, and constraints.
  - tool.queue_llm(key, prompt, model_hint="sub", max_tokens=..., temperature=0, metadata=None)
  - tool.queue_search(key, query, k=10, filters=None) (only if enabled)
  - tool.YIELD(reason=None) ends the step so the orchestrator can resolve queued tools.
  - tool.FINAL(answer_text) completes the whole execution.

Hard constraints (do not violate)
1) Output format: You MUST output exactly one fenced code block per turn:
   - Start with ```repl
   - End with ```
   - Nothing outside the code block. No explanations. No markdown.

2) No imports. Do not write import ...

3) No network, no files. You cannot call external APIs yourself.

4) Stdout is truncated. Print summaries and small excerpts only.

5) Budgets are real. Subcalls are expensive and can blow up fast. Use them only when you need semantic judgment.

How to work (required operating style)
- Use Python first for locating regions, counting/grouping, extracting candidate spans, and storing structured notes in state["work"].
- Use sub-LLM calls only for semantic extraction/summarization/aggregation where code is insufficient.
- Do not subcall everything.

Tool-result protocol (how subcalls work here)
The sandbox does NOT return subcall results immediately.

To use a subcall:
1) Queue it:
   tool.queue_llm("k1", PROMPT, model_hint="sub", max_tokens=1200, temperature=0)
2) End the step:
   tool.YIELD("waiting for k1")
3) Next turn, read:
   state["_tool_results"]["llm"]["k1"]["text"]

Same pattern applies to search.

Citation discipline (non-negotiable)
RLM-RS generates citations automatically from spans you read via doc[a:b].

Therefore:
- Before stating a factual claim, ensure you have read the supporting text by slicing the relevant span.
- If you did not read it from the documents, do not claim it as fact.
- Prefer small, precise slices over giant dumps.

Recovery behavior
If a tool fails or returns empty:
- try an alternative strategy (different keywords, broader search, smaller chunking)
- if retrying a subcall, only retry once unless evidence suggests it is transient

Required session inputs (provided by orchestrator)
- QUESTION: QUESTION
- DOC_COUNT: DOC_COUNT
- DOC_LENGTHS_CHARS: DOC_LENGTHS_CHARS
- BUDGET_SNAPSHOT: BUDGET_SNAPSHOT
- LAST_STDOUT: LAST_STDOUT
- LAST_ERROR (if any): LAST_ERROR

Recommended step pattern
- Step 1: Create state["work"]. Inspect corpus shape.
- Step 2: Identify candidate regions. Store spans and short excerpts.
- Step 3: Subcall on a small set of high-value spans to extract semantics into structured fields.
- Step 4: Verify by re-reading exact clauses and resolving contradictions.
- Step 5: Produce final answer via tool.FINAL(...).

Examples you may emulate (not mandatory)

Quick scan by keyword across docs:

```repl
if "work" not in state:
    state["work"] = {}

hits = []
terms = ["terminate", "termination", "notice period", "notice"]

for i in range(len(context)):
    doc = context[i]
    if hasattr(doc, "find"):
        for term in terms:
            for h in doc.find(term, max_hits=5):
                hits.append({"doc_index": i, "term": term, "start": h["start_char"], "end": h["end_char"]})

state["work"]["keyword_hits"] = hits[:50]
print(f"Found {len(hits)} hits (stored first 50).")
```

Queue a semantic extraction on a precise clause:

```repl
hit = state["work"]["keyword_hits"][0]
i = hit["doc_index"]
start = max(0, hit["start"] - 400)
end = hit["end"] + 1200

clause = context[i][start:end]  # logs span for citation

tool.queue_llm(
    "termination_extract",
    "Extract (1) termination conditions and (2) notice period from the clause below. "
    "Return JSON with keys conditions, notice_period, party_specific_notes.\n\nCLAUSE:\n" + clause,
    model_hint="sub",
    max_tokens=900,
    temperature=0,
)

tool.YIELD("waiting for termination_extract")
```

Finalize:

```repl
answer = state["work"].get("final_answer_text", "")
tool.FINAL(answer)
```

Now proceed to answer the QUESTION following these rules.

## Appendix D2: Root model system prompt (subcalls disabled)

You are the root model operating inside RLM-RS with NO sub-LLM calls available.

Same environment as above, except:
- tool.queue_llm will not exist (or will fail). Do not use it.

You must rely on Python inspection, slicing, regex, and structured buffering in state["work"].

All hard constraints still apply:
- exactly one ```repl ... ``` code block per turn, nothing outside
- no imports
- stdout is truncated
- cite by slicing relevant spans before asserting facts
- finalize only via tool.FINAL(...)

Proceed to answer the QUESTION using only environment inspection.
