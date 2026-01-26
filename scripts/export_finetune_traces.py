#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Iterable, Mapping

from boto3.dynamodb.conditions import Key

from rlm_rs.finetune.traces import (
    build_trace_from_storage,
    load_trace_artifact,
    persist_trace_artifact,
)
from rlm_rs.orchestrator.worker import build_worker
from rlm_rs.storage import ddb


def _scan_executions(
    table: Any,
    *,
    tenant_id: str | None,
    session_id: str | None,
    status: str | None,
    limit: int | None,
) -> Iterable[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    response = table.scan()
    items.extend(response.get("Items", []))
    while response.get("LastEvaluatedKey"):
        response = table.scan(ExclusiveStartKey=response["LastEvaluatedKey"])
        items.extend(response.get("Items", []))
    results: list[dict[str, Any]] = []
    for item in items:
        if tenant_id and item.get("tenant_id") != tenant_id:
            continue
        if session_id and item.get("session_id") != session_id:
            continue
        if status and item.get("status") != status:
            continue
        results.append(item)
        if limit and len(results) >= limit:
            break
    return results


def _query_documents(table: Any, *, session_id: str) -> list[dict[str, Any]]:
    pk = f"{ddb.DOCUMENT_PK_PREFIX}{session_id}"
    response = table.query(KeyConditionExpression=Key("PK").eq(pk))
    items = list(response.get("Items", []))
    while response.get("LastEvaluatedKey"):
        response = table.query(
            KeyConditionExpression=Key("PK").eq(pk),
            ExclusiveStartKey=response["LastEvaluatedKey"],
        )
        items.extend(response.get("Items", []))
    return items


def _list_code_log_entries(table: Any, *, execution_id: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    start_key = None
    while True:
        batch, start_key = ddb.list_code_log_entries(
            table,
            execution_id=execution_id,
            limit=1000,
            exclusive_start_key=start_key,
        )
        items.extend(batch)
        if not start_key:
            break
    return items


def _rebuild_trace(
    *,
    worker,
    execution_item: Mapping[str, Any],
) -> dict[str, Any]:
    session_id = str(execution_item.get("session_id") or "")
    execution_id = str(execution_item.get("execution_id") or "")
    tenant_id = str(execution_item.get("tenant_id") or "")
    sessions_table = worker.ddb_resource.Table(worker.table_names.sessions)
    documents_table = worker.ddb_resource.Table(worker.table_names.documents)
    execution_state_table = worker.ddb_resource.Table(worker.table_names.execution_state)
    evaluations_table = worker.ddb_resource.Table(worker.table_names.evaluations)
    code_log_table = worker.ddb_resource.Table(worker.table_names.code_log)

    session_item = ddb.get_session(sessions_table, tenant_id=tenant_id, session_id=session_id)
    if session_item is None:
        raise ValueError("Session not found for execution.")
    documents = _query_documents(documents_table, session_id=session_id)
    steps = ddb.list_execution_state_steps(execution_state_table, execution_id=execution_id)
    code_entries = _list_code_log_entries(code_log_table, execution_id=execution_id)
    evaluation_item = ddb.get_evaluation(evaluations_table, execution_id=execution_id)
    return build_trace_from_storage(
        execution_item=execution_item,
        session_item=session_item,
        documents=documents,
        steps=steps,
        code_log_entries=code_entries,
        evaluation_item=evaluation_item,
        s3_client=worker.s3_client,
    )


def _update_trace_uri(
    *,
    worker,
    execution_item: Mapping[str, Any],
    trace_s3_uri: str,
) -> None:
    status = str(execution_item.get("status") or "COMPLETED")
    ddb.update_execution_status(
        worker.ddb_resource.Table(worker.table_names.executions),
        session_id=str(execution_item.get("session_id") or ""),
        execution_id=str(execution_item.get("execution_id") or ""),
        expected_status=status,
        new_status=status,
        trace_s3_uri=trace_s3_uri,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export execution traces to JSONL for fine-tuning.",
    )
    parser.add_argument("--execution-id", default=None)
    parser.add_argument("--session-id", default=None)
    parser.add_argument("--tenant-id", default=None)
    parser.add_argument("--status", default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--output", default="finetune_traces.jsonl")
    parser.add_argument(
        "--rebuild-missing",
        action="store_true",
        help="Rebuild and persist traces when trace_s3_uri is missing.",
    )
    args = parser.parse_args(argv)

    worker = build_worker()
    executions_table = worker.ddb_resource.Table(worker.table_names.executions)
    items = _scan_executions(
        executions_table,
        tenant_id=args.tenant_id,
        session_id=args.session_id,
        status=args.status,
        limit=args.limit,
    )

    if not items:
        print("No executions matched.", file=sys.stderr)
        return 1

    with open(args.output, "w", encoding="utf-8") as handle:
        for item in items:
            trace_s3_uri = item.get("trace_s3_uri")
            trace_payload = None
            if trace_s3_uri:
                trace_payload = load_trace_artifact(
                    s3_client=worker.s3_client,
                    trace_s3_uri=str(trace_s3_uri),
                )
            else:
                trace_payload = _rebuild_trace(worker=worker, execution_item=item)
                if args.rebuild_missing:
                    trace_s3_uri = persist_trace_artifact(
                        s3_client=worker.s3_client,
                        bucket=worker.settings.s3_bucket,
                        tenant_id=str(item.get("tenant_id") or ""),
                        execution_id=str(item.get("execution_id") or ""),
                        artifact=trace_payload,
                    )
                    _update_trace_uri(
                        worker=worker,
                        execution_item=item,
                        trace_s3_uri=trace_s3_uri,
                    )
            handle.write(json.dumps(trace_payload) + "\n")

    print(f"Wrote traces to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
