from __future__ import annotations

from typing import Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, JsonValue

JsonObject: TypeAlias = dict[str, JsonValue]
StatePayload: TypeAlias = JsonObject | str | None

SessionStatus: TypeAlias = Literal["CREATING", "READY", "FAILED", "EXPIRED", "DELETING"]
IngestStatus: TypeAlias = Literal[
    "REGISTERED", "PARSING", "PARSED", "INDEXING", "INDEXED", "FAILED"
]
ExecutionStatus: TypeAlias = Literal[
    "RUNNING",
    "COMPLETED",
    "FAILED",
    "CANCELLED",
    "TIMEOUT",
    "BUDGET_EXCEEDED",
    "MAX_TURNS_EXCEEDED",
]
ExecutionMode: TypeAlias = Literal["ANSWERER", "RUNTIME"]
ToolRequestStatus: TypeAlias = Literal["pending", "resolved", "error"]


class RLMBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Budgets(RLMBaseModel):
    max_turns: int | None = None
    max_total_seconds: int | None = None
    max_step_seconds: int | None = None
    max_spans_total: int | None = None
    max_spans_per_step: int | None = None
    max_tool_requests_per_step: int | None = None
    max_llm_subcalls: int | None = None
    max_llm_prompt_chars: int | None = None
    max_total_llm_prompt_chars: int | None = None
    max_stdout_chars: int | None = None
    max_state_chars: int | None = None


class LimitsSnapshot(RLMBaseModel):
    max_step_seconds: int | None = None
    max_spans_per_step: int | None = None
    max_tool_requests_per_step: int | None = None
    max_stdout_chars: int | None = None
    max_state_chars: int | None = None


class BudgetsConsumed(RLMBaseModel):
    turns: int | None = None
    llm_subcalls: int | None = None
    total_seconds: int | None = None


class ModelsConfig(RLMBaseModel):
    root_model: str | None = None
    sub_model: str | None = None


class SessionOptions(RLMBaseModel):
    enable_search: bool | None = None
    readiness_mode: Literal["LAX", "STRICT"] | None = None


class ExecutionOptions(RLMBaseModel):
    return_trace: bool | None = None
    redact_trace: bool | None = None
    synchronous: bool | None = None
    synchronous_timeout_seconds: int | None = None


class RuntimeStepOptions(RLMBaseModel):
    resolve_tools: bool | None = None


class SessionDocumentInput(RLMBaseModel):
    source_name: str
    mime_type: str
    raw_s3_uri: str
    raw_s3_version_id: str | None = None
    raw_s3_etag: str | None = None


class SessionDocumentStatus(RLMBaseModel):
    doc_id: str
    doc_index: int
    source_name: str
    mime_type: str
    ingest_status: IngestStatus
    text_s3_uri: str | None = None
    meta_s3_uri: str | None = None
    offsets_s3_uri: str | None = None


class SessionDocumentSummary(RLMBaseModel):
    id: str
    session_id: str
    source_name: str
    mime_type: str
    raw_s3_uri: str
    text_s3_uri: str | None = None
    meta_s3_uri: str | None = None
    offsets_s3_uri: str | None = None
    text_checksum: str | None = None
    ingest_status: IngestStatus


class SessionReadiness(RLMBaseModel):
    parsed_ready: bool
    search_ready: bool
    ready: bool


class CreateSessionRequest(RLMBaseModel):
    ttl_minutes: int | None = None
    docs: list[SessionDocumentInput]
    options: SessionOptions | None = None
    models_default: ModelsConfig | None = None
    budgets_default: Budgets | None = None


class CreateSessionResponse(RLMBaseModel):
    session_id: str
    status: SessionStatus
    created_at: str
    expires_at: str
    docs: list[SessionDocumentStatus]


class GetSessionResponse(RLMBaseModel):
    session_id: str
    status: SessionStatus
    created_at: str
    expires_at: str
    readiness: SessionReadiness
    docs: list[SessionDocumentStatus]


class DeleteSessionResponse(RLMBaseModel):
    status: Literal["DELETING"]


class SessionListItem(RLMBaseModel):
    id: str
    tenant_id: str
    status: SessionStatus
    readiness_mode: Literal["LAX", "STRICT"]
    docs: list[SessionDocumentSummary]
    options: SessionOptions | None = None
    ttl_seconds: int | None = None
    created_at: str
    expires_at: str


class ListSessionsResponse(RLMBaseModel):
    sessions: list[SessionListItem]
    next_cursor: str | None = None


class CreateExecutionRequest(RLMBaseModel):
    question: str
    models: ModelsConfig | None = None
    budgets: Budgets | None = None
    options: ExecutionOptions | None = None


class CreateExecutionResponse(RLMBaseModel):
    execution_id: str
    status: ExecutionStatus


class SpanRef(RLMBaseModel):
    tenant_id: str
    session_id: str
    doc_id: str
    doc_index: int
    start_char: int
    end_char: int
    checksum: str


