import type { ExecutionMode, ExecutionStatus } from "./types";

export interface StoredExecution {
  id: string;
  session_id: string;
  mode: ExecutionMode;
  status?: ExecutionStatus;
  started_at?: string | null;
  completed_at?: string | null;
  created_at: string;
}

const STORAGE_KEY = "rlm_execution_index";

function mergeExecution(base: StoredExecution, patch: Partial<StoredExecution>): StoredExecution {
  return {
    ...base,
    ...patch,
    status: patch.status ?? base.status,
    started_at: patch.started_at ?? base.started_at,
    completed_at: patch.completed_at ?? base.completed_at,
    session_id: patch.session_id ?? base.session_id,
    mode: patch.mode ?? base.mode,
    created_at: base.created_at || patch.created_at || new Date().toISOString(),
  };
}

export function loadStoredExecutions(): StoredExecution[] {
  if (typeof window === "undefined") {
    return [];
  }
  const raw = window.localStorage.getItem(STORAGE_KEY);
  if (!raw) {
    return [];
  }
  try {
    const parsed = JSON.parse(raw) as StoredExecution[];
    if (!Array.isArray(parsed)) {
      return [];
    }
    return parsed.filter((entry) => entry && typeof entry.id === "string");
  } catch {
    return [];
  }
}

export function saveStoredExecutions(executions: StoredExecution[]) {
  if (typeof window === "undefined") {
    return;
  }
  const deduped = new Map<string, StoredExecution>();
  for (const entry of executions) {
    if (!entry || typeof entry.id !== "string") {
      continue;
    }
    const existing = deduped.get(entry.id);
    deduped.set(entry.id, existing ? mergeExecution(existing, entry) : entry);
  }
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify([...deduped.values()]));
}

export function recordExecution(entry: StoredExecution) {
  const stored = loadStoredExecutions();
  const next = stored.filter((item) => item.id !== entry.id);
  next.unshift(entry);
  saveStoredExecutions(next);
}

export function updateExecution(
  id: string,
  patch: Partial<StoredExecution>,
): StoredExecution | null {
  const stored = loadStoredExecutions();
  let updated: StoredExecution | null = null;
  const next = stored.map((entry) => {
    if (entry.id !== id) {
      return entry;
    }
    updated = mergeExecution(entry, patch);
    return updated;
  });
  saveStoredExecutions(next);
  return updated;
}
