from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, JsonValue


class ErrorCode(StrEnum):
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    SESSION_NOT_FOUND = "SESSION_NOT_FOUND"
    EXECUTION_NOT_FOUND = "EXECUTION_NOT_FOUND"
    SESSION_NOT_READY = "SESSION_NOT_READY"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    RATE_LIMITED = "RATE_LIMITED"
    REQUEST_TOO_LARGE = "REQUEST_TOO_LARGE"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    MAX_TURNS_EXCEEDED = "MAX_TURNS_EXCEEDED"
    STEP_TIMEOUT = "STEP_TIMEOUT"
    SANDBOX_AST_REJECTED = "SANDBOX_AST_REJECTED"
    SANDBOX_LINE_LIMIT = "SANDBOX_LINE_LIMIT"
    STATE_INVALID_TYPE = "STATE_INVALID_TYPE"
    STATE_TOO_LARGE = "STATE_TOO_LARGE"
    CHECKSUM_MISMATCH = "CHECKSUM_MISMATCH"
    S3_READ_ERROR = "S3_READ_ERROR"
    PARSER_ERROR = "PARSER_ERROR"
    LLM_PROVIDER_ERROR = "LLM_PROVIDER_ERROR"
    LAMBDA_ERROR = "LAMBDA_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"


ERROR_HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.UNAUTHORIZED: 401,
    ErrorCode.FORBIDDEN: 403,
    ErrorCode.SESSION_NOT_FOUND: 404,
    ErrorCode.EXECUTION_NOT_FOUND: 404,
    ErrorCode.SESSION_NOT_READY: 409,
    ErrorCode.SESSION_EXPIRED: 410,
    ErrorCode.VALIDATION_ERROR: 422,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.REQUEST_TOO_LARGE: 413,
    ErrorCode.BUDGET_EXCEEDED: 400,
    ErrorCode.MAX_TURNS_EXCEEDED: 400,
    ErrorCode.STEP_TIMEOUT: 400,
    ErrorCode.SANDBOX_AST_REJECTED: 400,
    ErrorCode.SANDBOX_LINE_LIMIT: 400,
    ErrorCode.STATE_INVALID_TYPE: 400,
    ErrorCode.STATE_TOO_LARGE: 400,
    ErrorCode.CHECKSUM_MISMATCH: 400,
    ErrorCode.S3_READ_ERROR: 502,
    ErrorCode.PARSER_ERROR: 502,
    ErrorCode.LLM_PROVIDER_ERROR: 502,
    ErrorCode.LAMBDA_ERROR: 500,
    ErrorCode.INTERNAL_ERROR: 500,
}


class ErrorInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: ErrorCode
    message: str
    request_id: str | None = None
    details: dict[str, JsonValue] | None = None


class ErrorEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    error: ErrorInfo


class RLMHTTPError(Exception):
    def __init__(self, status_code: int, error: ErrorEnvelope) -> None:
        super().__init__(error.error.message)
        self.status_code = status_code
        self.error = error


def raise_http_error(
    code: ErrorCode | str,
    message: str,
    details: dict[str, JsonValue] | None = None,
) -> None:
    error_code = ErrorCode(code)
    status_code = ERROR_HTTP_STATUS.get(error_code, 500)
    envelope = ErrorEnvelope(
        error=ErrorInfo(code=error_code, message=message, details=details)
    )
    raise RLMHTTPError(status_code, envelope)
