"use client";

import {
  type MouseEvent,
  type ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { ApiClient, ApiError } from "../../../lib/api-client";
import type {
  Budgets,
  BudgetsConsumed,
  CodeLogEntry,
  CodeLogSource,
  EvaluationRecord,
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
import { CodeBlock } from "../../../components/ui/CodeBlock";

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

const BASELINE_STATUS_STYLES: Record<string, string> = {
  COMPLETED: "bg-emerald-100 text-emerald-900",
  SKIPPED: "bg-slate-100 text-slate-600",
  RUNNING: "bg-amber-100 text-amber-900",
};

const EVALUATION_POLL_INTERVAL_MS = 2000;
const CODE_SOURCE_LABELS: Record<CodeLogSource, string> = {
  ROOT: "Root",
  SUB: "Sub",
  TOOL: "Tool",
};
const CODE_KIND_LABELS: Record<string, string> = {
  REPL: "Repl",
  TOOL_REQUEST: "Tool request",
  TOOL_RESULT: "Tool result",
};

const CODE_SOURCE_TOOLTIPS: Record<CodeLogSource, string> = {
  ROOT: "Root model output (drives the main loop).",
  SUB: "Subcall model output (invoked by the root).",
  TOOL: "Tool request/result handled by the orchestrator.",
};

const CODE_KIND_TOOLTIPS: Record<string, string> = {
  REPL: "Sandbox-executed Python code (the repl block).",
  TOOL_REQUEST: "A tool call queued by the sandbox for the orchestrator to resolve.",
  TOOL_RESULT: "Resolved tool output injected back into the sandbox state.",
};

const STEP_HISTORY_TOOLTIPS = {
  status: "Whether the sandbox step completed successfully.",
  final: "Step returned a final answer and ended the execution.",
  error: "Step returned an error payload.",
  stdoutChars: "Number of characters written to stdout during this step (print output).",
  toolRequests: "Tool requests queued by this step (LLM + search).",
  spans: "Span log entries recorded this step (used to derive citations).",
  stateChars: "Approximate persisted state size in characters after this step.",
  stateBytes: "Approximate persisted state size in bytes after this step.",
  llmRequests: "LLM tool requests queued by the sandbox.",
  searchRequests: "Search tool requests queued by the sandbox.",
  stdoutPanel: "Captured stdout (e.g. print output) from the sandbox step.",
  statePanel: "JSON state snapshot persisted after the step.",
  toolRequestsPanel: "Tool requests emitted by the step; resolved by the orchestrator.",
  spanLogPanel: "Span log entries (doc index + char ranges) used for citation derivation.",
  finalPanel: "Final payload returned by the step, if any.",
  errorPanel: "Error payload returned by the step, if any.",
  timingsPanel: "Per-turn timings captured by the orchestrator and runtime pipeline.",
} as const;

const TIMING_LABELS: Record<string, string> = {
  prompt_build_ms: "Prompt build",
  root_call_ms: "Root model call",
  root_parse_ms: "Root output parse",
  sandbox_ms: "Sandbox step",
  state_persist_ms: "State persist",
  tool_resolve_ms: "Tool resolve",
  tool_apply_ms: "Tool apply",
  tool_state_persist_ms: "Tool state persist",
};

const TIMING_ORDER = [
  "prompt_build_ms",
  "root_call_ms",
  "root_parse_ms",
  "sandbox_ms",
  "state_persist_ms",
  "tool_resolve_ms",
  "tool_apply_ms",
  "tool_state_persist_ms",
];

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

function formatDurationMs(value?: number | null) {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "-";
  }
  if (value >= 1000) {
    return `${(value / 1000).toFixed(2)}s`;
  }
  return `${value}ms`;
}

function formatScore(value?: number | null) {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return "-";
  }
  return value.toFixed(2);
}

const JUDGE_SKIP_REASON_LABELS: Record<string, string> = {
  CONTEXT_WINDOW_EXCEEDED: "Couldn't compute (context too large)",
};

