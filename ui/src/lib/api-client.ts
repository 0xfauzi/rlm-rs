import {
  type CitationVerifyResponse,
  type CodeLogResponse,
  type CreateExecutionRequest,
  type CreateExecutionResponse,
  type CreateRuntimeExecutionResponse,
  type CreateSessionRequest,
  type CreateSessionResponse,
  type ErrorEnvelope,
  type EvaluationRecord,
  type ExecutionMode,
  type ExecutionStatus,
  type ExecutionStepHistoryResponse,
  type ExecutionStatusResponse,
  type GetSessionResponse,
  type HealthResponse,
  type ListExecutionsResponse,
  type ListSessionsResponse,
  type SpanGetResponse,
  type SpanRef,
  type StepRequest,
  type StepResult,
  type ToolResolveRequest,
  type ToolResolveResponse,
} from "./types";
import { addErrorLog, addRequestLog } from "./request-log";

export class ApiError extends Error {
  code: string;
  details?: ErrorEnvelope["details"] | null;
  requestId?: string | null;

  constructor(payload: ErrorEnvelope, fallbackMessage?: string) {
    super(payload.message || fallbackMessage || "Request failed");
    this.name = "ApiError";
    this.code = payload.code;
    this.details = payload.details ?? null;
    this.requestId = payload.request_id ?? null;
  }
}

const DEFAULT_ERROR: ErrorEnvelope = {
  code: "INTERNAL_ERROR",
  message: "Request failed",
  details: null,
  request_id: null,
};

function nowMs() {
  if (typeof performance !== "undefined" && typeof performance.now === "function") {
    return performance.now();
  }
  return Date.now();
}

function extractErrorEnvelope(payload: unknown): ErrorEnvelope | null {
  if (!payload || typeof payload !== "object") {
    return null;
  }
  const record = payload as Record<string, unknown>;
  const raw = record.error ?? record;
  if (raw && typeof raw === "object") {
    const candidate = raw as Record<string, unknown>;
    const code = candidate.code;
    const message = candidate.message;
    if (typeof code === "string" && typeof message === "string") {
      return {
        code,
        message,
        details: (candidate.details as ErrorEnvelope["details"]) ?? null,
        request_id: (candidate.request_id as string | null | undefined) ?? null,
      };
    }
  }
  return null;
}

export class ApiClient {
  private baseUrl: string;
  private apiKey: string;

