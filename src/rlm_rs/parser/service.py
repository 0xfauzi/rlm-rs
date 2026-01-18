from __future__ import annotations

import hashlib
import io
import os
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from botocore.client import BaseClient
from botocore.exceptions import ClientError
from fastapi import FastAPI

from rlm_rs.settings import Settings
from rlm_rs.storage.s3 import build_s3_client, put_bytes, put_json

from .models import (
    ParseErrorInfo,
    ParseFailure,
    ParseOutputs,
    ParseRequest,
    ParseResponse,
    ParseStats,
    ParseSuccess,
)


PARSER_VERSION = "parser-0.1.0"
CHECKPOINT_INTERVAL = 10_000

app = FastAPI(title="RLM Parser Service")


@dataclass(frozen=True)
class ParsedText:
    text: str
    pages: list[str]
    warnings: list[str]


class ParserServiceError(Exception):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details


def _split_s3_uri(uri: str) -> tuple[str, str]:
    parsed = urlparse(uri)
    if parsed.scheme != "s3" or not parsed.netloc:
        raise ParserServiceError("INVALID_URI", "Invalid S3 URI", {"s3_uri": uri})
    return parsed.netloc, parsed.path.lstrip("/")


def _normalize_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _extract_pdf_text(raw_bytes: bytes) -> list[str]:
    try:
        from pdfminer.high_level import extract_text
    except ImportError as exc:
        raise ParserServiceError(
            "UNSUPPORTED_FORMAT",
            "PDF parsing requires pdfminer.six",
            {"error": str(exc)},
        ) from exc

    raw_text = extract_text(io.BytesIO(raw_bytes)) or ""
    pages = raw_text.split("\f")
    if pages and pages[-1] == "":
        pages = pages[:-1]
    if not pages:
        pages = [raw_text]
    return pages


def _extract_text(raw_bytes: bytes, *, key: str, content_type: str | None) -> ParsedText:
    warnings: list[str] = []
    key_lower = key.lower()
    is_pdf = key_lower.endswith(".pdf") or content_type == "application/pdf"
    is_text = bool(content_type and content_type.startswith("text/")) or key_lower.endswith(
        (".txt", ".md", ".json", ".csv", ".tsv", ".log")
    )

    if is_pdf:
        page_texts = _extract_pdf_text(raw_bytes)
        normalized_pages = [_normalize_text(page) for page in page_texts]
        if len(normalized_pages) > 1:
            normalized_pages = [
                page if index == len(normalized_pages) - 1 or page.endswith("\n") else f"{page}\n"
                for index, page in enumerate(normalized_pages)
            ]
        text = "".join(normalized_pages)
        if not text:
            warnings.append("PDF extraction produced empty text")
        return ParsedText(text=text, pages=normalized_pages, warnings=warnings)

    if not is_text:
        warnings.append("Unknown content type; treating as UTF-8 text")

    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        text = raw_bytes.decode("utf-8", errors="replace")
        warnings.append("Non-UTF-8 bytes replaced during decode")

    text = _normalize_text(text)
    return ParsedText(text=text, pages=[text], warnings=warnings)


def _build_page_spans(pages: list[str]) -> list[dict[str, int]]:
    if not pages:
        pages = [""]
    spans: list[dict[str, int]] = []
    offset = 0
    for page_num, page_text in enumerate(pages, start=1):
        end = offset + len(page_text)
        spans.append({"page_num": page_num, "start_char": offset, "end_char": end})
        offset = end
    return spans


def _build_checkpoints(text: str, interval: int) -> tuple[list[dict[str, int]], int]:
    checkpoints: list[dict[str, int]] = [{"char": 0, "byte": 0}]
    if interval <= 0:
        interval = max(len(text), 1)
    byte_offset = 0
    for index, char in enumerate(text, start=1):
        byte_offset += len(char.encode("utf-8"))
        if index % interval == 0:
            checkpoints.append({"char": index, "byte": byte_offset})
    if checkpoints[-1]["char"] != len(text):
        checkpoints.append({"char": len(text), "byte": byte_offset})
    return checkpoints, byte_offset


def _doc_id_from_prefix(prefix_key: str) -> str:
    trimmed = prefix_key.rstrip("/")
    if not trimmed:
        return "unknown"
    return os.path.basename(trimmed)


