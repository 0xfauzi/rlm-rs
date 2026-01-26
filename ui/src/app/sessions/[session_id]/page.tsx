"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { ApiClient, ApiError } from "../../../lib/api-client";
import type { GetSessionResponse, IngestStatus, SessionStatus } from "../../../lib/types";
import { useApp } from "../../../contexts/AppContext";
import { useToast } from "../../../contexts/ToastContext";
import { StartAnswererModal } from "../../../components/modals/StartAnswererModal";
import { StartRuntimeModal } from "../../../components/modals/StartRuntimeModal";
import { SkeletonCard } from "../../../components/ui/Skeleton";
import { ErrorPanel } from "../../../components/ui/ErrorPanel";

const SESSION_STATUS_STYLES: Record<SessionStatus, string> = {
  CREATING: "bg-amber-100 text-amber-900",
  READY: "bg-emerald-100 text-emerald-900",
  EXPIRED: "bg-slate-200 text-slate-700",
  FAILED: "bg-rose-100 text-rose-900",
  DELETING: "bg-slate-200 text-slate-700",
};

const INGEST_STATUS_STYLES: Record<IngestStatus, string> = {
  REGISTERED: "bg-slate-200 text-slate-700",
  PARSING: "bg-amber-100 text-amber-900",
  PARSED: "bg-emerald-100 text-emerald-900",
  INDEXING: "bg-indigo-100 text-indigo-900",
  INDEXED: "bg-emerald-100 text-emerald-900",
  FAILED: "bg-rose-100 text-rose-900",
};

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

function formatCountdown(expiresAt: string | null, now: number) {
  if (!expiresAt) {
    return "-";
  }
  const expiry = new Date(expiresAt).getTime();
  if (Number.isNaN(expiry)) {
    return "-";
  }
  const remainingSeconds = Math.max(0, Math.floor((expiry - now) / 1000));
  const minutes = Math.floor(remainingSeconds / 60);
  const seconds = remainingSeconds % 60;
  return `${minutes.toString().padStart(2, "0")}:${seconds.toString().padStart(2, "0")}`;
}

function parseS3Uri(uri: string) {
  const match = uri.match(/^s3:\/\/([^/]+)\/(.+)$/);
  if (!match || !match[1] || !match[2]) {
    return null;
  }
  return { bucket: match[1], key: match[2] };
}

function buildS3HttpUrl(uri: string, endpoint: string | null) {
  const parsed = parseS3Uri(uri);
  if (!parsed) {
    return null;
  }
  const encodedKey = parsed.key
    .split("/")
    .map((segment) => encodeURIComponent(segment))
    .join("/");
  if (endpoint) {
    return `${endpoint.replace(/\/$/, "")}/${parsed.bucket}/${encodedKey}`;
  }
  return `https://${parsed.bucket}.s3.amazonaws.com/${encodedKey}`;
}

function buildErrorMessage(error: unknown, fallback: string) {
  if (error instanceof ApiError) {
    return error.message;
  }
  return fallback;
}

