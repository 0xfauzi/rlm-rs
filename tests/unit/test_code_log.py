from __future__ import annotations

from typing import Any

from rlm_rs import code_log
from rlm_rs.models import (
    LLMToolRequest,
    LLMToolResult,
    SearchToolRequest,
    SearchToolResult,
    ToolRequestsEnvelope,
    ToolResultsEnvelope,
)
from rlm_rs.settings import Settings
from rlm_rs.storage import ddb


class _FakeTable:
    def __init__(self) -> None:
        self.items: dict[tuple[str, str], dict[str, Any]] = {}

    def put_item(self, *, Item: dict[str, Any], ConditionExpression: str | None = None) -> None:
        key = (Item["PK"], Item["SK"])
        if ConditionExpression and "attribute_not_exists" in ConditionExpression:
            if key in self.items:
                raise RuntimeError("Conditional check failed")
        self.items[key] = dict(Item)

    def query(
        self,
        *,
        KeyConditionExpression: Any | None = None,
        ExclusiveStartKey: dict[str, Any] | None = None,
        Limit: int | None = None,
        ScanIndexForward: bool = True,
    ) -> dict[str, Any]:
        items = list(self.items.values())
        items.sort(key=lambda entry: entry["SK"], reverse=not ScanIndexForward)
        start_index = 0
        if ExclusiveStartKey is not None:
            for index, item in enumerate(items):
                if (
                    item.get("PK") == ExclusiveStartKey.get("PK")
                    and item.get("SK") == ExclusiveStartKey.get("SK")
                ):
                    start_index = index + 1
                    break
        end_index = len(items) if Limit is None else start_index + Limit
        page = items[start_index:end_index]
        response: dict[str, Any] = {"Items": page}
        if end_index < len(items):
            last = page[-1]
            response["LastEvaluatedKey"] = {"PK": last["PK"], "SK": last["SK"]}
        return response


def _writer(table: _FakeTable, *, redaction: bool = False) -> code_log.CodeLogWriter:
    settings = Settings()
    settings.enable_trace_redaction = redaction
    return code_log.CodeLogWriter(table=table, execution_id="exec-1", settings=settings)


def test_extract_repl_code_single_block() -> None:
    output = """```repl\nprint(\"hi\")\n```"""
    assert code_log.extract_repl_code(output) == "print(\"hi\")"


def test_code_log_ordering_by_sequence() -> None:
    table = _FakeTable()
    writer = _writer(table)

    writer.write(
        [
            code_log.build_repl_entry(
                source="ROOT",
                model_name="model-a",
                content="print('a')",
            )
        ]
    )
    writer.write(
        [
            code_log.build_repl_entry(
                source="ROOT",
                model_name="model-a",
                content="print('b')",
            )
        ]
    )

    items, _ = ddb.list_code_log_entries(table, execution_id="exec-1")
    sequences = [int(item["sequence"]) for item in items]
    assert sequences == [1, 2]


def test_tool_call_logging() -> None:
    table = _FakeTable()
    writer = _writer(table)

    requests = ToolRequestsEnvelope(
        llm=[
            LLMToolRequest(
                key="k1",
                prompt="Summarize",
                model_hint="sub",
                max_tokens=50,
                temperature=0,
            )
        ],
        search=[SearchToolRequest(key="s1", query="alpha", k=5)],
    )
    writer.write(code_log.build_tool_request_entries(requests))

    results = ToolResultsEnvelope(
        llm={"k1": LLMToolResult(text="ok")},
        search={"s1": SearchToolResult(hits=[])},
    )

    statuses = {"k1": "resolved", "s1": "error"}
    writer.write(code_log.build_tool_result_entries(results, statuses))

    items, _ = ddb.list_code_log_entries(table, execution_id="exec-1")
    kinds = [item["kind"] for item in items]
    tool_types = [item["tool_type"] for item in items if item["kind"].startswith("TOOL")]

    assert kinds.count("TOOL_REQUEST") == 2
    assert kinds.count("TOOL_RESULT") == 2
    assert set(tool_types) == {"llm", "search"}
    assert any(item["content"].get("key") == "k1" for item in items)


def test_redaction_applies_to_content() -> None:
    table = _FakeTable()
    writer = _writer(table, redaction=True)

    writer.write(
        [
            code_log.build_repl_entry(
                source="ROOT",
                model_name="model-a",
                content="secret",
            )
        ]
    )

    items, _ = ddb.list_code_log_entries(table, execution_id="exec-1")
    assert items[0]["content"] == "[REDACTED]"
