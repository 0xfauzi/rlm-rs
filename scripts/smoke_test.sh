#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

export UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/uv-cache}"
export AWS_REGION="${AWS_REGION:-us-east-1}"
export AWS_ACCESS_KEY_ID="${AWS_ACCESS_KEY_ID:-test}"
export AWS_SECRET_ACCESS_KEY="${AWS_SECRET_ACCESS_KEY:-test}"
export AWS_SESSION_TOKEN="${AWS_SESSION_TOKEN:-test}"
export S3_BUCKET="${S3_BUCKET:-rlm-local}"
export DDB_TABLE_PREFIX="${DDB_TABLE_PREFIX:-rlm}"
export LOCALSTACK_ENDPOINT_URL="${LOCALSTACK_ENDPOINT_URL:-http://localhost:4566}"
export AWS_ENDPOINT_URL="${AWS_ENDPOINT_URL:-$LOCALSTACK_ENDPOINT_URL}"
export API_KEY_PEPPER="${API_KEY_PEPPER:-smoke-pepper}"
export API_KEY="${API_KEY:-rlm_key_smoke}"
export TENANT_ID="${TENANT_ID:-tenant_smoke}"
export API_HOST="${API_HOST:-127.0.0.1}"
export API_PORT="${API_PORT:-8080}"
export PARSER_HOST="${PARSER_HOST:-127.0.0.1}"
export PARSER_PORT="${PARSER_PORT:-8081}"
export PARSER_SERVICE_URL="${PARSER_SERVICE_URL:-http://${PARSER_HOST}:${PARSER_PORT}}"
export LLM_PROVIDER="${LLM_PROVIDER:-fake}"
export DEFAULT_ROOT_MODEL="${DEFAULT_ROOT_MODEL:-fake-root}"
export SMOKE_TIMEOUT_SECONDS="${SMOKE_TIMEOUT_SECONDS:-60}"
if [[ -z "${DEFAULT_BUDGETS_JSON:-}" ]]; then
  export DEFAULT_BUDGETS_JSON="{\"max_total_seconds\": ${SMOKE_TIMEOUT_SECONDS}}"
fi

API_URL="http://${API_HOST}:${API_PORT}"
PARSER_URL="http://${PARSER_HOST}:${PARSER_PORT}"
export API_URL PARSER_URL

log() {
  printf '%s\n' "$*"
}

compose() {
  (cd "${REPO_ROOT}" && docker compose "$@")
}

aws_local() {
  compose exec -T localstack awslocal "$@"
}

table_name() {
  local suffix="$1"
  if [[ -n "${DDB_TABLE_PREFIX}" ]]; then
    printf '%s_%s' "${DDB_TABLE_PREFIX}" "${suffix}"
  else
    printf '%s' "${suffix}"
  fi
}

wait_for_url() {
  local url="$1"
  local label="$2"
  local attempts="${3:-40}"
  local delay="${4:-0.5}"
  local i
  for ((i = 1; i <= attempts; i += 1)); do
    if curl -fsS "${url}" >/dev/null 2>&1; then
      return 0
    fi
    sleep "${delay}"
  done
  echo "ERROR: timed out waiting for ${label} at ${url}" >&2
  return 1
}

cleanup() {
  set +e
  if [[ -n "${ORCH_PID:-}" ]]; then
    kill "${ORCH_PID}" >/dev/null 2>&1
    wait "${ORCH_PID}" >/dev/null 2>&1
  fi
  if [[ -n "${INGEST_PID:-}" ]]; then
    kill "${INGEST_PID}" >/dev/null 2>&1
    wait "${INGEST_PID}" >/dev/null 2>&1
  fi
  if [[ -n "${API_PID:-}" ]]; then
    kill "${API_PID}" >/dev/null 2>&1
    wait "${API_PID}" >/dev/null 2>&1
  fi
  if [[ -n "${PARSER_PID:-}" ]]; then
    kill "${PARSER_PID}" >/dev/null 2>&1
    wait "${PARSER_PID}" >/dev/null 2>&1
  fi
  if [[ "${KEEP_DOCKER:-0}" -ne 1 ]]; then
    compose down >/dev/null 2>&1
  fi
}

trap cleanup EXIT

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker is required." >&2
  exit 1
fi
if ! command -v uv >/dev/null 2>&1; then
  echo "ERROR: uv is required." >&2
  exit 1
fi
if ! command -v curl >/dev/null 2>&1; then
  echo "ERROR: curl is required." >&2
  exit 1
fi

log "Starting LocalStack..."
compose up -d localstack >/dev/null

log "Waiting for LocalStack..."
for i in {1..40}; do
  if compose exec -T localstack awslocal s3 ls >/dev/null 2>&1; then
    break
  fi
  sleep 0.5
done

if ! compose exec -T localstack awslocal s3 ls >/dev/null 2>&1; then
  echo "ERROR: LocalStack did not become ready." >&2
  exit 1