  constructor(baseUrl: string, apiKey: string) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
    this.apiKey = apiKey;
  }

  private headers(): HeadersInit {
    return {
      Authorization: `Bearer ${this.apiKey}`,
      "Content-Type": "application/json",
    };
  }

  private async request<T>(path: string, options: RequestInit = {}): Promise<T> {
    const url = new URL(path, this.baseUrl).toString();
    const method = (options.method ?? "GET").toUpperCase();
    const startTime = nowMs();
    const requestTimestamp = new Date().toISOString();
    let didLogRequest = false;
    let didLogError = false;

    const logRequest = (status: number) => {
      if (didLogRequest) {
        return;
      }
      const latency = Math.max(0, Math.round(nowMs() - startTime));
      addRequestLog({
        timestamp: requestTimestamp,
        method,
        url,
        status,
        latency_ms: latency,
      });
      didLogRequest = true;
    };
    const logError = (error: Error) => {
      if (didLogError) {
        return;
      }
      addErrorLog({
        timestamp: new Date().toISOString(),
        message: error.message,
        stack: error.stack ?? null,
      });
      didLogError = true;
    };

    try {
      const response = await fetch(url, {
        ...options,
        headers: {
          ...this.headers(),
          ...(options.headers ?? {}),
        },
      });
      const text = await response.text();
      let payload: unknown = null;
      try {
        payload = text ? (JSON.parse(text) as unknown) : null;
      } catch (error) {
        logRequest(response.status);
        const parseError = error instanceof Error ? error : new Error("Invalid JSON response");
        logError(parseError);
        throw parseError;
      }

      logRequest(response.status);

      if (!response.ok) {
        const envelope = extractErrorEnvelope(payload) ?? DEFAULT_ERROR;
        const error = new ApiError(envelope, response.statusText);
        logError(error);
        throw error;
      }

      return payload as T;
    } catch (error) {
      if (error instanceof ApiError) {
        throw error;
      }
      logRequest(0);
      const fallback = error instanceof Error ? error : new Error("Unknown request error");
      logError(fallback);
      throw error;
    }
  }

  getHealth(): Promise<HealthResponse> {
    return this.request<HealthResponse>("/health/ready");
  }

  getSessions(): Promise<ListSessionsResponse> {
    return this.request<ListSessionsResponse>("/v1/sessions");
  }

  getSession(id: string): Promise<GetSessionResponse> {
    return this.request<GetSessionResponse>(`/v1/sessions/${id}`);
  }

  createSession(req: CreateSessionRequest): Promise<CreateSessionResponse> {
    return this.request<CreateSessionResponse>("/v1/sessions", {
      method: "POST",
      body: JSON.stringify(req),
    });
  }

  deleteSession(id: string): Promise<{ status: string }> {
    return this.request<{ status: string }>(`/v1/sessions/${id}`, {
      method: "DELETE",
    });
  }

  getExecution(id: string): Promise<ExecutionStatusResponse> {
    return this.request<ExecutionStatusResponse>(`/v1/executions/${id}`);
  }

  getExecutionEvaluation(id: string): Promise<EvaluationRecord> {
    return this.request<EvaluationRecord>(`/v1/executions/${id}/evaluation`);
  }

  recomputeExecutionEvaluation(id: string): Promise<EvaluationRecord> {
    return this.request<EvaluationRecord>(`/v1/executions/${id}/evaluation/recompute`, {
      method: "POST",
      body: JSON.stringify({}),
    });
  }

  cancelExecution(id: string): Promise<ExecutionStatusResponse> {
    return this.request<ExecutionStatusResponse>(`/v1/executions/${id}/cancel`, {
      method: "POST",
    });
  }

  getExecutionSteps(id: string): Promise<ExecutionStepHistoryResponse> {
    return this.request<ExecutionStepHistoryResponse>(`/v1/executions/${id}/steps`);
  }

  getExecutionCode(
    id: string,
    params?: { limit?: number; cursor?: string | null },
  ): Promise<CodeLogResponse> {
    const search = new URLSearchParams();
    if (params?.limit) {
      search.set("limit", String(params.limit));
    }
    if (params?.cursor) {
      search.set("cursor", params.cursor);
    }
    const suffix = search.toString();
    const path = suffix ? `/v1/executions/${id}/code?${suffix}` : `/v1/executions/${id}/code`;
    return this.request<CodeLogResponse>(path);
  }

  listExecutions(params?: {
    status?: ExecutionStatus;
    mode?: ExecutionMode;
    sessionId?: string;
    limit?: number;
    cursor?: string | null;
  }): Promise<ListExecutionsResponse> {
    const search = new URLSearchParams();
    if (params?.status) {
      search.set("status", params.status);
    }
    if (params?.mode) {
      search.set("mode", params.mode);
    }
    if (params?.sessionId) {
      search.set("session_id", params.sessionId);
    }
    if (params?.limit) {
      search.set("limit", String(params.limit));
    }
    if (params?.cursor) {
      search.set("cursor", params.cursor);
    }
    const suffix = search.toString();
    const path = suffix ? `/v1/executions?${suffix}` : "/v1/executions";
    return this.request<ListExecutionsResponse>(path);
  }

  createExecution(
    sessionId: string,
    req: CreateExecutionRequest,
  ): Promise<CreateExecutionResponse> {
    return this.request<CreateExecutionResponse>(`/v1/sessions/${sessionId}/executions`, {
      method: "POST",
      body: JSON.stringify(req),
    });
  }

  createRuntimeExecution(sessionId: string): Promise<CreateRuntimeExecutionResponse> {
    return this.request<CreateRuntimeExecutionResponse>(
      `/v1/sessions/${sessionId}/executions/runtime`,
      {
        method: "POST",
        body: JSON.stringify({}),
      },
    );
  }

  postStep(executionId: string, code: string): Promise<StepResult> {
    const payload: StepRequest = { code };
    return this.request<StepResult>(`/v1/executions/${executionId}/steps`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }

  resolveTools(
    executionId: string,
    request?: ToolResolveRequest,
  ): Promise<ToolResolveResponse> {
    const payload =
      request ??
      ({ tool_requests: { llm: [], search: [] }, models: { sub_model: "" } } as ToolResolveRequest);
    return this.request<ToolResolveResponse>(`/v1/executions/${executionId}/tools/resolve`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }

  getSpan(spanRef: SpanRef): Promise<SpanGetResponse> {
    return this.request<SpanGetResponse>("/v1/spans/get", {
      method: "POST",
      body: JSON.stringify({
        session_id: spanRef.session_id,
        doc_id: spanRef.doc_id,
        start_char: spanRef.start_char,
        end_char: spanRef.end_char,
      }),
    });
  }

  verifyCitation(spanRef: SpanRef): Promise<CitationVerifyResponse> {
    return this.request<CitationVerifyResponse>("/v1/citations/verify", {
      method: "POST",
      body: JSON.stringify({ ref: spanRef }),
    });
  }
}
