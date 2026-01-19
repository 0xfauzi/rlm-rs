"use client";

import React from "react";

interface ErrorBoundaryProps {
  children: React.ReactNode;
}

interface ErrorBoundaryState {
  error: Error | null;
}

export class ErrorBoundary extends React.Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  handleReload = () => {
    window.location.reload();
  };

  render() {
    const { error } = this.state;
    if (!error) {
      return this.props.children;
    }

    return (
      <div className="rounded-3xl border border-rose-200 bg-rose-50 p-6 text-slate-900">
        <h2 className="text-lg font-semibold text-rose-900">Something went wrong</h2>
        <p className="mt-2 text-sm text-rose-700">
          The app hit an unexpected error while rendering this view.
        </p>
        <button
          type="button"
          onClick={this.handleReload}
          className="mt-4 rounded-full border border-rose-200 px-4 py-2 text-xs font-semibold uppercase tracking-[0.2em] text-rose-700"
        >
          Reload
        </button>
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
}