fi

log "Ensuring S3 bucket and DynamoDB tables..."
if ! aws_local s3api head-bucket --bucket "${S3_BUCKET}" >/dev/null 2>&1; then
  aws_local s3 mb "s3://${S3_BUCKET}" >/dev/null
fi

ensure_table() {
  local name="$1"
  if aws_local dynamodb describe-table --table-name "${name}" >/dev/null 2>&1; then
    return 0
  fi
  aws_local dynamodb create-table \
    --table-name "${name}" \
    --attribute-definitions AttributeName=PK,AttributeType=S AttributeName=SK,AttributeType=S \
    --key-schema AttributeName=PK,KeyType=HASH AttributeName=SK,KeyType=RANGE \
    --billing-mode PAY_PER_REQUEST >/dev/null
  aws_local dynamodb wait table-exists --table-name "${name}"
}

ensure_table "$(table_name "sessions")"
ensure_table "$(table_name "documents")"
ensure_table "$(table_name "executions")"
ensure_table "$(table_name "execution_state")"
ensure_table "$(table_name "api_keys")"
ensure_table "$(table_name "audit_log")"

API_KEY_HASH="$(uv run python - <<'PY'
import hashlib
import hmac
import os

api_key = os.environ["API_KEY"]
pepper = os.environ["API_KEY_PEPPER"]
digest = hmac.new(pepper.encode("utf-8"), api_key.encode("utf-8"), hashlib.sha256)
print(digest.hexdigest())
PY
)"

API_KEYS_TABLE="$(table_name "api_keys")"
API_KEY_ITEM=$(cat <<JSON
{"PK":{"S":"KEY#${API_KEY_HASH}"},"SK":{"S":"KEY#${API_KEY_HASH}"},"tenant_id":{"S":"${TENANT_ID}"}}
JSON
)
aws_local dynamodb put-item --table-name "${API_KEYS_TABLE}" --item "${API_KEY_ITEM}" >/dev/null

log "Starting parser service..."
(cd "${REPO_ROOT}" && uv run uvicorn rlm_rs.parser.service:app \
  --host "${PARSER_HOST}" --port "${PARSER_PORT}" --log-level warning) &
PARSER_PID=$!

wait_for_url "${PARSER_URL}/docs" "parser service"

log "Starting API service..."
(cd "${REPO_ROOT}" && uv run uvicorn rlm_rs.api.app:app \
  --host "${API_HOST}" --port "${API_PORT}" --log-level warning) &
API_PID=$!

wait_for_url "${API_URL}/health/ready" "API readiness"

log "Starting ingestion worker..."
(cd "${REPO_ROOT}" && uv run python - <<'PY'
import time

from rlm_rs.ingestion.worker import build_worker

worker = build_worker()
try:
    while True:
        processed = worker.run_once(limit=10)
        if processed == 0:
            time.sleep(0.5)
except KeyboardInterrupt:
    pass
finally:
    worker.close()
PY
) &
INGEST_PID=$!

log "Starting orchestrator worker..."
(cd "${REPO_ROOT}" && uv run python - <<'PY'
import time

from rlm_rs.orchestrator.providers import FakeLLMProvider
from rlm_rs.orchestrator.worker import build_worker

provider = FakeLLMProvider(
    default_root_output="```repl\nsnippet = context[0][0:5]\ntool.FINAL(snippet)\n```",
)
worker = build_worker(provider=provider)
try:
    while True:
        processed = worker.run_once(limit=1)
        if processed == 0:
            time.sleep(0.5)
except KeyboardInterrupt:
    pass
PY
) &
ORCH_PID=$!

RUN_ID="$(uv run python - <<'PY'
import uuid

print(uuid.uuid4().hex)
PY
)"
RAW_KEY="raw/${TENANT_ID}/${RUN_ID}/fixture.txt"
RAW_URI="s3://${S3_BUCKET}/${RAW_KEY}"
export RAW_URI

log "Uploading fixture to S3..."
printf 'Hello world from RLM-RS' | aws_local s3 cp - "s3://${S3_BUCKET}/${RAW_KEY}" \
  --content-type text/plain >/dev/null

log "Running API flow..."
uv run python - <<'PY'
import json
import os
import sys
import time

import httpx

from rlm_rs.storage.s3 import build_s3_client

api_url = os.environ["API_URL"]
api_key = os.environ["API_KEY"]
raw_uri = os.environ["RAW_URI"]
root_model = os.environ.get("DEFAULT_ROOT_MODEL", "fake-root")
timeout_seconds = int(os.environ.get("SMOKE_TIMEOUT_SECONDS", "60"))
tenant_id = os.environ.get("TENANT_ID", "")
cache_bucket = os.environ.get("S3_BUCKET")
cache_prefix = os.environ.get("LLM_CACHE_PREFIX", "cache")
cache_enabled = os.environ.get("LLM_PROVIDER", "fake") == "openai"
cache_endpoint = os.environ.get("LOCALSTACK_ENDPOINT_URL") or os.environ.get(
    "AWS_ENDPOINT_URL"
)
cache_region = os.environ.get("AWS_REGION")
output_path = os.environ.get("SMOKE_OUTPUT_JSON")

