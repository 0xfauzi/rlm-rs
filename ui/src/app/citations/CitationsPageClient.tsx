"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { ApiClient, ApiError } from "../../lib/api-client";
import type { SpanRef } from "../../lib/types";
import { useApp } from "../../contexts/AppContext";
import { useToast } from "../../contexts/ToastContext";

const DEFAULT_CONTEXT_CHARS = 200;

function buildErrorMessage(error: unknown, fallback: string) {
  if (error instanceof ApiError) {
    return error.message;
  }
  return fallback;
}

function parseInteger(value: string | null) {
  if (value === null) {
    return null;
  }
  const parsed = Number.parseInt(value, 10);
  if (!Number.isFinite(parsed)) {
    return null;
  }
  return parsed;
}

function clampNonNegative(value: number) {
  if (Number.isNaN(value)) {
    return 0;
  }
  return Math.max(0, Math.floor(value));
}

function renderHighlightedText(text: string, highlightStart: number, highlightEnd: number) {
  const safeStart = Math.max(0, Math.min(text.length, highlightStart));
  const safeEnd = Math.max(safeStart, Math.min(text.length, highlightEnd));
  const before = text.slice(0, safeStart);
  const highlight = text.slice(safeStart, safeEnd);
  const after = text.slice(safeEnd);

  if (!highlight) {
    return text;
  }

  return (
    <>
      {before}
      <span className="rounded bg-amber-200/70 px-1 text-slate-900">{highlight}</span>
      {after}
    </>
  );
}

