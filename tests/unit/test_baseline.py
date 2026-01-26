import io

from rlm_rs.orchestrator.baseline import (
    BaselineCheckResult,
    build_baseline_prompt,
    prepare_baseline_prompt,
)
from rlm_rs.settings import Settings


class FakeS3Client:
    def __init__(self, objects: dict[tuple[str, str], bytes] | None = None) -> None:
        self.objects = objects or {}

    def get_object(self, *, Bucket: str, Key: str, **kwargs: object) -> dict[str, object]:
        payload = self.objects.get((Bucket, Key))
        if payload is None:
            raise KeyError(f"Missing object {Bucket}/{Key}")
        return {"Body": io.BytesIO(payload)}


class FakeUsage:
    def __init__(self, input_tokens: int) -> None:
        self.input_tokens = input_tokens


class FakeResponse:
    def __init__(self, input_tokens: int) -> None:
        self.usage = FakeUsage(input_tokens)


class FakeResponses:
    def __init__(self, input_tokens: int) -> None:
        self._input_tokens = input_tokens
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> FakeResponse:
        self.calls.append(kwargs)
        return FakeResponse(self._input_tokens)


class FakeChatUsage:
    def __init__(self, prompt_tokens: int) -> None:
        self.prompt_tokens = prompt_tokens


class FakeChatResponse:
    def __init__(self, prompt_tokens: int) -> None:
        self.usage = FakeChatUsage(prompt_tokens)


class FakeChatCompletions:
    def __init__(self, prompt_tokens: int) -> None:
        self._prompt_tokens = prompt_tokens
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> FakeChatResponse:
        self.calls.append(kwargs)
        return FakeChatResponse(self._prompt_tokens)


class FakeChat:
    def __init__(self, prompt_tokens: int) -> None:
        self.completions = FakeChatCompletions(prompt_tokens)


class FakeOpenAIClient:
    def __init__(self, input_tokens: int) -> None:
        self.responses = FakeResponses(input_tokens)
        self.chat = FakeChat(input_tokens)


class OutputLimitError(Exception):
    pass


class ProbeChatCompletions:
    def __init__(self, prompt_tokens: int) -> None:
        self._prompt_tokens = prompt_tokens
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> FakeChatResponse:
        self.calls.append(kwargs)
        max_tokens = kwargs.get("max_completion_tokens") or kwargs.get("max_tokens")
        if max_tokens == 1:
            raise OutputLimitError(
                "Could not finish the message because max_tokens or model output limit was reached."
            )
        return FakeChatResponse(self._prompt_tokens)


class ProbeChat:
    def __init__(self, prompt_tokens: int) -> None:
        self.completions = ProbeChatCompletions(prompt_tokens)


class ProbeOpenAIClient:
    def __init__(self, prompt_tokens: int) -> None:
        self.chat = ProbeChat(prompt_tokens)
        self.responses = FakeResponses(prompt_tokens)


class FailingResponses:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> FakeResponse:
        self.calls.append(kwargs)
        raise OutputLimitError(
            "Could not finish the message because max_tokens or model output limit was reached."
        )


class FailingChatCompletions:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> FakeChatResponse:
        self.calls.append(kwargs)
        raise OutputLimitError(
            "Could not finish the message because max_tokens or model output limit was reached."
        )


class FailingChat:
    def __init__(self) -> None:
        self.completions = FailingChatCompletions()


class FailingOpenAIClient:
    def __init__(self) -> None:
        self.chat = FailingChat()
        self.responses = FailingResponses()


def test_build_baseline_prompt_concatenates_in_doc_index_order() -> None:
    s3_client = FakeS3Client(
        {
            ("bucket", "doc-0.txt"): b"Alpha",
            ("bucket", "doc-1.txt"): b"Beta",
        }
    )
    documents = [
        {
            "doc_index": 1,
            "ingest_status": "PARSED",
            "text_s3_uri": "s3://bucket/doc-1.txt",
        },
        {
            "doc_index": 0,
            "ingest_status": "PARSED",
            "text_s3_uri": "s3://bucket/doc-0.txt",
        },
    ]

    prompt = build_baseline_prompt(documents, s3_client)

    assert prompt == "Alpha\n\nBeta"


def test_prepare_baseline_prompt_skips_runtime_mode() -> None:
    result = prepare_baseline_prompt(
        mode="RUNTIME",
        model="gpt-5",
        question="What is the policy?",
        documents=[],
        s3_client=FakeS3Client(),
        settings=Settings(),
        openai_client=FakeOpenAIClient(1),
    )

    assert isinstance(result, BaselineCheckResult)
    assert result.skip_reason == "RUNTIME_MODE"


def test_prepare_baseline_prompt_skips_missing_parsed_text() -> None:
    documents = [
        {
            "doc_index": 0,
            "ingest_status": "PARSING",
            "text_s3_uri": "s3://bucket/doc-0.txt",
        }
    ]

    result = prepare_baseline_prompt(
        mode="ANSWERER",
        model="gpt-5",
        question="What is the policy?",
        documents=documents,
        s3_client=FakeS3Client(),
        settings=Settings(),
        openai_client=FakeOpenAIClient(1),
    )

    assert result.skip_reason == "MISSING_PARSED_TEXT"


