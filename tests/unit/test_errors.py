import json

import pytest

from rlm_rs.errors import (
    ErrorCode,
    ErrorEnvelope,
    ErrorInfo,
    RLMHTTPError,
    raise_http_error,
)


def test_error_envelope_json_roundtrip() -> None:
    details = {"field": "name", "reason": "missing"}
    envelope = ErrorEnvelope(
        error=ErrorInfo(
            code=ErrorCode.VALIDATION_ERROR,
            message="Invalid request",
            details=details,
        )
    )

    payload = json.loads(envelope.model_dump_json())
    assert payload["error"]["code"] == "VALIDATION_ERROR"
    assert payload["error"]["message"] == "Invalid request"
    assert payload["error"]["details"] == details


def test_raise_http_error_includes_details() -> None:
    details = {"missing": ["session_id"]}

    with pytest.raises(RLMHTTPError) as exc:
        raise_http_error(ErrorCode.VALIDATION_ERROR, "Missing fields", details)

    err = exc.value
    assert err.status_code == 422
    assert err.error.error.code == ErrorCode.VALIDATION_ERROR
    assert err.error.error.message == "Missing fields"
    assert err.error.error.details == details
