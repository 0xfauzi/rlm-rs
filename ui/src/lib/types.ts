export type JsonValue =
  | string
  | number
  | boolean
  | null
  | JsonValue[]
  | { [key: string]: JsonValue };

export type SessionStatus = "CREATING" | "READY" | "FAILED" | "EXPIRED" | "DELETING";
export type IngestStatus =
  | "REGISTERED"
  | "PARSING"
  | "PARSED"
  | "INDEXING"
  | "INDEXED"
  | "FAILED";
export type ExecutionStatus =
  | "PENDING"
  | "RUNNING"
  | "COMPLETED"
  | "FAILED"
  | "CANCELLED"
  | "TIMEOUT"
  | "BUDGET_EXCEEDED"
  | "MAX_TURNS_EXCEEDED";
export type ExecutionMode = "ANSWERER" | "RUNTIME";

export interface SessionOptions {
  enable_search?: boolean | null;
  readiness_mode?: "LAX" | "STRICT" | null;
}

export interface Session {
  id: string;
  tenant_id: string;
  status: SessionStatus;
  readiness_mode: "LAX" | "STRICT";
  docs: Document[];
  options?: SessionOptions | null;
  ttl_seconds?: number | null;
  created_at: string;
  expires_at: string;
}

export interface Document {
  id: string;
  session_id: string;
  source_name: string;
  mime_type: string;
  raw_s3_uri: string;
  text_s3_uri?: string | null;
  meta_s3_uri?: string | null;
  offsets_s3_uri?: string | null;
  text_checksum?: string | null;
  ingest_status: IngestStatus;
}

export interface Budgets {
  max_turns?: number | null;
  max_total_seconds?: number | null;
  max_step_seconds?: number | null;
  max_spans_total?: number | null;
  max_spans_per_step?: number | null;
  max_tool_requests_per_step?: number | null;
  max_llm_subcalls?: number | null;
  max_llm_prompt_chars?: number | null;
  max_total_llm_prompt_chars?: number | null;
  max_stdout_chars?: number | null;
}

export interface BudgetsConsumed {
  turns?: number | null;
  llm_subcalls?: number | null;
  total_seconds?: number | null;
}

export interface ModelsConfig {
  root_model?: string | null;
  sub_model?: string | null;
}

export interface ExecutionOptions {
  return_trace?: boolean | null;
  redact_trace?: boolean | null;
  synchronous?: boolean | null;
  synchronous_timeout_seconds?: number | null;
}

export interface SpanRef {
  tenant_id: string;
  session_id: string;
  doc_id: string;
  doc_index: number;
  start_char: number;
  end_char: number;
  checksum: string;
}

export interface Citation {
  doc_index: number;
  start_char: number;
  end_char: number;
  checksum: string;
  text: string;
}

export interface ToolRequest {
  type: string;
  payload: JsonValue;
}

export interface ToolRequestsEnvelope {
  llm: ToolRequest[];
  search: ToolRequest[];
}

export interface StepError {
  code: string;
  message: string;
  details?: { [key: string]: JsonValue } | null;
}

export interface StepFinal {
  is_final: boolean;
  answer?: string | null;
}

export interface SpanLogEntry {
  doc_index: number;
  start_char: number;
  end_char: number;
  tag?: string | null;
}

export interface StepResult {
  success: boolean;
  stdout: string;
  state: JsonValue | string | null;
  span_log: SpanLogEntry[];
  tool_requests?: ToolRequestsEnvelope | null;
  final?: StepFinal | null;
  error?: StepError | null;
}

export interface ExecutionStepSnapshot {
  turn_index: number;
  updated_at?: string | null;
  success?: boolean | null;
  stdout?: string | null;
  state?: JsonValue | string | null;
  span_log?: SpanLogEntry[] | null;
  tool_requests?: ToolRequestsEnvelope | null;
  final?: StepFinal | null;
  error?: StepError | null;
  checksum?: string | null;
  summary?: { [key: string]: JsonValue } | null;
  timings?: { [key: string]: JsonValue } | null;
}

export interface ExecutionStepHistoryResponse {
  steps: ExecutionStepSnapshot[];
}

export type CodeLogSource = "ROOT" | "SUB" | "TOOL";
export type CodeLogKind = "REPL" | "TOOL_REQUEST" | "TOOL_RESULT";

export interface CodeLogEntry {
  execution_id: string;
  sequence: number;
  created_at: string;
  source: CodeLogSource;
  kind: CodeLogKind;
  model_name?: string | null;
  tool_type?: string | null;
  content: JsonValue;
}

export interface CodeLogResponse {
  entries: CodeLogEntry[];
  next_cursor?: string | null;
}

export interface Execution {
  id: string;
  session_id: string;
  tenant_id: string;
  mode: ExecutionMode;
  status: ExecutionStatus;
  question?: string | null;
  answer?: string | null;
  citations?: Citation[] | null;
  budgets_consumed?: BudgetsConsumed | null;
  turn_count?: number | null;
  started_at?: string | null;
  completed_at?: string | null;
}

