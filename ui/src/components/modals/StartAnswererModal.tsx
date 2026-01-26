"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { ApiClient, ApiError } from "../../lib/api-client";
import type { Budgets, ExecutionOptions, ModelsConfig } from "../../lib/types";
import { recordExecution } from "../../lib/executions-store";
import { useApp } from "../../contexts/AppContext";
import { useToast } from "../../contexts/ToastContext";

interface StartAnswererModalProps {
  isOpen: boolean;
  onClose: () => void;
  sessionId: string;
  budgetsDefault?: Budgets | null;
}

const BUDGETS_EXAMPLE = `{
  "max_turns": 3,
  "max_total_seconds": 120,
  "max_llm_subcalls": 5
}`;

function parseBudgets(raw: string): { value: Budgets | null; error: string | null } {
  if (!raw.trim()) {
    return { value: null, error: null };
  }
  try {
    const parsed = JSON.parse(raw) as unknown;
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      return { value: null, error: "Budgets must be a JSON object." };
    }
    return { value: parsed as Budgets, error: null };
  } catch {
    return { value: null, error: "Budgets must be valid JSON." };
  }
}

function budgetsToText(budgets: Budgets | null | undefined) {
  if (!budgets) {
    return BUDGETS_EXAMPLE;
  }
  const cleaned: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(budgets)) {
    if (value !== null && value !== undefined) {
      cleaned[key] = value;
    }
  }
  return JSON.stringify(cleaned, null, 2);
}

export function StartAnswererModal({
  isOpen,
  onClose,
  sessionId,
  budgetsDefault,
}: StartAnswererModalProps) {
  const router = useRouter();
  const { config } = useApp();
  const { showToast } = useToast();
  const apiClient = useMemo(
    () => new ApiClient(config.apiBaseUrl, config.devKey),
    [config.apiBaseUrl, config.devKey],
  );

  const defaultBudgetsText = useMemo(() => budgetsToText(budgetsDefault), [budgetsDefault]);

  const [question, setQuestion] = useState("");
  const [rootModel, setRootModel] = useState("");
  const [subModel, setSubModel] = useState("");
  const [budgetsText, setBudgetsText] = useState("");
  const [returnTrace, setReturnTrace] = useState(false);
  const [redactTrace, setRedactTrace] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!isOpen) {
      return;
    }
    setBudgetsText(defaultBudgetsText);
  }, [defaultBudgetsText, isOpen]);

  const budgetsParse = parseBudgets(budgetsText);

  const handleClose = () => {
    if (isSubmitting) {
      return;
    }
    onClose();
    setError(null);
  };

  const handleSubmit = async () => {
    if (!sessionId || !question.trim() || budgetsParse.error) {
      return;
    }
    setIsSubmitting(true);
    setError(null);

    const models: ModelsConfig | null =
      rootModel.trim() || subModel.trim()
        ? { root_model: rootModel.trim() || null, sub_model: subModel.trim() || null }
        : null;
    const options: ExecutionOptions | null =
      returnTrace || redactTrace
        ? { return_trace: returnTrace, redact_trace: redactTrace }
        : null;

    try {
      const response = await apiClient.createExecution(sessionId, {
        question: question.trim(),
        models,
        budgets: budgetsParse.value,
        options,
      });
      recordExecution({
        id: response.execution_id,
        session_id: sessionId,
        mode: "ANSWERER",
        status: response.status,
        created_at: new Date().toISOString(),
      });
      showToast("Answerer execution started", "success", 2000);
      onClose();
      router.push(`/executions/${response.execution_id}`);
    } catch (err) {
      const message = err instanceof ApiError ? err.message : "Failed to start execution.";
      setError(message);
      showToast(message, "error", 3000);
    } finally {
      setIsSubmitting(false);
    }
  };

  if (!isOpen) {
    return null;
  }

  const isDisabled = isSubmitting || !question.trim() || !!budgetsParse.error;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4">
      <div className="w-full max-w-2xl rounded-3xl border border-slate-200 bg-white p-6 shadow-xl">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.3em] text-slate-400">
              Start Answerer
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

        <div className="mt-5 grid gap-4">
          <label className="grid gap-2 text-sm text-slate-600">
            Question
            <textarea
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              rows={4}
              className="rounded-2xl border border-slate-200 px-3 py-2 text-sm text-slate-700 shadow-sm focus:border-slate-400 focus:outline-none"
              placeholder="Ask a question for the Answerer to solve"
              required
            />
          </label>

          <div className="grid gap-4 md:grid-cols-2">
            <label className="grid gap-2 text-sm text-slate-600">
              Root model
              <input
                value={rootModel}
                onChange={(event) => setRootModel(event.target.value)}
                className="rounded-full border border-slate-200 px-3 py-2 text-sm text-slate-700 shadow-sm focus:border-slate-400 focus:outline-none"
                placeholder="Use API default"
              />
            </label>
            <label className="grid gap-2 text-sm text-slate-600">
              Sub model
              <input
                value={subModel}
                onChange={(event) => setSubModel(event.target.value)}
                className="rounded-full border border-slate-200 px-3 py-2 text-sm text-slate-700 shadow-sm focus:border-slate-400 focus:outline-none"
                placeholder="Use API default"
              />
            </label>
          </div>

          <label className="grid gap-2 text-sm text-slate-600">
            Budgets JSON
            <textarea
              value={budgetsText}
              onChange={(event) => setBudgetsText(event.target.value)}
              rows={5}
              className="rounded-2xl border border-slate-200 px-3 py-2 text-sm text-slate-700 shadow-sm focus:border-slate-400 focus:outline-none font-mono"
              placeholder={BUDGETS_EXAMPLE}
            />
            {budgetsParse.error ? (
              <span className="text-xs text-rose-600">{budgetsParse.error}</span>
            ) : null}
          </label>

          <div className="grid gap-3 text-sm text-slate-600 md:grid-cols-2">
            <label className="flex items-center gap-2 rounded-2xl border border-slate-200 px-3 py-2">
              <input
                type="checkbox"
                checked={returnTrace}
                onChange={(event) => setReturnTrace(event.target.checked)}
                className="h-4 w-4 rounded border-slate-300 text-slate-900"
              />
              Return trace
            </label>
            <label className="flex items-center gap-2 rounded-2xl border border-slate-200 px-3 py-2">
              <input
                type="checkbox"
                checked={redactTrace}
                onChange={(event) => setRedactTrace(event.target.checked)}
                className="h-4 w-4 rounded border-slate-300 text-slate-900"
              />
              Redact trace
            </label>
          </div>

          {error ? (
            <div className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
              {error}
            </div>
          ) : null}

          <div className="flex flex-wrap items-center justify-end gap-3">
            <button
              type="button"
              onClick={handleClose}
              className="rounded-full border border-slate-200 px-4 py-2 text-xs font-semibold uppercase tracking-[0.2em] text-slate-500"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={() => void handleSubmit()}
              disabled={isDisabled}
              className="rounded-full bg-slate-900 px-4 py-2 text-xs font-semibold uppercase tracking-[0.2em] text-white shadow-sm disabled:cursor-not-allowed disabled:bg-slate-300"
            >
              {isSubmitting ? "Starting..." : "Start Execution"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
