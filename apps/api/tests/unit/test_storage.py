"""Unit tests for ``Resume_Storage`` and the object-key builder.

These tests exercise the two security guarantees the storage layer owns
without talking to a real S3/MinIO endpoint: a fake S3 client records the
keyword arguments each call receives so the assertions can inspect them.

* Requirement 2.5 — object keys are ``<uuidv7>.<ext>`` and never incorporate
  any part of the client filename.
* Requirement 2.10 — objects are written with no public-read access (no
  ``ACL`` argument at all, inheriting the bucket's default-private setting).
"""

from __future__ import annotations

import re
import uuid
from typing import Any

import pytest

from matchlayer_api.core.storage import (
    Resume_Storage,
    build_object_key,
)


class _FakeS3Client:
    """Minimal stand-in for a boto3 S3 client.

    Records the kwargs of the most recent ``put_object`` call and serves a
    canned body for ``get_object`` so the wrapper's read path is covered.
    """

    def __init__(self) -> None:
        self.put_calls: list[dict[str, Any]] = []
        self.stored: dict[str, bytes] = {}

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        self.put_calls.append(kwargs)
        self.stored[kwargs["Key"]] = kwargs["Body"]
        return {}

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        return {"Body": _FakeStreamingBody(self.stored[Key])}


class _FakeStreamingBody:
    """Mimics botocore's ``StreamingBody.read()``."""

    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


_UUIDV7_DOT_EXT = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[0-9a-f]{4}-[0-9a-f]{12}\.(pdf|docx)$"
)


@pytest.mark.parametrize("extension", ["pdf", "docx"])
def test_build_object_key_shape(extension: str) -> None:
    """Keys are ``<uuidv7>.<ext>`` with a parseable UUIDv7 stem (2.5)."""
    key = build_object_key(extension)  # type: ignore[arg-type]
    assert _UUIDV7_DOT_EXT.match(key), key
    stem, _, ext = key.rpartition(".")
    assert ext == extension
    parsed = uuid.UUID(stem)
    assert parsed.version == 7


def test_build_object_key_is_unique() -> None:
    """Two calls never collide — the stem is a fresh UUIDv7 each time."""
    keys = {build_object_key("pdf") for _ in range(100)}
    assert len(keys) == 100


def test_build_object_key_never_contains_filename() -> None:
    """The builder takes only the extension, so no filename can leak in (2.5)."""
    # The function signature accepts no filename argument at all; the only
    # variable input is the extension. This guards against a regression that
    # would widen the signature to thread a filename through.
    key = build_object_key("pdf")
    assert "resume" not in key
    assert "/" not in key and "\\" not in key


async def test_put_sets_no_public_read_acl() -> None:
    """``put`` passes no ACL argument, so objects stay default-private (2.10)."""
    client = _FakeS3Client()
    storage = Resume_Storage(client, bucket="resumes")

    await storage.put(key="abc.pdf", data=b"%PDF-1.4 data", content_type="application/pdf")

    assert len(client.put_calls) == 1
    call = client.put_calls[0]
    # No ACL key at all — not even "private" — per the module docstring's
    # rationale (ACL-disabled buckets reject any ACL parameter).
    assert "ACL" not in call
    assert call["Bucket"] == "resumes"
    assert call["Key"] == "abc.pdf"
    assert call["Body"] == b"%PDF-1.4 data"
    assert call["ContentType"] == "application/pdf"


async def test_put_then_get_roundtrip() -> None:
    """``get`` returns exactly the bytes ``put`` wrote."""
    client = _FakeS3Client()
    storage = Resume_Storage(client, bucket="resumes")
    payload = b"PK\x03\x04 docx bytes"

    key = build_object_key("docx")
    await storage.put(key=key, data=payload, content_type="application/octet-stream")
    fetched = await storage.get(key=key)

    assert fetched == payload