export type EvaluationBaselineStatus = "COMPLETED" | "SKIPPED" | "RUNNING";

export interface EvaluationJudgeScores {
  answer_relevancy?: number | null;
  faithfulness?: number | null;
  faithfulness_skip_reason?: string | null;
}

export interface EvaluationJudgeMetrics {
  answerer?: EvaluationJudgeScores | null;
  baseline?: EvaluationJudgeScores | null;
}

export interface EvaluationRecord {
  evaluation_id: string;
  tenant_id: string;
  session_id: string;
  execution_id: string;
  mode: ExecutionMode;
  question: string;
  answer?: string | null;
  baseline_status: EvaluationBaselineStatus;
  baseline_skip_reason?: string | null;
  baseline_answer?: string | null;
  baseline_input_tokens?: number | null;
  baseline_context_window?: number | null;
  judge_metrics?: EvaluationJudgeMetrics | null;
  created_at: string;
}

export interface ErrorEnvelope {
  code: string;
  message: string;
  details?: { [key: string]: JsonValue } | null;
  request_id?: string | null;
}

export interface ErrorEnvelopeResponse {
  error: ErrorEnvelope;
}

export interface HealthResponse {
  status: "ok";
}

export interface CreateSessionRequest {
  ttl_minutes?: number | null;
  docs: Array<{
    source_name: string;
    mime_type: string;
    raw_s3_uri: string;
    raw_s3_version_id?: string | null;
    raw_s3_etag?: string | null;
  }>;
  options?: SessionOptions | null;
}

export interface CreateSessionResponse {
  session_id: string;
  status: SessionStatus;
  created_at: string;
  expires_at: string;
  docs: Array<{
    doc_id: string;
    doc_index: number;
    ingest_status: IngestStatus;
    text_s3_uri?: string | null;
    meta_s3_uri?: string | null;
    offsets_s3_uri?: string | null;
  }>;
}

export interface GetSessionResponse {
  session_id: string;
  status: SessionStatus;
  created_at: string;
  expires_at: string;
  budgets_default?: Budgets | null;
  readiness: {
    parsed_ready: boolean;
    search_ready: boolean;
    ready: boolean;
  };
  docs: Array<{
    doc_id: string;
    doc_index: number;
    source_name: string;
    mime_type: string;
    ingest_status: IngestStatus;
    text_s3_uri?: string | null;
    meta_s3_uri?: string | null;
    offsets_s3_uri?: string | null;
  }>;
}

export interface ListSessionsResponse {
  sessions: Session[];
  next_cursor?: string | null;
}

export interface CreateExecutionRequest {
  question: string;
  budgets?: Budgets | null;
  models?: ModelsConfig | null;
  options?: ExecutionOptions | null;
}

export interface CreateExecutionResponse {
  execution_id: string;
  status: ExecutionStatus;
}

export interface CreateRuntimeExecutionResponse {
  execution_id: string;
  status: ExecutionStatus;
}

export interface ExecutionStatusResponse {
  execution_id: string;
  mode?: ExecutionMode | null;
  status: ExecutionStatus;
  question?: string | null;
  answer?: string | null;
  citations?: SpanRef[] | null;
  budgets_requested?: Budgets | null;
  budgets_consumed?: BudgetsConsumed | null;
  started_at?: string | null;
  completed_at?: string | null;
  trace_s3_uri?: string | null;
}

export interface ExecutionListItem {
  execution_id: string;
  session_id: string;
  tenant_id: string;
  mode?: ExecutionMode | null;
  status: ExecutionStatus;
  question?: string | null;
  answer?: string | null;
  citations?: SpanRef[] | null;
  budgets_consumed?: BudgetsConsumed | null;
  started_at?: string | null;
  completed_at?: string | null;
}

export interface ListExecutionsResponse {
  executions: ExecutionListItem[];
  next_cursor?: string | null;
}

export interface ToolResolveRequest {
  tool_requests: {
    llm: Array<{
      type: "llm";
      key: string;
      prompt: string;
      model_hint?: string | null;
      max_tokens: number;
      temperature?: number | null;
      metadata?: { [key: string]: JsonValue } | null;
    }>;
    search: Array<{
      type: "search";
      key: string;
      query: string;
      k: number;
      filters?: { [key: string]: JsonValue } | null;
    }>;
  };
  models: {
    sub_model: string;
  };
}

export interface ToolResolveResponse {
  tool_results: {
    llm: { [key: string]: { text: string; meta?: { [key: string]: JsonValue } | null } };
    search: {
      [key: string]: {
        hits: Array<{
          doc_index: number;
          start_char: number;
          end_char: number;
          score?: number | null;
          preview?: string | null;
        }>;
        meta?: { [key: string]: JsonValue } | null;
      };
    };
  };
  statuses: { [key: string]: "pending" | "resolved" | "error" };
}

export interface SpanGetResponse {
  text: string;
  ref: SpanRef;
}

export interface CitationVerifyResponse {
  valid: boolean;
  text?: string | null;
  source_name?: string | null;
  char_range?: { start_char: number; end_char: number } | null;
}

export interface StepRequest {
  code: string;
}
