"use client";

import { useEffect } from "react";
import Link from "next/link";

interface ErrorPageProps {
  error: Error & { digest?: string };
  reset: () => void;
}

export default function ErrorPage({ error, reset }: ErrorPageProps) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <div className="rounded-3xl border border-rose-200 bg-rose-50 p-6 text-slate-900">
      <h1 className="text-2xl font-semibold text-rose-900">Something went wrong</h1>
      <p className="mt-2 text-sm text-rose-700">
        The page encountered an unexpected error. Try reloading or go back home.
      </p>
      <div className="mt-4 flex flex-wrap gap-3">
        <button
          type="button"
          onClick={reset}
          className="rounded-full border border-rose-200 px-4 py-2 text-xs font-semibold uppercase tracking-[0.2em] text-rose-700"
        >
          Reload
        </button>
        <Link
          href="/"
          className="rounded-full border border-rose-200 px-4 py-2 text-xs font-semibold uppercase tracking-[0.2em] text-rose-700"
        >
          Go home
        </Link>
      </div>
      <details className="mt-4 rounded-2xl border border-rose-200 bg-white/70 px-4 py-3">
        <summary className="cursor-pointer text-xs font-semibold uppercase tracking-[0.2em] text-rose-600">
          Show details
        </summary>
        <pre className="mt-3 whitespace-pre-wrap text-xs text-rose-700">
          {error.stack ?? error.message}
        </pre>
      </details>
    </div>
  );
}
