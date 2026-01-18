import io
import json
from collections import deque

from rlm_rs.orchestrator.providers import (
    OPENAI_PROVIDER_NAME,
    OpenAIProvider,
    build_llm_cache_key,
)


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], dict[str, object]] = {}

    def put_object(
        self,
        *,
        Bucket: str,
        Key: str,
        Body: bytes,
        **kwargs: object,
    ) -> dict[str, str]:
        if isinstance(Body, str):
            Body = Body.encode("utf-8")
        self.objects[(Bucket, Key)] = {
            "Body": Body,
            "ContentType": kwargs.get("ContentType"),
            "ContentEncoding": kwargs.get("ContentEncoding"),
        }
        return {"ETag": "fake"}

    def get_object(
        self,
        *,
        Bucket: str,
        Key: str,
        **kwargs: object,
    ) -> dict[str, object]:
        if (Bucket, Key) not in self.objects:
            raise KeyError(f"Missing object {Bucket}/{Key}")
        stored = self.objects[(Bucket, Key)]
        return {
            "Body": io.BytesIO(stored["Body"]),
            "ContentType": stored.get("ContentType"),
        }


class FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = FakeMessage(content)


class FakeChatCompletion:
    def __init__(self, content: str) -> None:
        self.choices = [FakeChoice(content)]
        self.id = "resp-1"


class FakeChatCompletions:
    def __init__(self, responses: list[str]) -> None:
        self._responses = deque(responses)
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> FakeChatCompletion:
        self.calls.append(kwargs)
        if self._responses:
            content = self._responses.popleft()
        else:
            content = ""
        return FakeChatCompletion(content)


class FakeChat:
    def __init__(self, responses: list[str]) -> None:
        self.completions = FakeChatCompletions(responses)


class FakeOpenAIClient:
    def __init__(self, responses: list[str]) -> None:
        self.chat = FakeChat(responses)


def test_openai_provider_s3_cache_hits() -> None:
    fake_s3 = FakeS3Client()
    fake_client = FakeOpenAIClient(["cached-text"])
    provider = OpenAIProvider(
        client=fake_client,
        s3_client=fake_s3,
        s3_bucket="cache-bucket",
    )

    tenant_id = "tenant-1"
    prompt = "Hello world"
    model = "gpt-test"

    first = provider.complete_subcall(
        prompt,
        model,
        max_tokens=10,
        temperature=None,
        tenant_id=tenant_id,
    )
    second = provider.complete_subcall(
        prompt,
        model,
        max_tokens=10,
        temperature=None,
        tenant_id=tenant_id,
    )

    assert first == "cached-text"
    assert second == "cached-text"
    assert len(fake_client.chat.completions.calls) == 1

    key = build_llm_cache_key(
        tenant_id=tenant_id,
        provider=OPENAI_PROVIDER_NAME,
        model=model,
        max_tokens=10,
        temperature=0.0,
        prompt=prompt,
    )
    assert ("cache-bucket", key) in fake_s3.objects

    stored = json.loads(fake_s3.objects[("cache-bucket", key)]["Body"])
    assert stored["provider"] == OPENAI_PROVIDER_NAME
    assert stored["response"]["text"] == "cached-text"
