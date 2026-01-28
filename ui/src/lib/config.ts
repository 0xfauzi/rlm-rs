const rawApiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL;
const rawLocalstackEndpointUrl = process.env.NEXT_PUBLIC_LOCALSTACK_ENDPOINT_URL;

function resolveApiBaseUrl() {
  if (rawApiBaseUrl && rawApiBaseUrl.trim()) {
    return rawApiBaseUrl;
  }
  if (typeof window !== "undefined") {
    return window.location.origin;
  }
  return "http://localhost:8080";
}

function resolveLocalstackEndpointUrl() {
  if (rawLocalstackEndpointUrl && rawLocalstackEndpointUrl.trim()) {
    return rawLocalstackEndpointUrl;
  }
  return "http://localhost:4566";
}

export const API_BASE_URL = resolveApiBaseUrl();
export const LOCALSTACK_ENDPOINT_URL = resolveLocalstackEndpointUrl();
export const S3_BUCKET = "rlm-local";
export const DDB_TABLE_PREFIX = "rlm";
export const TENANT = "tenant_local";
export const DEV_KEY = "rlm_key_local";
export const API_KEY_PEPPER = "smoke-pepper";
