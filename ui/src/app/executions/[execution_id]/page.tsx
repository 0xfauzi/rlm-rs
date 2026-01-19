"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { ApiClient, ApiError } from "../../../lib/api-client";
import type {
  Budgets,
  BudgetsConsumed,
  ExecutionStepSnapshot,
  ExecutionStatus,
  ExecutionStatusResponse,
  JsonValue,
  SpanRef,
  ToolRequestsEnvelope,
} from "../../../lib/types";
import { useApp } from "../../../contexts/AppContext";
import { useToast } from "../../../contexts/ToastContext";
import { SkeletonCard } from "../../../components/ui/Skeleton";
import { EmptyState } from "../../../components/ui/EmptyState";
import { ErrorPanel } from "../../../components/ui/ErrorPanel";

const STATUS_STYLES: Record<string, string> = {
  PENDING: "bg-slate-100 text-slate-700",
  RUNNING: "bg-amber-100 text-amber-900",
  COMPLETED: "bg-emerald-100 text-emerald-900",
  FAILED: "bg-rose-100 text-rose-900",
  CANCELLED: "bg-slate-100 text-slate-700",
  TIMEOUT: "bg-rose-100 text-rose-900",
  BUDGET_EXCEEDED: "bg-rose-100 text-rose-900",
  MAX_TURNS_EXCEEDED: "bg-rose-100 text-rose-900",
};

