"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiClient, ApiError } from "../../lib/api-client";
import { listTables } from "../../lib/ddb-client";
import {
  clearRequestLog,
  getErrorLog,
  getRequestLog,
  type ErrorLogEntry,
  type RequestLogEntry,
} from "../../lib/request-log";
import {
  API_BASE_URL,
  API_KEY_PEPPER,
  DDB_TABLE_PREFIX,
  DEV_KEY,
  LOCALSTACK_ENDPOINT_URL,
  S3_BUCKET,
  TENANT,
} from "../../lib/config";
import { triggerReseed } from "../../lib/seed";
import { useApp, type AppConfig, type HealthStatus } from "../../contexts/AppContext";
import { useToast } from "../../contexts/ToastContext";
import { SkeletonTable, SkeletonText } from "../../components/ui/Skeleton";
import { ErrorPanel } from "../../components/ui/ErrorPanel";

const STATUS_LABEL: Record<HealthStatus, string> = {
  online: "Online",
  offline: "Offline",
  unknown: "Unknown",
};

const DEFAULT_CONFIG: AppConfig = {
  apiBaseUrl: API_BASE_URL,
  localstackEndpointUrl: LOCALSTACK_ENDPOINT_URL,
  s3Bucket: S3_BUCKET,
  ddbTablePrefix: DDB_TABLE_PREFIX,
  tenant: TENANT,
  devKey: DEV_KEY,
  apiKeyPepper: API_KEY_PEPPER,
};

function normalizeEndpoint(value: string) {
  return value.replace(/\/$/, "");
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
    timeStyle: "medium",
  }).format(date);
}

function StatusPill({ status }: { status: HealthStatus }) {
  const className =
    status === "online"
      ? "bg-emerald-100 text-emerald-900"
      : status === "offline"
        ? "bg-rose-100 text-rose-900"
        : "bg-slate-200 text-slate-700";
  return (
    <span className={`rounded-full px-3 py-1 text-xs font-semibold uppercase ${className}`}>
      {STATUS_LABEL[status]}
    </span>
  );
}

function formatUrl(url: string) {
  if (url.length <= 52) {
    return url;
  }
  return `${url.slice(0, 32)}...${url.slice(-16)}`;
}

