from __future__ import annotations

from functools import lru_cache

from boto3.resources.base import ServiceResource
from botocore.client import BaseClient
from structlog.stdlib import BoundLogger

from rlm_rs.logging import get_logger as _get_logger
from rlm_rs.settings import Settings
from rlm_rs.storage.ddb import (
    DdbTableNames,
    build_ddb_client,
    build_ddb_resource,
    build_table_names,
)
from rlm_rs.storage.s3 import build_s3_client


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def get_s3_client() -> BaseClient:
    settings = get_settings()
    return build_s3_client(
        region=settings.aws_region,
        endpoint_url=settings.localstack_endpoint_url,
    )


@lru_cache
def get_ddb_client() -> BaseClient:
    settings = get_settings()
    return build_ddb_client(
        region=settings.aws_region,
        endpoint_url=settings.localstack_endpoint_url,
    )


@lru_cache
def get_ddb_resource() -> ServiceResource:
    settings = get_settings()
    return build_ddb_resource(
        region=settings.aws_region,
        endpoint_url=settings.localstack_endpoint_url,
    )


@lru_cache
def get_table_names() -> DdbTableNames:
    settings = get_settings()
    return build_table_names(settings.ddb_table_prefix)


def get_logger() -> BoundLogger:
    return _get_logger("rlm_rs.api")
