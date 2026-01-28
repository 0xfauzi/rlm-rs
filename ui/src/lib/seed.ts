import { API_KEY_PEPPER, DEV_KEY, LOCALSTACK_ENDPOINT_URL, TENANT } from "./config";
import { computeApiKeyHash } from "./crypto";
import { putApiKey } from "./ddb-client";

export const SEED_STORAGE_KEY = "rlm_seeded";

export async function seedDevKey(): Promise<boolean> {
  try {
    const hash = await computeApiKeyHash(DEV_KEY, API_KEY_PEPPER);
    await putApiKey(hash, TENANT, LOCALSTACK_ENDPOINT_URL);
    return true;
  } catch {
    return false;
  }
}

export async function triggerReseed(): Promise<boolean> {
  if (typeof window !== "undefined") {
    window.localStorage.removeItem(SEED_STORAGE_KEY);
  }
  return seedDevKey();
}
