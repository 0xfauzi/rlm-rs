from __future__ import annotations

from typing import Any

from rlm_rs import code_log
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


def _writer(table: _FakeTable) -> code_log.CodeLogWriter:
    settings = Settings()
    return code_log.CodeLogWriter(table=table, execution_id="exec-1", settings=settings)


def test_code_log_turn_index_increases_for_multi_turn_answerer_execution() -> None:
    table = _FakeTable()
    writer = _writer(table)

    writer.write(
        [
            code_log.build_repl_entry(
                source="ROOT",
                model_name="model-a",
                content="print('turn0')",
                turn_index=0,
            )
        ]
    )
    writer.write(
        [
            code_log.build_repl_entry(
                source="ROOT",
                model_name="model-a",
                content="print('turn1')",
                turn_index=1,
            )
        ]
    )

    items, _ = ddb.list_code_log_entries(table, execution_id="exec-1")
    turn_indexes = [item.get("turn_index") for item in items]

    assert all(turn_index is not None for turn_index in turn_indexes)
    assert turn_indexes == [0, 1]
