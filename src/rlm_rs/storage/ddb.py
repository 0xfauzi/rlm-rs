from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping

import boto3
from boto3.dynamodb.conditions import Key
from boto3.resources.base import ServiceResource
from botocore.client import BaseClient
from botocore.exceptions import ClientError
from pydantic import JsonValue


SESSION_PK_PREFIX = "TENANT#"
SESSION_SK_PREFIX = "SESSION#"
DOCUMENT_PK_PREFIX = "SESSION#"
DOCUMENT_SK_PREFIX = "DOC#"
EXECUTION_PK_PREFIX = "SESSION#"
EXECUTION_SK_PREFIX = "EXEC#"
EXECUTION_STATE_PK_PREFIX = "EXEC#"
EXECUTION_STATE_SK = "STATE"
EXECUTION_STATE_STEP_SK_PREFIX = "STATE#"
EVALUATION_PK_PREFIX = "EXEC#"
EVALUATION_SK = "EVAL"
CODE_LOG_PK_PREFIX = "EXEC#"
CODE_LOG_SK_PREFIX = "CODE#"
CODE_LOG_SEQUENCE_WIDTH = 20


@dataclass(frozen=True)
class DdbTableNames:
    sessions: str
    documents: str
    executions: str
    execution_state: str
    evaluations: str
    code_log: str
    api_keys: str
    audit_log: str


def table_name(prefix: str | None, suffix: str) -> str:
    return f"{prefix}_{suffix}" if prefix else suffix


def build_table_names(prefix: str | None) -> DdbTableNames:
    return DdbTableNames(
        sessions=table_name(prefix, "sessions"),
        documents=table_name(prefix, "documents"),
        executions=table_name(prefix, "executions"),
        execution_state=table_name(prefix, "execution_state"),
        evaluations=table_name(prefix, "evaluations"),
        code_log=table_name(prefix, "code_log"),
        api_keys=table_name(prefix, "api_keys"),
        audit_log=table_name(prefix, "audit_log"),
    )


def build_ddb_resource(
    *,
    region: str | None = None,
    endpoint_url: str | None = None,
) -> ServiceResource:
    return boto3.resource("dynamodb", region_name=region, endpoint_url=endpoint_url)


def build_ddb_client(
    *,
    region: str | None = None,
    endpoint_url: str | None = None,
) -> BaseClient:
    return boto3.client("dynamodb", region_name=region, endpoint_url=endpoint_url)


def ensure_table(client: BaseClient, name: str) -> None:
    try:
        client.describe_table(TableName=name)
        return
    except ClientError as err:
        if err.response.get("Error", {}).get("Code") != "ResourceNotFoundException":
            raise

    client.create_table(
        TableName=name,
        AttributeDefinitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
        ],
        KeySchema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    client.get_waiter("table_exists").wait(TableName=name)


def session_key(tenant_id: str, session_id: str) -> dict[str, str]:
    return {"PK": f"{SESSION_PK_PREFIX}{tenant_id}", "SK": f"{SESSION_SK_PREFIX}{session_id}"}


def document_key(session_id: str, doc_id: str) -> dict[str, str]:
    return {"PK": f"{DOCUMENT_PK_PREFIX}{session_id}", "SK": f"{DOCUMENT_SK_PREFIX}{doc_id}"}


def execution_key(session_id: str, execution_id: str) -> dict[str, str]:
    return {"PK": f"{EXECUTION_PK_PREFIX}{session_id}", "SK": f"{EXECUTION_SK_PREFIX}{execution_id}"}


def execution_state_key(execution_id: str) -> dict[str, str]:
    return {"PK": f"{EXECUTION_STATE_PK_PREFIX}{execution_id}", "SK": EXECUTION_STATE_SK}


def execution_state_step_key(execution_id: str, turn_index: int) -> dict[str, str]:
    return {
        "PK": f"{EXECUTION_STATE_PK_PREFIX}{execution_id}",
        "SK": f"{EXECUTION_STATE_STEP_SK_PREFIX}{turn_index}",
    }


def evaluation_key(execution_id: str) -> dict[str, str]:
    return {"PK": f"{EVALUATION_PK_PREFIX}{execution_id}", "SK": EVALUATION_SK}


