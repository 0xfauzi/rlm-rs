import React from "react";
import { ApiError } from "../../lib/api-client";

interface ErrorPanelProps {
  error: unknown;
  title?: string;
  onRetry?: () => void;
}

function formatDetails(details: ApiError["details"]) {
  if (details === null || details === undefined) {
    return null;
  }
  try {
    if (typeof details === "string") {
      return details;
    }
    return JSON.stringify(details, null, 2);
  } catch {
    return String(details);
  }
}

export function ErrorPanel({ error, title = "Request failed", onRetry }: ErrorPanelProps) {
  const isApiError = error instanceof ApiError;
  const message = isApiError ? error.message : error instanceof Error ? error.message : title;
  const code = isApiError ? error.code : "ERROR";
  const details = isApiError ? formatDetails(error.details) : null;
  const requestId = isApiError ? error.requestId : null;

  return (
    <div className="rounded-3xl border border-rose-200 bg-rose-50 p-6 text-sm text-rose-800">
      <div className="flex flex-wrap items-center gap-3">
        <span className="rounded-full bg-rose-100 px-3 py-1 text-xs font-semibold uppercase tracking-[0.2em] text-rose-700">
          {code}
        </span>
        <p className="font-semibold text-rose-900">{message}</p>
      </div>
      {details ? (
        <details className="mt-4 rounded-2xl border border-rose-200 bg-white/70 px-4 py-3 text-rose-800">
          <summary className="cursor-pointer text-xs font-semibold uppercase tracking-[0.2em] text-rose-600">
            Details
          </summary>
          <pre className="mt-3 whitespace-pre-wrap text-xs text-rose-700">{details}</pre>
        </details>
      ) : null}
      {requestId ? (
        <p className="mt-3 text-xs text-rose-700">Request ID: {requestId}</p>
      ) : null}
      {onRetry ? (
        <button
          type="button"
          onClick={onRetry}
          className="mt-4 rounded-full border border-rose-200 px-4 py-2 text-xs font-semibold uppercase tracking-[0.2em] text-rose-700"
        >
          Retry
        </button>
      ) : null}
    </div>
  );
}
