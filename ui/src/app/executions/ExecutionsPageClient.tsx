"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { ApiClient, ApiError } from "../../lib/api-client";
import type { ExecutionListItem, ExecutionMode } from "../../lib/types";
import { useApp } from "../../contexts/AppContext";
import { useToast } from "../../contexts/ToastContext";
import { SkeletonTable } from "../../components/ui/Skeleton";
import { EmptyState } from "../../components/ui/EmptyState";

const PAGE_SIZE = 50;

const STATUS_FILTERS = ["ALL", "PENDING", "RUNNING", "COMPLETED", "FAILED", "CANCELLED"] as const;
const MODE_FILTERS = ["ALL", "ANSWERER", "RUNTIME"] as const;

type StatusFilter = (typeof STATUS_FILTERS)[number];
type ModeFilter = (typeof MODE_FILTERS)[number];

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

const MODE_STYLES: Record<ExecutionMode, string> = {
  ANSWERER: "bg-slate-100 text-slate-700",
  RUNTIME: "bg-sky-100 text-sky-900",
};

function normalizeStatus(value: string | null): StatusFilter {
  if (!value) {
    return "ALL";
  }
  const upper = value.toUpperCase();
  if (STATUS_FILTERS.includes(upper as StatusFilter)) {
    return upper as StatusFilter;
  }
  return "ALL";
}

function normalizeMode(value: string | null): ModeFilter {
  if (!value) {
    return "ALL";
  }
  const upper = value.toUpperCase();
  if (MODE_FILTERS.includes(upper as ModeFilter)) {
    return upper as ModeFilter;
  }
  return "ALL";
}

function truncateId(id: string) {
  if (id.length <= 16) {
    return id;
  }
  return `${id.slice(0, 8)}...${id.slice(-6)}`;
}

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

