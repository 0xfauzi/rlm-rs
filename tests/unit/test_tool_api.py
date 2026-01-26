import pytest

from rlm_rs.models import LimitsSnapshot, ToolRequestsEnvelope
from rlm_rs.sandbox.tool_api import (
    ToolAPI,
    ToolAPIError,
    ToolFinal,
    ToolRequestLimitError,
    ToolYield,
    TOOL_SCHEMA_VERSION,
)


def test_tool_requests_envelope_normalization() -> None:
    tool = ToolAPI()

    tool.queue_llm("k1", "prompt", max_tokens=256)
    tool.queue_search("s1", "query")

    envelope = tool.tool_requests
    assert isinstance(envelope, ToolRequestsEnvelope)
    assert len(envelope.llm) == 1
    assert len(envelope.search) == 1
    llm_request = envelope.llm[0]
    search_request = envelope.search[0]
    assert llm_request.key == "k1"
    assert llm_request.model_hint == "sub"
    assert llm_request.temperature == 0
    assert search_request.key == "s1"
    assert search_request.k == 10


def test_queue_llm_accepts_max_output_chars_alias() -> None:
    tool = ToolAPI()

    tool.queue_llm("k1", "prompt", max_output_chars=64)

    envelope = tool.tool_requests
    assert envelope.llm[0].max_tokens == 64


def test_queue_llm_accepts_max_output_tokens_alias() -> None:
    tool = ToolAPI()

    tool.queue_llm("k1", "prompt", max_output_tokens=32)

    envelope = tool.tool_requests
    assert envelope.llm[0].max_tokens == 32


def test_queue_llm_requires_exactly_one_limit() -> None:
    tool = ToolAPI()

    with pytest.raises(ToolAPIError):
        tool.queue_llm("k1", "prompt")

    with pytest.raises(ToolAPIError):
        tool.queue_llm("k1", "prompt", max_tokens=1, max_output_tokens=2)


def test_tool_schema_exposes_signatures() -> None:
    tool = ToolAPI()

    schema = tool.schema()
    assert schema["version"] == TOOL_SCHEMA_VERSION
    assert "signature_text" in schema
    tools = schema["tools"]
    assert isinstance(tools, list)
    names = {item.get("name") for item in tools if isinstance(item, dict)}
    assert names == {"queue_llm", "queue_search", "YIELD", "FINAL"}


def test_tool_request_limit_enforced() -> None:
    tool = ToolAPI(limits=LimitsSnapshot(max_tool_requests_per_step=1))
    tool.queue_llm("k1", "prompt", max_tokens=32)

    with pytest.raises(ToolRequestLimitError) as excinfo:
        tool.queue_search("s1", "query")
    assert excinfo.value.limit == 1


def test_tool_yield_and_final_raise() -> None:
    tool = ToolAPI()

    with pytest.raises(ToolYield) as excinfo:
        tool.YIELD("waiting for k1")
    assert excinfo.value.reason == "waiting for k1"

    with pytest.raises(ToolFinal) as excinfo:
        tool.FINAL("done")
    assert excinfo.value.answer == "done"
