#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

API_URL="${API_URL:-http://localhost:8080}"
UI_URL="${UI_URL:-http://localhost:3000}"
TIMEOUT_SECONDS="${UI_SMOKE_TIMEOUT_SECONDS:-60}"

log() {
  printf '%s\n' "$*"
}

compose() {
  (cd "${REPO_ROOT}" && docker compose "$@")
}

wait_for_api_ready() {
  local deadline
  deadline=$((SECONDS + TIMEOUT_SECONDS))
  while (( SECONDS < deadline )); do
    if curl -fsS "${API_URL}/health/ready" | tr -d '[:space:]' | grep -q '"status":"ok"'; then
      return 0
    fi
    sleep 1
  done
  echo "ERROR: timed out waiting for API readiness at ${API_URL}/health/ready" >&2
  return 1
}

wait_for_ui_ready() {
  local deadline
  deadline=$((SECONDS + TIMEOUT_SECONDS))
  while (( SECONDS < deadline )); do
    if curl -fsS "${UI_URL}" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  echo "ERROR: timed out waiting for UI at ${UI_URL}" >&2
  return 1
}

cleanup() {
  set +e
  compose down >/dev/null 2>&1
}

trap cleanup INT TERM

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker is required." >&2
  exit 1
fi
if ! command -v curl >/dev/null 2>&1; then
  echo "ERROR: curl is required." >&2
  exit 1
fi

log "Starting services..."
compose up -d \
  localstack \
  localstack-init \
  rlm-parser \
  rlm-api \
  rlm-ingestion-worker \
  rlm-orchestrator-worker \
  rlm-ui >/dev/null

log "Waiting for API readiness..."
wait_for_api_ready

log "Waiting for UI readiness..."
wait_for_ui_ready

log "UI smoke environment is ready."

log "Screenshots collected:"
ls -al "${REPO_ROOT}/ui/tests/e2e/screenshots"