function renderJudgeScore(
  value?: number | null,
  skipReason?: string | null,
): ReactNode {
  const formatted = formatScore(value);
  if (formatted !== "-") {
    return formatted;
  }
  if (skipReason) {
    const label = JUDGE_SKIP_REASON_LABELS[skipReason] ?? `Couldn't compute (${skipReason})`;
    return (
      <span className="text-xs text-slate-500" title={skipReason}>
        {label}
      </span>
    );
  }
  return "-";
}

function formatJson(value: JsonValue) {
  if (typeof value === "string") {
    return value;
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
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

function EvaluationPendingCard({
  title,
  description,
}: {
  title: string;
  description: string;
}) {
  return (
    <div className="rounded-3xl border border-slate-200 bg-white p-6 shadow-sm">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-slate-900">{title}</h2>
        <span className="rounded-full bg-amber-100 px-3 py-1 text-xs font-semibold uppercase tracking-[0.25em] text-amber-900">
          Running
        </span>
      </div>
      <div className="mt-4 flex items-center gap-2 text-sm text-slate-600">
        <span className="h-2 w-2 animate-pulse rounded-full bg-amber-500" />
        <span>{description}</span>
      </div>
    </div>
  );
}

function ChevronRightIcon({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 20 20"
      fill="none"
      aria-hidden="true"
      className={className}
    >
      <path
        d="M7.5 4.5l5 5-5 5"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function CollapsibleCard({
  title,
  subtitle,
  right,
  defaultOpen = true,
  children,
}: {
  title: string;
  subtitle?: string;
  right?: ReactNode;
  defaultOpen?: boolean;
  children: ReactNode;
}) {
  const [isOpen, setIsOpen] = useState(defaultOpen);
  return (
    <details
      open={isOpen}
      onToggle={(event) => setIsOpen(event.currentTarget.open)}
      className="group rounded-3xl border border-slate-200 bg-white shadow-sm"
    >
      <summary className="flex cursor-pointer list-none flex-wrap items-start justify-between gap-3 p-6 [&::-webkit-details-marker]:hidden">
        <div>
          <h2 className="text-lg font-semibold text-slate-900">{title}</h2>
          {subtitle ? <p className="mt-1 text-sm text-slate-500">{subtitle}</p> : null}
        </div>
        <div className="flex items-center gap-2">
          {right}
          <ChevronRightIcon className="h-4 w-4 text-slate-400 transition-transform group-open:rotate-90" />
        </div>
      </summary>
      <div className="px-6 pb-6">{children}</div>
    </details>
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
  const [evaluation, setEvaluation] = useState<EvaluationRecord | null>(null);
  const [isLoadingEvaluation, setIsLoadingEvaluation] = useState(false);
  const [evaluationError, setEvaluationError] = useState<Error | null>(null);
  const [evaluationNotFound, setEvaluationNotFound] = useState(false);
  const [isRecomputingEvaluation, setIsRecomputingEvaluation] = useState(false);
  const [codeEntries, setCodeEntries] = useState<CodeLogEntry[]>([]);
  const [isLoadingCode, setIsLoadingCode] = useState(true);
  const [codeError, setCodeError] = useState<Error | null>(null);
  const [codeFilters, setCodeFilters] = useState<Record<CodeLogSource, boolean>>({
    ROOT: true,
    SUB: true,
    TOOL: true,
  });
  const codeCursorRef = useRef<string | null>(null);

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

  const refreshCode = useCallback(
    async (options?: { reset?: boolean }) => {
      if (!executionId) {
        return;
      }
      const shouldReset = options?.reset ?? false;
      if (shouldReset) {
        setIsLoadingCode(true);
        setCodeError(null);
        codeCursorRef.current = null;
      }
      try {
        const payload = await apiClient.getExecutionCode(executionId, {
          limit: 200,
          cursor: shouldReset ? null : codeCursorRef.current,
        });
        const incoming = payload.entries ?? [];
        setCodeEntries((previous) => {
          if (shouldReset) {
            return incoming;
          }
          const seen = new Set(previous.map((entry) => entry.sequence));
          const merged = [...previous];
          for (const entry of incoming) {
            if (!seen.has(entry.sequence)) {
              merged.push(entry);
            }
          }
          merged.sort((a, b) => a.sequence - b.sequence);
          return merged;
        });
        if (payload.next_cursor) {
          codeCursorRef.current = payload.next_cursor;
        } else if (shouldReset && incoming.length === 0) {
          codeCursorRef.current = null;
        }
        setCodeError(null);
      } catch (err) {
        if (err instanceof Error) {
          setCodeError(err);
        } else {
          setCodeError(new Error("Failed to load code log."));
        }
      } finally {
        if (shouldReset) {
          setIsLoadingCode(false);
        }
      }
    },
    [apiClient, executionId],
  );

  const refreshEvaluation = useCallback(async () => {
    if (!executionId) {
      return;
    }
    setIsLoadingEvaluation(true);
    try {
      const payload = await apiClient.getExecutionEvaluation(executionId);
      setEvaluation(payload);
      setEvaluationError(null);
      setEvaluationNotFound(false);
    } catch (err) {
      if (err instanceof ApiError && err.code === "EXECUTION_NOT_FOUND") {
        setEvaluation(null);
        setEvaluationError(null);
        setEvaluationNotFound(true);
      } else if (err instanceof Error) {
        setEvaluation(null);
        setEvaluationError(err);
        setEvaluationNotFound(false);
      } else {
        setEvaluation(null);
        setEvaluationError(new Error("Failed to load evaluation."));
        setEvaluationNotFound(false);
      }
    } finally {
      setIsLoadingEvaluation(false);
    }
  }, [apiClient, executionId]);

  useEffect(() => {
    void refreshExecution();
  }, [refreshExecution]);

  useEffect(() => {
    void refreshSteps();
  }, [refreshSteps]);

  useEffect(() => {
    setCodeEntries([]);
    setCodeError(null);
    setIsLoadingCode(true);
    codeCursorRef.current = null;
    void refreshCode({ reset: true });
  }, [executionId, refreshCode]);

  useEffect(() => {
    setEvaluation(null);
    setEvaluationError(null);
    setEvaluationNotFound(false);
    setIsLoadingEvaluation(false);
  }, [executionId]);

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

  useEffect(() => {
    if (!execution?.status || (execution.status !== "RUNNING" && execution.status !== "PENDING")) {
      return undefined;
    }
    const interval = window.setInterval(() => {
      void refreshCode();
    }, 2000);
    return () => window.clearInterval(interval);
  }, [execution?.status, refreshCode]);

  useEffect(() => {
    if (!executionId || !execution?.status) {
      return;
    }
    if (execution.status === "RUNNING" || execution.status === "PENDING") {
      return;
    }
    void refreshCode();
  }, [executionId, execution?.status, refreshCode]);

  useEffect(() => {
    if (!executionId) {
      return;
    }
    if (execution?.mode === "RUNTIME") {
      return;
    }
    if (execution?.status !== "COMPLETED") {
      return;
    }
    void refreshEvaluation();
  }, [executionId, execution?.mode, execution?.status, refreshEvaluation]);

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
  const isRuntime = execution?.mode === "RUNTIME";
  const isEvaluationRunning =
    !isRuntime &&
    execution?.status === "COMPLETED" &&
    !evaluationError &&
    (evaluationNotFound || !evaluation || evaluation?.baseline_status === "RUNNING");
  const visibleCodeEntries = useMemo(
    () => codeEntries.filter((entry) => codeFilters[entry.source]),
    [codeEntries, codeFilters],
  );

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

  const handleRecomputeEvaluation = useCallback(
    async (event?: MouseEvent<HTMLButtonElement>) => {
      if (event) {
        event.preventDefault();
        event.stopPropagation();
      }
      if (!executionId || isRecomputingEvaluation) {
        return;
      }
      if (execution?.mode === "RUNTIME" || execution?.status !== "COMPLETED") {
        return;
      }
      setIsRecomputingEvaluation(true);
      try {
        const payload = await apiClient.recomputeExecutionEvaluation(executionId);
        setEvaluation(payload);
        setEvaluationError(null);
        setEvaluationNotFound(false);
        showToast("Evaluation recomputed", "success", 2000);
      } catch (err) {
        const message =
          err instanceof ApiError ? err.message : "Failed to recompute evaluation. Please try again.";
        showToast(message, "error", 3000);
      } finally {
        setIsRecomputingEvaluation(false);
      }
    },
    [apiClient, executionId, execution?.mode, execution?.status, isRecomputingEvaluation, showToast],
  );

  const durationSeconds = computeDurationSeconds(
    execution?.started_at,
    execution?.completed_at,
    now,
    execution?.budgets_consumed?.total_seconds ?? null,
  );

  const statusStyle = STATUS_STYLES[execution?.status ?? ""] ?? "bg-slate-100 text-slate-700";
  const modeLabel = execution?.mode === "RUNTIME" ? "Runtime" : "Answerer";

  const handleToggleCodeFilter = useCallback((source: CodeLogSource) => {
    setCodeFilters((previous) => ({ ...previous, [source]: !previous[source] }));
  }, []);

  const budgetsRequested: Budgets | null | undefined = execution?.budgets_requested;
  const budgetsConsumed: BudgetsConsumed | null | undefined = execution?.budgets_consumed;

  const citations = execution?.citations ?? [];
  const question = execution?.question ?? "";
  const answer = execution?.answer ?? "";
  const baselineStatus = evaluation?.baseline_status;
  const baselineStatusStyle = baselineStatus
    ? BASELINE_STATUS_STYLES[baselineStatus] ?? "bg-slate-100 text-slate-600"
    : "bg-slate-100 text-slate-600";
  const judgeMetrics = evaluation?.judge_metrics ?? null;

  const shouldPollEvaluation =
    !isRuntime &&
    execution?.status === "COMPLETED" &&
    !evaluationError &&
    (evaluationNotFound || !evaluation || evaluation?.baseline_status === "RUNNING");

  useEffect(() => {
    if (!shouldPollEvaluation) {
      return;
    }
    const interval = window.setInterval(() => {
      void refreshEvaluation();
    }, EVALUATION_POLL_INTERVAL_MS);
    return () => window.clearInterval(interval);
  }, [refreshEvaluation, shouldPollEvaluation]);

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
          <div className="grid items-start gap-6 lg:grid-cols-[1.1fr_1.4fr]">
            <CollapsibleCard
              title="Execution"
              right={
                <span className="rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">
                  {formatTimestamp(execution.started_at)}
                </span>
              }
              defaultOpen
            >
              <div className="grid gap-4">
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
            </CollapsibleCard>

            <section className="space-y-6">
              <CollapsibleCard title="Question" defaultOpen>
                <div className="rounded-2xl border border-slate-200 bg-slate-50 p-4 text-sm text-slate-600">
                  {question ? (
                    <p className="whitespace-pre-wrap text-slate-700">{question}</p>
                  ) : (
                    <span className="text-slate-500">No question available.</span>
                  )}
                </div>
              </CollapsibleCard>

              <div className="grid items-start gap-6 lg:grid-cols-2">
                <CollapsibleCard
                  title="Answer"
                  right={
                    <span className="text-xs uppercase tracking-[0.25em] text-slate-400">
                      {execution.status}
                    </span>
                  }
                  defaultOpen
                >
                  <div className="min-h-[120px] rounded-2xl border border-slate-200 bg-slate-50 p-4 text-sm text-slate-600">
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
                </CollapsibleCard>

                {isLoadingEvaluation ? (
                  <SkeletonCard lines={4} />
                ) : isEvaluationRunning ? (
                  <EvaluationPendingCard
                    title="Baseline"
                    description="Baseline evaluation is running."
                  />
                ) : (
                  <CollapsibleCard
                    title="Baseline"
                    right={
                      evaluation ? (
                        <span
                          className={`rounded-full px-3 py-1 text-xs font-semibold uppercase tracking-[0.25em] ${baselineStatusStyle}`}
                        >
                          {evaluation.baseline_status}
                        </span>
                      ) : null
                    }
                    defaultOpen={false}
                  >
                    <div className="space-y-4">
                      {isRuntime ? (
                        <EmptyState
                          title="Not supported"
                          description="Baseline evaluations are not available for runtime executions."
                        />
                      ) : evaluationError ? (
                        <ErrorPanel
                          error={evaluationError}
                          title="Baseline evaluation failed"
                          onRetry={() => void refreshEvaluation()}
                        />
                      ) : evaluationNotFound ? (
                        <EmptyState
                          title="Not available yet"
                          description="Baseline evaluation has not been created yet."
                        />
                      ) : evaluation ? (
                        <>
                          <div className="rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-600">
                            <div className="flex justify-between">
                              <span className="text-xs uppercase tracking-[0.2em] text-slate-400">
                                Input tokens
                              </span>
                              <span className="font-semibold text-slate-700">
                                {formatNumber(evaluation.baseline_input_tokens)}
                              </span>
                            </div>
                            <div className="mt-2 flex justify-between">
                              <span className="text-xs uppercase tracking-[0.2em] text-slate-400">
                                Context window
                              </span>
                              <span className="font-semibold text-slate-700">
                                {formatNumber(evaluation.baseline_context_window)}
                              </span>
                            </div>
                            {evaluation.baseline_status === "SKIPPED" ? (
                              <div className="mt-2 text-xs text-slate-500">
                                Skip reason: {evaluation.baseline_skip_reason ?? "-"}
                              </div>
                            ) : null}
                          </div>
                          <div className="min-h-[120px] rounded-2xl border border-slate-200 bg-slate-50 p-4 text-sm text-slate-600">
                            {evaluation.baseline_answer ? (
                              <p className="whitespace-pre-wrap text-slate-700">
                                {evaluation.baseline_answer}
                              </p>
                            ) : (
                              <span className="text-slate-500">
                                No baseline answer available.
                              </span>
                            )}
                          </div>
                        </>
                      ) : (
                        <EmptyState
                          title="Not available yet"
                          description="Baseline evaluation has not been created yet."
                        />
                      )}
                    </div>
                  </CollapsibleCard>
                )}
              </div>

              {!isRuntime ? (
                isLoadingEvaluation ? (
                  <SkeletonCard lines={3} />
                ) : isEvaluationRunning ? (
                  <EvaluationPendingCard
                    title="Evaluation"
                    description="LLM judge evaluation is running."
                  />
                ) : (
                  <CollapsibleCard
                    title="Evaluation"
                    right={
                      <div className="flex items-center gap-3">
                        <button
                          type="button"
                          onClick={handleRecomputeEvaluation}
                          disabled={
                            isRecomputingEvaluation ||
                            execution.status !== "COMPLETED" ||
                            evaluation?.baseline_status === "RUNNING" ||
                            Boolean(evaluationError)
                          }
                          className="rounded-full border border-slate-200 px-3 py-1 text-xs font-semibold uppercase tracking-[0.2em] text-slate-600 disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          {isRecomputingEvaluation ? "Re-running..." : "Re-run eval"}
                        </button>
                        <span className="text-xs uppercase tracking-[0.25em] text-slate-400">
                          LLM Judge
                        </span>
                      </div>
                    }
                    defaultOpen
                  >
                    <div>
                      {evaluationError ? (
                        <ErrorPanel
                          error={evaluationError}
                          title="Evaluation failed"
                          onRetry={() => void refreshEvaluation()}
                        />
                      ) : !evaluation || !judgeMetrics ? (
                        <EmptyState
                          title="Evaluation not run"
                          description="LLM judge metrics are not available yet."
                        />
                      ) : (
                        <div className="overflow-hidden rounded-2xl border border-slate-200">
                          <table className="w-full text-left text-sm">
                            <thead className="bg-slate-50 text-xs uppercase tracking-[0.2em] text-slate-400">
                              <tr>
                                <th className="px-4 py-2">Metric</th>
                                <th className="px-4 py-2">Answerer</th>
                                <th className="px-4 py-2">Baseline</th>
                              </tr>
                            </thead>
                            <tbody className="divide-y divide-slate-100">
                              <tr>
                                <td className="px-4 py-3 text-slate-700">
                                  Answer relevancy
                                </td>
                                <td className="px-4 py-3 text-slate-700">
                                  {formatScore(judgeMetrics.answerer?.answer_relevancy)}
                                </td>
                                <td className="px-4 py-3 text-slate-700">
                                  {formatScore(judgeMetrics.baseline?.answer_relevancy)}
                                </td>
                              </tr>
                              <tr>
                                <td className="px-4 py-3 text-slate-700">Faithfulness</td>
                                <td className="px-4 py-3 text-slate-700">
                                  {renderJudgeScore(
                                    judgeMetrics.answerer?.faithfulness,
                                    judgeMetrics.answerer?.faithfulness_skip_reason,
                                  )}
                                </td>
                                <td className="px-4 py-3 text-slate-700">
                                  {renderJudgeScore(
                                    judgeMetrics.baseline?.faithfulness,
                                    judgeMetrics.baseline?.faithfulness_skip_reason,
                                  )}
                                </td>
                              </tr>
                            </tbody>
                          </table>
                        </div>
                      )}
                    </div>
                  </CollapsibleCard>
                )
              ) : null}

              <CollapsibleCard
                title="Citations"
                right={
                  <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">
                    {citations.length} refs
                  </span>
                }
                defaultOpen={false}
              >
                <div className="space-y-3">
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
              </CollapsibleCard>
            </section>
          </div>

          <CollapsibleCard
            title="Code"
            subtitle="Root, subcall, and tool traces from the execution."
            right={
              <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">
                {visibleCodeEntries.length} entries
              </span>
            }
            defaultOpen={false}
          >
            <div className="space-y-4">
              <div className="flex flex-wrap items-center gap-2">
                {Object.entries(CODE_SOURCE_LABELS).map(([key, label]) => {
                  const source = key as CodeLogSource;
                  const isActive = codeFilters[source];
                  return (
                    <button
                      key={source}
                      type="button"
                      onClick={() => handleToggleCodeFilter(source)}
                      title={CODE_SOURCE_TOOLTIPS[source]}
                      className={`rounded-full border px-3 py-1 text-xs font-semibold uppercase tracking-[0.2em] ${
                        isActive
                          ? "border-slate-900 bg-slate-900 text-white"
                          : "border-slate-200 text-slate-500"
                      }`}
                    >
                      {label}
                    </button>
                  );
                })}
              </div>

              {isLoadingCode ? <SkeletonCard lines={4} /> : null}
              {!isLoadingCode && codeError ? (
                <ErrorPanel error={codeError} onRetry={() => void refreshCode({ reset: true })} />
              ) : null}
              {!isLoadingCode && !codeError && visibleCodeEntries.length === 0 ? (
                <EmptyState
                  title="No code yet"
                  description="Code logs appear as the execution runs."
                />
              ) : null}
              {!isLoadingCode && !codeError
                ? visibleCodeEntries.map((entry) => (
                    <div
                      key={`${entry.sequence}-${entry.source}`}
                      className="rounded-2xl border border-slate-200 bg-slate-50 p-4"
                    >
                      <div className="flex flex-wrap items-center justify-between gap-3">
                        <div className="flex flex-wrap items-center gap-2">
                          <span
                            title={CODE_SOURCE_TOOLTIPS[entry.source]}
                            className="rounded-full bg-white px-3 py-1 text-xs font-semibold uppercase tracking-[0.2em] text-slate-500"
                          >
                            {CODE_SOURCE_LABELS[entry.source]}
                          </span>
                          <span
                            title={CODE_KIND_TOOLTIPS[entry.kind] ?? entry.kind}
                            className="rounded-full bg-white px-3 py-1 text-xs font-semibold uppercase tracking-[0.2em] text-slate-500"
                          >
                            {CODE_KIND_LABELS[entry.kind] ?? entry.kind}
                          </span>
                          {entry.model_name ? (
                            <span className="rounded-full bg-white px-3 py-1 text-xs text-slate-500">
                              model {entry.model_name}
                            </span>
                          ) : null}
                          {entry.tool_type ? (
                            <span className="rounded-full bg-white px-3 py-1 text-xs text-slate-500">
                              tool {entry.tool_type}
                            </span>
                          ) : null}
                        </div>
                        <span className="text-xs uppercase tracking-[0.2em] text-slate-400">
                          {formatTimestamp(entry.created_at)}
                        </span>
                      </div>
                      <div className="mt-3">
                        {entry.kind === "REPL" ? (
                          <CodeBlock
                            language="python"
                            content={
                              typeof entry.content === "string"
                                ? entry.content
                                : formatJson(entry.content)
                            }
                          />
                        ) : (
                          <CodeBlock language="json" content={formatJson(entry.content)} />
                        )}
                      </div>
                    </div>
                  ))
                : null}
            </div>
          </CollapsibleCard>

          <CollapsibleCard
            title="Step History"
            subtitle="Polling every 2 seconds while the execution is running."
            right={
              <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">
                {stepHistory.length} steps
              </span>
            }
            defaultOpen={false}
          >
            <div className="space-y-4">
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
                    const timings = step.timings ?? null;
                    const timingEntries = [] as Array<[string, number]>;
                    const seenTimings = new Set<string>();
                    if (timings && typeof timings === "object") {
                      TIMING_ORDER.forEach((key) => {
                        const value = timings[key];
                        if (typeof value === "number") {
                          timingEntries.push([key, value]);
                          seenTimings.add(key);
                        }
                      });
                      Object.entries(timings)
                        .filter(([key, value]) => !seenTimings.has(key) && typeof value === "number")
                        .sort(([a], [b]) => a.localeCompare(b))
                        .forEach(([key, value]) => {
                          timingEntries.push([key, value as number]);
                        });
                    }
                    const totalTimingMs =
                      timingEntries.length > 0
                        ? timingEntries.reduce((sum, [, value]) => sum + value, 0)
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
                                title={STEP_HISTORY_TOOLTIPS.status}
                                className={`rounded-full px-3 py-1 text-xs font-semibold uppercase tracking-[0.2em] ${statusStyle}`}
                              >
                                {statusLabel}
                              </span>
                              {step.final?.is_final ? (
                                <span
                                  title={STEP_HISTORY_TOOLTIPS.final}
                                  className="rounded-full bg-indigo-100 px-3 py-1 text-xs font-semibold uppercase tracking-[0.2em] text-indigo-700"
                                >
                                  Final
                                </span>
                              ) : null}
                              {step.error ? (
                                <span
                                  title={STEP_HISTORY_TOOLTIPS.error}
                                  className="rounded-full bg-rose-50 px-3 py-1 text-xs font-semibold uppercase tracking-[0.2em] text-rose-600"
                                >
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
                          <span title={STEP_HISTORY_TOOLTIPS.stdoutChars} className="rounded-full bg-white px-3 py-1">
                            stdout {formatNumber(stdoutLength)} chars
                          </span>
                          <span title={STEP_HISTORY_TOOLTIPS.toolRequests} className="rounded-full bg-white px-3 py-1">
                            tool requests {formatNumber(toolCounts.total)}
                          </span>
                          <span title={STEP_HISTORY_TOOLTIPS.spans} className="rounded-full bg-white px-3 py-1">
                            spans {formatNumber(spanCount)}
                          </span>
                          {step.summary ? (
                            <span title={STEP_HISTORY_TOOLTIPS.stateChars} className="rounded-full bg-white px-3 py-1">
                              state chars {formatNumber(stateCharLength)}
                            </span>
                          ) : null}
                          {step.summary ? (
                            <span title={STEP_HISTORY_TOOLTIPS.stateBytes} className="rounded-full bg-white px-3 py-1">
                              state bytes {formatNumber(stateByteLength)}
                            </span>
                          ) : null}
                        </div>

                        <div className="mt-5 grid items-start gap-4 lg:grid-cols-2">
                          <div className="rounded-2xl border border-slate-200 bg-white p-4">
                            <p
                              title={STEP_HISTORY_TOOLTIPS.stdoutPanel}
                              className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-400"
                            >
                              Stdout
                            </p>
                            <pre className="mt-3 max-h-64 overflow-auto whitespace-pre-wrap text-sm text-slate-700">
                              {step.stdout || "No stdout output."}
                            </pre>
                          </div>

                          <div className="rounded-2xl border border-slate-200 bg-white p-4">
                            <p
                              title={STEP_HISTORY_TOOLTIPS.statePanel}
                              className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-400"
                            >
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
                            <p
                              title={STEP_HISTORY_TOOLTIPS.toolRequestsPanel}
                              className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-400"
                            >
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
                                <span title={STEP_HISTORY_TOOLTIPS.llmRequests}>
                                  llm {toolCounts.llmCount}
                                </span>
                                <span title={STEP_HISTORY_TOOLTIPS.searchRequests}>
                                  search {toolCounts.searchCount}
                                </span>
                              </div>
                            ) : null}
                          </div>

                          <div className="rounded-2xl border border-slate-200 bg-white p-4">
                            <p
                              title={STEP_HISTORY_TOOLTIPS.timingsPanel}
                              className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-400"
                            >
                              Timings
                            </p>
                            <div className="mt-3 space-y-2 text-sm text-slate-600">
                              {timingEntries.length > 0 ? (
                                <div className="space-y-2">
                                  {timingEntries.map(([key, value]) => (
                                    <div
                                      key={`${step.turn_index}-timing-${key}`}
                                      className="flex items-center justify-between gap-3"
                                    >
                                      <span className="text-xs uppercase tracking-[0.2em] text-slate-400">
                                        {TIMING_LABELS[key] ?? key}
                                      </span>
                                      <span className="font-semibold text-slate-700">
                                        {formatDurationMs(value)}
                                      </span>
                                    </div>
                                  ))}
                                  {totalTimingMs !== null ? (
                                    <div className="mt-3 flex items-center justify-between border-t border-slate-100 pt-2">
                                      <span className="text-xs uppercase tracking-[0.2em] text-slate-400">
                                        Total
                                      </span>
                                      <span className="text-sm font-semibold text-slate-700">
                                        {formatDurationMs(totalTimingMs)}
                                      </span>
                                    </div>
                                  ) : null}
                                </div>
                              ) : (
                                <span className="text-slate-500">No timing data.</span>
                              )}
                            </div>
                          </div>

                          <div className="rounded-2xl border border-slate-200 bg-white p-4">
                            <p
                              title={STEP_HISTORY_TOOLTIPS.spanLogPanel}
                              className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-400"
                            >
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
                            <p
                              title={STEP_HISTORY_TOOLTIPS.finalPanel}
                              className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-400"
                            >
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
                            <p
                              title={STEP_HISTORY_TOOLTIPS.errorPanel}
                              className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-400"
                            >
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
          </CollapsibleCard>
        </div>
      ) : null}
    </div>
  );
}