def code_log_key(execution_id: str, sequence: int) -> dict[str, str]:
    return {
        "PK": f"{CODE_LOG_PK_PREFIX}{execution_id}",
        "SK": f"{CODE_LOG_SK_PREFIX}{sequence:0{CODE_LOG_SEQUENCE_WIDTH}d}",
    }


def _without_none(item: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if value is not None}


def _conditional_failed(err: ClientError) -> bool:
    return err.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"


def _coerce_decimals(value: Any) -> Any:
    """Convert floats to Decimal recursively for DynamoDB compatibility."""
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _coerce_decimals(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_coerce_decimals(v) for v in value]
    return value


def create_session(
    table: Any,
    *,
    tenant_id: str,
    session_id: str,
    status: str,
    created_at: str,
    expires_at: str,
    ttl_epoch: int,
    doc_count: int | None = None,
    total_chars: int | None = None,
    options: dict[str, JsonValue] | None = None,
    models_default: dict[str, JsonValue] | None = None,
    budgets_default: dict[str, JsonValue] | None = None,
) -> dict[str, Any]:
    item = _without_none(
        {
            **session_key(tenant_id, session_id),
            "tenant_id": tenant_id,
            "session_id": session_id,
            "status": status,
            "created_at": created_at,
            "expires_at": expires_at,
            "ttl_epoch": ttl_epoch,
            "doc_count": doc_count,
            "total_chars": total_chars,
            "options": options,
            "models_default": models_default,
            "budgets_default": budgets_default,
        }
    )
    table.put_item(Item=item, ConditionExpression="attribute_not_exists(PK)")
    return item


def get_session(table: Any, *, tenant_id: str, session_id: str) -> dict[str, Any] | None:
    response = table.get_item(Key=session_key(tenant_id, session_id))
    return response.get("Item")


def update_session_status(
    table: Any,
    *,
    tenant_id: str,
    session_id: str,
    expected_status: str,
    new_status: str,
    updated_at: str | None = None,
) -> bool:
    updates = ["#status = :new_status"]
    values: dict[str, Any] = {
        ":new_status": new_status,
        ":expected_status": expected_status,
    }
    if updated_at is not None:
        updates.append("updated_at = :updated_at")
        values[":updated_at"] = updated_at

    try:
        table.update_item(
            Key=session_key(tenant_id, session_id),
            UpdateExpression=f"SET {', '.join(updates)}",
            ConditionExpression="#status = :expected_status",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues=values,
        )
    except ClientError as err:
        if _conditional_failed(err):
            return False
        raise
    return True


def create_document(
    table: Any,
    *,
    tenant_id: str,
    session_id: str,
    doc_id: str,
    doc_index: int,
    source_name: str,
    mime_type: str,
    raw_s3_uri: str,
    ingest_status: str,
    raw_s3_version_id: str | None = None,
    raw_s3_etag: str | None = None,
    text_s3_uri: str | None = None,
    meta_s3_uri: str | None = None,
    offsets_s3_uri: str | None = None,
    char_length: int | None = None,
    byte_length: int | None = None,
    page_count: int | None = None,
    parser_version: str | None = None,
    text_checksum: str | None = None,
    failure_reason: str | None = None,
) -> dict[str, Any]:
    item = _without_none(
        {
            **document_key(session_id, doc_id),
            "tenant_id": tenant_id,
            "session_id": session_id,
            "doc_id": doc_id,
            "doc_index": doc_index,
            "source_name": source_name,
            "mime_type": mime_type,
            "raw_s3_uri": raw_s3_uri,
            "raw_s3_version_id": raw_s3_version_id,
            "raw_s3_etag": raw_s3_etag,
            "text_s3_uri": text_s3_uri,
            "meta_s3_uri": meta_s3_uri,
            "offsets_s3_uri": offsets_s3_uri,
            "char_length": char_length,
            "byte_length": byte_length,
            "page_count": page_count,
            "parser_version": parser_version,
            "text_checksum": text_checksum,
            "ingest_status": ingest_status,
            "failure_reason": failure_reason,
        }
    )
    table.put_item(Item=item, ConditionExpression="attribute_not_exists(PK)")
    return item


def get_document(table: Any, *, session_id: str, doc_id: str) -> dict[str, Any] | None:
    response = table.get_item(Key=document_key(session_id, doc_id))
    return response.get("Item")


