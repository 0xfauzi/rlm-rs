from rlm_rs.models import (
    CreateSessionRequest,
    StepEvent,
    StepResult,
    ToolRequestsEnvelope,
)


def test_step_schema_titles() -> None:
    assert StepEvent.model_json_schema()["title"] == "StepEvent"
    assert StepResult.model_json_schema()["title"] == "StepResult"


def test_tool_requests_envelope_defaults() -> None:
    envelope = ToolRequestsEnvelope()
    assert envelope.llm == []
    assert envelope.search == []


def test_create_session_request_roundtrip() -> None:
    payload = {
        "ttl_minutes": 120,
        "docs": [
            {
                "source_name": "contract.pdf",
                "mime_type": "application/pdf",
                "raw_s3_uri": "s3://bucket/raw/contract.pdf",
                "raw_s3_version_id": "optional",
                "raw_s3_etag": "optional",
            }
        ],
        "options": {"enable_search": False, "readiness_mode": "LAX"},
        "models_default": {"root_model": "gpt-5", "sub_model": "gpt-5-mini"},
        "budgets_default": {"max_turns": 20, "max_total_seconds": 180, "max_llm_subcalls": 50},
    }

    parsed = CreateSessionRequest.model_validate(payload)
    assert parsed.docs[0].raw_s3_uri == "s3://bucket/raw/contract.pdf"
    assert parsed.options is not None
    assert parsed.options.readiness_mode == "LAX"