export function ExecutionsPageClient() {
  const { config } = useApp();
  const { showToast } = useToast();
  const router = useRouter();
  const searchParams = useSearchParams();

  const apiClient = useMemo(
    () => new ApiClient(config.apiBaseUrl, config.devKey),
    [config.apiBaseUrl, config.devKey],
  );

  const initialFilters = useMemo(() => {
    return {
      status: normalizeStatus(searchParams?.get("status") ?? null),
      mode: normalizeMode(searchParams?.get("mode") ?? null),
      sessionId: searchParams?.get("session_id") ?? "",
    };
  }, [searchParams]);

  const [statusFilter, setStatusFilter] = useState<StatusFilter>(initialFilters.status);
  const [modeFilter, setModeFilter] = useState<ModeFilter>(initialFilters.mode);
  const [sessionFilter, setSessionFilter] = useState(initialFilters.sessionId);
  const [pageIndex, setPageIndex] = useState(0);
  const [pageCursors, setPageCursors] = useState<Array<string | null>>([null]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);

  const [executions, setExecutions] = useState<ExecutionListItem[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);
  const [cancelingIds, setCancelingIds] = useState<Set<string>>(() => new Set());

  useEffect(() => {
    setStatusFilter(initialFilters.status);
    setModeFilter(initialFilters.mode);
    setSessionFilter(initialFilters.sessionId);
  }, [initialFilters]);

  useEffect(() => {
    setPageIndex(0);
    setPageCursors([null]);
    setNextCursor(null);
  }, [statusFilter, modeFilter, sessionFilter]);

  useEffect(() => {
    const params = new URLSearchParams();
    if (statusFilter !== "ALL") {
      params.set("status", statusFilter);
    }
    if (modeFilter !== "ALL") {
      params.set("mode", modeFilter);
    }
    if (sessionFilter.trim()) {
      params.set("session_id", sessionFilter.trim());
    }
    const next = params.toString();
    const current = searchParams?.toString() ?? "";
    if (next !== current) {
      const suffix = next ? `?${next}` : "";
      router.replace(`/executions${suffix}`, { scroll: false });
    }
  }, [modeFilter, router, searchParams, sessionFilter, statusFilter]);

  const currentCursor = pageCursors[pageIndex] ?? null;

  const refreshExecutions = useCallback(
    async (showLoading: boolean) => {
      if (showLoading) {
        setIsLoading(true);
      } else {
        setIsRefreshing(true);
      }

      try {
        const payload = await apiClient.listExecutions({
          status: statusFilter === "ALL" ? undefined : statusFilter,
          mode: modeFilter === "ALL" ? undefined : modeFilter,
          sessionId: sessionFilter.trim() ? sessionFilter.trim() : undefined,
          limit: PAGE_SIZE,
          cursor: currentCursor ?? undefined,
        });
        setExecutions(payload.executions ?? []);
        setNextCursor(payload.next_cursor ?? null);
        setLastRefresh(new Date());
      } catch (error) {
        const message =
          error instanceof ApiError ? error.message : "Failed to refresh executions.";
        showToast(message, "error", 3000);
      } finally {
        if (showLoading) {
          setIsLoading(false);
        } else {
          setIsRefreshing(false);
        }
      }
    },
    [
      apiClient,
      currentCursor,
      modeFilter,
      sessionFilter,
      showToast,
      statusFilter,
    ],
  );

  useEffect(() => {
    void refreshExecutions(true);
  }, [refreshExecutions]);

  useEffect(() => {
    const interval = window.setInterval(() => {
      void refreshExecutions(false);
    }, 10000);
    return () => window.clearInterval(interval);
  }, [refreshExecutions]);

  const showPagination = pageIndex > 0 || !!nextCursor;

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

  const handleRefreshClick = useCallback(() => {
    void refreshExecutions(false);
  }, [refreshExecutions]);

  const handleCancel = useCallback(
    async (executionId: string) => {
      setCancelingIds((prev) => {
        const next = new Set(prev);
        next.add(executionId);
        return next;
      });
      try {
        const payload = await apiClient.cancelExecution(executionId);
        setExecutions((prev) =>
          prev.map((execution) =>
            execution.execution_id === executionId
              ? {
                  ...execution,
                  status: payload.status,
                  completed_at: payload.completed_at ?? execution.completed_at,
                }
              : execution,
          ),
        );
        if (payload.status === "CANCELLED") {
          showToast("Execution cancelled", "success", 2000);
        } else {
          showToast(`Execution is ${payload.status}`, "success", 2000);
        }
      } catch (error) {
        const message =
          error instanceof ApiError ? error.message : "Failed to cancel execution.";
        showToast(message, "error", 3000);
      } finally {
        setCancelingIds((prev) => {
          const next = new Set(prev);
          next.delete(executionId);
          return next;
        });
      }
    },
    [apiClient, showToast],
  );

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.35em] text-slate-400">
            Executions
          </p>
          <h1 className="mt-2 text-2xl font-semibold text-slate-900">
            Monitor Answerer and Runtime activity.
          </h1>
        </div>
        <div className="flex items-center gap-3">
          <div className="rounded-2xl border border-slate-200 bg-white px-4 py-2 text-xs text-slate-600">
            {isRefreshing ? "Refreshing..." : `Last refresh: ${lastRefresh?.toLocaleTimeString() ?? "-"}`}
          </div>
          <button
            type="button"
            onClick={handleRefreshClick}
            className="rounded-full border border-slate-200 px-4 py-2 text-xs font-semibold uppercase tracking-[0.25em] text-slate-600 transition hover:border-slate-400"
          >
            Refresh
          </button>
        </div>
      </header>

      <section className="rounded-3xl border border-slate-200 bg-white p-6 shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold text-slate-900">Filters</h2>
            <p className="text-sm text-slate-500">
              Narrow by status, mode, or session prefix.
            </p>
          </div>
        </div>

        <div className="mt-4 grid gap-4 md:grid-cols-[200px_200px_1fr]">
          <label className="grid gap-2 text-sm text-slate-600">
            Status
            <select
              value={statusFilter}
              onChange={(event) => setStatusFilter(event.target.value as StatusFilter)}
              className="rounded-full border border-slate-200 px-3 py-2 text-sm text-slate-700 shadow-sm focus:border-slate-400 focus:outline-none"
            >
              {STATUS_FILTERS.map((status) => (
                <option key={status} value={status}>
                  {status === "ALL" ? "All" : status}
                </option>
              ))}
            </select>
          </label>
          <label className="grid gap-2 text-sm text-slate-600">
            Mode
            <select
              value={modeFilter}
              onChange={(event) => setModeFilter(event.target.value as ModeFilter)}
              className="rounded-full border border-slate-200 px-3 py-2 text-sm text-slate-700 shadow-sm focus:border-slate-400 focus:outline-none"
            >
              {MODE_FILTERS.map((mode) => (
                <option key={mode} value={mode}>
                  {mode === "ALL" ? "All" : mode === "ANSWERER" ? "Answerer" : "Runtime"}
                </option>
              ))}
            </select>
          </label>
          <label className="grid gap-2 text-sm text-slate-600">
            Session ID
            <input
              value={sessionFilter}
              onChange={(event) => setSessionFilter(event.target.value)}
              className="rounded-full border border-slate-200 px-3 py-2 text-sm text-slate-700 shadow-sm focus:border-slate-400 focus:outline-none"
              placeholder="Filter by session prefix"
            />
          </label>
        </div>
      </section>

      <section className="rounded-3xl border border-slate-200 bg-white p-6 shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-lg font-semibold text-slate-900">Executions</h2>
          {showPagination ? (
            <div className="text-xs uppercase tracking-[0.2em] text-slate-400">
              Page {pageIndex + 1}
            </div>
          ) : null}
        </div>

        {isLoading ? (
          <div className="mt-6">
            <SkeletonTable rows={5} columns={7} />
          </div>
        ) : null}

        {!isLoading && executions.length === 0 ? (
          <div className="mt-6">
            <EmptyState
              title="No executions found"
              description="Start an Answerer or Runtime execution to see it here."
            />
          </div>
        ) : null}

        {!isLoading && executions.length > 0 ? (
          <div className="mt-6 overflow-x-auto">
            <table className="w-full border-collapse text-left text-sm">
              <thead>
                <tr className="text-xs uppercase tracking-wide text-slate-400">
                  <th className="pb-3">Execution ID</th>
                  <th className="pb-3">Session ID</th>
                  <th className="pb-3">Mode</th>
                  <th className="pb-3">Status</th>
                  <th className="pb-3">Started</th>
                  <th className="pb-3">Completed</th>
                  <th className="pb-3" />
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {executions.map((execution) => {
                  const status = execution.status ?? "PENDING";
                  const mode: ExecutionMode = execution.mode ?? "ANSWERER";
                  const modeLabel = mode === "RUNTIME" ? "Runtime" : "Answerer";
                  const openHref =
                    mode === "RUNTIME"
                      ? `/executions/${execution.execution_id}/runtime?session_id=${execution.session_id}`
                      : `/executions/${execution.execution_id}`;
                  const isCanceling = cancelingIds.has(execution.execution_id);
                  const canCancel = status === "RUNNING";
                  return (
                    <tr key={execution.execution_id} className="text-slate-700">
                      <td className="py-4">
                        <div className="flex items-center gap-2">
                          <span className="font-semibold text-slate-900">
                            {truncateId(execution.execution_id)}
                          </span>
                          <button
                            type="button"
                            onClick={() => handleCopy(execution.execution_id)}
                            className="rounded-full border border-slate-200 px-2 py-1 text-[10px] font-semibold uppercase tracking-[0.2em] text-slate-500"
                          >
                            Copy
                          </button>
                        </div>
                      </td>
                      <td className="py-4">
                        <Link
                          href={`/sessions/${execution.session_id}`}
                          className="font-semibold text-slate-900 transition hover:text-slate-600"
                        >
                          {truncateId(execution.session_id)}
                        </Link>
                      </td>
                      <td className="py-4">
                        <span
                          className={`rounded-full px-3 py-1 text-xs font-semibold ${
                            MODE_STYLES[mode]
                          }`}
                        >
                          {modeLabel}
                        </span>
                      </td>
                      <td className="py-4">
                        <span
                          className={`rounded-full px-3 py-1 text-xs font-semibold ${
                            STATUS_STYLES[status] ?? "bg-slate-100 text-slate-700"
                          }`}
                        >
                          {status}
                        </span>
                      </td>
                      <td className="py-4 text-sm text-slate-600">
                        {formatTimestamp(execution.started_at)}
                      </td>
                      <td className="py-4 text-sm text-slate-600">
                        {formatTimestamp(execution.completed_at)}
                      </td>
                      <td className="py-4">
                        <div className="flex items-center gap-2">
                          <Link
                            href={openHref}
                            className="rounded-full border border-slate-200 px-4 py-2 text-xs font-semibold uppercase tracking-[0.2em] text-slate-600 transition hover:border-slate-400"
                          >
                            Open
                          </Link>
                          <button
                            type="button"
                            onClick={() => handleCancel(execution.execution_id)}
                            disabled={!canCancel || isCanceling}
                            className="rounded-full border border-rose-200 px-4 py-2 text-xs font-semibold uppercase tracking-[0.2em] text-rose-600 disabled:cursor-not-allowed disabled:opacity-50"
                          >
                            {isCanceling ? "Cancelling..." : "Cancel"}
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : null}

        {showPagination ? (
          <div className="mt-6 flex flex-wrap items-center justify-between gap-3">
            <p className="text-xs text-slate-500">
              Showing {executions.length} results
            </p>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => setPageIndex((prev) => Math.max(0, prev - 1))}
                disabled={pageIndex === 0}
                className="rounded-full border border-slate-200 px-3 py-1 text-xs font-semibold uppercase tracking-[0.2em] text-slate-600 disabled:cursor-not-allowed disabled:opacity-50"
              >
                Prev
              </button>
              <button
                type="button"
                onClick={() => {
                  if (!nextCursor) {
                    return;
                  }
                  setPageCursors((prev) => {
                    const next = [...prev];
                    next[pageIndex + 1] = nextCursor;
                    return next;
                  });
                  setPageIndex((prev) => prev + 1);
                }}
                disabled={!nextCursor}
                className="rounded-full border border-slate-200 px-3 py-1 text-xs font-semibold uppercase tracking-[0.2em] text-slate-600 disabled:cursor-not-allowed disabled:opacity-50"
              >
                Next
              </button>
            </div>
          </div>
        ) : null}
      </section>
    </div>
  );
}
