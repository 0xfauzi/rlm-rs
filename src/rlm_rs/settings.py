from __future__ import annotations

import json

from pydantic import AliasChoices, Field, JsonValue, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _parse_json_blob(value: JsonValue | str | None) -> JsonValue | None:
    if value is None:
        return None
    if isinstance(value, str):
        if not value.strip():
            return None
        return json.loads(value)
    return value


def _parse_optional_scalar(value: JsonValue | str | None) -> JsonValue | None:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", case_sensitive=False, extra="ignore")

    aws_region: str | None = Field(
        default=None, validation_alias=AliasChoices("AWS_REGION")
    )
    ddb_table_prefix: str | None = Field(
        default=None, validation_alias=AliasChoices("DDB_TABLE_PREFIX")
    )
    s3_bucket: str | None = Field(default=None, validation_alias=AliasChoices("S3_BUCKET"))
    localstack_endpoint_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("LOCALSTACK_ENDPOINT_URL", "AWS_ENDPOINT_URL"),
    )
    parser_service_url: str | None = Field(
        default=None, validation_alias=AliasChoices("PARSER_SERVICE_URL")
    )
    parser_service_auth_secret_arn: str | None = Field(
        default=None, validation_alias=AliasChoices("PARSER_SERVICE_AUTH_SECRET_ARN")
    )
    api_key_pepper: str | None = Field(
        default=None, validation_alias=AliasChoices("API_KEY_PEPPER")
    )
    llm_provider: str | None = Field(
        default=None, validation_alias=AliasChoices("LLM_PROVIDER")
    )
    llm_provider_secret_arn: str | None = Field(
        default=None, validation_alias=AliasChoices("LLM_PROVIDER_SECRET_ARN")
    )
    openai_api_key: str | None = Field(
        default=None, validation_alias=AliasChoices("OPENAI_API_KEY")
    )
    openai_base_url: str | None = Field(
        default=None, validation_alias=AliasChoices("OPENAI_BASE_URL")
    )
    openai_timeout_seconds: float | None = Field(
        default=None, validation_alias=AliasChoices("OPENAI_TIMEOUT_SECONDS")
    )
    openai_max_retries: int | None = Field(
        default=None, validation_alias=AliasChoices("OPENAI_MAX_RETRIES")
    )
    default_root_model: str | None = Field(
        default=None, validation_alias=AliasChoices("DEFAULT_ROOT_MODEL")
    )
    default_sub_model: str | None = Field(
        default=None, validation_alias=AliasChoices("DEFAULT_SUB_MODEL")
    )
    default_budgets_json: JsonValue | None = Field(
        default=None, validation_alias=AliasChoices("DEFAULT_BUDGETS_JSON")
    )
    default_models_json: JsonValue | None = Field(
        default=None, validation_alias=AliasChoices("DEFAULT_MODELS_JSON")
    )
    rate_limits_json: JsonValue | None = Field(
        default=None, validation_alias=AliasChoices("RATE_LIMITS_JSON")
    )
    search_backend_config: JsonValue | None = Field(
        default=None, validation_alias=AliasChoices("SEARCH_BACKEND_CONFIG")
    )
    request_size_limit_bytes: int | None = Field(
        default=None, validation_alias=AliasChoices("REQUEST_SIZE_LIMIT_BYTES")
    )
    enable_search: bool = Field(
        default=False, validation_alias=AliasChoices("ENABLE_SEARCH_DEFAULT")
    )
    enable_mcp: bool = Field(default=False, validation_alias=AliasChoices("ENABLE_MCP"))
    enable_metrics: bool = Field(default=False, validation_alias=AliasChoices("ENABLE_METRICS"))
    enable_otel_tracing: bool = Field(
        default=False, validation_alias=AliasChoices("ENABLE_OTEL_TRACING")
    )
    enable_return_trace: bool = Field(
        default=False, validation_alias=AliasChoices("ENABLE_RETURN_TRACE")
    )
    enable_trace_redaction: bool = Field(
        default=False, validation_alias=AliasChoices("ENABLE_TRACE_REDACTION")
    )

    @field_validator(
        "default_budgets_json",
        "default_models_json",
        "rate_limits_json",
        "search_backend_config",
        mode="before",
    )
    @classmethod
    def _load_json_blob(cls, value: JsonValue | str | None) -> JsonValue | None:
        return _parse_json_blob(value)

    @field_validator("openai_timeout_seconds", "openai_max_retries", mode="before")
    @classmethod
    def _load_optional_scalars(cls, value: JsonValue | str | None) -> JsonValue | None:
        return _parse_optional_scalar(value)
