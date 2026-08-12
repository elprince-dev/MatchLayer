"""Unit tests for ``services/llm/results.py`` (phase-3-llm-layer task 8.1).

Covers the pure cursor helpers of the newest-first keyset pagination
(Requirements 16.4, 16.10): round-trip fidelity of the opaque token and
the 422 ``validation_error`` on malformed cursors, with the mangled value
never echoed back. The query paths against a real Postgres are covered by
the router integration tests (task 10.9) and Property 18 (task 10.7).
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime

import pytest
from uuid_utils.compat import uuid7

from matchlayer_api.core.errors import MatchLayerError
from matchlayer_api.db.models import LLMResult
from matchlayer_api.services.llm.results import _decode_cursor, _encode_cursor


def test_cursor_round_trips_created_at_and_id() -> None:
    """Encoding a row and decoding the token recovers ``(created_at, id)``."""
    row = LLMResult(id=uuid7(), created_at=datetime(2026, 1, 15, 12, 30, 45, tzinfo=UTC))

    token = _encode_cursor(row)
    created_at, row_id = _decode_cursor(token)

    assert created_at == row.created_at
    assert row_id == row.id
    # Opaque, URL-safe token: decodable base64url, ASCII-clean.
    assert base64.urlsafe_b64decode(token.encode("ascii"))


@pytest.mark.parametrize(
    "malformed",
    [
        "not-base64!!!",
        base64.urlsafe_b64encode(b"no separator here").decode("ascii"),
        base64.urlsafe_b64encode(b"2026-01-15T12:00:00+00:00|not-a-uuid").decode("ascii"),
        base64.urlsafe_b64encode(b"not-a-timestamp|01890000-0000-7000-8000-000000000001").decode(
            "ascii"
        ),
        "",
    ],
)
def test_malformed_cursor_raises_422_without_echoing_value(malformed: str) -> None:
    """Any undecodable cursor is a 422 validation_error (Req 16.10)."""
    with pytest.raises(MatchLayerError) as exc_info:
        _decode_cursor(malformed)

    assert exc_info.value.status_code == 422
    assert exc_info.value.error_type == "validation_error"
    # The mangled value is never echoed back in the detail.
    if malformed:
        assert malformed not in exc_info.value.detail
