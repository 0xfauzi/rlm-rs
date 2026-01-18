from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import boto3
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


@dataclass(frozen=True)
class DdbTableNames:
    sessions: str
    documents: str
    executions: str
    execution_state: str
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


def _without_none(item: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if value is not None}


def _conditional_failed(err: ClientError) -> bool:
    return err.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"


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
            "success": success,
            "stdout": stdout,
            "span_log": span_log,
            "tool_requests": tool_requests,
            "final": final,
            "error": error,
        }
    )
    table.put_item(Item=item)
    return item


def get_execution_state(table: Any, *, execution_id: str) -> dict[str, Any] | None:
    response = table.get_item(Key=execution_state_key(execution_id))
    return response.get("Item")


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