export default function SessionDetailPage() {
  const params = useParams();
  const sessionId = typeof params.session_id === "string" ? params.session_id : "";
  const { config } = useApp();
  const { showToast } = useToast();

  const apiClient = useMemo(
    () => new ApiClient(config.apiBaseUrl, config.devKey),
    [config.apiBaseUrl, config.devKey],
  );

  const [session, setSession] = useState<GetSessionResponse | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const [now, setNow] = useState(() => Date.now());

  const [isAnswererOpen, setIsAnswererOpen] = useState(false);
  const [isRuntimeOpen, setIsRuntimeOpen] = useState(false);

  const [drawerDoc, setDrawerDoc] = useState<GetSessionResponse["docs"][number] | null>(null);
  const [drawerText, setDrawerText] = useState<string | null>(null);
  const [drawerError, setDrawerError] = useState<string | null>(null);
  const [drawerLoading, setDrawerLoading] = useState(false);

  const refreshSession = useCallback(async () => {
    if (!sessionId) {
      return;
    }
    try {
      const payload = await apiClient.getSession(sessionId);
      setSession(payload);
      setError(null);
    } catch (err) {
      if (err instanceof Error) {
        setError(err);
      } else {
        setError(new Error("Failed to load session."));
      }
    } finally {
      setIsLoading(false);
    }
  }, [apiClient, sessionId]);

  useEffect(() => {
    void refreshSession();
  }, [refreshSession]);

  useEffect(() => {
    if (!session?.expires_at) {
      return undefined;
    }
    const interval = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(interval);
  }, [session?.expires_at]);

  useEffect(() => {
    if (session?.status !== "CREATING") {
      return undefined;
    }
    const interval = window.setInterval(() => {
      void refreshSession();
    }, 5000);
    return () => window.clearInterval(interval);
  }, [refreshSession, session?.status]);

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

  const handleOpenDrawer = useCallback((doc: GetSessionResponse["docs"][number]) => {
    setDrawerDoc(doc);
  }, []);

  const handleCloseDrawer = useCallback(() => {
    setDrawerDoc(null);
    setDrawerText(null);
    setDrawerError(null);
    setDrawerLoading(false);
  }, []);

  useEffect(() => {
    const run = async () => {
      if (!drawerDoc?.text_s3_uri) {
        return;
      }
      const url = buildS3HttpUrl(drawerDoc.text_s3_uri, config.localstackEndpointUrl);
      if (!url) {
        setDrawerError("Invalid text URI.");
        return;
      }
      setDrawerLoading(true);
      setDrawerError(null);
      try {
        const response = await fetch(url);
        if (!response.ok) {
          throw new Error("Failed to fetch parsed text.");
        }
        const text = await response.text();
        setDrawerText(text);
      } catch (err) {
        setDrawerError(buildErrorMessage(err, "Failed to fetch parsed text."));
      } finally {
        setDrawerLoading(false);
      }
    };
    void run();
  }, [drawerDoc, config.localstackEndpointUrl]);

  const lineItems = useMemo(() => {
    if (!drawerText) {
      return [] as Array<{ number: number; text: string }>;
    }
    return drawerText.split(/\r?\n/).map((line, index) => ({
      number: index + 1,
      text: line.length > 0 ? line : " ",
    }));
  }, [drawerText]);

  const readinessLabel = session?.readiness?.ready
    ? "Ready"
    : session?.readiness?.parsed_ready
      ? "Parsed"
      : "Pending";

  const isReady = session?.status === "READY";

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
            Session Detail
          </p>
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="text-2xl font-semibold text-slate-900">{sessionId || "Session"}</h1>
            {session ? (
              <span
                className={`rounded-full px-3 py-1 text-xs font-semibold uppercase tracking-[0.25em] ${
                  SESSION_STATUS_STYLES[session.status]
                }`}
              >
                {session.status}
              </span>
            ) : null}
          </div>
          <div className="flex flex-wrap items-center gap-4 text-sm text-slate-500">
            <button
              type="button"
              onClick={() => handleCopy(sessionId)}
              className="rounded-full border border-slate-200 px-3 py-1 text-xs font-semibold uppercase tracking-[0.2em] text-slate-500"
            >
              Copy Session ID
            </button>
            <div className="rounded-full border border-slate-200 px-3 py-1 text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">
              TTL: {formatCountdown(session?.expires_at ?? null, now)}
            </div>
            <div className="rounded-full border border-slate-200 px-3 py-1 text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">
              Created: {formatTimestamp(session?.created_at ?? null)}
            </div>
            <div className="rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">
              {readinessLabel}
            </div>
          </div>
        </div>
        <div className="flex flex-wrap gap-3">
          <button
            type="button"
            disabled={!isReady}
            onClick={() => setIsAnswererOpen(true)}
            className="rounded-full bg-slate-900 px-4 py-2 text-xs font-semibold uppercase tracking-[0.2em] text-white shadow-sm disabled:cursor-not-allowed disabled:bg-slate-300"
          >
            Start Answerer
          </button>
          <button
            type="button"
            disabled={!isReady}
            onClick={() => setIsRuntimeOpen(true)}
            className="rounded-full border border-slate-200 px-4 py-2 text-xs font-semibold uppercase tracking-[0.2em] text-slate-600 shadow-sm disabled:cursor-not-allowed disabled:text-slate-300"
          >
            Start Runtime
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
        <ErrorPanel error={error} onRetry={() => void refreshSession()} />
      ) : null}

      {!isLoading && !error && session ? (
        <section className="rounded-3xl border border-slate-200 bg-white p-6 shadow-sm">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-semibold text-slate-900">Documents</h2>
            <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">
              {session.docs.length} docs
            </span>
          </div>
          <div className="mt-4 overflow-x-auto">
            <table className="min-w-full text-left text-sm">
              <thead className="border-b border-slate-200 text-xs uppercase tracking-[0.25em] text-slate-400">
                <tr>
                  <th className="px-3 py-2">Doc ID</th>
                  <th className="px-3 py-2">Source</th>
                  <th className="px-3 py-2">Status</th>
                  <th className="px-3 py-2">Parsed URIs</th>
                  <th className="px-3 py-2">Actions</th>
                </tr>
              </thead>
              <tbody>
                {session.docs.map((doc) => (
                  <tr key={doc.doc_id} className="border-b border-slate-100">
                    <td className="px-3 py-3 text-sm font-semibold text-slate-700">
                      <div className="flex items-center gap-2">
                        <span>{truncateId(doc.doc_id)}</span>
                        <button
                          type="button"
                          onClick={() => handleCopy(doc.doc_id)}
                          className="rounded-full border border-slate-200 px-2 py-1 text-[10px] font-semibold uppercase tracking-[0.2em] text-slate-400"
                        >
                          Copy
                        </button>
                      </div>
                    </td>
                    <td className="px-3 py-3 text-sm text-slate-600">{doc.source_name}</td>
                    <td className="px-3 py-3">
                      <span
                        className={`rounded-full px-3 py-1 text-xs font-semibold uppercase tracking-[0.2em] ${
                          INGEST_STATUS_STYLES[doc.ingest_status]
                        }`}
                      >
                        {doc.ingest_status}
                      </span>
                    </td>
                    <td className="px-3 py-3 text-xs text-slate-500">
                      <div className="space-y-1">
                        {doc.text_s3_uri ? (
                          <a
                            href={doc.text_s3_uri}
                            className="block text-slate-600 underline decoration-dashed underline-offset-2"
                          >
                            Text
                          </a>
                        ) : (
                          <span className="block text-slate-300">Text</span>
                        )}
                        {doc.meta_s3_uri ? (
                          <a
                            href={doc.meta_s3_uri}
                            className="block text-slate-600 underline decoration-dashed underline-offset-2"
                          >
                            Meta
                          </a>
                        ) : (
                          <span className="block text-slate-300">Meta</span>
                        )}
                        {doc.offsets_s3_uri ? (
                          <a
                            href={doc.offsets_s3_uri}
                            className="block text-slate-600 underline decoration-dashed underline-offset-2"
                          >
                            Offsets
                          </a>
                        ) : (
                          <span className="block text-slate-300">Offsets</span>
                        )}
                      </div>
                    </td>
                    <td className="px-3 py-3">
                      <button
                        type="button"
                        disabled={!doc.text_s3_uri}
                        onClick={() => handleOpenDrawer(doc)}
                        className="rounded-full border border-slate-200 px-3 py-1 text-xs font-semibold uppercase tracking-[0.2em] text-slate-600 disabled:cursor-not-allowed disabled:text-slate-300"
                      >
                        View parsed text
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}

      <StartAnswererModal
        isOpen={isAnswererOpen}
        onClose={() => setIsAnswererOpen(false)}
        sessionId={sessionId}
        budgetsDefault={session?.budgets_default ?? null}
      />
      <StartRuntimeModal
        isOpen={isRuntimeOpen}
        onClose={() => setIsRuntimeOpen(false)}
        sessionId={sessionId}
      />

      {drawerDoc ? (
        <div className="fixed inset-y-0 right-0 z-40 w-full max-w-2xl overflow-hidden bg-white shadow-2xl">
          <div className="flex h-full flex-col">
            <div className="flex items-center justify-between border-b border-slate-200 px-6 py-4">
              <div>
                <p className="text-xs font-semibold uppercase tracking-[0.3em] text-slate-400">
                  Parsed Text
                </p>
                <h3 className="text-lg font-semibold text-slate-900">{drawerDoc.source_name}</h3>
              </div>
              <button
                type="button"
                onClick={handleCloseDrawer}
                className="rounded-full border border-slate-200 px-3 py-1 text-xs font-semibold uppercase tracking-[0.2em] text-slate-500"
              >
                Close
              </button>
            </div>
            <div className="flex-1 overflow-y-auto px-6 py-4">
              {drawerLoading ? (
                <div className="space-y-3">
                  <div className="h-4 rounded bg-slate-200/70 animate-pulse" />
                  <div className="h-4 rounded bg-slate-200/70 animate-pulse" />
                  <div className="h-4 rounded bg-slate-200/70 animate-pulse" />
                </div>
              ) : null}
              {drawerError ? (
                <div className="rounded-2xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-700">
                  {drawerError}
                </div>
              ) : null}
              {!drawerLoading && !drawerError ? (
                <div className="space-y-2">
                  {lineItems.map((line) => (
                    <div key={`${drawerDoc.doc_id}-${line.number}`} className="grid grid-cols-[auto_1fr] gap-x-4">
                      <span className="text-xs text-slate-400 tabular-nums">{line.number}</span>
                      <span className="whitespace-pre-wrap font-mono text-sm text-slate-700">
                        {line.text}
                      </span>
                    </div>
                  ))}
                </div>
              ) : null}
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