export function CitationsPageClient() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const { config } = useApp();
  const { showToast } = useToast();

  const apiClient = useMemo(
    () => new ApiClient(config.apiBaseUrl, config.devKey),
    [config.apiBaseUrl, config.devKey],
  );

  const { spanRef, paramError } = useMemo(() => {
    if (!searchParams) {
      return { spanRef: null, paramError: "Missing citation parameters." };
    }

    const tenantId = searchParams.get("tenant_id");
    const sessionId = searchParams.get("session_id");
    const docId = searchParams.get("doc_id");
    const docIndex = parseInteger(searchParams.get("doc_index"));
    const startChar = parseInteger(searchParams.get("start_char"));
    const endChar = parseInteger(searchParams.get("end_char"));
    const checksum = searchParams.get("checksum");

    if (
      !tenantId ||
      !sessionId ||
      !docId ||
      docIndex === null ||
      startChar === null ||
      endChar === null ||
      !checksum
    ) {
      return { spanRef: null, paramError: "Missing citation parameters." };
    }

    if (startChar < 0 || endChar < 0 || endChar < startChar) {
      return { spanRef: null, paramError: "Invalid citation parameters." };
    }

    return {
      spanRef: {
        tenant_id: tenantId,
        session_id: sessionId,
        doc_id: docId,
        doc_index: docIndex,
        start_char: startChar,
        end_char: endChar,
        checksum,
      } satisfies SpanRef,
      paramError: null,
    };
  }, [searchParams]);

  const [beforeChars, setBeforeChars] = useState(DEFAULT_CONTEXT_CHARS);
  const [afterChars, setAfterChars] = useState(DEFAULT_CONTEXT_CHARS);
  const [excerpt, setExcerpt] = useState<string | null>(null);
  const [expandedRange, setExpandedRange] = useState<{ start: number; end: number } | null>(
    null,
  );
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [verifyState, setVerifyState] = useState<
    | { status: "idle" }
    | { status: "loading" }
    | { status: "valid" }
    | { status: "invalid"; reason: string }
    | { status: "error"; message: string }
  >({ status: "idle" });

  const requestRange = useMemo(() => {
    if (!spanRef) {
      return null;
    }
    const start = clampNonNegative(spanRef.start_char - beforeChars);
    const end = clampNonNegative(spanRef.end_char + afterChars);
    return { start, end: Math.max(start, end) };
  }, [spanRef, beforeChars, afterChars]);

  const fetchSpan = useCallback(async () => {
    if (!spanRef || !requestRange) {
      return;
    }
    const requestRef: SpanRef = {
      ...spanRef,
      start_char: requestRange.start,
      end_char: requestRange.end,
    };
    setIsLoading(true);
    setError(null);
    try {
      const payload = await apiClient.getSpan(requestRef);
      setExcerpt(payload.text);
      setExpandedRange({ start: requestRange.start, end: requestRange.end });
      setError(null);
    } catch (err) {
      setError(buildErrorMessage(err, "Failed to load span."));
      setExcerpt(null);
      setExpandedRange(null);
    } finally {
      setIsLoading(false);
    }
  }, [apiClient, requestRange, spanRef]);

  useEffect(() => {
    void fetchSpan();
  }, [fetchSpan]);

  const highlightContent = useMemo(() => {
    if (!excerpt || !spanRef || !expandedRange) {
      return null;
    }
    const highlightStart = spanRef.start_char - expandedRange.start;
    const highlightEnd = highlightStart + (spanRef.end_char - spanRef.start_char);
    return renderHighlightedText(excerpt, highlightStart, highlightEnd);
  }, [excerpt, expandedRange, spanRef]);

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

  const handleVerify = useCallback(async () => {
    if (!spanRef) {
      return;
    }
    setVerifyState({ status: "loading" });
    try {
      const payload = await apiClient.verifyCitation(spanRef);
      if (payload.valid) {
        setVerifyState({ status: "valid" });
      } else {
        setVerifyState({ status: "invalid", reason: "Checksum mismatch" });
      }
    } catch (err) {
      setVerifyState({ status: "error", message: buildErrorMessage(err, "Verification failed.") });
    }
  }, [apiClient, spanRef]);

  const handleBack = useCallback(() => {
    if (typeof window !== "undefined" && window.history.length > 1) {
      router.back();
    } else {
      router.push("/executions");
    }
  }, [router]);

  if (paramError) {
    return (
      <div className="space-y-6">
        <button
          type="button"
          onClick={handleBack}
          className="text-xs font-semibold uppercase tracking-[0.3em] text-slate-400"
        >
          Back
        </button>
        <div className="rounded-3xl border border-rose-200 bg-rose-50 p-6 text-sm text-rose-700">
          {paramError}
          <Link
            href="/executions"
            className="mt-4 block text-xs font-semibold uppercase tracking-[0.2em] text-rose-600"
          >
            Return to Executions
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <button
        type="button"
        onClick={handleBack}
        className="text-xs font-semibold uppercase tracking-[0.3em] text-slate-400"
      >
        Back
      </button>

      <header className="space-y-2">
        <p className="text-xs font-semibold uppercase tracking-[0.35em] text-slate-400">
          Citation Viewer
        </p>
        <h1 className="text-2xl font-semibold text-slate-900">Span Metadata</h1>
      </header>

      <section className="rounded-3xl border border-slate-200 bg-white p-6 shadow-sm">
        <div className="grid gap-4 md:grid-cols-2">
          <div className="space-y-1 text-sm">
            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-400">
              Tenant ID
            </p>
            <p className="font-semibold text-slate-700">{spanRef?.tenant_id}</p>
          </div>
          <div className="space-y-1 text-sm">
            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-400">
              Session ID
            </p>
            <p className="font-semibold text-slate-700">{spanRef?.session_id}</p>
          </div>
          <div className="space-y-1 text-sm">
            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-400">
              Doc ID
            </p>
            <p className="font-semibold text-slate-700">{spanRef?.doc_id}</p>
          </div>
          <div className="space-y-1 text-sm">
            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-400">
              Doc Index
            </p>
            <p className="font-semibold text-slate-700">{spanRef?.doc_index}</p>
          </div>
          <div className="space-y-1 text-sm">
            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-400">
              Start Char
            </p>
            <p className="font-semibold text-slate-700">{spanRef?.start_char}</p>
          </div>
          <div className="space-y-1 text-sm">
            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-400">
              End Char
            </p>
            <p className="font-semibold text-slate-700">{spanRef?.end_char}</p>
          </div>
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-3 text-sm">
          <div className="space-y-1">
            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-400">
              Checksum
            </p>
            <p className="font-mono text-xs text-slate-600">{spanRef?.checksum}</p>
          </div>
          <button
            type="button"
            onClick={() => spanRef && handleCopy(spanRef.checksum)}
            className="rounded-full border border-slate-200 px-3 py-1 text-xs font-semibold uppercase tracking-[0.2em] text-slate-500"
          >
            Copy
          </button>
        </div>
      </section>

      <section className="rounded-3xl border border-slate-200 bg-white p-6 shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold text-slate-900">Excerpt</h2>
            <p className="text-sm text-slate-500">Span with surrounding context.</p>
          </div>
          <div className="flex flex-wrap items-center gap-3 text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">
            <label className="flex items-center gap-2">
              Before
              <input
                type="number"
                min={0}
                value={beforeChars}
                onChange={(event) =>
                  setBeforeChars(clampNonNegative(Number(event.target.value)))
                }
                className="w-20 rounded-full border border-slate-200 px-3 py-1 text-xs text-slate-600"
              />
            </label>
            <label className="flex items-center gap-2">
              After
              <input
                type="number"
                min={0}
                value={afterChars}
                onChange={(event) => setAfterChars(clampNonNegative(Number(event.target.value)))}
                className="w-20 rounded-full border border-slate-200 px-3 py-1 text-xs text-slate-600"
              />
            </label>
          </div>
        </div>

        {isLoading ? (
          <div className="mt-4 h-40 animate-pulse rounded-2xl bg-slate-200/70" />
        ) : null}

        {!isLoading && error ? (
          <div className="mt-4 rounded-2xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-700">
            {error}
          </div>
        ) : null}

        {!isLoading && !error && excerpt ? (
          <pre className="mt-4 whitespace-pre-wrap rounded-2xl border border-slate-100 bg-slate-50 p-4 text-sm text-slate-700">
            {highlightContent ?? excerpt}
          </pre>
        ) : null}
      </section>

      <section className="rounded-3xl border border-slate-200 bg-white p-6 shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold text-slate-900">Verification</h2>
            <p className="text-sm text-slate-500">Confirm the citation checksum.</p>
          </div>
          <button
            type="button"
            onClick={() => void handleVerify()}
            className="rounded-full bg-slate-900 px-4 py-2 text-xs font-semibold uppercase tracking-[0.2em] text-white shadow-sm disabled:cursor-not-allowed disabled:bg-slate-300"
            disabled={verifyState.status === "loading"}
          >
            {verifyState.status === "loading" ? "Verifying" : "Verify Citation"}
          </button>
        </div>

        {verifyState.status === "valid" ? (
          <div className="mt-4 rounded-2xl border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-700">
            Valid
          </div>
        ) : null}
        {verifyState.status === "invalid" ? (
          <div className="mt-4 rounded-2xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-700">
            Invalid: {verifyState.reason}
          </div>
        ) : null}
        {verifyState.status === "error" ? (
          <div className="mt-4 rounded-2xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-700">
            {verifyState.message}
          </div>
        ) : null}
      </section>
    </div>
  );
}
