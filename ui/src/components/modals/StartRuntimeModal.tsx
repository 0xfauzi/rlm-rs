"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { ApiClient, ApiError } from "../../lib/api-client";
import { recordExecution } from "../../lib/executions-store";
import { useApp } from "../../contexts/AppContext";
import { useToast } from "../../contexts/ToastContext";

interface StartRuntimeModalProps {
  isOpen: boolean;
  onClose: () => void;
  sessionId: string;
}

export function StartRuntimeModal({ isOpen, onClose, sessionId }: StartRuntimeModalProps) {
  const router = useRouter();
  const { config } = useApp();
  const { showToast } = useToast();
  const apiClient = useMemo(
    () => new ApiClient(config.apiBaseUrl, config.devKey),
    [config.apiBaseUrl, config.devKey],
  );
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleClose = () => {
    if (isSubmitting) {
      return;
    }
    onClose();
    setError(null);
  };

  const handleStart = async () => {
    if (!sessionId) {
      setError("Missing session ID.");
      return;
    }
    setIsSubmitting(true);
    setError(null);
    try {
      const response = await apiClient.createRuntimeExecution(sessionId);
      recordExecution({
        id: response.execution_id,
        session_id: sessionId,
        mode: "RUNTIME",
        status: response.status,
        created_at: new Date().toISOString(),
      });
      showToast("Runtime execution started", "success", 2000);
      onClose();
      router.push(`/executions/${response.execution_id}/runtime?session_id=${sessionId}`);
    } catch (err) {
      const message = err instanceof ApiError ? err.message : "Failed to start runtime.";
      setError(message);
      showToast(message, "error", 3000);
    } finally {
      setIsSubmitting(false);
    }
  };

  if (!isOpen) {
    return null;
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4">
      <div className="w-full max-w-lg rounded-3xl border border-slate-200 bg-white p-6 shadow-xl">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.3em] text-slate-400">
              Start Runtime
            </p>
            <h2 className="text-lg font-semibold text-slate-900">Execution setup</h2>
          </div>
          <button
            type="button"
            onClick={handleClose}
            className="rounded-full border border-slate-200 px-3 py-1 text-xs font-semibold uppercase tracking-[0.2em] text-slate-500"
          >
            Close
          </button>
        </div>
        <p className="mt-3 text-sm text-slate-600">
          Start a fresh runtime execution to iterate through multi-step code with state.
        </p>
        {error ? (
          <div className="mt-4 rounded-2xl border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
            {error}
          </div>
        ) : null}
        <div className="mt-6 flex items-center justify-end gap-3">
          <button
            type="button"
            onClick={handleClose}
            className="rounded-full border border-slate-200 px-4 py-2 text-xs font-semibold uppercase tracking-[0.2em] text-slate-500"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleStart}
            disabled={isSubmitting}
            className="rounded-full bg-slate-900 px-4 py-2 text-xs font-semibold uppercase tracking-[0.2em] text-white transition disabled:cursor-not-allowed disabled:bg-slate-400"
          >
            {isSubmitting ? "Starting..." : "Start Runtime"}
          </button>
        </div>
      </div>
    </div>
  );
}