def parse_to_s3(
    request: ParseRequest,
    *,
    s3_client: BaseClient | None = None,
    settings: Settings | None = None,
    parser_version: str = PARSER_VERSION,
) -> ParseSuccess:
    if s3_client is None:
        resolved_settings = settings or Settings()
        s3_client = build_s3_client(
            region=resolved_settings.aws_region,
            endpoint_url=resolved_settings.localstack_endpoint_url,
        )

    start_time = time.perf_counter()
    source_bucket, source_key = _split_s3_uri(request.source.s3_uri)
    try:
        params: dict[str, Any] = {"Bucket": source_bucket, "Key": source_key}
        if request.source.s3_version_id:
            params["VersionId"] = request.source.s3_version_id
        response = s3_client.get_object(**params)
    except ClientError as exc:
        raise ParserServiceError(
            "S3_READ_ERROR",
            "Failed to read source object",
            {"s3_uri": request.source.s3_uri, "error": str(exc)},
        ) from exc

    raw_bytes = response["Body"].read()
    content_type = response.get("ContentType")
    parsed = _extract_text(raw_bytes, key=source_key, content_type=content_type)

    output_bucket, output_prefix = _split_s3_uri(request.output.s3_prefix)
    output_prefix = output_prefix.rstrip("/")
    if output_prefix:
        output_prefix = f"{output_prefix}/"

    text_bytes = parsed.text.encode("utf-8")
    text_key = f"{output_prefix}text.txt"
    put_bytes(s3_client, output_bucket, text_key, text_bytes, content_type="text/plain")

    doc_id = _doc_id_from_prefix(output_prefix)
    page_spans = _build_page_spans(parsed.pages)
    meta_payload = {
        "version": "1.0",
        "doc_id": doc_id,
        "parser_version": parser_version,
        "source": {
            "s3_uri": request.source.s3_uri,
            "s3_version_id": request.source.s3_version_id,
            "s3_etag": request.source.s3_etag,
        },
        "structure": {
            "type": "document",
            "title": os.path.basename(source_key) or None,
            "children": [],
        },
        "pages": page_spans,
        "tables": [],
    }
    meta_key = f"{output_prefix}meta.json"
    put_json(s3_client, output_bucket, meta_key, meta_payload)

    checkpoints, byte_length = _build_checkpoints(parsed.text, CHECKPOINT_INTERVAL)
    offsets_payload = {
        "version": "1.0",
        "doc_id": doc_id,
        "char_length": len(parsed.text),
        "byte_length": byte_length,
        "encoding": "utf-8",
        "checkpoints": checkpoints,
        "checkpoint_interval": CHECKPOINT_INTERVAL,
        "parser_version": parser_version,
        "source": {
            "s3_uri": request.source.s3_uri,
            "s3_version_id": request.source.s3_version_id,
            "s3_etag": request.source.s3_etag,
        },
    }
    offsets_key = f"{output_prefix}offsets.json"
    put_json(s3_client, output_bucket, offsets_key, offsets_payload)

    checksum = hashlib.sha256(text_bytes).hexdigest()
    duration_ms = int((time.perf_counter() - start_time) * 1000)

    outputs = ParseOutputs(
        text_s3_uri=f"s3://{output_bucket}/{text_key}",
        meta_s3_uri=f"s3://{output_bucket}/{meta_key}",
        offsets_s3_uri=f"s3://{output_bucket}/{offsets_key}",
    )
    stats = ParseStats(
        char_length=len(parsed.text),
        byte_length=byte_length,
        page_count=len(page_spans),
        parse_duration_ms=duration_ms,
    )
    return ParseSuccess(
        request_id=request.request_id,
        outputs=outputs,
        stats=stats,
        parser_version=parser_version,
        text_checksum=f"sha256:{checksum}",
        warnings=parsed.warnings or None,
    )


@app.post("/parse", response_model=ParseResponse)
def parse_handler(request: ParseRequest) -> ParseResponse:
    try:
        return parse_to_s3(request)
    except ParserServiceError as exc:
        return ParseFailure(
            request_id=request.request_id,
            error=ParseErrorInfo(code=exc.code, message=str(exc), details=exc.details),
            parser_version=PARSER_VERSION,
        )
    except Exception as exc:  # pragma: no cover - defensive fallback
        return ParseFailure(
            request_id=request.request_id,
            error=ParseErrorInfo(
                code="PARSER_ERROR",
                message="Parser failed",
                details={"error": str(exc)},
            ),
            parser_version=PARSER_VERSION,
        )
