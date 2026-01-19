"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import {
  API_BASE_URL,
  API_KEY_PEPPER,
  DDB_TABLE_PREFIX,
  DEV_KEY,
  LOCALSTACK_ENDPOINT_URL,
  S3_BUCKET,
  TENANT,
} from "../lib/config";
import { addErrorLog } from "../lib/request-log";

export type HealthStatus = "online" | "offline" | "unknown";

export interface AppConfig {
  apiBaseUrl: string;
  localstackEndpointUrl: string;
  s3Bucket: string;
  ddbTablePrefix: string;
  tenant: string;
  devKey: string;
  apiKeyPepper: string;
}

interface AppContextValue {
  apiHealth: HealthStatus;
  localstackHealth: HealthStatus;
  runningExecutionsCount: number;
  config: AppConfig;
  refreshHealth: () => void;
  updateConfig: (patch: Partial<AppConfig>) => void;
  setRunningExecutionsCount: (count: number) => void;
}

const STORAGE_KEY = "rlm_app_config";
const LEGACY_API_BASE_URL = "http://localhost:8080";
const LEGACY_LOCALSTACK_ENDPOINT_URL = "http://localhost:4566";
const LEGACY_LOCALSTACK_PROXY_URL = "http://localhost:3000/localstack";

const DEFAULT_CONFIG: AppConfig = {
  apiBaseUrl: API_BASE_URL,
  localstackEndpointUrl: LOCALSTACK_ENDPOINT_URL,
  s3Bucket: S3_BUCKET,
  ddbTablePrefix: DDB_TABLE_PREFIX,
  tenant: TENANT,
  devKey: DEV_KEY,
  apiKeyPepper: API_KEY_PEPPER,
};

const AppContext = createContext<AppContextValue | null>(null);

function migrateConfig(config: AppConfig): AppConfig {
  if (typeof window === "undefined") {
    return config;
  }
  const migrated = { ...config };
  if (migrated.apiBaseUrl === LEGACY_API_BASE_URL && API_BASE_URL !== LEGACY_API_BASE_URL) {
    migrated.apiBaseUrl = API_BASE_URL;
  }
  if (
    (migrated.localstackEndpointUrl === LEGACY_LOCALSTACK_ENDPOINT_URL ||
      migrated.localstackEndpointUrl === LEGACY_LOCALSTACK_PROXY_URL) &&
    LOCALSTACK_ENDPOINT_URL !== migrated.localstackEndpointUrl
  ) {
    migrated.localstackEndpointUrl = LOCALSTACK_ENDPOINT_URL;
  }
  return migrated;
}

function loadConfig(): AppConfig {
  if (typeof window === "undefined") {
    return DEFAULT_CONFIG;
  }
  const raw = window.localStorage.getItem(STORAGE_KEY);
  if (!raw) {
    return DEFAULT_CONFIG;
  }
  try {
    const parsed = JSON.parse(raw) as Partial<AppConfig>;
    return migrateConfig({ ...DEFAULT_CONFIG, ...parsed });
  } catch {
    return DEFAULT_CONFIG;
  }
}

function normalizeBaseUrl(value: string) {
  return value.replace(/\/$/, "");
}

async function fetchApiHealth(apiBaseUrl: string): Promise<HealthStatus> {
  try {
    const response = await fetch(`${normalizeBaseUrl(apiBaseUrl)}/health/ready`, {
      headers: { Accept: "application/json" },
    });
    if (!response.ok) {
      return "offline";
    }
    const payload = (await response.json()) as { status?: string } | null;
    if (payload?.status === "ok") {
      return "online";
    }
    return "offline";
  } catch {
    return "offline";
  }
}

async function fetchLocalstackHealth(endpointUrl: string, bucket: string): Promise<HealthStatus> {
  try {
    const response = await fetch(`${normalizeBaseUrl(endpointUrl)}/${bucket}`, {
      method: "HEAD",
    });
    return response.ok ? "online" : "offline";
  } catch {
    return "offline";
  }
}

async function fetchRunningExecutionsCount(
  apiBaseUrl: string,
  apiKey: string,
): Promise<number | null> {
  try {
    const response = await fetch(`${normalizeBaseUrl(apiBaseUrl)}/v1/executions`, {
      headers: {
        Authorization: `Bearer ${apiKey}`,
        Accept: "application/json",
      },
    });
    if (!response.ok) {
      return null;
    }
    const payload = (await response.json()) as { executions?: Array<{ status?: string }> };
    if (!Array.isArray(payload.executions)) {
      return null;
    }
    return payload.executions.filter((execution) => execution.status === "RUNNING").length;
  } catch {
    return null;
  }
}

export function AppProvider({ children }: { children: React.ReactNode }) {
  const [config, setConfig] = useState<AppConfig>(() => loadConfig());
  const [apiHealth, setApiHealth] = useState<HealthStatus>("unknown");
  const [localstackHealth, setLocalstackHealth] = useState<HealthStatus>("unknown");
  const [runningExecutionsCount, setRunningExecutionsCount] = useState(0);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(config));
  }, [config]);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }

    const handleError = (event: ErrorEvent) => {
      const error = event.error instanceof Error ? event.error : new Error(event.message);
      addErrorLog({
        timestamp: new Date().toISOString(),
        message: error.message,
        stack: error.stack ?? null,
      });
    };

    const handleRejection = (event: PromiseRejectionEvent) => {
      const error =
        event.reason instanceof Error
          ? event.reason
          : new Error(typeof event.reason === "string" ? event.reason : "Unhandled rejection");
      addErrorLog({
        timestamp: new Date().toISOString(),
        message: error.message,
        stack: error.stack ?? null,
      });
    };

    window.addEventListener("error", handleError);
    window.addEventListener("unhandledrejection", handleRejection);
    return () => {
      window.removeEventListener("error", handleError);
      window.removeEventListener("unhandledrejection", handleRejection);
    };
  }, []);

  const refreshHealth = useCallback(async () => {
    const [apiStatus, localstackStatus] = await Promise.all([
      fetchApiHealth(config.apiBaseUrl),
      fetchLocalstackHealth(config.localstackEndpointUrl, config.s3Bucket),
    ]);
    setApiHealth(apiStatus);
    setLocalstackHealth(localstackStatus);
  }, [config.apiBaseUrl, config.localstackEndpointUrl, config.s3Bucket]);

  const refreshRunningExecutions = useCallback(async () => {
    const count = await fetchRunningExecutionsCount(config.apiBaseUrl, config.devKey);
    if (count !== null) {
      setRunningExecutionsCount(count);
    }
  }, [config.apiBaseUrl, config.devKey]);

  useEffect(() => {
    const run = () => {
      void refreshHealth();
      void refreshRunningExecutions();
    };
    const kickoff = window.setTimeout(run, 0);
    const interval = window.setInterval(run, 10000);
    return () => {
      window.clearTimeout(kickoff);
      window.clearInterval(interval);
    };
  }, [refreshHealth, refreshRunningExecutions]);

  const updateConfig = useCallback((patch: Partial<AppConfig>) => {
    setConfig((current) => ({ ...current, ...patch }));
  }, []);

  const value = useMemo(
    () => ({
      apiHealth,
      localstackHealth,
      runningExecutionsCount,
      config,
      refreshHealth,
      updateConfig,
      setRunningExecutionsCount,
    }),
    [apiHealth, localstackHealth, runningExecutionsCount, config, refreshHealth, updateConfig],
  );

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}

export function useApp() {
  const context = useContext(AppContext);
  if (!context) {
    throw new Error("useApp must be used within AppProvider");
  }
  return context;
}
