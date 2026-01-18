import io
import json

from rlm_rs.parser.models import ParseOutput, ParseRequest, ParseSource
from rlm_rs.parser.service import parse_to_s3


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], dict[str, object]] = {}

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, **kwargs: object) -> dict[str, str]:
        if isinstance(Body, str):
            Body = Body.encode("utf-8")
        self.objects[(Bucket, Key)] = {
            "Body": Body,
            "ContentType": kwargs.get("ContentType"),
            "ContentEncoding": kwargs.get("ContentEncoding"),
        }
        return {"ETag": "fake"}

    def get_object(self, *, Bucket: str, Key: str, **kwargs: object) -> dict[str, object]:
        if (Bucket, Key) not in self.objects:
            raise KeyError(f"Missing object {Bucket}/{Key}")
        stored = self.objects[(Bucket, Key)]
        return {
            "Body": io.BytesIO(stored["Body"]),
            "ContentType": stored.get("ContentType"),
        }


def test_parse_to_s3_deterministic_outputs() -> None:
    fake_s3 = FakeS3Client()
    raw_key = "raw/input.txt"
    raw_text = "Hello\r\nWorld\r"
    fake_s3.put_object(
        Bucket="demo",
        Key=raw_key,
        Body=raw_text.encode("utf-8"),
        ContentType="text/plain",
    )

    request = ParseRequest(
        request_id="req-1",
        source=ParseSource(s3_uri=f"s3://demo/{raw_key}"),
        output=ParseOutput(s3_prefix="s3://demo/parsed/tenant/session/doc"),
    )

    first = parse_to_s3(request, s3_client=fake_s3)
    text_key = "parsed/tenant/session/doc/text.txt"
    meta_key = "parsed/tenant/session/doc/meta.json"
    offsets_key = "parsed/tenant/session/doc/offsets.json"

    text_bytes_first = fake_s3.objects[("demo", text_key)]["Body"]
    meta_bytes_first = fake_s3.objects[("demo", meta_key)]["Body"]
    offsets_bytes_first = fake_s3.objects[("demo", offsets_key)]["Body"]

    second = parse_to_s3(request, s3_client=fake_s3)
    text_bytes_second = fake_s3.objects[("demo", text_key)]["Body"]
    meta_bytes_second = fake_s3.objects[("demo", meta_key)]["Body"]
    offsets_bytes_second = fake_s3.objects[("demo", offsets_key)]["Body"]

    assert text_bytes_first == text_bytes_second
    assert meta_bytes_first == meta_bytes_second
    assert offsets_bytes_first == offsets_bytes_second
    assert first.text_checksum
    assert first.text_checksum == second.text_checksum

    text_value = text_bytes_first.decode("utf-8")
    assert text_value == "Hello\nWorld\n"

    meta_payload = json.loads(meta_bytes_first)
    assert meta_payload["doc_id"] == "doc"
    assert meta_payload["pages"][0]["start_char"] == 0

    offsets_payload = json.loads(offsets_bytes_first)
    assert offsets_payload["char_length"] == len(text_value)
    assert offsets_payload["checkpoints"][0] == {"char": 0, "byte": 0}
