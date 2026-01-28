export interface RequestLogEntry {
  timestamp: string;
  method: string;
  url: string;
  status: number;
  latency_ms: number;
}

export interface ErrorLogEntry {
  timestamp: string;
  message: string;
  stack?: string | null;
}

const REQUEST_LOG_KEY = "rlm_request_log";
const ERROR_LOG_KEY = "rlm_error_log";
const MAX_REQUESTS = 20;
const MAX_ERRORS = 10;

function readLog<T>(key: string): T[] {
  if (typeof window === "undefined") {
    return [];
  }
  const raw = window.localStorage.getItem(key);
  if (!raw) {
    return [];
  }
  try {
    const parsed = JSON.parse(raw) as T[];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function writeLog<T>(key: string, entries: T[]): void {
  if (typeof window === "undefined") {
    return;
  }
  window.localStorage.setItem(key, JSON.stringify(entries));
}

export function getRequestLog(): RequestLogEntry[] {
  return readLog<RequestLogEntry>(REQUEST_LOG_KEY);
}

export function addRequestLog(entry: RequestLogEntry): RequestLogEntry[] {
  const entries = [entry, ...getRequestLog()].slice(0, MAX_REQUESTS);
  writeLog(REQUEST_LOG_KEY, entries);
  return entries;
}

export function clearRequestLog(): void {
  writeLog<RequestLogEntry>(REQUEST_LOG_KEY, []);
}

export function getErrorLog(): ErrorLogEntry[] {
  return readLog<ErrorLogEntry>(ERROR_LOG_KEY);
}

export function addErrorLog(entry: ErrorLogEntry): ErrorLogEntry[] {
  const entries = [entry, ...getErrorLog()].slice(0, MAX_ERRORS);
  writeLog(ERROR_LOG_KEY, entries);
  return entries;
}