def test_prepare_baseline_prompt_accepts_indexed_status(monkeypatch) -> None:
    monkeypatch.setenv("MODEL_CONTEXT_WINDOWS_JSON", '{"gpt-5": 5}')
    documents = [
        {
            "doc_index": 0,
            "ingest_status": "INDEXED",
            "text_s3_uri": "s3://bucket/doc-0.txt",
        }
    ]
    s3_client = FakeS3Client({("bucket", "doc-0.txt"): b"Alpha"})
    openai_client = FakeOpenAIClient(2)

    result = prepare_baseline_prompt(
        mode="ANSWERER",
        model="gpt-5",
        question="What is the policy?",
        documents=documents,
        s3_client=s3_client,
        settings=Settings(),
        openai_client=openai_client,
    )

    assert result.skip_reason is None
    assert result.prompt == "Alpha\n\nQuestion: What is the policy?\nAnswer:"


def test_prepare_baseline_prompt_skips_unknown_context_window() -> None:
    documents = [
        {
            "doc_index": 0,
            "ingest_status": "PARSED",
            "text_s3_uri": "s3://bucket/doc-0.txt",
        }
    ]

    result = prepare_baseline_prompt(
        mode="ANSWERER",
        model="unknown-model",
        question="What is the policy?",
        documents=documents,
        s3_client=FakeS3Client(),
        settings=Settings(),
        openai_client=FakeOpenAIClient(1),
    )

    assert result.skip_reason == "UNKNOWN_CONTEXT_WINDOW"
    assert result.input_tokens is None


def test_prepare_baseline_prompt_skips_context_window_exceeded(monkeypatch) -> None:
    monkeypatch.setenv("MODEL_CONTEXT_WINDOWS_JSON", '{"gpt-5": 2}')
    documents = [
        {
            "doc_index": 0,
            "ingest_status": "PARSED",
            "text_s3_uri": "s3://bucket/doc-0.txt",
        }
    ]
    s3_client = FakeS3Client({("bucket", "doc-0.txt"): b"Alpha"})
    openai_client = FakeOpenAIClient(3)

    result = prepare_baseline_prompt(
        mode="ANSWERER",
        model="gpt-5",
        question="What is the policy?",
        documents=documents,
        s3_client=s3_client,
        settings=Settings(),
        openai_client=openai_client,
    )

    assert result.skip_reason == "CONTEXT_WINDOW_EXCEEDED"
    assert result.context_window == 2
    assert result.input_tokens == 3


def test_prepare_baseline_prompt_accepts_fit(monkeypatch) -> None:
    monkeypatch.setenv("MODEL_CONTEXT_WINDOWS_JSON", '{"gpt-5": 5}')
    documents = [
        {
            "doc_index": 0,
            "ingest_status": "PARSED",
            "text_s3_uri": "s3://bucket/doc-0.txt",
        }
    ]
    s3_client = FakeS3Client({("bucket", "doc-0.txt"): b"Alpha"})
    openai_client = FakeOpenAIClient(2)

    result = prepare_baseline_prompt(
        mode="ANSWERER",
        model="gpt-5",
        question="What is the policy?",
        documents=documents,
        s3_client=s3_client,
        settings=Settings(),
        openai_client=openai_client,
    )

    assert result.skip_reason is None
    assert result.prompt == "Alpha\n\nQuestion: What is the policy?\nAnswer:"
    assert result.context_window == 5
    assert result.input_tokens == 2


def test_prepare_baseline_prompt_retries_token_probe_on_output_limit(monkeypatch) -> None:
    monkeypatch.setenv("MODEL_CONTEXT_WINDOWS_JSON", '{"gpt-5": 5}')
    documents = [
        {
            "doc_index": 0,
            "ingest_status": "PARSED",
            "text_s3_uri": "s3://bucket/doc-0.txt",
        }
    ]
    s3_client = FakeS3Client({("bucket", "doc-0.txt"): b"Alpha"})
    openai_client = ProbeOpenAIClient(2)

    result = prepare_baseline_prompt(
        mode="ANSWERER",
        model="gpt-5",
        question="What is the policy?",
        documents=documents,
        s3_client=s3_client,
        settings=Settings(),
        openai_client=openai_client,
    )

    assert result.skip_reason is None
    assert result.input_tokens == 2
    assert openai_client.chat.completions.calls[0]["max_completion_tokens"] == 1
    assert openai_client.chat.completions.calls[1]["max_completion_tokens"] == 16


def test_prepare_baseline_prompt_output_limit_maps_to_context_window(monkeypatch) -> None:
    monkeypatch.setenv("MODEL_CONTEXT_WINDOWS_JSON", '{"gpt-5": 5}')
    documents = [
        {
            "doc_index": 0,
            "ingest_status": "PARSED",
            "text_s3_uri": "s3://bucket/doc-0.txt",
        }
    ]
    s3_client = FakeS3Client({("bucket", "doc-0.txt"): b"Alpha"})
    openai_client = FailingOpenAIClient()

    result = prepare_baseline_prompt(
        mode="ANSWERER",
        model="gpt-5",
        question="What is the policy?",
        documents=documents,
        s3_client=s3_client,
        settings=Settings(),
        openai_client=openai_client,
    )

    assert result.skip_reason == "CONTEXT_WINDOW_EXCEEDED"
    assert result.context_window == 5
