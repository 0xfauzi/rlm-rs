from __future__ import annotations

import bisect
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlparse

from botocore.client import BaseClient

from rlm_rs.models import ContextDocument, ContextManifest, SpanLogEntry
from rlm_rs.storage.s3 import build_s3_client, get_json, get_range_bytes


def _split_s3_uri(uri: str) -> tuple[str, str]:
    parsed = urlparse(uri)
    if parsed.scheme != "s3" or not parsed.netloc:
        raise ValueError(f"Invalid S3 URI: {uri}")
    return parsed.netloc, parsed.path.lstrip("/")


@dataclass(frozen=True)
class _Checkpoint:
    char: int
    byte: int


class _OffsetsIndex:
    def __init__(self, payload: dict[str, Any]) -> None:
        checkpoints_raw = payload.get("checkpoints")
        if not isinstance(checkpoints_raw, list) or not checkpoints_raw:
            raise ValueError("Offsets checkpoints missing")
        self.char_length = int(payload.get("char_length", 0))
        self.byte_length = int(payload.get("byte_length", 0))
        self.checkpoints = [
            _Checkpoint(int(item["char"]), int(item["byte"])) for item in checkpoints_raw
        ]
        self._chars = [checkpoint.char for checkpoint in self.checkpoints]

    def resolve_window(self, start_char: int, end_char: int) -> tuple[_Checkpoint, _Checkpoint]:
        if start_char < 0 or end_char < 0 or start_char > end_char:
            raise ValueError("Invalid character range")
        if end_char > self.char_length:
            raise ValueError("Character range exceeds document length")
        start_index = bisect.bisect_right(self._chars, start_char) - 1
        if start_index < 0:
            start_index = 0
        end_index = bisect.bisect_left(self._chars, end_char)
        if end_index >= len(self.checkpoints):
            end_index = len(self.checkpoints) - 1
        return self.checkpoints[start_index], self.checkpoints[end_index]


class DocView:
    def __init__(
        self,
        document: ContextDocument,
        *,
        s3_client: BaseClient,
        span_logger: Callable[[SpanLogEntry], None],
    ) -> None:
        self._document = document
        self._s3_client = s3_client
        self._span_logger = span_logger
        self._offsets: _OffsetsIndex | None = None
        self._text_bucket, self._text_key = _split_s3_uri(document.text_s3_uri)
        if not document.offsets_s3_uri:
            raise ValueError("offsets_s3_uri is required for DocView")
        self._offsets_bucket, self._offsets_key = _split_s3_uri(document.offsets_s3_uri)

    @property
    def doc_id(self) -> str:
        return self._document.doc_id

    @property
    def doc_index(self) -> int:
        return self._document.doc_index

    def __len__(self) -> int:
        return self._get_offsets().char_length

    def __getitem__(self, key: slice | int) -> str:
        if isinstance(key, slice):
            if key.step not in (None, 1):
                raise ValueError("Slice step is not supported")
            return self.slice(key.start, key.stop, tag=None)
        if isinstance(key, int):
            start, end = self._normalize_index(key)
            return self.slice(start, end, tag=None)
        raise TypeError("DocView indices must be int or slice")

    def slice(self, start: int | None, end: int | None, tag: str | None = None) -> str:
        start_char, end_char = self._normalize_range(start, end)
        self._span_logger(
            SpanLogEntry(
                doc_index=self._document.doc_index,
                start_char=start_char,
                end_char=end_char,
                tag=tag,
            )
        )
        if start_char == end_char:
            return ""
        return self._read_range(start_char, end_char)

    def _get_offsets(self) -> _OffsetsIndex:
        if self._offsets is None:
            payload = get_json(self._s3_client, self._offsets_bucket, self._offsets_key)
            if not isinstance(payload, dict):
                raise ValueError("Offsets payload must be a JSON object")
            self._offsets = _OffsetsIndex(payload)
        return self._offsets

    def _normalize_range(self, start: int | None, end: int | None) -> tuple[int, int]:
        length = self._get_offsets().char_length
        start_index, end_index, step = slice(start, end).indices(length)
        if step != 1:
            raise ValueError("Slice step is not supported")
        return start_index, end_index

    def _normalize_index(self, index: int) -> tuple[int, int]:
        length = self._get_offsets().char_length
        if index < 0:
            index += length
        if index < 0 or index >= length:
            raise IndexError("DocView index out of range")
        return index, index + 1

    def _read_range(self, start_char: int, end_char: int) -> str:
        offsets = self._get_offsets()
        start_checkpoint, end_checkpoint = offsets.resolve_window(start_char, end_char)
        if end_checkpoint.byte <= start_checkpoint.byte:
            return ""
        # Read only the checkpoint window to avoid loading the whole document.
        payload = get_range_bytes(
            self._s3_client,
            self._text_bucket,
            self._text_key,
            start_checkpoint.byte,
            end_checkpoint.byte - 1,
        )
        chunk = payload.decode("utf-8")
        start_offset = start_char - start_checkpoint.char
        end_offset = end_char - start_checkpoint.char
        return chunk[start_offset:end_offset]


class ContextView:
    def __init__(
        self,
        manifest: ContextManifest,
        *,
        s3_client: BaseClient | None = None,
        region: str | None = None,
        endpoint_url: str | None = None,
    ) -> None:
        if s3_client is None:
            s3_client = build_s3_client(region=region, endpoint_url=endpoint_url)
        self._span_log: list[SpanLogEntry] = []
        self._docs = [
            DocView(doc, s3_client=s3_client, span_logger=self._span_log.append)
            for doc in manifest.docs
        ]

    def __len__(self) -> int:
        return len(self._docs)

    def __getitem__(self, index: int) -> DocView:
        return self._docs[index]

    @property
    def span_log(self) -> list[SpanLogEntry]:
        return self._span_log
