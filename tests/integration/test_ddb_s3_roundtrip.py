import os
from datetime import datetime, timezone
from typing import Any

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError, NoCredentialsError

from rlm_rs.storage import ddb, s3


def _localstack_config() -> tuple[str, str, str, str]:
    region = os.getenv("AWS_REGION", "us-east-1")
    endpoint_url = os.getenv(
        "LOCALSTACK_ENDPOINT_URL",
        os.getenv("AWS_ENDPOINT_URL", "http://localhost:4566"),
    )
    bucket = os.getenv("S3_BUCKET", "rlm-local")
    prefix = os.getenv("DDB_TABLE_PREFIX", "rlm")
    return region, endpoint_url, bucket, prefix


def _ensure_localstack_clients() -> tuple[Any, Any, Any, str, str]:
    region, endpoint_url, bucket, prefix = _localstack_config()
    os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
    os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
    os.environ.setdefault("AWS_DEFAULT_REGION", region)

    s3_client = s3.build_s3_client(region=region, endpoint_url=endpoint_url)
    ddb_client = ddb.build_ddb_client(region=region, endpoint_url=endpoint_url)
    ddb_resource = ddb.build_ddb_resource(region=region, endpoint_url=endpoint_url)

    try:
        s3_client.list_buckets()
        ddb_client.list_tables()
    except (EndpointConnectionError, NoCredentialsError) as exc:
        pytest.skip(f"LocalStack not available: {exc}")

    return s3_client, ddb_client, ddb_resource, bucket, prefix


def _ensure_bucket(s3_client: Any, bucket: str) -> None:
    try:
        s3_client.head_bucket(Bucket=bucket)
    except ClientError:
        s3_client.create_bucket(Bucket=bucket)


def _ensure_tables(ddb_client: Any, prefix: str) -> ddb.DdbTableNames:
    tables = ddb.build_table_names(prefix)
    for name in tables.__dict__.values():
        ddb.ensure_table(ddb_client, name)
    return tables


def test_ddb_s3_roundtrip() -> None:
    s3_client, ddb_client, ddb_resource, bucket, prefix = _ensure_localstack_clients()
    _ensure_bucket(s3_client, bucket)
    tables = _ensure_tables(ddb_client, prefix)

    tenant_id = "tenant-ddb-s3"
    session_id = "sess-ddb-s3"
    doc_id = "doc-ddb-s3"
    execution_id = "exec-ddb-s3"
    ttl_epoch = int(datetime(2026, 1, 2, tzinfo=timezone.utc).timestamp())

    s3_key = "parsed/test/text.json"
    payload = {"hello": "world"}
    s3.put_json(s3_client, bucket, s3_key, payload)
    assert s3.get_json(s3_client, bucket, s3_key) == payload

    sessions_table = ddb_resource.Table(tables.sessions)
    documents_table = ddb_resource.Table(tables.documents)
    executions_table = ddb_resource.Table(tables.executions)
    execution_state_table = ddb_resource.Table(tables.execution_state)

    ddb.create_session(
        sessions_table,
        tenant_id=tenant_id,
        session_id=session_id,
        status="CREATING",
        created_at="2026-01-01T00:00:00Z",
        expires_at="2026-01-02T00:00:00Z",
        ttl_epoch=ttl_epoch,
        doc_count=1,
        options={"readiness_mode": "LAX"},
    )
    assert ddb.update_session_status(
        sessions_table,
        tenant_id=tenant_id,
        session_id=session_id,
        expected_status="CREATING",
        new_status="READY",
        updated_at="2026-01-01T00:01:00Z",
    )

    ddb.create_document(
        documents_table,
        tenant_id=tenant_id,
        session_id=session_id,
        doc_id=doc_id,
        doc_index=0,
        source_name="sample.txt",
        mime_type="text/plain",
        raw_s3_uri="s3://raw/sample.txt",
        ingest_status="REGISTERED",
    )
    assert ddb.update_document_status(
        documents_table,
        session_id=session_id,
        doc_id=doc_id,
        expected_status="REGISTERED",
        new_status="PARSED",
        text_s3_uri=f"s3://{bucket}/{s3_key}",
        text_checksum="sha256:deadbeef",
        parser_version="parser-1",
    )

    ddb.create_execution(
        executions_table,
        tenant_id=tenant_id,
        session_id=session_id,
        execution_id=execution_id,
        status="RUNNING",
        mode="ANSWERER",
        question="hello?",
        started_at="2026-01-01T00:02:00Z",
    )
    assert ddb.acquire_execution_lease(
        executions_table,
        session_id=session_id,
        execution_id=execution_id,
        owner_id="worker-a",
        now_epoch=100,
        lease_duration_seconds=30,
    )
    assert not ddb.acquire_execution_lease(
        executions_table,
        session_id=session_id,
        execution_id=execution_id,
        owner_id="worker-b",
        now_epoch=110,
        lease_duration_seconds=30,
    )
    assert ddb.release_execution_lease(
        executions_table,
        session_id=session_id,
        execution_id=execution_id,
        owner_id="worker-a",
    )
    assert ddb.update_execution_status(
        executions_table,
        session_id=session_id,
        execution_id=execution_id,
        expected_status="RUNNING",
        new_status="COMPLETED",
        answer="done",
        completed_at="2026-01-01T00:03:00Z",
        duration_ms=60000,
    )

    ddb.put_execution_state(
        execution_state_table,
        execution_id=execution_id,
        turn_index=1,
        updated_at="2026-01-01T00:03:00Z",
        ttl_epoch=ttl_epoch,
        state_json={"result": "ok"},
        checksum="sha256:state",
        summary={"size": 1},
    )
    state = ddb.get_execution_state(execution_state_table, execution_id=execution_id)
    assert state is not None
    assert state["state_json"] == {"result": "ok"}
