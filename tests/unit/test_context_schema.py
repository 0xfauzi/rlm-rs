from rlm_rs.models import SpanLogEntry
from rlm_rs.orchestrator.citations import DocumentText
from rlm_rs.orchestrator.worker import ContextSpanEntry, _build_contexts_and_citations


def test_context_schema_sequence_and_order() -> None:
    text = "Alpha beta gamma delta"
    document = DocumentText(
        doc_id="doc-1",
        doc_index=0,
        text=text,
        source_name="sample.txt",
        mime_type="text/plain",
    )
    span_entries = [
        ContextSpanEntry(
            turn_index=0,
            span_index=0,
            span=SpanLogEntry(doc_index=0, start_char=0, end_char=5, tag="context"),
        ),
        ContextSpanEntry(
            turn_index=0,
            span_index=1,
            span=SpanLogEntry(doc_index=0, start_char=6, end_char=10, tag="context:foo"),
        ),
        ContextSpanEntry(
            turn_index=0,
            span_index=2,
            span=SpanLogEntry(doc_index=0, start_char=11, end_char=16, tag="note"),
        ),
        ContextSpanEntry(
            turn_index=1,
            span_index=0,
            span=SpanLogEntry(doc_index=0, start_char=0, end_char=5, tag="context"),
        ),
        ContextSpanEntry(
            turn_index=1,
            span_index=1,
            span=SpanLogEntry(doc_index=0, start_char=17, end_char=22, tag="context"),
        ),
    ]

    contexts, citations = _build_contexts_and_citations(
        span_log=span_entries,
        documents=[document],
        tenant_id="tenant-a",
        session_id="session-a",
    )

    assert [item["text"] for item in contexts] == ["Alpha", "beta", "delta"]
    assert [item["sequence_index"] for item in contexts] == [0, 1, 2]
    assert [item["turn_index"] for item in contexts] == [0, 0, 1]
    assert [item["span_index"] for item in contexts] == [0, 1, 1]
    assert [item["tag"] for item in contexts] == ["context", "context:foo", "context"]
    assert [item["text_char_length"] for item in contexts] == [5, 4, 5]
    assert all(item["source_name"] == "sample.txt" for item in contexts)
    assert all(item["mime_type"] == "text/plain" for item in contexts)

    assert len(citations) == len(contexts)
    for context_item, citation in zip(contexts, citations, strict=True):
        assert context_item["ref"]["checksum"] == citation["checksum"]