def update_document_status(
    table: Any,
    *,
    session_id: str,
    doc_id: str,
    expected_status: str,
    new_status: str,
    text_s3_uri: str | None = None,
    meta_s3_uri: str | None = None,
    offsets_s3_uri: str | None = None,
    char_length: int | None = None,
    byte_length: int | None = None,
    page_count: int | None = None,
    parser_version: str | None = None,
    text_checksum: str | None = None,
    search_index_s3_uri: str | None = None,
    search_chunk_count: int | None = None,
    search_chunk_size: int | None = None,
    search_chunk_overlap: int | None = None,
    failure_reason: str | None = None,
) -> bool:
    updates = ["#status = :new_status"]
    values: dict[str, Any] = {
        ":new_status": new_status,
        ":expected_status": expected_status,
    }
    if text_s3_uri is not None:
        updates.append("text_s3_uri = :text_s3_uri")
        values[":text_s3_uri"] = text_s3_uri
    if meta_s3_uri is not None:
        updates.append("meta_s3_uri = :meta_s3_uri")
        values[":meta_s3_uri"] = meta_s3_uri
    if offsets_s3_uri is not None:
        updates.append("offsets_s3_uri = :offsets_s3_uri")
        values[":offsets_s3_uri"] = offsets_s3_uri
    if char_length is not None:
        updates.append("char_length = :char_length")
        values[":char_length"] = char_length
    if byte_length is not None:
        updates.append("byte_length = :byte_length")
        values[":byte_length"] = byte_length
    if page_count is not None:
        updates.append("page_count = :page_count")
        values[":page_count"] = page_count
    if parser_version is not None:
        updates.append("parser_version = :parser_version")
        values[":parser_version"] = parser_version
    if text_checksum is not None:
        updates.append("text_checksum = :text_checksum")
        values[":text_checksum"] = text_checksum
    if search_index_s3_uri is not None:
        updates.append("search_index_s3_uri = :search_index_s3_uri")
        values[":search_index_s3_uri"] = search_index_s3_uri
    if search_chunk_count is not None:
        updates.append("search_chunk_count = :search_chunk_count")
        values[":search_chunk_count"] = search_chunk_count
    if search_chunk_size is not None:
        updates.append("search_chunk_size = :search_chunk_size")
        values[":search_chunk_size"] = search_chunk_size
    if search_chunk_overlap is not None:
        updates.append("search_chunk_overlap = :search_chunk_overlap")
        values[":search_chunk_overlap"] = search_chunk_overlap
    if failure_reason is not None:
        updates.append("failure_reason = :failure_reason")
        values[":failure_reason"] = failure_reason

    try:
        table.update_item(
            Key=document_key(session_id, doc_id),
            UpdateExpression=f"SET {', '.join(updates)}",
            ConditionExpression="#status = :expected_status",
            ExpressionAttributeNames={"#status": "ingest_status"},
            ExpressionAttributeValues=values,
        )
    except ClientError as err:
        if _conditional_failed(err):
            return False
        raise
    return True


def create_execution(
    table: Any,
    *,
    tenant_id: str,
    session_id: str,
    execution_id: str,
    status: str,
    mode: str,
    question: str | None = None,
    budgets_requested: dict[str, JsonValue] | None = None,
    budgets_consumed: dict[str, JsonValue] | None = None,
    models: dict[str, JsonValue] | None = None,
    options: dict[str, JsonValue] | None = None,
    started_at: str | None = None,
    completed_at: str | None = None,
    duration_ms: int | None = None,
) -> dict[str, Any]:
    item = _without_none(
        {
            **execution_key(session_id, execution_id),
            "tenant_id": tenant_id,
            "session_id": session_id,
            "execution_id": execution_id,
            "status": status,
            "mode": mode,
            "question": question,
            "budgets_requested": budgets_requested,
            "budgets_consumed": budgets_consumed,
            "models": models,
            "options": options,
            "started_at": started_at,
            "completed_at": completed_at,
            "duration_ms": duration_ms,
        }
    )
    table.put_item(Item=item, ConditionExpression="attribute_not_exists(PK)")
    return item


def get_execution(table: Any, *, session_id: str, execution_id: str) -> dict[str, Any] | None:
    response = table.get_item(Key=execution_key(session_id, execution_id))
    return response.get("Item")


