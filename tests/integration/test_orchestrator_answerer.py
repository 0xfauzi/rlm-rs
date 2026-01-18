import os
from datetime import datetime, timezone
from typing import Any

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError, NoCredentialsError

from rlm_rs.orchestrator.providers import FakeLLMProvider
from rlm_rs.orchestrator.worker import OrchestratorWorker
from rlm_rs.settings import Settings
from rlm_rs.storage import ddb, s3, state as state_store


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


def _settings_with_env(
    *,
    bucket: str,
    region: str,
    endpoint_url: str,
    prefix: str,
) -> Settings:
    previous = {
        "S3_BUCKET": os.environ.get("S3_BUCKET"),
        "AWS_REGION": os.environ.get("AWS_REGION"),
        "LOCALSTACK_ENDPOINT_URL": os.environ.get("LOCALSTACK_ENDPOINT_URL"),
        "DDB_TABLE_PREFIX": os.environ.get("DDB_TABLE_PREFIX"),
    }
    os.environ["S3_BUCKET"] = bucket
    os.environ["AWS_REGION"] = region
    os.environ["LOCALSTACK_ENDPOINT_URL"] = endpoint_url
    os.environ["DDB_TABLE_PREFIX"] = prefix
    try:
        return Settings()
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _build_offsets_payload(text: str, interval: int = 5) -> dict[str, Any]:
    checkpoints: list[dict[str, int]] = [{"char": 0, "byte": 0}]
    byte_offset = 0
    for index, char in enumerate(text, start=1):
        byte_offset += len(char.encode("utf-8"))
        if index % interval == 0:
            checkpoints.append({"char": index, "byte": byte_offset})
    if checkpoints[-1]["char"] != len(text):
        checkpoints.append({"char": len(text), "byte": byte_offset})
    return {
        "version": "1.0",
        "doc_id": "doc-1",
        "char_length": len(text),
        "byte_length": byte_offset,
        "encoding": "utf-8",
        "checkpoints": checkpoints,
        "checkpoint_interval": interval,
    }


def test_orchestrator_fake_provider_completes_answerer() -> None:
    s3_client, ddb_client, ddb_resource, bucket, prefix = _ensure_localstack_clients()
    _ensure_bucket(s3_client, bucket)
    tables = _ensure_tables(ddb_client, prefix)

    tenant_id = "tenant-1"
    session_id = "sess-1"
    doc_id = "doc-1"
    execution_id = "exec-1"
    ttl_epoch = int(datetime(2026, 1, 2, tzinfo=timezone.utc).timestamp())

    text = "Alpha beta gamma delta"
    text_key = "parsed/tenant-1/sess-1/doc-1/text.txt"
    offsets_key = "parsed/tenant-1/sess-1/doc-1/offsets.json"
    s3.put_bytes(s3_client, bucket, text_key, text.encode("utf-8"))
    s3.put_json(s3_client, bucket, offsets_key, _build_offsets_payload(text))

    sessions_table = ddb_resource.Table(tables.sessions)
    documents_table = ddb_resource.Table(tables.documents)
    executions_table = ddb_resource.Table(tables.executions)
    execution_state_table = ddb_resource.Table(tables.execution_state)

    ddb.create_session(
        sessions_table,
        tenant_id=tenant_id,
        session_id=session_id,
        status="READY",
        created_at="2026-01-01T00:00:00Z",
        expires_at="2026-01-02T00:00:00Z",
        ttl_epoch=ttl_epoch,
        doc_count=1,
        options={"enable_search": False, "readiness_mode": "LAX"},
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
        ingest_status="PARSED",
        text_s3_uri=f"s3://{bucket}/{text_key}",
        offsets_s3_uri=f"s3://{bucket}/{offsets_key}",
        char_length=len(text),
        byte_length=len(text.encode("utf-8")),
    )

    budgets = {
        "max_turns": 3,
        "max_total_seconds": 60,
        "max_llm_subcalls": 2,
        "max_llm_prompt_chars": 2000,
        "max_total_llm_prompt_chars": 5000,
    }
    models = {"root_model": "fake-root", "sub_model": "fake-sub"}

    ddb.create_execution(
        executions_table,
        tenant_id=tenant_id,
        session_id=session_id,
        execution_id=execution_id,
        status="RUNNING",
        mode="ANSWERER",
        question="Summarize",
        budgets_requested=budgets,
        models=models,
        started_at="2026-01-01T00:01:00Z",
    )

    state_record = state_store.persist_state_payload(
        state={},
        tenant_id=tenant_id,
        execution_id=execution_id,
        turn_index=0,
    )
    ddb.put_execution_state(
        execution_state_table,
        execution_id=execution_id,
        turn_index=0,
        updated_at="2026-01-01T00:01:00Z",
        ttl_epoch=ttl_epoch,
        state_json=state_record.state_json,
        state_s3_uri=state_record.state_s3_uri,
        checksum=state_record.checksum,
        summary=state_record.summary,
    )

    region, endpoint_url, _, _ = _localstack_config()
    settings = _settings_with_env(
        bucket=bucket,
        region=region,
        endpoint_url=endpoint_url,
        prefix=prefix,
    )

    root_outputs = [
        """```repl
if \"work\" not in state:
    state[\"work\"] = {}

tool.queue_llm(
    \"k1\",
    \"Summarize: \" + str(len(context)),
    max_tokens=50,
    temperature=0,
)
tool.YIELD(\"waiting\")
```""",
        """```repl
snippet = context[0][0:5]
answer = (
    snippet
    + \" | \"
    + state[\"_tool_results\"][\"llm\"][\"k1\"][\"text\"]
)
tool.FINAL(answer)
```""",
    ]
    provider = FakeLLMProvider(root_outputs=root_outputs)
    worker = OrchestratorWorker(
        settings=settings,
        ddb_resource=ddb_resource,
        table_names=tables,
        s3_client=s3_client,
        provider=provider,
    )

    processed = worker.run_once()
    assert processed == 1

    execution_item = ddb.get_execution(
        executions_table,
        session_id=session_id,
        execution_id=execution_id,
    )
    assert execution_item is not None
    assert execution_item["status"] == "COMPLETED"
    assert execution_item.get("answer")
    citations = execution_item.get("citations")
    assert isinstance(citations, list)
    assert citations
