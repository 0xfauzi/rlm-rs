"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { ApiClient, ApiError } from "../../../../lib/api-client";
import type {
  ExecutionStatus,
  JsonValue,
  StepResult,
  ToolRequestsEnvelope,
} from "../../../../lib/types";
import { useApp } from "../../../../contexts/AppContext";
import { useToast } from "../../../../contexts/ToastContext";

const TAB_OPTIONS = ["stdout", "state", "span_log", "tool_requests"] as const;
const STATUS_STYLES: Record<string, string> = {
  RUNNING: "bg-amber-100 text-amber-900",
  COMPLETED: "bg-emerald-100 text-emerald-900",
  FAILED: "bg-rose-100 text-rose-900",
  CANCELLED: "bg-slate-100 text-slate-700",
  TIMEOUT: "bg-rose-100 text-rose-900",
  BUDGET_EXCEEDED: "bg-rose-100 text-rose-900",
  MAX_TURNS_EXCEEDED: "bg-rose-100 text-rose-900",
};

type OutputTab = (typeof TAB_OPTIONS)[number];

type StepDraft = {
  id: string;
  code: string;
};

type StepRun = {
  id: string;
  stepIndex: number;
  code: string;
  durationMs: number;
  success: boolean;
  result: StepResult | null;
  errorMessage: string | null;
};

