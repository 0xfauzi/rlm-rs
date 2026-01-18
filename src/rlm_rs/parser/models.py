from __future__ import annotations

from typing import Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, JsonValue


class ParserBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ParseSource(ParserBaseModel):
    s3_uri: str
    s3_version_id: str | None = None
    s3_etag: str | None = None


class ParseOutput(ParserBaseModel):
    s3_prefix: str


class ParseOptions(ParserBaseModel):
    extract_structure: bool | None = None
    ocr_enabled: bool | None = None
    language_hint: str | None = None
    timeout_seconds: int | None = None


class ParseRequest(ParserBaseModel):
    request_id: str
    source: ParseSource
    output: ParseOutput
    options: ParseOptions | None = None


class ParseOutputs(ParserBaseModel):
    text_s3_uri: str
    meta_s3_uri: str
    offsets_s3_uri: str


class ParseStats(ParserBaseModel):
    char_length: int
    byte_length: int
    page_count: int
    parse_duration_ms: int


class ParseErrorInfo(ParserBaseModel):
    code: str
    message: str
    details: dict[str, JsonValue] | None = None


class ParseSuccess(ParserBaseModel):
    request_id: str
    status: Literal["success"] = "success"
    outputs: ParseOutputs
    stats: ParseStats
    parser_version: str
    text_checksum: str
    warnings: list[str] | None = None


class ParseFailure(ParserBaseModel):
    request_id: str
    status: Literal["failed"] = "failed"
    error: ParseErrorInfo
    parser_version: str


ParseResponse: TypeAlias = ParseSuccess | ParseFailure
