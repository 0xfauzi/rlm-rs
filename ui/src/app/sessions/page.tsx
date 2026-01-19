"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { ApiClient, ApiError } from "../../lib/api-client";
import type { ListSessionsResponse, Session, SessionStatus } from "../../lib/types";
import { uploadFile } from "../../lib/s3-client";
import { useApp } from "../../contexts/AppContext";
import { useToast } from "../../contexts/ToastContext";
import { SkeletonTable } from "../../components/ui/Skeleton";
import { EmptyState } from "../../components/ui/EmptyState";
import { ErrorPanel } from "../../components/ui/ErrorPanel";

const DEFAULT_TTL_MINUTES = 60;
const MAX_TTL_MINUTES = 1440;

const STATUS_STYLES: Record<SessionStatus, string> = {
  CREATING: "bg-amber-100 text-amber-900",
  READY: "bg-emerald-100 text-emerald-900",
  EXPIRED: "bg-slate-200 text-slate-700",
  FAILED: "bg-rose-100 text-rose-900",
  DELETING: "bg-slate-200 text-slate-700",
};

function buildUuid() {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `uuid_${Date.now()}_${Math.random().toString(16).slice(2)}`;
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

function extractSessions(payload: ListSessionsResponse): Session[] {
  if (!payload || !Array.isArray(payload.sessions)) {
    return [];
  }
  return payload.sessions;
}

export default function SessionsPage() {
  const { config } = useApp();
  const { showToast } = useToast();
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const apiClient = useMemo(
    () => new ApiClient(config.apiBaseUrl, config.devKey),
    [config.apiBaseUrl, config.devKey],
  );

  const [file, setFile] = useState<File | null>(null);
  const [sourceName, setSourceName] = useState("");
  const [mimeType, setMimeType] = useState("");
  const [ttlMinutes, setTtlMinutes] = useState(DEFAULT_TTL_MINUTES);
  const [enableSearch, setEnableSearch] = useState(false);
  const [readinessMode, setReadinessMode] = useState<"LAX" | "STRICT">("LAX");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isDragActive, setIsDragActive] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);

  const [sessions, setSessions] = useState<Session[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);
  const [error, setError] = useState<Error | null>(null);

  const handleFileSelect = useCallback((selected: File | null) => {
    setFile(selected);
    if (selected) {
      setSourceName(selected.name);
      setMimeType(selected.type || "application/octet-stream");
    } else {
      setSourceName("");
      setMimeType("");
    }
  }, []);

  const handleFileChange = useCallback(
    (event: React.ChangeEvent<HTMLInputElement>) => {
      const selected = event.target.files?.[0] ?? null;
      handleFileSelect(selected);
    },
    [handleFileSelect],
  );

  const handleDrop = useCallback(
    (event: React.DragEvent<HTMLLabelElement>) => {
      event.preventDefault();
      setIsDragActive(false);
      const selected = event.dataTransfer.files?.[0] ?? null;
      handleFileSelect(selected);
    },
    [handleFileSelect],
  );

  const refreshSessions = useCallback(async () => {
    setIsLoading(true);
    try {
      const payload = await apiClient.getSessions();
      const nextSessions = extractSessions(payload);
      nextSessions.sort((left, right) => {
        const leftTime = new Date(left.created_at).getTime();
        const rightTime = new Date(right.created_at).getTime();
        if (Number.isNaN(leftTime) || Number.isNaN(rightTime)) {
          return 0;
        }
        return rightTime - leftTime;
      });
      setSessions(nextSessions);
      setLastRefresh(new Date());
      setError(null);
    } catch (error) {
      const message = error instanceof ApiError ? error.message : "Failed to load sessions";
      showToast(message, "error");
      if (error instanceof Error) {
        setError(error);
      } else {
        setError(new Error(message));
      }
    } finally {
      setIsLoading(false);
    }
  }, [apiClient, showToast]);

  useEffect(() => {
    void refreshSessions();
  }, [refreshSessions]);

  const handleUpload = useCallback(async () => {
    if (!file) {
      return;
    }

    const ttlValue = Math.min(Math.max(ttlMinutes, 1), MAX_TTL_MINUTES);
    setIsSubmitting(true);
    setUploadProgress(0);

    const objectId = buildUuid();
    const key = `raw/${config.tenant}/${objectId}/${file.name}`;
    const rawS3Uri = `s3://${config.s3Bucket}/${key}`;

    try {
      await uploadFile(config.s3Bucket, key, file, config.localstackEndpointUrl, (percent) => {
        setUploadProgress(percent);
      });

      await apiClient.createSession({
        ttl_minutes: ttlValue,
        docs: [
          {
            source_name: sourceName || file.name,
            mime_type: mimeType || "application/octet-stream",
            raw_s3_uri: rawS3Uri,
          },
        ],
        options: {
          enable_search: enableSearch,
          readiness_mode: readinessMode,
        },
      });

      showToast("Session created and queued for ingestion.", "success");
      handleFileSelect(null);
      setTtlMinutes(DEFAULT_TTL_MINUTES);
      setEnableSearch(false);
      setReadinessMode("LAX");
      setUploadProgress(null);
      await refreshSessions();
    } catch (error) {
      const message = error instanceof ApiError ? error.message : "Upload failed";
      showToast(message, "error");
      setUploadProgress(null);
    } finally {
      setIsSubmitting(false);
    }
  }, [
    apiClient,
    config.localstackEndpointUrl,
    config.s3Bucket,
    config.tenant,
    enableSearch,
    file,
    handleFileSelect,
    mimeType,
    readinessMode,
    refreshSessions,
    showToast,
    sourceName,
    ttlMinutes,
  ]);

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

  const canSubmit = file !== null && !isSubmitting;
  const readinessLabel = readinessMode === "STRICT" ? "Search" : "Parsed";

  return (
    <div className="space-y-8">
      <header className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.35em] text-slate-400">
            Sessions
          </p>
          <h1 className="mt-2 text-2xl font-semibold text-slate-900">
            Upload documents and track ingestion status.
          </h1>
        </div>
        <div className="rounded-2xl border border-slate-200 bg-white px-4 py-2 text-xs text-slate-600">
          Last refresh: {lastRefresh ? lastRefresh.toLocaleTimeString() : "-"}
        </div>
      </header>

      <section className="grid gap-6 lg:grid-cols-[1.1fr_1fr]">
        <div
          id="session-uploader"
          className="rounded-3xl border border-slate-200 bg-white p-6 shadow-sm"
        >
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-semibold text-slate-900">Upload and Create</h2>
            <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold uppercase tracking-[0.25em] text-slate-500">
              {readinessLabel}
            </span>
          </div>
          <p className="mt-2 text-sm text-slate-500">
            Drop a file below and configure the session before creating it.
          </p>

          <label
            className={`mt-6 flex cursor-pointer flex-col items-center justify-center rounded-2xl border-2 border-dashed px-6 py-8 text-center transition ${
              isDragActive ? "border-slate-900 bg-slate-50" : "border-slate-200"
            }`}
            onDragOver={(event) => {
              event.preventDefault();
              setIsDragActive(true);
            }}
            onDragLeave={() => setIsDragActive(false)}
            onDrop={handleDrop}
          >
            <input
              ref={fileInputRef}
              type="file"
              className="sr-only"
              onChange={handleFileChange}
            />
            <span className="text-sm font-semibold text-slate-700">
              {file ? file.name : "Drag and drop any file"}
            </span>
            <span className="mt-2 text-xs text-slate-500">
              {file ? "Click to replace" : "or click to browse"}
            </span>
          </label>

          {uploadProgress !== null ? (
            <div className="mt-4">
              <div className="flex items-center justify-between text-xs text-slate-500">
                <span>Upload progress</span>
                <span>{uploadProgress}%</span>
              </div>
              <div className="mt-2 h-2 w-full rounded-full bg-slate-100">
                <div
                  className="h-2 rounded-full bg-slate-900 transition-all"
                  style={{ width: `${uploadProgress}%` }}
                />
              </div>
            </div>
          ) : null}

          <div className="mt-6 grid gap-4 sm:grid-cols-2">
            <label className="space-y-2 text-sm font-semibold text-slate-700">
              Source name
              <input
                value={sourceName}
                onChange={(event) => setSourceName(event.target.value)}
                placeholder="Document name"
                className="w-full rounded-2xl border border-slate-200 px-4 py-2 text-sm text-slate-900"
              />
            </label>
            <label className="space-y-2 text-sm font-semibold text-slate-700">
              MIME type
              <input
                value={mimeType}
                onChange={(event) => setMimeType(event.target.value)}
                placeholder="application/pdf"
                className="w-full rounded-2xl border border-slate-200 px-4 py-2 text-sm text-slate-900"
              />
            </label>
            <label className="space-y-2 text-sm font-semibold text-slate-700">
              TTL minutes
              <input
                type="number"
                value={ttlMinutes}
                min={1}
                max={MAX_TTL_MINUTES}
                onChange={(event) => setTtlMinutes(Number(event.target.value))}
                className="w-full rounded-2xl border border-slate-200 px-4 py-2 text-sm text-slate-900"
              />
            </label>
            <label className="space-y-2 text-sm font-semibold text-slate-700">
              Readiness mode
              <select
                value={readinessMode}
                onChange={(event) =>
                  setReadinessMode(event.target.value === "STRICT" ? "STRICT" : "LAX")
                }
                className="w-full rounded-2xl border border-slate-200 px-4 py-2 text-sm text-slate-900"
              >
                <option value="LAX">LAX</option>
                <option value="STRICT">STRICT</option>
              </select>
            </label>
          </div>

          <label className="mt-4 flex items-center gap-3 text-sm font-semibold text-slate-700">
            <input
              type="checkbox"
              checked={enableSearch}
              onChange={(event) => setEnableSearch(event.target.checked)}
              className="h-4 w-4 rounded border-slate-300 text-slate-900"
            />
            Enable search indexing
          </label>

          <button
            type="button"
            onClick={handleUpload}
            disabled={!canSubmit}
            className={`mt-6 flex w-full items-center justify-center gap-2 rounded-2xl px-5 py-3 text-sm font-semibold transition ${
              canSubmit
                ? "bg-slate-900 text-white hover:bg-slate-800"
                : "cursor-not-allowed bg-slate-200 text-slate-500"
            }`}
          >
            {isSubmitting ? (
              <span className="h-4 w-4 animate-spin rounded-full border-2 border-white/40 border-t-white" />
            ) : null}
            Upload and Create
          </button>
        </div>

        <div className="rounded-3xl border border-slate-200 bg-gradient-to-br from-white via-white to-slate-50 p-6 shadow-sm">
          <h3 className="text-sm font-semibold uppercase tracking-[0.3em] text-slate-400">
            Quick Tips
          </h3>
          <ul className="mt-4 space-y-3 text-sm text-slate-600">
            <li>Use LAX readiness for rapid parsing, STRICT for indexed search.</li>
            <li>Uploads target the LocalStack bucket configured in settings.</li>
            <li>Sessions expire automatically after the TTL window.</li>
          </ul>
        </div>
      </section>

      <section className="rounded-3xl border border-slate-200 bg-white p-6 shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-lg font-semibold text-slate-900">Sessions</h2>
          <button
            type="button"
            onClick={refreshSessions}
            className="rounded-full border border-slate-200 px-4 py-2 text-xs font-semibold uppercase tracking-[0.25em] text-slate-600 transition hover:border-slate-400"
          >
            Refresh
          </button>
        </div>

        {isLoading ? (
          <div className="mt-6">
            <SkeletonTable rows={4} columns={6} />
          </div>
        ) : null}

        {!isLoading && error ? (
          <div className="mt-6">
            <ErrorPanel error={error} onRetry={() => void refreshSessions()} />
          </div>
        ) : null}

        {!isLoading && !error && sessions.length === 0 ? (
          <div className="mt-6">
            <EmptyState
              title="No sessions yet"
              description="Upload a document to create your first session."
              action={{ label: "Upload a document", href: "#session-uploader" }}
            />
          </div>
        ) : null}

        {!isLoading && !error && sessions.length > 0 ? (
          <div className="mt-6 overflow-x-auto">
            <table className="w-full border-collapse text-left text-sm">
              <thead>
                <tr className="text-xs uppercase tracking-wide text-slate-400">
                  <th className="pb-3">Session ID</th>
                  <th className="pb-3">Status</th>
                  <th className="pb-3">Readiness</th>
                  <th className="pb-3">Docs</th>
                  <th className="pb-3">Created</th>
                  <th className="pb-3" />
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {sessions.map((session) => (
                  <tr key={session.id} className="text-slate-700">
                    <td className="py-4">
                      <div className="flex items-center gap-2">
                        <span className="font-semibold text-slate-900">
                          {truncateId(session.id)}
                        </span>
                        <button
                          type="button"
                          onClick={() => handleCopy(session.id)}
                          className="rounded-full border border-slate-200 px-2 py-1 text-[10px] font-semibold uppercase tracking-[0.2em] text-slate-500"
                        >
                          Copy
                        </button>
                      </div>
                    </td>
                    <td className="py-4">
                      <span
                        className={`rounded-full px-3 py-1 text-xs font-semibold ${
                          STATUS_STYLES[session.status]
                        }`}
                      >
                        {session.status}
                      </span>
                    </td>
                    <td className="py-4">
                      <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold text-slate-600">
                        {session.readiness_mode === "STRICT" ? "Search" : "Parsed"}
                      </span>
                    </td>
                    <td className="py-4 text-sm text-slate-600">
                      {session.docs?.length ?? 0}
                    </td>
                    <td className="py-4 text-sm text-slate-600">
                      {formatTimestamp(session.created_at)}
                    </td>
                    <td className="py-4">
                      <Link
                        href={`/sessions/${session.id}`}
                        className="rounded-full border border-slate-200 px-4 py-2 text-xs font-semibold uppercase tracking-[0.2em] text-slate-600 transition hover:border-slate-400"
                      >
                        Open
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </section>
    </div>
  );
}