def update_execution_status(
    table: Any,
    *,
    session_id: str,
    execution_id: str,
    expected_status: str,
    new_status: str,
    answer: str | None = None,
    citations: list[dict[str, JsonValue]] | None = None,
    contexts: list[JsonValue] | None = None,
    contexts_s3_uri: str | None = None,
    trace_s3_uri: str | None = None,
    budgets_consumed: dict[str, JsonValue] | None = None,
    completed_at: str | None = None,
    duration_ms: int | None = None,
) -> bool:
    updates = ["#status = :new_status"]
    values: dict[str, Any] = {
        ":new_status": new_status,
        ":expected_status": expected_status,
    }
    if answer is not None:
        updates.append("answer = :answer")
        values[":answer"] = answer
    if citations is not None:
        updates.append("citations = :citations")
        values[":citations"] = citations
    if contexts is not None:
        updates.append("contexts = :contexts")
        values[":contexts"] = contexts
    if contexts_s3_uri is not None:
        updates.append("contexts_s3_uri = :contexts_s3_uri")
        values[":contexts_s3_uri"] = contexts_s3_uri
    if trace_s3_uri is not None:
        updates.append("trace_s3_uri = :trace_s3_uri")
        values[":trace_s3_uri"] = trace_s3_uri
    if budgets_consumed is not None:
        updates.append("budgets_consumed = :budgets_consumed")
        values[":budgets_consumed"] = budgets_consumed
    if completed_at is not None:
        updates.append("completed_at = :completed_at")
        values[":completed_at"] = completed_at
    if duration_ms is not None:
        updates.append("duration_ms = :duration_ms")
        values[":duration_ms"] = duration_ms

    try:
        table.update_item(
            Key=execution_key(session_id, execution_id),
            UpdateExpression=f"SET {', '.join(updates)}",
            ConditionExpression="#status = :expected_status",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues=values,
        )
    except ClientError as err:
        if _conditional_failed(err):
            return False
        raise
    return True


def create_evaluation(
    table: Any,
    *,
    evaluation_id: str,
    tenant_id: str,
    session_id: str,
    execution_id: str,
    mode: str,
    question: str,
    answer: str | None = None,
    baseline_status: str,
    baseline_skip_reason: str | None = None,
    baseline_answer: str | None = None,
    baseline_input_tokens: int | None = None,
    baseline_context_window: int | None = None,
    judge_metrics: dict[str, JsonValue] | None = None,
    created_at: str,
) -> dict[str, Any]:
    item = _without_none(
        {
            **evaluation_key(execution_id),
            "evaluation_id": evaluation_id,
            "tenant_id": tenant_id,
            "session_id": session_id,
            "execution_id": execution_id,
            "mode": mode,
            "question": question,
            "answer": answer,
            "baseline_status": baseline_status,
            "baseline_skip_reason": baseline_skip_reason,
            "baseline_answer": baseline_answer,
            "baseline_input_tokens": baseline_input_tokens,
            "baseline_context_window": baseline_context_window,
            "judge_metrics": judge_metrics,
            "created_at": created_at,
        }
    )
    item["baseline_answer"] = baseline_answer
    item["baseline_input_tokens"] = baseline_input_tokens
    item["baseline_context_window"] = baseline_context_window
    coerced = _coerce_decimals(item)
    table.put_item(Item=coerced, ConditionExpression="attribute_not_exists(PK)")
    return coerced


