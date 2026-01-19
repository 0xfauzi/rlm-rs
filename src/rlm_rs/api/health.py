from __future__ import annotations

from dataclasses import asdict

from botocore.client import BaseClient
from botocore.exceptions import ClientError
from fastapi import APIRouter, Depends, HTTPException, status
from structlog.stdlib import BoundLogger

from rlm_rs.api.dependencies import (
    get_ddb_client,
    get_logger,
    get_s3_client,
    get_settings,
    get_table_names,
)
from rlm_rs.models import HealthResponse
from rlm_rs.settings import Settings
from rlm_rs.storage.ddb import DdbTableNames


router = APIRouter()


def _format_error(error: Exception) -> str:
    message = str(error)
    if message:
        return f"{error.__class__.__name__}: {message}"
    return error.__class__.__name__


def _check_s3(settings: Settings, s3_client: BaseClient) -> str | None:
    if not settings.s3_bucket:
        return "S3_BUCKET is not configured"
    try:
        s3_client.head_bucket(Bucket=settings.s3_bucket)
    except Exception as exc:  # pragma: no cover - dependent on AWS/localstack
        return _format_error(exc)
    return None


def _check_ddb_tables(ddb_client: BaseClient, table_names: DdbTableNames) -> str | None:
    missing_tables: list[str] = []
    for name in asdict(table_names).values():
        try:
            ddb_client.describe_table(TableName=name)
        except ClientError as exc:  # pragma: no cover - dependent on AWS/localstack
            if exc.response.get("Error", {}).get("Code") == "ResourceNotFoundException":
                missing_tables.append(name)
                continue
            return _format_error(exc)
        except Exception as exc:  # pragma: no cover - dependent on AWS/localstack
            return _format_error(exc)
    if missing_tables:
        return f"Missing tables: {', '.join(missing_tables)}"
    return None


@router.get("/health/live", response_model=HealthResponse)
def health_live(logger: BoundLogger = Depends(get_logger)) -> HealthResponse:
    logger.info("health.live")
    return HealthResponse(status="ok")


@router.get("/health/ready", response_model=HealthResponse)
def health_ready(
    settings: Settings = Depends(get_settings),
    s3_client: BaseClient = Depends(get_s3_client),
    ddb_client: BaseClient = Depends(get_ddb_client),
    table_names: DdbTableNames = Depends(get_table_names),
    logger: BoundLogger = Depends(get_logger),
) -> HealthResponse:
    errors: dict[str, str] = {}
    s3_error = _check_s3(settings, s3_client)
    if s3_error:
        errors["s3"] = s3_error

    ddb_error = _check_ddb_tables(ddb_client, table_names)
    if ddb_error:
        errors["dynamodb"] = ddb_error

    if errors:
        logger.warning("health.ready.failed", errors=errors)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "not_ready", "errors": errors},
        )

    logger.info("health.ready")
    return HealthResponse(status="ok")
