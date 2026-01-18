import io
import json
from typing import Any

from rlm_rs.models import ContextDocument, ContextManifest
from rlm_rs.sandbox.context import ContextView


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.range_calls: list[str] = []

    def put_object(self, *, Bucket: str, Key: str, Body: Any, **_kwargs: Any) -> dict[str, Any]:
        if hasattr(Body, "read"):
            Body = Body.read()
        if isinstance(Body, str):
            Body = Body.encode("utf-8")
        if not isinstance(Body, bytes):
            raise TypeError("Body must be bytes-like")
        self.objects[(Bucket, Key)] = Body
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}

    def get_object(self, *, Bucket: str, Key: str, Range: str | None = None, **_kwargs: Any) -> dict[str, Any]:
        data = self.objects[(Bucket, Key)]
        if Range:
            self.range_calls.append(Range)
            range_spec = Range.removeprefix("bytes=")
            start_str, end_str = range_spec.split("-", 1)
            start = int(start_str)
            end = int(end_str) if end_str else len(data) - 1
            if start > len(data):
                sliced = b""
            else:
                sliced = data[start : min(end + 1, len(data))]
            return {"Body": io.BytesIO(sliced)}
        return {"Body": io.BytesIO(data)}


def _build_offsets_payload(text: str, interval: int = 5) -> dict[str, Any]:
    checkpoints: list[dict[str, int]] = [{"char": 0, "byte": 0}]
    byte_offset = 0
    for index, char in enumerate(text, start=1):
        byte_offset += len(char.encode("utf-8"))
        if index % interval == 0:
            checkpoints.append({"char": index, "byte": byte_offset})
    if checkpoints[-1]["char"] != len(text):
        checkpoints.append({"char": len(text), "byte": byte_offset})
    return {
        "version": "1.0",
        "doc_id": "doc-1",
        "char_length": len(text),
        "byte_length": byte_offset,
        "encoding": "utf-8",
        "checkpoints": checkpoints,
        "checkpoint_interval": interval,
    }


def _parse_range_header(range_header: str) -> tuple[int, int]:
    range_spec = range_header.removeprefix("bytes=")
    start_str, end_str = range_spec.split("-", 1)
    return int(start_str), int(end_str)


def test_span_logging_slice_and_tag() -> None:
    text = "Alpha beta gamma delta"
    offsets_payload = _build_offsets_payload(text, interval=5)

    fake_s3 = FakeS3Client()
    bucket = "docs"
    text_key = "parsed/doc-1/text.txt"
    offsets_key = "parsed/doc-1/offsets.json"

    fake_s3.put_object(Bucket=bucket, Key=text_key, Body=text.encode("utf-8"))
    fake_s3.put_object(
        Bucket=bucket, Key=offsets_key, Body=json.dumps(offsets_payload).encode("utf-8")
    )

    manifest = ContextManifest(
        docs=[
            ContextDocument(
                doc_id="doc-1",
                doc_index=0,
                text_s3_uri=f"s3://{bucket}/{text_key}",
                offsets_s3_uri=f"s3://{bucket}/{offsets_key}",
            )
        ]
    )

    context = ContextView(manifest, s3_client=fake_s3)
    doc = context[0]

    sliced = doc[0:5]
    assert sliced == "Alpha"
    assert len(fake_s3.range_calls) == 1
    start, end = _parse_range_header(fake_s3.range_calls[0])
    assert end - start + 1 < len(text.encode("utf-8"))

    assert len(context.span_log) == 1
    entry = context.span_log[0]
    assert entry.doc_index == 0
    assert entry.start_char == 0
    assert entry.end_char == 5
    assert entry.tag is None

    tagged = doc.slice(6, 10, tag="word")
    assert tagged == "beta"
    assert len(context.span_log) == 2
    entry = context.span_log[1]
    assert entry.start_char == 6
    assert entry.end_char == 10
    assert entry.tag == "word"