export default function DebugPage() {
  const { config, refreshHealth, updateConfig } = useApp();
  const { showToast } = useToast();

  const apiClient = useMemo(
    () => new ApiClient(config.apiBaseUrl, config.devKey),
    [config.apiBaseUrl, config.devKey],
  );

  const [apiHealth, setApiHealth] = useState<{
    status: HealthStatus;
    checkedAt: string | null;
    payload: string | null;
    error: Error | null;
  }>({ status: "unknown", checkedAt: null, payload: null, error: null });
  const [isApiHealthLoading, setIsApiHealthLoading] = useState(false);

  const [localstackHealth, setLocalstackHealth] = useState<{
    status: HealthStatus;
    checkedAt: string | null;
    s3Accessible: boolean | null;
    ddbTables: string[];
    ddbError: string | null;
  }>({
    status: "unknown",
    checkedAt: null,
    s3Accessible: null,
    ddbTables: [],
    ddbError: null,
  });
  const [isLocalstackLoading, setIsLocalstackLoading] = useState(false);

  const [requestLog, setRequestLog] = useState<RequestLogEntry[]>([]);
  const [errorLog, setErrorLog] = useState<ErrorLogEntry[]>([]);
  const [isLogLoading, setIsLogLoading] = useState(false);
  const [showConfig, setShowConfig] = useState(false);
  const [configDraft, setConfigDraft] = useState<AppConfig>(config);

  const reloadLogs = useCallback(() => {
    setIsLogLoading(true);
    setRequestLog(getRequestLog());
    setErrorLog(getErrorLog());
    setIsLogLoading(false);
  }, []);

  const refreshApiHealth = useCallback(async () => {
    setIsApiHealthLoading(true);
    const checkedAt = new Date().toISOString();
    try {
      const payload = await apiClient.getHealth();
      setApiHealth({
        status: payload.status === "ok" ? "online" : "offline",
        checkedAt,
        payload: JSON.stringify(payload, null, 2),
        error: null,
      });
    } catch (error) {
      const message = error instanceof ApiError ? error.message : "API health check failed";
      setApiHealth({
        status: "offline",
        checkedAt,
        payload: null,
        error: error instanceof Error ? error : new Error(message),
      });
    } finally {
      setIsApiHealthLoading(false);
    }
  }, [apiClient]);

  const refreshLocalstackHealth = useCallback(async () => {
    setIsLocalstackLoading(true);
    const checkedAt = new Date().toISOString();
    let s3Accessible = false;
    let ddbTables: string[] = [];
    let ddbError: string | null = null;

    try {
      const endpoint = normalizeEndpoint(config.localstackEndpointUrl);
      const response = await fetch(`${endpoint}/${config.s3Bucket}`, { method: "HEAD" });
      s3Accessible = response.ok;
    } catch {
      s3Accessible = false;
    }

    try {
      const prefix = `${config.ddbTablePrefix}_`;
      ddbTables = await listTables(prefix, config.localstackEndpointUrl);
    } catch (error) {
      ddbError = error instanceof Error ? error.message : "Failed to list tables";
    }

    const status = s3Accessible && !ddbError ? "online" : "offline";
    setLocalstackHealth({ status, checkedAt, s3Accessible, ddbTables, ddbError });
    setIsLocalstackLoading(false);
  }, [config.ddbTablePrefix, config.localstackEndpointUrl, config.s3Bucket]);

  useEffect(() => {
    setConfigDraft(config);
  }, [config]);

  useEffect(() => {
    void refreshApiHealth();
    void refreshLocalstackHealth();
    reloadLogs();
  }, [refreshApiHealth, refreshLocalstackHealth, reloadLogs]);

  useEffect(() => {
    const interval = window.setInterval(() => {
      reloadLogs();
    }, 5000);
    return () => window.clearInterval(interval);
  }, [reloadLogs]);

  const handleRefresh = useCallback(async () => {
    refreshHealth();
    await Promise.all([refreshApiHealth(), refreshLocalstackHealth()]);
    reloadLogs();
  }, [refreshApiHealth, refreshHealth, refreshLocalstackHealth, reloadLogs]);

  const handleClearRequests = useCallback(() => {
    clearRequestLog();
    setRequestLog([]);
  }, []);

  const handleReseed = useCallback(async () => {
    const ok = await triggerReseed();
    showToast(ok ? "Dev key re-seeded." : "Dev key reseed failed.", ok ? "success" : "error");
  }, [showToast]);

  const handleSaveConfig = useCallback(() => {
    updateConfig(configDraft);
    showToast("Configuration saved.", "success", 2500);
  }, [configDraft, showToast, updateConfig]);

  const handleResetConfig = useCallback(() => {
    setConfigDraft(DEFAULT_CONFIG);
    updateConfig(DEFAULT_CONFIG);
    showToast("Configuration reset to defaults.", "info", 2500);
  }, [showToast, updateConfig]);

  const requestRows = requestLog.map((entry) => (
    <tr key={`${entry.timestamp}-${entry.url}`} className="border-t border-slate-100 text-sm">
      <td className="py-3 pr-3 text-slate-500">{formatTimestamp(entry.timestamp)}</td>
      <td className="py-3 pr-3 font-semibold text-slate-700">{entry.method}</td>
      <td className="py-3 pr-3 text-slate-600" title={entry.url}>
        {formatUrl(entry.url)}
      </td>
      <td className="py-3 pr-3 text-slate-700">{entry.status}</td>
      <td className="py-3 text-slate-700">{entry.latency_ms} ms</td>
    </tr>
  ));

  const errorRows = errorLog.map((entry) => (
    <div key={`${entry.timestamp}-${entry.message}`} className="border-t border-slate-100 py-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="text-sm font-semibold text-slate-800">{entry.message}</p>
          <p className="text-xs text-slate-500">{formatTimestamp(entry.timestamp)}</p>
        </div>
      </div>
      {entry.stack ? (
        <details className="mt-2 rounded-lg border border-slate-200 bg-slate-50 p-3">
          <summary className="cursor-pointer text-xs font-semibold uppercase tracking-wide text-slate-500">
            Stack trace
          </summary>
          <pre className="mt-2 whitespace-pre-wrap text-xs text-slate-700">
            {entry.stack}
          </pre>
        </details>
      ) : null}
    </div>
  ));

  return (
    <div className="space-y-8">
      <header className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.4em] text-slate-400">
            Diagnostics
          </p>
          <h1 className="text-3xl font-semibold text-slate-900">Debug Console</h1>
          <p className="mt-2 max-w-2xl text-sm text-slate-500">
            Inspect health checks, service connectivity, and client-side request telemetry.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={handleRefresh}
            className="rounded-full border border-slate-200 bg-white px-4 py-2 text-sm font-semibold text-slate-700 shadow-sm transition hover:bg-slate-100"
          >
            Refresh
          </button>
          <button
            type="button"
            onClick={() => setShowConfig((value) => !value)}
            className="rounded-full border border-slate-900 bg-slate-900 px-4 py-2 text-sm font-semibold text-white shadow-sm transition hover:bg-slate-800"
          >
            Settings
          </button>
        </div>
      </header>

      {showConfig ? (
        <section className="rounded-3xl border border-slate-200 bg-white p-6 shadow-sm">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="text-lg font-semibold text-slate-900">Configuration</h2>
              <p className="text-sm text-slate-500">
                Update endpoints, tenant metadata, and credentials stored in localStorage.
              </p>
            </div>
            <button
              type="button"
              onClick={() => setShowConfig(false)}
              className="text-sm font-semibold text-slate-500 transition hover:text-slate-700"
            >
              Close
            </button>
          </div>
          <div className="mt-6 grid gap-4 md:grid-cols-2">
            <label className="space-y-2 text-sm font-semibold text-slate-700">
              API base URL
              <input
                className="w-full rounded-2xl border border-slate-200 px-4 py-2 text-sm text-slate-700"
                value={configDraft.apiBaseUrl}
                onChange={(event) =>
                  setConfigDraft((current) => ({
                    ...current,
                    apiBaseUrl: event.target.value,
                  }))
                }
              />
            </label>
            <label className="space-y-2 text-sm font-semibold text-slate-700">
              LocalStack endpoint
              <input
                className="w-full rounded-2xl border border-slate-200 px-4 py-2 text-sm text-slate-700"
                value={configDraft.localstackEndpointUrl}
                onChange={(event) =>
                  setConfigDraft((current) => ({
                    ...current,
                    localstackEndpointUrl: event.target.value,
                  }))
                }
              />
            </label>
            <label className="space-y-2 text-sm font-semibold text-slate-700">
              S3 bucket
              <input
                className="w-full rounded-2xl border border-slate-200 px-4 py-2 text-sm text-slate-700"
                value={configDraft.s3Bucket}
                onChange={(event) =>
                  setConfigDraft((current) => ({
                    ...current,
                    s3Bucket: event.target.value,
                  }))
                }
              />
            </label>
            <label className="space-y-2 text-sm font-semibold text-slate-700">
              DDB table prefix
              <input
                className="w-full rounded-2xl border border-slate-200 px-4 py-2 text-sm text-slate-700"
                value={configDraft.ddbTablePrefix}
                onChange={(event) =>
                  setConfigDraft((current) => ({
                    ...current,
                    ddbTablePrefix: event.target.value,
                  }))
                }
              />
            </label>
            <label className="space-y-2 text-sm font-semibold text-slate-700">
              Tenant
              <input
                className="w-full rounded-2xl border border-slate-200 px-4 py-2 text-sm text-slate-700"
                value={configDraft.tenant}
                onChange={(event) =>
                  setConfigDraft((current) => ({
                    ...current,
                    tenant: event.target.value,
                  }))
                }
              />
            </label>
            <label className="space-y-2 text-sm font-semibold text-slate-700">
              Dev key
              <input
                className="w-full rounded-2xl border border-slate-200 px-4 py-2 text-sm text-slate-700"
                value={configDraft.devKey}
                onChange={(event) =>
                  setConfigDraft((current) => ({
                    ...current,
                    devKey: event.target.value,
                  }))
                }
              />
            </label>
            <label className="space-y-2 text-sm font-semibold text-slate-700">
              API key pepper
              <input
                className="w-full rounded-2xl border border-slate-200 px-4 py-2 text-sm text-slate-700"
                value={configDraft.apiKeyPepper}
                onChange={(event) =>
                  setConfigDraft((current) => ({
                    ...current,
                    apiKeyPepper: event.target.value,
                  }))
                }
              />
            </label>
          </div>
          <div className="mt-6 flex flex-wrap items-center gap-3">
            <button
              type="button"
              onClick={handleSaveConfig}
              className="rounded-full bg-slate-900 px-4 py-2 text-sm font-semibold text-white shadow-sm transition hover:bg-slate-800"
            >
              Save
            </button>
            <button
              type="button"
              onClick={handleResetConfig}
              className="rounded-full border border-slate-200 bg-white px-4 py-2 text-sm font-semibold text-slate-700 shadow-sm transition hover:bg-slate-100"
            >
              Reset to defaults
            </button>
            <button
              type="button"
              onClick={handleReseed}
              className="rounded-full border border-emerald-200 bg-emerald-50 px-4 py-2 text-sm font-semibold text-emerald-700 shadow-sm transition hover:bg-emerald-100"
            >
              Re-seed dev key
            </button>
          </div>
        </section>
      ) : null}

      <section className="grid gap-6 lg:grid-cols-2">
        <div className="rounded-3xl border border-slate-200 bg-white p-6 shadow-sm">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-semibold text-slate-900">API Health</h2>
            <StatusPill status={apiHealth.status} />
          </div>
          {isApiHealthLoading ? (
            <div className="mt-4">
              <SkeletonText lines={4} />
            </div>
          ) : (
            <>
              <p className="mt-2 text-sm text-slate-500">
                Last check: {formatTimestamp(apiHealth.checkedAt)}
              </p>
              {apiHealth.error ? (
                <div className="mt-4">
                  <ErrorPanel error={apiHealth.error} title="API health check failed" />
                </div>
              ) : null}
              <details className="mt-4 rounded-2xl border border-slate-200 bg-slate-50 p-4">
                <summary className="cursor-pointer text-xs font-semibold uppercase tracking-wide text-slate-500">
                  Response body
                </summary>
                <pre className="mt-3 whitespace-pre-wrap text-xs text-slate-700">
                  {apiHealth.payload ?? "No response yet."}
                </pre>
              </details>
            </>
          )}
        </div>

        <div className="rounded-3xl border border-slate-200 bg-white p-6 shadow-sm">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-semibold text-slate-900">LocalStack Health</h2>
            <StatusPill status={localstackHealth.status} />
          </div>
          {isLocalstackLoading ? (
            <div className="mt-4">
              <SkeletonText lines={4} />
            </div>
          ) : (
            <>
              <p className="mt-2 text-sm text-slate-500">
                Last check: {formatTimestamp(localstackHealth.checkedAt)}
              </p>
              <div className="mt-4 rounded-2xl border border-slate-200 bg-slate-50 p-4 text-sm text-slate-700">
                <p>
                  S3 bucket: {localstackHealth.s3Accessible ? "Accessible" : "Not accessible"}
                </p>
                <p className="mt-2 font-semibold text-slate-600">DynamoDB tables</p>
                {localstackHealth.ddbError ? (
                  <p className="mt-2 text-rose-600">{localstackHealth.ddbError}</p>
                ) : localstackHealth.ddbTables.length ? (
                  <ul className="mt-2 grid gap-1 text-xs text-slate-600">
                    {localstackHealth.ddbTables.map((table) => (
                      <li key={table} className="rounded-full bg-white px-3 py-1">
                        {table}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="mt-2 text-xs text-slate-500">No tables found.</p>
                )}
              </div>
            </>
          )}
        </div>
      </section>

      <section className="grid gap-6 lg:grid-cols-2">
        <div className="rounded-3xl border border-slate-200 bg-white p-6 shadow-sm">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="text-lg font-semibold text-slate-900">Recent Requests</h2>
              <p className="text-sm text-slate-500">Last 20 ApiClient calls.</p>
            </div>
            <button
              type="button"
              onClick={handleClearRequests}
              className="rounded-full border border-slate-200 bg-white px-3 py-1 text-xs font-semibold text-slate-600 shadow-sm transition hover:bg-slate-100"
            >
              Clear request log
            </button>
          </div>
          <div className="mt-4 max-h-80 overflow-auto">
            {isLogLoading ? (
              <SkeletonTable rows={4} columns={5} />
            ) : requestLog.length ? (
              <table className="w-full text-left">
                <thead className="text-xs font-semibold uppercase text-slate-400">
                  <tr>
                    <th className="pb-2 pr-3">Time</th>
                    <th className="pb-2 pr-3">Method</th>
                    <th className="pb-2 pr-3">URL</th>
                    <th className="pb-2 pr-3">Status</th>
                    <th className="pb-2">Latency</th>
                  </tr>
                </thead>
                <tbody>{requestRows}</tbody>
              </table>
            ) : (
              <p className="text-sm text-slate-500">No requests logged yet.</p>
            )}
          </div>
        </div>

        <div className="rounded-3xl border border-slate-200 bg-white p-6 shadow-sm">
          <div>
            <h2 className="text-lg font-semibold text-slate-900">Errors</h2>
            <p className="text-sm text-slate-500">Last 10 captured errors.</p>
          </div>
          <div className="mt-4 max-h-80 overflow-auto">
            {isLogLoading ? (
              <SkeletonText lines={3} />
            ) : errorLog.length ? (
              <div>{errorRows}</div>
            ) : (
              <p className="text-sm text-slate-500">No errors captured.</p>
            )}
          </div>
        </div>
      </section>
    </div>
  );
}
