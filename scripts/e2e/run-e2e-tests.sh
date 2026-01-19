#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT_DIR=$(cd "${SCRIPT_DIR}/../.." && pwd)

: "${NPM_CONFIG_CACHE:=/tmp/npm-cache}"
export NPM_CONFIG_CACHE

UI_LOG="${ROOT_DIR}/ui/tests/e2e/ui-dev-server.log"

cleanup() {
  if [[ -n "${UI_PID:-}" ]] && kill -0 "${UI_PID}" 2>/dev/null; then
    kill "${UI_PID}" >/dev/null 2>&1 || true
    wait "${UI_PID}" >/dev/null 2>&1 || true
  fi
}

trap cleanup EXIT

npm run dev --prefix "${ROOT_DIR}/ui" -- --hostname 127.0.0.1 --port 3000 >"${UI_LOG}" 2>&1 &
UI_PID=$!

for i in {1..60}; do
  if curl -sS http://127.0.0.1:3000 >/dev/null; then
    break
  fi
  sleep 1
  if [[ "$i" -eq 60 ]]; then
    echo "UI did not become ready within 60 seconds."
    echo "Check log: ${UI_LOG}"
    exit 1
  fi
done

echo "UI dev server is ready."

mapfile -t test_scripts < <(find "${SCRIPT_DIR}" -maxdepth 1 -type f -name "*.sh" ! -name "run-e2e-tests.sh" | sort)

if [[ ${#test_scripts[@]} -eq 0 ]]; then
  echo "No E2E scripts found in ${SCRIPT_DIR}."
  exit 0
fi

for script in "${test_scripts[@]}"; do
  echo "Running ${script}"
  bash "${script}"
done