function buildErrorMessage(error: unknown, fallback: string) {
  if (error instanceof ApiError) {
    return error.message;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return fallback;
}

function formatDuration(durationMs: number) {
  return `${Math.max(0, Math.round(durationMs))} ms`;
}

function formatJson(value: JsonValue | string | null) {
  if (value === null || value === undefined) {
    return "null";
  }
  if (typeof value === "string") {
    return value;
  }
  return JSON.stringify(value, null, 2);
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

function flattenToolRequests(envelope?: ToolRequestsEnvelope | null) {
  if (!envelope) {
    return [] as Array<{ type: string; payload: JsonValue }>;
  }
  const llm = envelope.llm.map((request) => ({
    type: "llm",
    payload: request as unknown as JsonValue,
  }));
  const search = envelope.search.map((request) => ({
    type: "search",
    payload: request as unknown as JsonValue,
  }));
  return [...llm, ...search];
}

export default function RuntimeExecutionPage() {
  const params = useParams();
  const router = useRouter();
  const searchParams = useSearchParams();
  const executionId = typeof params.execution_id === "string" ? params.execution_id : "";
  const { config } = useApp();
  const { showToast } = useToast();

  const apiClient = useMemo(
    () => new ApiClient(config.apiBaseUrl, config.devKey),
    [config.apiBaseUrl, config.devKey],
  );

  const stepCounter = useRef(1);

  const [isLoadingExecution, setIsLoadingExecution] = useState(true);
  const [executionError, setExecutionError] = useState<string | null>(null);
  const [executionStatus, setExecutionStatus] = useState<ExecutionStatus | null>(null);
  const [isCancelling, setIsCancelling] = useState(false);

  const [steps, setSteps] = useState<StepDraft[]>([{ id: "step-1", code: "" }]);
  const [activeStepId, setActiveStepId] = useState("step-1");
  const [draggingStepId, setDraggingStepId] = useState<string | null>(null);
  const [results, setResults] = useState<StepRun[]>([]);
  const [selectedResultId, setSelectedResultId] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<OutputTab>("stdout");
  const [isRunning, setIsRunning] = useState(false);
  const [runningAllIndex, setRunningAllIndex] = useState<number | null>(null);

  const sessionId = searchParams.get("session_id") ?? "";

  const refreshExecution = useCallback(async () => {
    if (!executionId) {
      return;
    }
    try {
      const payload = await apiClient.getExecution(executionId);
      setExecutionStatus(payload.status);
      setExecutionError(null);
    } catch (err) {
      setExecutionError(buildErrorMessage(err, "Failed to load execution."));
      setExecutionStatus(null);
    } finally {
      setIsLoadingExecution(false);
    }
  }, [apiClient, executionId]);

  useEffect(() => {
    void refreshExecution();
  }, [refreshExecution]);

  const updateStepCode = (id: string, code: string) => {
    setSteps((prev) => prev.map((step) => (step.id === id ? { ...step, code } : step)));
  };

  const addStep = () => {
    stepCounter.current += 1;
    const id = `step-${stepCounter.current}`;
    setSteps((prev) => [...prev, { id, code: "" }]);
    setActiveStepId(id);
  };

  const removeStep = (id: string) => {
    setSteps((prev) => {
      if (prev.length <= 1) {
        return prev;
      }
      const next = prev.filter((step) => step.id !== id);
      if (activeStepId === id) {
        setActiveStepId(next[next.length - 1]?.id ?? "");
      }
      return next;
    });
  };

  const reorderSteps = (sourceId: string, targetId: string) => {
    if (sourceId === targetId) {
      return;
    }
    setSteps((prev) => {
      const sourceIndex = prev.findIndex((step) => step.id === sourceId);
      const targetIndex = prev.findIndex((step) => step.id === targetId);
      if (sourceIndex === -1 || targetIndex === -1) {
        return prev;
      }
      const next = [...prev];
      const [removed] = next.splice(sourceIndex, 1);
      if (!removed) {
        return prev;
      }
      next.splice(targetIndex, 0, removed);
      return next;
    });
  };

  const recordResult = (run: StepRun) => {
    setResults((prev) => [...prev, run]);
    setSelectedResultId(run.id);
    setActiveTab("stdout");
  };

  const runSingleStep = async (step: StepDraft, index: number) => {
    const start = performance.now();
    try {
      const result = await apiClient.postStep(executionId, step.code);
      const durationMs = performance.now() - start;
      const success = result.success && !result.error;
      const run: StepRun = {
        id: `${step.id}-${Date.now()}`,
        stepIndex: index,
        code: step.code,
        durationMs,
        success,
        result,
        errorMessage: result.error?.message ?? null,
      };
      recordResult(run);
      if (!success) {
        showToast("Step failed", "error", 3000);
      } else {
        showToast(`Step ${index + 1} complete`, "success", 1500);
      }
      return run;
    } catch (err) {
      const durationMs = performance.now() - start;
      const message = buildErrorMessage(err, "Failed to run step.");
      const run: StepRun = {
        id: `${step.id}-${Date.now()}`,
        stepIndex: index,
        code: step.code,
        durationMs,
        success: false,
        result: null,
        errorMessage: message,
      };
      recordResult(run);
      showToast(message, "error", 3000);
      return run;
    }
  };

  const isExecutionRunning = executionStatus === "RUNNING";

  const handleRunFocusedStep = async () => {
    if (isRunning || !executionId) {
      return;
    }
    if (!isExecutionRunning) {
      showToast("Execution is not running.", "error", 2500);
      return;
    }
    const stepIndex = steps.findIndex((step) => step.id === activeStepId);
    const resolvedIndex = stepIndex >= 0 ? stepIndex : steps.length - 1;
    const step = steps[resolvedIndex];
    if (!step || !step.code.trim()) {
      showToast("Enter code before running a step.", "error", 2500);
      return;
    }
    setIsRunning(true);
    await runSingleStep(step, resolvedIndex);
    setIsRunning(false);
  };

  const handleRunAll = async () => {
    if (isRunning || !executionId) {
      return;
    }
    if (!isExecutionRunning) {
      showToast("Execution is not running.", "error", 2500);
      return;
    }
    setIsRunning(true);
    for (let index = 0; index < steps.length; index += 1) {
      const step = steps[index];
      if (!step || !step.code.trim()) {
        showToast(`Step ${index + 1} is empty.`, "error", 2500);
        break;
      }
      setRunningAllIndex(index);
      const run = await runSingleStep(step, index);
      if (!run.success) {
        break;
      }
    }
    setRunningAllIndex(null);
    setIsRunning(false);
  };

  const handleResetState = async () => {
    if (isRunning || !sessionId) {
      return;
    }
    setIsRunning(true);
    try {
      const response = await apiClient.createRuntimeExecution(sessionId);
      setResults([]);
      setSelectedResultId(null);
      router.replace(`/executions/${response.execution_id}/runtime`);
      showToast("Runtime state reset", "success", 2000);
    } catch (err) {
      showToast(buildErrorMessage(err, "Failed to reset state."), "error", 3000);
    } finally {
      setIsRunning(false);
    }
  };

  const handleCancel = async () => {
    if (isCancelling || !executionId) {
      return;
    }
    if (!isExecutionRunning) {
      showToast("Execution is not running.", "error", 2500);
      return;
    }
    setIsCancelling(true);
    try {
      const payload = await apiClient.cancelExecution(executionId);
      setExecutionStatus(payload.status);
      if (payload.status === "CANCELLED") {
        showToast("Execution cancelled", "success", 2000);
      } else {
        showToast(`Execution is ${payload.status}`, "success", 2000);
      }
    } catch (err) {
      showToast(buildErrorMessage(err, "Failed to cancel execution."), "error", 3000);
    } finally {
      setIsCancelling(false);
    }
  };

  const selectedResult = results.find((result) => result.id === selectedResultId) ?? null;
  const selectedOutput = selectedResult?.result ?? null;

  const activeRequests = flattenToolRequests(selectedOutput?.tool_requests);
  const statusStyle = STATUS_STYLES[executionStatus ?? ""] ?? "bg-slate-100 text-slate-700";

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.3em] text-slate-400">
            Runtime Execution
          </p>
          <h1 className="text-2xl font-semibold text-slate-900">Multi-step editor</h1>
          <p className="mt-2 text-sm text-slate-600">
            Execution {executionId || "-"}
            {sessionId ? ` · Session ${sessionId}` : ""}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {executionStatus ? (
            <span
              className={`rounded-full px-3 py-1 text-xs font-semibold uppercase tracking-[0.2em] ${statusStyle}`}
            >
              {executionStatus}
            </span>
          ) : null}
          <div className="rounded-2xl border border-slate-200 bg-white px-4 py-2 text-xs text-slate-500">
            {isLoadingExecution
              ? "Loading execution..."
              : executionError
                ? executionError
                : "Runtime mode"}
          </div>
        </div>
      </div>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <section className="rounded-3xl border border-slate-200 bg-white p-6 shadow-sm">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="text-lg font-semibold text-slate-900">Steps</h2>
              <p className="text-sm text-slate-500">Draft and reorder your runtime steps.</p>
            </div>
            <button
              type="button"
              onClick={addStep}
              className="rounded-full border border-slate-200 px-4 py-2 text-xs font-semibold uppercase tracking-[0.2em] text-slate-600"
            >
              Add Step
            </button>
          </div>

          <div className="mt-6 space-y-4">
            {steps.map((step, index) => (
              <div
                key={step.id}
                className="rounded-2xl border border-slate-200 bg-slate-50 p-4"
                onDragOver={(event) => event.preventDefault()}
                onDrop={(event) => {
                  event.preventDefault();
                  if (draggingStepId) {
                    reorderSteps(draggingStepId, step.id);
                  }
                  setDraggingStepId(null);
                }}
              >
                <div className="flex items-center justify-between gap-3">
                  <div className="flex items-center gap-3">
                    <button
                      type="button"
                      className="flex h-8 w-8 items-center justify-center rounded-full border border-slate-200 text-slate-500"
                      draggable
                      onDragStart={() => setDraggingStepId(step.id)}
                      onDragEnd={() => setDraggingStepId(null)}
                      aria-label={`Reorder step ${index + 1}`}
                    >
                      ::
                    </button>
                    <span className="text-sm font-semibold text-slate-700">Step {index + 1}</span>
                  </div>
                  <button
                    type="button"
                    onClick={() => removeStep(step.id)}
                    disabled={steps.length <= 1}
                    className="rounded-full border border-slate-200 px-3 py-1 text-xs font-semibold uppercase tracking-[0.2em] text-slate-500 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    Remove
                  </button>
                </div>
                <textarea
                  value={step.code}
                  onChange={(event) => updateStepCode(step.id, event.target.value)}
                  onFocus={() => setActiveStepId(step.id)}
                  placeholder="Write Python code for this step"
                  className="mt-3 h-36 w-full resize-none rounded-2xl border border-slate-200 bg-white p-3 font-mono text-sm text-slate-700 outline-none ring-slate-900/10 focus:ring-2"
                />
              </div>
            ))}
          </div>

          <div className="mt-6 flex flex-wrap items-center gap-3 rounded-2xl border border-slate-200 bg-white p-4">
            <button
              type="button"
              onClick={handleRunFocusedStep}
              disabled={isRunning || !isExecutionRunning}
              className="rounded-full bg-slate-900 px-4 py-2 text-xs font-semibold uppercase tracking-[0.2em] text-white disabled:cursor-not-allowed disabled:bg-slate-400"
            >
              Run Step
            </button>
            <button
              type="button"
              onClick={handleRunAll}
              disabled={isRunning || !isExecutionRunning}
              className="rounded-full border border-slate-200 px-4 py-2 text-xs font-semibold uppercase tracking-[0.2em] text-slate-600 disabled:cursor-not-allowed disabled:opacity-50"
            >
              Run All
            </button>
            <button
              type="button"
              onClick={handleResetState}
              disabled={isRunning || !sessionId}
              className="rounded-full border border-slate-200 px-4 py-2 text-xs font-semibold uppercase tracking-[0.2em] text-slate-600 disabled:cursor-not-allowed disabled:opacity-50"
            >
              Reset State
            </button>
            <button
              type="button"
              onClick={handleCancel}
              disabled={isCancelling || !isExecutionRunning}
              className="rounded-full border border-rose-200 px-4 py-2 text-xs font-semibold uppercase tracking-[0.2em] text-rose-600 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {isCancelling ? "Cancelling..." : "Cancel"}
            </button>
            {runningAllIndex !== null ? (
              <span className="text-xs text-slate-500">
                Running step {runningAllIndex + 1} of {steps.length}
              </span>
            ) : null}
          </div>
        </section>

        <section className="rounded-3xl border border-slate-200 bg-white p-6 shadow-sm">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-lg font-semibold text-slate-900">Output inspector</h2>
              <p className="text-sm text-slate-500">Review runtime results and state changes.</p>
            </div>
          </div>

          <div className="mt-4 space-y-3">
            {results.length === 0 ? (
              <div className="rounded-2xl border border-dashed border-slate-200 p-6 text-sm text-slate-500">
                Run a step to see stdout, state, spans, and tool requests.
              </div>
            ) : (
              results.map((run) => (
                <details
                  key={run.id}
                  open={run.id === selectedResultId}
                  className={`rounded-2xl border p-3 ${
                    run.id === selectedResultId
                      ? "border-slate-400 bg-slate-50"
                      : "border-slate-200"
                  }`}
                >
                  <summary
                    className="flex cursor-pointer items-center justify-between gap-3 text-sm text-slate-700"
                    onClick={() => setSelectedResultId(run.id)}
                  >
                    <span className="font-semibold">Step {run.stepIndex + 1}</span>
                    <span
                      className={`rounded-full px-3 py-1 text-xs font-semibold uppercase tracking-[0.2em] ${
                        run.success
                          ? "bg-emerald-100 text-emerald-900"
                          : "bg-rose-100 text-rose-900"
                      }`}
                    >
                      {run.success ? "Success" : "Error"}
                    </span>
                    <span className="text-xs text-slate-500">{formatDuration(run.durationMs)}</span>
                  </summary>
                  <div className="mt-3 space-y-2 text-sm text-slate-600">
                    {run.errorMessage ? (
                      <div className="rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-rose-700">
                        {run.errorMessage}
                      </div>
                    ) : null}
                    <div className="rounded-xl border border-slate-200 bg-white p-3 font-mono text-xs text-slate-600">
                      {run.code || "(empty)"}
                    </div>
                  </div>
                </details>
              ))
            )}
          </div>

          <div className="mt-6 border-t border-slate-200 pt-6">
            <div className="flex flex-wrap gap-2">
              {TAB_OPTIONS.map((tab) => (
                <button
                  key={tab}
                  type="button"
                  onClick={() => setActiveTab(tab)}
                  className={`rounded-full px-3 py-1 text-xs font-semibold uppercase tracking-[0.2em] ${
                    activeTab === tab
                      ? "bg-slate-900 text-white"
                      : "border border-slate-200 text-slate-500"
                  }`}
                >
                  {tab.replace("_", " ")}
                </button>
              ))}
            </div>

            <div className="mt-4">
              {activeTab === "stdout" ? (
                <pre className="min-h-[120px] whitespace-pre-wrap rounded-2xl border border-slate-200 bg-slate-50 p-4 text-xs text-slate-700">
                  {selectedOutput?.stdout
                    ? selectedOutput.stdout
                    : "No stdout captured for this step."}
                </pre>
              ) : null}

              {activeTab === "state" ? (
                <div className="rounded-2xl border border-slate-200 bg-slate-50 p-4 text-sm text-slate-700">
                  {selectedOutput?.state ? (
                    <JsonTree value={selectedOutput.state as JsonValue} label="State" />
                  ) : (
                    <span className="text-slate-500">No state returned.</span>
                  )}
                  {selectedOutput?.state && typeof selectedOutput.state === "string" ? (
                    <pre className="mt-4 whitespace-pre-wrap text-xs text-slate-600">
                      {formatJson(selectedOutput.state)}
                    </pre>
                  ) : null}
                </div>
              ) : null}

              {activeTab === "span_log" ? (
                <div className="overflow-hidden rounded-2xl border border-slate-200">
                  <table className="min-w-full text-left text-sm">
                    <thead className="bg-slate-50 text-xs uppercase tracking-[0.2em] text-slate-400">
                      <tr>
                        <th className="px-4 py-2">Doc</th>
                        <th className="px-4 py-2">Start</th>
                        <th className="px-4 py-2">End</th>
                        <th className="px-4 py-2">Tag</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                      {selectedOutput?.span_log && selectedOutput.span_log.length > 0 ? (
                        selectedOutput.span_log.map((span, index) => (
                          <tr key={`${span.doc_index}-${span.start_char}-${index}`}>
                            <td className="px-4 py-2 text-slate-700">{span.doc_index}</td>
                            <td className="px-4 py-2 text-slate-700">{span.start_char}</td>
                            <td className="px-4 py-2 text-slate-700">{span.end_char}</td>
                            <td className="px-4 py-2 text-slate-500">{span.tag ?? "-"}</td>
                          </tr>
                        ))
                      ) : (
                        <tr>
                          <td colSpan={4} className="px-4 py-6 text-center text-slate-500">
                            No spans recorded for this step.
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
              ) : null}

              {activeTab === "tool_requests" ? (
                <div className="space-y-3">
                  {activeRequests.length === 0 ? (
                    <div className="rounded-2xl border border-dashed border-slate-200 p-6 text-sm text-slate-500">
                      No tool requests were queued.
                    </div>
                  ) : (
                    activeRequests.map((request, index) => (
                      <div
                        key={`${request.type}-${index}`}
                        className="rounded-2xl border border-slate-200 bg-slate-50 p-4"
                      >
                        <div className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-400">
                          {request.type}
                        </div>
                        <pre className="mt-2 whitespace-pre-wrap text-xs text-slate-700">
                          {formatJson(request.payload)}
                        </pre>
                      </div>
                    ))
                  )}
                </div>
              ) : null}
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}
