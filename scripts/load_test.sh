#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

export UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/uv-cache}"

ITERATIONS=1
OUTPUT_PATH=""

usage() {
  cat <<'USAGE'
Usage: scripts/load_test.sh [--iterations N] [--output PATH]

Runs the smoke test repeatedly and writes a JSON artifact with execution
durations, budgets consumed, and cache hit rates.
USAGE
}

log() {
  printf '%s\n' "$*"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --iterations)
      ITERATIONS="${2:-}"
      shift 2
      ;;
    --output)
      OUTPUT_PATH="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "ERROR: unknown argument $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ -z "${ITERATIONS}" ]] || ! [[ "${ITERATIONS}" =~ ^[0-9]+$ ]] || [[ "${ITERATIONS}" -lt 1 ]]; then
  echo "ERROR: --iterations must be an integer >= 1" >&2
  exit 1
fi

if [[ -z "${OUTPUT_PATH}" ]]; then
  timestamp="$(date -u +"%Y%m%dT%H%M%SZ")"
  OUTPUT_PATH="/tmp/rlm_rs_load_test_${timestamp}.json"
fi

SMOKE_SCRIPT="${REPO_ROOT}/scripts/smoke_test.sh"
if [[ ! -f "${SMOKE_SCRIPT}" ]]; then
  echo "ERROR: smoke test script not found at ${SMOKE_SCRIPT}" >&2
  exit 1
fi

ORIGINAL_KEEP_DOCKER="${KEEP_DOCKER:-0}"
export KEEP_DOCKER=1
cleanup_docker=0
if [[ "${ORIGINAL_KEEP_DOCKER}" != "1" ]]; then
  cleanup_docker=1
fi

tmp_dir="$(mktemp -d)"
cleanup() {
  rm -rf "${tmp_dir}"
  if [[ "${cleanup_docker}" -eq 1 ]]; then
    (cd "${REPO_ROOT}" && docker compose down >/dev/null 2>&1) || true
  fi
}
trap cleanup EXIT

warmup_path=""
if [[ "${ITERATIONS}" -gt 1 ]]; then
  warmup_path="${tmp_dir}/warmup.json"
  log "Warmup run (sub-minute validation)..."
  SMOKE_OUTPUT_JSON="${warmup_path}" bash "${SMOKE_SCRIPT}"
fi

for i in $(seq 1 "${ITERATIONS}"); do
  log "Iteration ${i}/${ITERATIONS}..."
  SMOKE_OUTPUT_JSON="${tmp_dir}/run_${i}.json" bash "${SMOKE_SCRIPT}"
done

export LOAD_TEST_TMP_DIR="${tmp_dir}"
export LOAD_TEST_OUTPUT="${OUTPUT_PATH}"
export LOAD_TEST_ITERATIONS="${ITERATIONS}"
export LOAD_TEST_WARMUP_PATH="${warmup_path}"

(cd "${REPO_ROOT}" && uv run python - <<'PY'
from __future__ import annotations

import glob
import json
import os
import statistics
from datetime import datetime, timezone


def _summarize(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    return {
        "count": len(values),
        "min": min(values),
        "max": max(values),
        "avg": statistics.fmean(values),
    }


def _read_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


tmp_dir = os.environ["LOAD_TEST_TMP_DIR"]
output_path = os.environ["LOAD_TEST_OUTPUT"]
iterations = int(os.environ["LOAD_TEST_ITERATIONS"])
warmup_path = os.environ.get("LOAD_TEST_WARMUP_PATH") or ""

result_paths = sorted(glob.glob(os.path.join(tmp_dir, "run_*.json")))
results = []
for path in result_paths:
    entry = _read_json(path)
    name = os.path.basename(path)
    iteration = int(name.replace("run_", "").replace(".json", ""))
    entry["iteration"] = iteration
    results.append(entry)

warmup = _read_json(warmup_path) if warmup_path and os.path.exists(warmup_path) else None

duration_values = [
    float(entry["execution_duration_seconds"])
    for entry in results
    if "execution_duration_seconds" in entry
]

def _budget_values(field: str) -> list[float]:
    values: list[float] = []
    for entry in results:
        budgets = entry.get("budgets_consumed")
        if isinstance(budgets, dict) and budgets.get(field) is not None:
            values.append(float(budgets[field]))
    return values


def _cache_values(field: str) -> list[float]:
    values: list[float] = []
    for entry in results:
        cache = entry.get("cache")
        if isinstance(cache, dict) and cache.get(field) is not None:
            values.append(float(cache[field]))
    return values


summary = {
    "execution_duration_seconds": _summarize(duration_values),
    "budgets_consumed": {
        "turns": _summarize(_budget_values("turns")),
        "llm_subcalls": _summarize(_budget_values("llm_subcalls")),
        "total_seconds": _summarize(_budget_values("total_seconds")),
    },
    "cache_hit_rate": _summarize(_cache_values("hit_rate")),
}

payload = {
    "generated_at": datetime.now(timezone.utc)
    .replace(microsecond=0)
    .isoformat()
    .replace("+00:00", "Z"),
    "iterations": iterations,
    "warmup": warmup,
    "results": results,
    "summary": summary,
}

with open(output_path, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, sort_keys=True)
    handle.write("\n")

print(output_path)
PY
)

log "Load test artifact written to ${OUTPUT_PATH}"
