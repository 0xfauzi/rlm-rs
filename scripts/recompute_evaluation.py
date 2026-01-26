#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys

from rlm_rs.orchestrator.worker import build_worker
from rlm_rs.storage import ddb


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Recompute evaluation artifacts for an existing Answerer execution.\n\n"
            "By default, recomputes only LLM judge metrics (keeps baseline as-is).\n"
            "Use --recompute-baseline to recompute baseline + judge metrics (can be expensive)."
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("execution_id", help="Execution ID (e.g. exec_...)")
    parser.add_argument("--tenant-id", default=None, help="Tenant ID (optional disambiguation)")
    parser.add_argument(
        "--recompute-baseline",
        action="store_true",
        help="Recompute baseline answer + judge metrics (potentially expensive).",
    )
    args = parser.parse_args(argv)

    worker = build_worker()
    ok = worker.recompute_evaluation(
        execution_id=args.execution_id,
        tenant_id=args.tenant_id,
        recompute_baseline=bool(args.recompute_baseline),
    )
    if not ok:
        print("No evaluation update performed.", file=sys.stderr)
        return 2

    table = worker.ddb_resource.Table(worker.table_names.evaluations)
    item = ddb.get_evaluation(table, execution_id=args.execution_id)
    if not item:
        print("Evaluation record not found after recompute.", file=sys.stderr)
        return 3

    # Print a concise view for humans.
    payload = {
        "execution_id": item.get("execution_id"),
        "baseline_status": item.get("baseline_status"),
        "judge_metrics": item.get("judge_metrics"),
    }
    print(json.dumps(payload, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