function formatTimestamp(value?: string | null) {
  if (!value) {
    return "-";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat("en-US", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function formatNumber(value?: number | null) {
  if (value === null || value === undefined) {
    return "-";
  }
  return `${value}`;
}

function computeDurationSeconds(
  startedAt: string | null | undefined,
  completedAt: string | null | undefined,
  now: number,
  fallback?: number | null,
) {
  if (startedAt) {
    const start = new Date(startedAt).getTime();
    if (!Number.isNaN(start)) {
      const end = completedAt ? new Date(completedAt).getTime() : now;
      if (!Number.isNaN(end)) {
        return Math.max(0, Math.floor((end - start) / 1000));
      }
    }
  }
  if (fallback !== null && fallback !== undefined) {
    return Math.max(0, Math.floor(fallback));
  }
  return null;
}

function truncateChecksum(checksum: string) {
  if (checksum.length <= 16) {
    return checksum;
  }
  return `${checksum.slice(0, 8)}...${checksum.slice(-6)}`;
}

function buildCitationLink(citation: SpanRef) {
  const params = new URLSearchParams({
    tenant_id: citation.tenant_id,
    session_id: citation.session_id,
    doc_id: citation.doc_id,
    doc_index: `${citation.doc_index}`,
    start_char: `${citation.start_char}`,
    end_char: `${citation.end_char}`,
    checksum: citation.checksum,
  });
  return `/citations?${params.toString()}`;
}

function JsonTree({ value, label }: { value: JsonValue | null; label?: string }) {
  if (value === null || value === undefined) {
    return <span className="text-slate-500">null</span>;
  }
  if (typeof value !== "object") {
    return <span className="text-slate-700">{String(value)}</span>;
  }
  if (Array.isArray(value)) {
    return (
      <details open className="space-y-2">
        <summary className="cursor-pointer text-sm font-semibold text-slate-700">
          {label ?? `Array(${value.length})`}
        </summary>
        <div className="space-y-2 border-l border-slate-200 pl-4">
          {value.map((entry, index) => (
            <div key={index} className="text-sm text-slate-600">
              <span className="mr-2 text-xs uppercase tracking-[0.2em] text-slate-400">
                [{index}]
              </span>
              <JsonTree value={entry as JsonValue} />
            </div>
          ))}
        </div>
      </details>
    );
  }

  const entries = Object.entries(value as Record<string, JsonValue>);
  return (
    <details open className="space-y-2">
      <summary className="cursor-pointer text-sm font-semibold text-slate-700">
        {label ?? `Object(${entries.length})`}
      </summary>
      <div className="space-y-2 border-l border-slate-200 pl-4">
        {entries.map(([key, entryValue]) => (
          <div key={key} className="text-sm text-slate-600">
            <span className="mr-2 text-xs uppercase tracking-[0.2em] text-slate-400">
              {key}
            </span>
            <JsonTree value={entryValue} />
          </div>
        ))}
      </div>
    </details>
  );
}

function countToolRequests(envelope?: ToolRequestsEnvelope | null) {
  const llmCount = envelope?.llm?.length ?? 0;
  const searchCount = envelope?.search?.length ?? 0;
  return { llmCount, searchCount, total: llmCount + searchCount };
}

function ProgressBar({
  label,
  value,
  max,
}: {
  label: string;
  value?: number | null;
  max?: number | null;
}) {
  const hasMax = max !== null && max !== undefined;
  const safeValue = value ?? 0;
  const percent = hasMax && max ? Math.min(100, Math.round((safeValue / max) * 100)) : 0;

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between text-xs uppercase tracking-[0.2em] text-slate-400">
        <span>{label}</span>
        <span className="text-slate-500">
          {formatNumber(value)}
          {hasMax ? ` / ${formatNumber(max)}` : ""}
        </span>
      </div>
      <div className="h-2 rounded-full bg-slate-100">
        <div
          className="h-2 rounded-full bg-slate-900 transition-all"
          style={{ width: hasMax ? `${percent}%` : "0%" }}
        />
      </div>
    </div>
  );
}

export default function ExecutionDetailPage() {
  const params = useParams();
  const executionId = typeof params.execution_id === "string" ? params.execution_id : "";
  const { config } = useApp();
  const { showToast } = useToast();

  const apiClient = useMemo(
    () => new ApiClient(config.apiBaseUrl, config.devKey),
    [config.apiBaseUrl, config.devKey],
  );

  const [execution, setExecution] = useState<ExecutionStatusResponse | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [stepHistory, setStepHistory] = useState<ExecutionStepSnapshot[]>([]);
  const [isLoadingSteps, setIsLoadingSteps] = useState(true);
  const [stepsError, setStepsError] = useState<Error | null>(null);
  const [isCancelling, setIsCancelling] = useState(false);

  const refreshExecution = useCallback(async () => {
    if (!executionId) {
      return;
    }
    try {
      const payload = await apiClient.getExecution(executionId);
      setExecution(payload);
      setError(null);
    } catch (err) {
      if (err instanceof Error) {
        setError(err);
      } else {
        setError(new Error("Failed to load execution."));
      }
    } finally {
      setIsLoading(false);
    }
  }, [apiClient, executionId]);

  const refreshSteps = useCallback(async () => {
    if (!executionId) {
      return;
    }
    try {
      const payload = await apiClient.getExecutionSteps(executionId);
      setStepHistory(payload.steps ?? []);
      setStepsError(null);
    } catch (err) {
      if (err instanceof Error) {
        setStepsError(err);
      } else {
        setStepsError(new Error("Failed to load step history."));
      }
    } finally {
      setIsLoadingSteps(false);
    }
  }, [apiClient, executionId]);

  useEffect(() => {
    void refreshExecution();
  }, [refreshExecution]);

  useEffect(() => {
    void refreshSteps();
  }, [refreshSteps]);

  useEffect(() => {
    if (!execution?.status || (execution.status !== "RUNNING" && execution.status !== "PENDING")) {
      return undefined;
    }
    const interval = window.setInterval(() => {
      setNow(Date.now());
    }, 1000);
    return () => window.clearInterval(interval);
  }, [execution?.status]);

  useEffect(() => {
    if (!execution?.status || (execution.status !== "RUNNING" && execution.status !== "PENDING")) {
      return undefined;
    }
    const interval = window.setInterval(() => {
      void refreshExecution();
    }, 2000);
    return () => window.clearInterval(interval);
  }, [execution?.status, refreshExecution]);

  useEffect(() => {
    if (!execution?.status || (execution.status !== "RUNNING" && execution.status !== "PENDING")) {
      return undefined;
    }
    const interval = window.setInterval(() => {
      void refreshSteps();
    }, 2000);
    return () => window.clearInterval(interval);
  }, [execution?.status, refreshSteps]);

  const handleCopy = useCallback(
    async (value: string) => {
      try {
        await navigator.clipboard.writeText(value);
        showToast("Copied to clipboard", "success", 2000);
      } catch {
        showToast("Copy failed", "error", 2000);
      }
    },
    [showToast],
  );

  const isRunning = execution?.status === "RUNNING";

  const handleCancel = useCallback(async () => {
    if (!executionId || !isRunning || isCancelling) {
      return;
    }
    setIsCancelling(true);
    try {
      const payload = await apiClient.cancelExecution(executionId);
      setExecution(payload);
      if (payload.status === "CANCELLED") {
        showToast("Execution cancelled", "success", 2000);
      } else {
        showToast(`Execution is ${payload.status}`, "success", 2000);
      }
    } catch (err) {
      const message = err instanceof ApiError ? err.message : "Failed to cancel execution.";
      showToast(message, "error", 3000);
    } finally {
      setIsCancelling(false);
    }
  }, [apiClient, executionId, isRunning, isCancelling, showToast]);

  const durationSeconds = computeDurationSeconds(
    execution?.started_at,
    execution?.completed_at,
    now,
    execution?.budgets_consumed?.total_seconds ?? null,
  );

  const statusStyle = STATUS_STYLES[execution?.status ?? ""] ?? "bg-slate-100 text-slate-700";
  const modeLabel = execution?.mode === "RUNTIME" ? "Runtime" : "Answerer";

  const budgetsRequested: Budgets | null | undefined = execution?.budgets_requested;
  const budgetsConsumed: BudgetsConsumed | null | undefined = execution?.budgets_consumed;

  const citations = execution?.citations ?? [];
  const answer = execution?.answer ?? "";

  const status = execution?.status as ExecutionStatus | undefined;
  const showWaiting = status === "RUNNING" || status === "PENDING";
  const showError = status && status !== "COMPLETED" && !showWaiting;

  return (
    <div className="space-y-6">
      <Link
        href="/sessions"
        className="text-xs font-semibold uppercase tracking-[0.3em] text-slate-400"
      >
        Back to Sessions
      </Link>

      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="space-y-2">
          <p className="text-xs font-semibold uppercase tracking-[0.35em] text-slate-400">
            Execution Detail
          </p>
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="text-2xl font-semibold text-slate-900">
              {executionId || "Execution"}
            </h1>
            {execution ? (
              <span
                className={`rounded-full px-3 py-1 text-xs font-semibold uppercase tracking-[0.25em] ${statusStyle}`}
              >
                {execution.status}
              </span>
            ) : null}
            <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold uppercase tracking-[0.25em] text-slate-500">
              {modeLabel}
            </span>
          </div>
        </div>
        <div className="flex flex-wrap gap-3">
          <button
            type="button"
            onClick={handleCancel}
            disabled={!isRunning || isCancelling}
            className="rounded-full border border-rose-200 px-3 py-1 text-xs font-semibold uppercase tracking-[0.2em] text-rose-600 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {isCancelling ? "Cancelling..." : "Cancel"}
          </button>
          <button
            type="button"
            onClick={() => handleCopy(executionId)}
            className="rounded-full border border-slate-200 px-3 py-1 text-xs font-semibold uppercase tracking-[0.2em] text-slate-500"
          >
            Copy Execution ID
          </button>
        </div>
      </header>

      {isLoading ? (
        <div className="space-y-4">
          <SkeletonCard lines={4} />
          <SkeletonCard lines={6} />
        </div>
      ) : null}

      {!isLoading && error ? (
        <ErrorPanel error={error} onRetry={() => void refreshExecution()} />
      ) : null}

      {!isLoading && !error && execution ? (
        <div className="space-y-6">
          <div className="grid gap-6 lg:grid-cols-[1.1fr_1.4fr]">
            <section className="rounded-3xl border border-slate-200 bg-white p-6 shadow-sm">
              <div className="flex items-center justify-between">
                <h2 className="text-lg font-semibold text-slate-900">Execution</h2>
                <span className="rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">
                  {formatTimestamp(execution.started_at)}
                </span>
              </div>

              <div className="mt-5 grid gap-4">
                <div className="rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-600">
                  <div className="flex justify-between">
                    <span className="text-xs uppercase tracking-[0.2em] text-slate-400">
                      Turn count
                    </span>
                    <span className="font-semibold text-slate-700">
                      {formatNumber(budgetsConsumed?.turns)}
                    </span>
                  </div>
                  <div className="mt-2 flex justify-between">
                    <span className="text-xs uppercase tracking-[0.2em] text-slate-400">
                      Total seconds
                    </span>
                    <span className="font-semibold text-slate-700">
                      {durationSeconds !== null ? `${durationSeconds}s` : "-"}
                    </span>
                  </div>
                  <div className="mt-2 flex justify-between">
                    <span className="text-xs uppercase tracking-[0.2em] text-slate-400">
                      LLM subcalls
                    </span>
                    <span className="font-semibold text-slate-700">
                      {formatNumber(budgetsConsumed?.llm_subcalls)}
                    </span>
                  </div>
                </div>

                <div className="rounded-2xl border border-slate-200 bg-white p-4">
                  <p className="text-xs font-semibold uppercase tracking-[0.25em] text-slate-400">
                    Budgets
                  </p>
                  <div className="mt-4 grid gap-4">
                    <ProgressBar
                      label="Turns"
                      value={budgetsConsumed?.turns}
                      max={budgetsRequested?.max_turns}
                    />
                    <ProgressBar
                      label="Total seconds"
                      value={budgetsConsumed?.total_seconds}
                      max={budgetsRequested?.max_total_seconds}
                    />
                    <ProgressBar
                      label="LLM subcalls"
                      value={budgetsConsumed?.llm_subcalls}
                      max={budgetsRequested?.max_llm_subcalls}
                    />
                  </div>
                </div>

                <div className="rounded-2xl border border-slate-200 bg-white p-4 text-sm text-slate-600">
                  <div className="flex items-center justify-between">
                    <span className="text-xs uppercase tracking-[0.2em] text-slate-400">
                      Started
                    </span>
                    <span>{formatTimestamp(execution.started_at)}</span>
                  </div>
                  <div className="mt-2 flex items-center justify-between">
                    <span className="text-xs uppercase tracking-[0.2em] text-slate-400">
                      Completed
                    </span>
                    <span>{formatTimestamp(execution.completed_at)}</span>
                  </div>
                </div>
              </div>
            </section>

            <section className="space-y-6">
              <div className="rounded-3xl border border-slate-200 bg-white p-6 shadow-sm">
                <div className="flex items-center justify-between">
                  <h2 className="text-lg font-semibold text-slate-900">Answer</h2>
                  <span className="text-xs uppercase tracking-[0.25em] text-slate-400">
                    {execution.status}
                  </span>
                </div>
                <div className="mt-4 min-h-[120px] rounded-2xl border border-slate-200 bg-slate-50 p-4 text-sm text-slate-600">
                  {showWaiting ? (
                    <div className="flex items-center gap-2 text-slate-500">
                      <span className="h-2 w-2 animate-pulse rounded-full bg-amber-500" />
                      <span>Waiting for answer...</span>
                    </div>
                  ) : null}
                  {showError ? (
                    <div className="text-rose-600">
                      Execution ended with status: {execution.status}
                    </div>
                  ) : null}
                  {!showWaiting && !showError ? (
                    <p className="whitespace-pre-wrap text-slate-700">
                      {answer || "No answer available."}
                    </p>
                  ) : null}
                </div>
              </div>

              <div className="rounded-3xl border border-slate-200 bg-white p-6 shadow-sm">
                <div className="flex items-center justify-between">
                  <h2 className="text-lg font-semibold text-slate-900">Citations</h2>
                  <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">
                    {citations.length} refs
                  </span>
                </div>
                <div className="mt-4 space-y-3">
                  {citations.length === 0 ? (
                    <EmptyState
                      title="No citations yet"
                      description="Citations will appear after the execution finishes."
                    />
                  ) : null}
                  {citations.map((citation, index) => (
                    <div
                      key={`${citation.checksum}-${index}`}
                      className="rounded-2xl border border-slate-200 p-4"
                    >
                      <div className="flex flex-wrap items-center justify-between gap-3">
                        <div>
                          <p className="text-xs uppercase tracking-[0.25em] text-slate-400">
                            Doc {citation.doc_index}
                          </p>
                          <p className="text-sm font-semibold text-slate-700">
                            {citation.start_char} - {citation.end_char}
                          </p>
                          <p className="mt-1 text-xs text-slate-500">
                            {truncateChecksum(citation.checksum)}
                          </p>
                        </div>
                        <Link
                          href={buildCitationLink(citation)}
                          className="rounded-full border border-slate-200 px-3 py-2 text-xs font-semibold uppercase tracking-[0.2em] text-slate-600"
                        >
                          Inspect
                        </Link>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </section>
          </div>

          <section className="rounded-3xl border border-slate-200 bg-white p-6 shadow-sm">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h2 className="text-lg font-semibold text-slate-900">Step History</h2>
                <p className="text-sm text-slate-500">
                  Polling every 2 seconds while the execution is running.
                </p>
              </div>
              <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">
                {stepHistory.length} steps
              </span>
            </div>

            <div className="mt-5 space-y-4">
              {isLoadingSteps ? <SkeletonCard lines={5} /> : null}
              {!isLoadingSteps && stepsError ? (
                <ErrorPanel error={stepsError} onRetry={() => void refreshSteps()} />
              ) : null}
              {!isLoadingSteps && !stepsError && stepHistory.length === 0 ? (
                <EmptyState
                  title="No steps recorded yet"
                  description="Steps appear after the execution runs its first turn."
                />
              ) : null}
              {!isLoadingSteps && !stepsError
                ? stepHistory.map((step) => {
                    const toolCounts = countToolRequests(step.tool_requests ?? null);
                    const spanCount = step.span_log?.length ?? 0;
                    const stdoutLength = step.stdout ? step.stdout.length : 0;
                    const stateCharLength =
                      typeof step.summary?.char_length === "number"
                        ? step.summary.char_length
                        : null;
                    const stateByteLength =
                      typeof step.summary?.byte_length === "number"
                        ? step.summary.byte_length
                        : null;
                    const statusLabel =
                      step.success === true
                        ? "Success"
                        : step.success === false
                          ? "Error"
                          : "Pending";
                    const statusStyle =
                      step.success === true
                        ? "bg-emerald-100 text-emerald-800"
                        : step.success === false
                          ? "bg-rose-100 text-rose-800"
                          : "bg-slate-100 text-slate-600";
                    return (
                      <div
                        key={`step-${step.turn_index}`}
                        className="rounded-3xl border border-slate-200 bg-slate-50 p-5"
                      >
                        <div className="flex flex-wrap items-start justify-between gap-3">
                          <div className="space-y-2">
                            <p className="text-xs font-semibold uppercase tracking-[0.3em] text-slate-400">
                              Step {step.turn_index}
                            </p>
                            <div className="flex flex-wrap items-center gap-2">
                              <span
                                className={`rounded-full px-3 py-1 text-xs font-semibold uppercase tracking-[0.2em] ${statusStyle}`}
                              >
                                {statusLabel}
                              </span>
                              {step.final?.is_final ? (
                                <span className="rounded-full bg-indigo-100 px-3 py-1 text-xs font-semibold uppercase tracking-[0.2em] text-indigo-700">
                                  Final
                                </span>
                              ) : null}
                              {step.error ? (
                                <span className="rounded-full bg-rose-50 px-3 py-1 text-xs font-semibold uppercase tracking-[0.2em] text-rose-600">
                                  Error
                                </span>
                              ) : null}
                            </div>
                          </div>
                          <div className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-400">
                            {formatTimestamp(step.updated_at)}
                          </div>
                        </div>

                        <div className="mt-4 flex flex-wrap gap-3 text-xs text-slate-500">
                          <span className="rounded-full bg-white px-3 py-1">
                            stdout {formatNumber(stdoutLength)} chars
                          </span>
                          <span className="rounded-full bg-white px-3 py-1">
                            tool requests {formatNumber(toolCounts.total)}
                          </span>
                          <span className="rounded-full bg-white px-3 py-1">
                            spans {formatNumber(spanCount)}
                          </span>
                          {step.summary ? (
                            <span className="rounded-full bg-white px-3 py-1">
                              state chars {formatNumber(stateCharLength)}
                            </span>
                          ) : null}
                          {step.summary ? (
                            <span className="rounded-full bg-white px-3 py-1">
                              state bytes {formatNumber(stateByteLength)}
                            </span>
                          ) : null}
                        </div>

                        <div className="mt-5 grid gap-4 lg:grid-cols-2">
                          <div className="rounded-2xl border border-slate-200 bg-white p-4">
                            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-400">
                              Stdout
                            </p>
                            <pre className="mt-3 max-h-64 overflow-auto whitespace-pre-wrap text-sm text-slate-700">
                              {step.stdout || "No stdout output."}
                            </pre>
                          </div>

                          <div className="rounded-2xl border border-slate-200 bg-white p-4">
                            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-400">
                              State
                            </p>
                            <div className="mt-3">
                              <JsonTree value={(step.state ?? null) as JsonValue | null} />
                            </div>
                            {step.checksum ? (
                              <p className="mt-3 text-xs text-slate-400">
                                checksum {step.checksum}
                              </p>
                            ) : null}
                          </div>

                          <div className="rounded-2xl border border-slate-200 bg-white p-4">
                            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-400">
                              Tool requests
                            </p>
                            <div className="mt-3">
                              {step.tool_requests ? (
                                <JsonTree
                                  value={step.tool_requests as unknown as JsonValue}
                                  label="Tool requests"
                                />
                              ) : (
                                <span className="text-sm text-slate-500">
                                  No tool requests.
                                </span>
                              )}
                            </div>
                            {toolCounts.total ? (
                              <div className="mt-3 flex flex-wrap gap-2 text-xs text-slate-400">
                                <span>llm {toolCounts.llmCount}</span>
                                <span>search {toolCounts.searchCount}</span>
                              </div>
                            ) : null}
                          </div>

                          <div className="rounded-2xl border border-slate-200 bg-white p-4">
                            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-400">
                              Span log
                            </p>
                            <div className="mt-3 space-y-2 text-sm text-slate-600">
                              {spanCount === 0 ? (
                                <span className="text-slate-500">No spans logged.</span>
                              ) : (
                                step.span_log?.map((span, index) => (
                                  <div
                                    key={`span-${step.turn_index}-${index}`}
                                    className="flex flex-wrap items-center gap-3"
                                  >
                                    <span className="rounded-full bg-slate-100 px-2 py-1 text-xs uppercase tracking-[0.2em] text-slate-500">
                                      Doc {span.doc_index}
                                    </span>
                                    <span>
                                      {span.start_char} - {span.end_char}
                                    </span>
                                    {span.tag ? (
                                      <span className="rounded-full bg-slate-100 px-2 py-1 text-xs uppercase tracking-[0.2em] text-slate-500">
                                        {span.tag}
                                      </span>
                                    ) : null}
                                  </div>
                                ))
                              )}
                            </div>
                          </div>

                          <div className="rounded-2xl border border-slate-200 bg-white p-4">
                            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-400">
                              Final
                            </p>
                            <div className="mt-3 text-sm text-slate-600">
                              {step.final ? (
                                <div className="space-y-3">
                                  <span className="text-xs uppercase tracking-[0.2em] text-slate-400">
                                    {step.final.is_final ? "Finalized" : "Not final"}
                                  </span>
                                  {step.final.answer ? (
                                    <pre className="whitespace-pre-wrap text-sm text-slate-700">
                                      {step.final.answer}
                                    </pre>
                                  ) : null}
                                  <JsonTree
                                    value={step.final as unknown as JsonValue}
                                    label="Final payload"
                                  />
                                </div>
                              ) : (
                                <span className="text-slate-500">No final output.</span>
                              )}
                            </div>
                          </div>

                          <div className="rounded-2xl border border-slate-200 bg-white p-4">
                            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-400">
                              Error
                            </p>
                            <div className="mt-3 text-sm text-slate-600">
                              {step.error ? (
                                <div className="space-y-3">
                                  <p className="text-sm font-semibold text-rose-600">
                                    {step.error.message}
                                  </p>
                                  <JsonTree
                                    value={step.error as unknown as JsonValue}
                                    label="Error payload"
                                  />
                                </div>
                              ) : (
                                <span className="text-slate-500">No error reported.</span>
                              )}
                            </div>
                          </div>
                        </div>
                      </div>
                    );
                  })
                : null}
            </div>
          </section>
        </div>
      ) : null}
    </div>
  );
}
