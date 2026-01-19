from rlm_rs.models import SpanLogEntry
from rlm_rs.orchestrator.citations import DocumentText, checksum_text, make_spanrefs


def test_checksum_determinism_normalizes_unicode() -> None:
    text_nfc = "caf\u00e9"
    text_nfd = "cafe\u0301"

    checksum_nfc = checksum_text(text_nfc)
    checksum_nfd = checksum_text(text_nfd)

    assert checksum_nfc == checksum_nfd
    assert checksum_nfc.startswith("sha256:")


def test_checksum_determinism_merges_and_dedupes_spans() -> None:
    docs = [DocumentText(doc_id="doc-1", doc_index=0, text="abcdef")]
    span_log = [
        SpanLogEntry(doc_index=0, start_char=0, end_char=2),
        SpanLogEntry(doc_index=0, start_char=1, end_char=4),
        SpanLogEntry(doc_index=0, start_char=4, end_char=6),
    ]

    span_refs = make_spanrefs(
        span_log=span_log,
        documents=docs,
        tenant_id="tenant-1",
        session_id="session-1",
        merge_gap_chars=0,
    )

    assert len(span_refs) == 1
    assert span_refs[0].start_char == 0
    assert span_refs[0].end_char == 6
