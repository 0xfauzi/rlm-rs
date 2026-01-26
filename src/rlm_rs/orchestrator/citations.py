from __future__ import annotations

import hashlib
import unicodedata
from dataclasses import dataclass
from typing import Iterable, Sequence

from rlm_rs.models import SpanLogEntry, SpanRef

CHECKSUM_PREFIX = "sha256:"


@dataclass(frozen=True)
class DocumentText:
    doc_id: str
    doc_index: int
    text: str


@dataclass(frozen=True)
class SpanRange:
    doc_index: int
    start_char: int
    end_char: int


def normalize_text(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def checksum_text(text: str) -> str:
    normalized = normalize_text(text)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"{CHECKSUM_PREFIX}{digest}"


def merge_span_log(
    span_log: Iterable[SpanLogEntry],
    *,
    merge_gap_chars: int | None = None,
) -> list[SpanRange]:
    gap = 0 if merge_gap_chars is None else merge_gap_chars
    if gap < 0:
        raise ValueError("merge_gap_chars must be >= 0")

    spans_by_doc: dict[int, list[SpanRange]] = {}
    for span in span_log:
        if span.start_char < 0 or span.end_char < 0:
            raise ValueError("Span bounds must be non-negative")
        if span.end_char < span.start_char:
            raise ValueError("Span end_char precedes start_char")
        spans_by_doc.setdefault(span.doc_index, []).append(
            SpanRange(
                doc_index=span.doc_index,
                start_char=span.start_char,
                end_char=span.end_char,
            )
        )

    merged: list[SpanRange] = []
    for doc_index in sorted(spans_by_doc):
        spans = sorted(
            spans_by_doc[doc_index],
            key=lambda item: (item.start_char, item.end_char),
        )
        if not spans:
            continue
        current_start = spans[0].start_char
        current_end = spans[0].end_char
        for span in spans[1:]:
            if span.start_char <= current_end + gap:
                current_end = max(current_end, span.end_char)
            else:
                merged.append(
                    SpanRange(
                        doc_index=doc_index,
                        start_char=current_start,
                        end_char=current_end,
                    )
                )
                current_start = span.start_char
                current_end = span.end_char
        merged.append(
            SpanRange(doc_index=doc_index, start_char=current_start, end_char=current_end)
        )

    return merged


def _validate_span_bounds(text: str, start_char: int, end_char: int) -> None:
    if start_char < 0 or end_char < 0:
        raise ValueError("Span bounds must be non-negative")
    if end_char < start_char:
        raise ValueError("Span end_char precedes start_char")
    if end_char > len(text):
        raise ValueError("Span end_char exceeds text length")


def build_span_ref(
    *,
    tenant_id: str,
    session_id: str,
    doc_id: str,
    doc_index: int,
    start_char: int,
    end_char: int,
    text: str,
) -> SpanRef:
    _validate_span_bounds(text, start_char, end_char)
    checksum = checksum_text(text[start_char:end_char])
    return SpanRef(
        tenant_id=tenant_id,
        session_id=session_id,
        doc_id=doc_id,
        doc_index=doc_index,
        start_char=start_char,
        end_char=end_char,
        checksum=checksum,
    )


def make_spanrefs(
    *,
    span_log: Iterable[SpanLogEntry],
    documents: Sequence[DocumentText],
    tenant_id: str,
    session_id: str,
    merge_gap_chars: int | None = None,
) -> list[SpanRef]:
    doc_lookup = {doc.doc_index: doc for doc in documents}
    filtered = [
        span for span in span_log if not (span.tag or "").startswith("scan:")
    ]
    merged_spans = merge_span_log(filtered, merge_gap_chars=merge_gap_chars)

    span_refs: list[SpanRef] = []
    for span in merged_spans:
        document = doc_lookup.get(span.doc_index)
        if document is None:
            raise KeyError(f"Missing document for doc_index={span.doc_index}")
        span_refs.append(
            build_span_ref(
                tenant_id=tenant_id,
                session_id=session_id,
                doc_id=document.doc_id,
                doc_index=span.doc_index,
                start_char=span.start_char,
                end_char=span.end_char,
                text=document.text,
            )
        )

    return span_refs