headers = {"Authorization": f"Bearer {api_key}"}


def poll_until(func, *, deadline, sleep_seconds=0.5):
    while True:
        result = func()
        if result is not None:
            return result
        if time.time() > deadline:
            raise RuntimeError("Timed out waiting for condition")
        time.sleep(sleep_seconds)


def count_cache_objects() -> int:
    if not cache_enabled or not cache_bucket:
        return 0
    client = build_s3_client(region=cache_region, endpoint_url=cache_endpoint)
    prefix = f"{cache_prefix}/{tenant_id}/llm/"
    count = 0
    token = None
    while True:
        params = {"Bucket": cache_bucket, "Prefix": prefix}
        if token:
            params["ContinuationToken"] = token
        response = client.list_objects_v2(**params)
        count += response.get("KeyCount", 0)
        if not response.get("IsTruncated"):
            break
        token = response.get("NextContinuationToken")
    return count


with httpx.Client(timeout=10.0) as client:
    create_session = {
        "ttl_minutes": 10,
        "docs": [
            {
                "source_name": "fixture.txt",
                "mime_type": "text/plain",
                "raw_s3_uri": raw_uri,
            }
        ],
    }
    response = client.post(f"{api_url}/v1/sessions", json=create_session, headers=headers)
    response.raise_for_status()
    session_id = response.json()["session_id"]

    deadline = time.time() + timeout_seconds
    def session_ready():
        resp = client.get(f"{api_url}/v1/sessions/{session_id}", headers=headers)
        resp.raise_for_status()
        data = resp.json()
        status = data.get("status")
        if status == "READY":
            return data
        if status == "FAILED":
            raise RuntimeError("Session failed to parse")
        return None

    session_data = poll_until(session_ready, deadline=deadline)
    if session_data.get("status") != "READY":
        raise RuntimeError("Session did not become READY")

    cache_before = count_cache_objects()
    execution_start = time.monotonic()
    create_execution = {
        "question": "Return the first word of the document.",
        "models": {"root_model": root_model},
    }
    response = client.post(
        f"{api_url}/v1/sessions/{session_id}/executions",
        json=create_execution,
        headers=headers,
    )
    response.raise_for_status()
    execution_id = response.json()["execution_id"]

    deadline = time.time() + timeout_seconds
    def execution_done():
        resp = client.get(f"{api_url}/v1/executions/{execution_id}", headers=headers)
        resp.raise_for_status()
        data = resp.json()
        status = data.get("status")
        if status != "RUNNING":
            return data
        return None

    execution_data = poll_until(execution_done, deadline=deadline)
    duration_seconds = time.monotonic() - execution_start
    status = execution_data.get("status")
    if status != "COMPLETED":
        raise RuntimeError(f"Execution ended with status={status}")

    answer = execution_data.get("answer", "")
    print(f"Final answer: {answer}")

    citations = execution_data.get("citations") or []
    if not citations:
        raise RuntimeError("No citations returned from execution")
    ref = citations[0]

    response = client.post(
        f"{api_url}/v1/citations/verify",
        json={"ref": ref},
        headers=headers,
    )
    response.raise_for_status()
    verify_data = response.json()
    if not verify_data.get("valid"):
        raise RuntimeError("Citation verification returned valid=false")
    print("Citation verify:", json.dumps(verify_data, sort_keys=True))

    budgets_consumed = execution_data.get("budgets_consumed")
    cache_after = count_cache_objects()
    cache_misses = max(cache_after - cache_before, 0)
    llm_subcalls = 0
    if isinstance(budgets_consumed, dict):
        llm_subcalls = int(budgets_consumed.get("llm_subcalls") or 0)
    cache_hits = max(llm_subcalls - cache_misses, 0) if cache_enabled else 0
    cache_hit_rate = (
        cache_hits / llm_subcalls if cache_enabled and llm_subcalls else 0.0
    )
    summary = {
        "session_id": session_id,
        "execution_id": execution_id,
        "execution_duration_seconds": duration_seconds,
        "budgets_consumed": budgets_consumed,
        "cache": {
            "enabled": cache_enabled,
            "hits": cache_hits,
            "misses": cache_misses if cache_enabled else 0,
            "hit_rate": cache_hit_rate,
        },
    }
    if output_path:
        with open(output_path, "w", encoding="utf-8") as handle:
            json.dump(summary, handle, sort_keys=True)
            handle.write("\n")

sys.exit(0)
PY

log "Smoke test completed."