def update_evaluation(
    table: Any,
    *,
    execution_id: str,
    baseline_status: str,
    baseline_skip_reason: str | None = None,
    baseline_answer: str | None = None,
    baseline_input_tokens: int | None = None,
    baseline_context_window: int | None = None,
    judge_metrics: dict[str, JsonValue] | None = None,
) -> bool:
    updates = ["baseline_status = :baseline_status"]
    values: dict[str, Any] = {":baseline_status": baseline_status}
    if baseline_skip_reason is not None:
        updates.append("baseline_skip_reason = :baseline_skip_reason")
        values[":baseline_skip_reason"] = baseline_skip_reason
    if baseline_answer is not None:
        updates.append("baseline_answer = :baseline_answer")
        values[":baseline_answer"] = baseline_answer
    if baseline_input_tokens is not None:
        updates.append("baseline_input_tokens = :baseline_input_tokens")
        values[":baseline_input_tokens"] = baseline_input_tokens
    if baseline_context_window is not None:
        updates.append("baseline_context_window = :baseline_context_window")
        values[":baseline_context_window"] = baseline_context_window
    if judge_metrics is not None:
        updates.append("judge_metrics = :judge_metrics")
        values[":judge_metrics"] = judge_metrics

    try:
        table.update_item(
            Key=evaluation_key(execution_id),
            UpdateExpression=f"SET {', '.join(updates)}",
            ExpressionAttributeValues=_coerce_decimals(values),
            ConditionExpression="attribute_exists(PK)",
        )
    except ClientError as err:
        if _conditional_failed(err):
            return False
        raise
    return True


def get_evaluation(table: Any, *, execution_id: str) -> dict[str, Any] | None:
    response = table.get_item(Key=evaluation_key(execution_id))
    return response.get("Item")


def _parse_code_log_sequence(item: Mapping[str, Any]) -> int:
    raw = item.get("sequence")
    if isinstance(raw, (int, float)):
        return int(raw)
    if isinstance(raw, Decimal):
        return int(raw)
    sk = str(item.get("SK", ""))
    if sk.startswith(CODE_LOG_SK_PREFIX):
        suffix = sk.removeprefix(CODE_LOG_SK_PREFIX)
        if suffix.isdigit():
            return int(suffix)
    return 0


def next_code_log_sequence(
    table: Any,
    *,
    execution_id: str,
    count: int = 1,
) -> int:
    pk = f"{CODE_LOG_PK_PREFIX}{execution_id}"
    response = table.query(KeyConditionExpression=Key("PK").eq(pk))
    items = list(response.get("Items", []))
    while response.get("LastEvaluatedKey"):
        response = table.query(
            KeyConditionExpression=Key("PK").eq(pk),
            ExclusiveStartKey=response["LastEvaluatedKey"],
        )
        items.extend(response.get("Items", []))
    max_seq = 0
    for item in items:
        sk = str(item.get("SK", ""))
        if not sk.startswith(CODE_LOG_SK_PREFIX):
            continue
        max_seq = max(max_seq, _parse_code_log_sequence(item))
    return max_seq + 1