class ExecutionStatusResponse(RLMBaseModel):
    execution_id: str
    mode: ExecutionMode | None = None
    status: ExecutionStatus
    answer: str | None = None
    citations: list[SpanRef] | None = None
    budgets_requested: Budgets | None = None
    budgets_consumed: BudgetsConsumed | None = None
    started_at: str | None = None
    completed_at: str | None = None
    trace_s3_uri: str | None = None


class ExecutionListItem(RLMBaseModel):
    execution_id: str
    session_id: str
    tenant_id: str
    mode: ExecutionMode | None = None
    status: ExecutionStatus
    question: str | None = None
    answer: str | None = None
    citations: list[SpanRef] | None = None
    budgets_consumed: BudgetsConsumed | None = None
    started_at: str | None = None
    completed_at: str | None = None


class ListExecutionsResponse(RLMBaseModel):
    executions: list[ExecutionListItem]
    next_cursor: str | None = None


class ExecutionWaitRequest(RLMBaseModel):
    timeout_seconds: int


class CreateRuntimeExecutionResponse(RLMBaseModel):
    execution_id: str
    status: ExecutionStatus


class StepRequest(RLMBaseModel):
    code: str
    state: StatePayload = None
    options: RuntimeStepOptions | None = None


class StepFinal(RLMBaseModel):
    is_final: bool
    answer: str | None = None


class StepError(RLMBaseModel):
    code: str
    message: str
    details: dict[str, JsonValue] | None = None


class SpanLogEntry(RLMBaseModel):
    doc_index: int
    start_char: int
    end_char: int
    tag: str | None = None


class LLMToolRequest(RLMBaseModel):
    type: Literal["llm"] = "llm"
    key: str
    prompt: str
    model_hint: str | None = None
    max_tokens: int
    temperature: float | None = None
    metadata: dict[str, JsonValue] | None = None


class SearchToolRequest(RLMBaseModel):
    type: Literal["search"] = "search"
    key: str
    query: str
    k: int
    filters: dict[str, JsonValue] | None = None


class ToolRequestsEnvelope(RLMBaseModel):
    llm: list[LLMToolRequest] = Field(default_factory=list)
    search: list[SearchToolRequest] = Field(default_factory=list)


class LLMToolResult(RLMBaseModel):
    text: str
    meta: dict[str, JsonValue] | None = None


class SearchHit(RLMBaseModel):
    doc_index: int
    start_char: int
    end_char: int
    score: float | None = None
    preview: str | None = None


class SearchToolResult(RLMBaseModel):
    hits: list[SearchHit]
    meta: dict[str, JsonValue] | None = None


class ToolResultsEnvelope(RLMBaseModel):
    llm: dict[str, LLMToolResult] = Field(default_factory=dict)
    search: dict[str, SearchToolResult] = Field(default_factory=dict)


class StepResult(RLMBaseModel):
    success: bool
    stdout: str
    state: StatePayload = None
    span_log: list[SpanLogEntry] = Field(default_factory=list)
    tool_requests: ToolRequestsEnvelope | None = None
    final: StepFinal | None = None
    error: StepError | None = None


class ExecutionStepSnapshot(RLMBaseModel):
    turn_index: int
    updated_at: str | None = None
    success: bool | None = None
    stdout: str | None = None
    state: StatePayload = None
    span_log: list[SpanLogEntry] = Field(default_factory=list)
    tool_requests: ToolRequestsEnvelope | None = None
    final: StepFinal | None = None
    error: StepError | None = None
    checksum: str | None = None
    summary: dict[str, JsonValue] | None = None


class ExecutionStepHistoryResponse(RLMBaseModel):
    steps: list[ExecutionStepSnapshot]


class ContextDocument(RLMBaseModel):
    doc_id: str
    doc_index: int
    text_s3_uri: str
    meta_s3_uri: str | None = None
    offsets_s3_uri: str | None = None


class ContextManifest(RLMBaseModel):
    docs: list[ContextDocument]


class StepEvent(RLMBaseModel):
    tenant_id: str
    session_id: str
    execution_id: str
    turn_index: int
    code: str
    state: StatePayload = None
    context_manifest: ContextManifest
    tool_results: ToolResultsEnvelope | None = None
    limits: LimitsSnapshot | None = None


class ToolResolveModels(RLMBaseModel):
    sub_model: str


class ToolResolveRequest(RLMBaseModel):
    tool_requests: ToolRequestsEnvelope
    models: ToolResolveModels


class ToolResolveResponse(RLMBaseModel):
    tool_results: ToolResultsEnvelope
    statuses: dict[str, ToolRequestStatus]


class SpanGetRequest(RLMBaseModel):
    session_id: str
    doc_id: str
    start_char: int
    end_char: int


class SpanGetResponse(RLMBaseModel):
    text: str
    ref: SpanRef


class CitationVerifyRequest(RLMBaseModel):
    ref: SpanRef


class CharRange(RLMBaseModel):
    start_char: int
    end_char: int


class CitationVerifyResponse(RLMBaseModel):
    valid: bool
    text: str | None = None
    source_name: str | None = None
    char_range: CharRange | None = None


class HealthResponse(RLMBaseModel):
    status: Literal["ok"]