def put_code_log_entries(
    table: Any,
    *,
    execution_id: str,
    entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not entries:
        return []
    start_seq = next_code_log_sequence(table, execution_id=execution_id, count=len(entries))
    items: list[dict[str, Any]] = []
    for offset, entry in enumerate(entries):
        sequence = start_seq + offset
        item = _without_none(
            {
                **code_log_key(execution_id, sequence),
                "execution_id": execution_id,
                "sequence": sequence,
                **entry,
            }
        )
        table.put_item(Item=_coerce_decimals(item), ConditionExpression="attribute_not_exists(PK)")
        items.append(item)
    return items


def list_code_log_entries(
    table: Any,
    *,
    execution_id: str,
    limit: int | None = None,
    exclusive_start_key: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    key_condition = Key("PK").eq(f"{CODE_LOG_PK_PREFIX}{execution_id}") & Key("SK").begins_with(
        CODE_LOG_SK_PREFIX
    )
    kwargs: dict[str, Any] = {"KeyConditionExpression": key_condition}
    if exclusive_start_key is not None:
        kwargs["ExclusiveStartKey"] = exclusive_start_key
    if limit is not None:
        kwargs["Limit"] = limit
    response = table.query(**kwargs)
    items = list(response.get("Items", []))
    return items, response.get("LastEvaluatedKey")


def put_execution_state(
    table: Any,
    *,
    execution_id: str,
    turn_index: int,
    updated_at: str,
    ttl_epoch: int,
    state_json: JsonValue | None = None,
    state_s3_uri: str | None = None,
    checksum: str | None = None,
    summary: dict[str, JsonValue] | None = None,
    timings: dict[str, JsonValue] | None = None,
    success: bool | None = None,
    stdout: str | None = None,
    span_log: list[dict[str, JsonValue]] | None = None,
    tool_requests: dict[str, JsonValue] | None = None,
    final: dict[str, JsonValue] | None = None,
    error: dict[str, JsonValue] | None = None,
) -> dict[str, Any]:
    item = _without_none(
        {
            **execution_state_key(execution_id),
            "execution_id": execution_id,
            "turn_index": turn_index,
            "updated_at": updated_at,
            "ttl_epoch": ttl_epoch,
            "state_json": state_json,
            "state_s3_uri": state_s3_uri,
            "checksum": checksum,
            "summary": summary,
            "timings": timings,
            "success": success,
            "stdout": stdout,
            "span_log": span_log,
            "tool_requests": tool_requests,
            "final": final,
            "error": error,
        }
    )
    table.put_item(Item=_coerce_decimals(item))
    return item


def put_execution_state_step(
    table: Any,
    *,
    execution_id: str,
    turn_index: int,
    updated_at: str,
    ttl_epoch: int,
    state_json: JsonValue | None = None,
    state_s3_uri: str | None = None,
    checksum: str | None = None,
    summary: dict[str, JsonValue] | None = None,
    timings: dict[str, JsonValue] | None = None,
    success: bool | None = None,
    stdout: str | None = None,
    span_log: list[dict[str, JsonValue]] | None = None,
    tool_requests: dict[str, JsonValue] | None = None,
    final: dict[str, JsonValue] | None = None,
    error: dict[str, JsonValue] | None = None,
) -> dict[str, Any]:
    item = _without_none(
        {
            **execution_state_step_key(execution_id, turn_index),
            "execution_id": execution_id,
            "turn_index": turn_index,
            "updated_at": updated_at,
            "ttl_epoch": ttl_epoch,
            "state_json": state_json,
            "state_s3_uri": state_s3_uri,
            "checksum": checksum,
            "summary": summary,
            "timings": timings,
            "success": success,
            "stdout": stdout,
            "span_log": span_log,
            "tool_requests": tool_requests,
            "final": final,
            "error": error,
        }
    )
    table.put_item(Item=_coerce_decimals(item))
    return item


def get_execution_state(table: Any, *, execution_id: str) -> dict[str, Any] | None:
    response = table.get_item(Key=execution_state_key(execution_id))
    return response.get("Item")


def list_execution_state_steps(table: Any, *, execution_id: str) -> list[dict[str, Any]]:
    key_condition = Key("PK").eq(f"{EXECUTION_STATE_PK_PREFIX}{execution_id}") & Key(
        "SK"
    ).begins_with(EXECUTION_STATE_STEP_SK_PREFIX)
    items: list[dict[str, Any]] = []
    response = table.query(KeyConditionExpression=key_condition)
    items.extend(response.get("Items", []))
    while response.get("LastEvaluatedKey"):
        response = table.query(
            KeyConditionExpression=key_condition,
            ExclusiveStartKey=response["LastEvaluatedKey"],
        )
        items.extend(response.get("Items", []))
    return items


def acquire_execution_lease(
    table: Any,
    *,
    session_id: str,
    execution_id: str,
    owner_id: str,
    now_epoch: int,
    lease_duration_seconds: int,
) -> bool:
    lease_expires_at = now_epoch + lease_duration_seconds
    try:
        table.update_item(
            Key=execution_key(session_id, execution_id),
            UpdateExpression=(
                "SET lease_owner = :owner, lease_expires_at = :expires, "
                "lease_updated_at = :updated"
            ),
            ConditionExpression=(
                "attribute_not_exists(lease_expires_at) OR "
                "lease_expires_at < :now OR lease_owner = :owner"
            ),
            ExpressionAttributeValues={
                ":owner": owner_id,
                ":expires": lease_expires_at,
                ":updated": now_epoch,
                ":now": now_epoch,
            },
        )
    except ClientError as err:
        if _conditional_failed(err):
            return False
        raise
    return True


def release_execution_lease(
    table: Any,
    *,
    session_id: str,
    execution_id: str,
    owner_id: str,
) -> bool:
    try:
        table.update_item(
            Key=execution_key(session_id, execution_id),
            UpdateExpression="REMOVE lease_owner, lease_expires_at, lease_updated_at",
            ConditionExpression="lease_owner = :owner",
            ExpressionAttributeValues={":owner": owner_id},
        )
    except ClientError as err:
        if _conditional_failed(err):
            return False
        raise
    return True
