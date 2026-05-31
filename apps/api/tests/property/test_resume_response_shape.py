"""Feature: phase-1-matching — Property 17.

Property 17: Resume response never exposes sensitive fields.

    *For any* ``Resume``, the serialized resume response (both the single-item
    and list-item shapes) contains exactly the safe field set
    ``{id, original_filename, content_type, byte_size, extraction_status,
    created_at, updated_at}`` and never contains ``extracted_text`` or
    ``storage_key``.

**Validates: Requirements 2.9, 4.2**

This module is the universal companion to the integration coverage of the resume
endpoints (task 10.7). Where those tests drive concrete HTTP requests, this file
asserts the *shape* invariant of the resume projection holds across a wide,
generated input space using Hypothesis (>=100 examples), driving the
:class:`~matchlayer_api.api.resumes.schemas.ResumeResponse` and
:class:`~matchlayer_api.api.resumes.schemas.ResumeListResponse` Pydantic models
directly. No FastAPI app, database, or storage is touched: the schemas are the
single source of truth for the wire shape (``conventions.md`` "Shared schemas"),
so verifying them in isolation verifies the contract the router and the
generated frontend types depend on.

The source of every projection in production is a ``Resume`` ORM row
(``db/models.py``) carrying the full column set — including the Restricted PII
column ``extracted_text`` and the internal object-storage location
``storage_key`` (``security.md`` "Data classification"), plus the internal-only
``user_id``, ``deleted_at``, and ``extraction_char_count`` columns. The router
builds each response via ``ResumeResponse.model_validate(row)``
(``from_attributes=True``). The danger this property guards against is a schema
change that lets one of those non-response columns leak into a serialized body.
So each generated example builds a ``Resume``-like source that deliberately
carries **all** ORM columns (with sensitive ``extracted_text`` and
``storage_key`` values that must be excluded) and asserts the projection drops
everything outside its declared seven-field set.

The exclusion is proven for both shapes Requirement 2.9 / 4.2 enumerate:

* **The single-item response.** ``ResumeResponse`` serializes to *exactly* the
  seven enumerated fields, and never to ``extracted_text`` / ``storage_key``
  (nor ``user_id`` / ``deleted_at`` / ``extraction_char_count``), regardless of
  the source values.

* **Every list item.** Each item of a ``ResumeListResponse`` built from a list
  of such sources serializes to *exactly* the same seven fields and likewise
  excludes the sensitive columns (Requirement 4.2).

Both shapes are checked through three lenses so the exclusion is proven for the
real wire form, not just the Python object: ``model_dump()`` (Python mode),
``model_dump(mode="json")`` (JSON mode), and the serialized ``model_dump_json()``
string. Each source is validated through **both** the mapping path
(``model_validate(dict)``) and the attribute path
(``model_validate(SimpleNamespace(...))``), the latter mirroring the router's
``model_validate(orm_row)`` usage exactly.

To prove the sensitive *values* themselves never reach the wire (not merely that
no field is *named* for them), the generated ``extracted_text`` and
``storage_key`` are each prefixed with a distinct sentinel built from uppercase
letters and a non-ASCII glyph (``XTEXTPII\u2620`` / ``SKEYPII\u2622``). Every
other string field — including the *allowed* ``original_filename``,
``content_type``, ``extraction_status``, and ``next_cursor`` that legitimately
appear on the wire — is constrained to lowercase ASCII, digits, and spaces (or a
fixed lowercase enum), so the sentinels are collision-free: if either appears
anywhere in a serialized body, that sensitive value leaked. The assertion that
neither sentinel is present in any serialized projection is therefore a direct,
non-fragile check of the PII/secret-exclusion guarantee.
"""

from __future__ import annotations

from datetime import UTC, datetime
from string import ascii_lowercase, digits
from types import SimpleNamespace
from typing import Any, Final

from hypothesis import example, given, settings
from hypothesis import strategies as st

from matchlayer_api.api.resumes.schemas import ResumeListResponse, ResumeResponse

# ---------------------------------------------------------------------------
# The exact field set the resume projection must carry (Requirements 2.9, 4.2).
# ---------------------------------------------------------------------------
EXPECTED_RESPONSE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "id",
        "original_filename",
        "content_type",
        "byte_size",
        "extraction_status",
        "created_at",
        "updated_at",
    }
)

# The non-response ORM columns that must never appear in a serialized body.
_FORBIDDEN_FIELD_NAMES: Final[tuple[str, ...]] = (
    "extracted_text",
    "storage_key",
    "user_id",
    "deleted_at",
    "extraction_char_count",
)

# ---------------------------------------------------------------------------
# Sentinels proving the sensitive ``extracted_text`` and ``storage_key`` *values*
# never reach the wire. Each is built from uppercase ASCII + a distinct non-ASCII
# glyph so neither can be produced by the lowercase-ASCII-only generators used
# for every other string field below; if either shows up in a serialized body,
# that sensitive value leaked.
# ---------------------------------------------------------------------------
_EXTRACTED_TEXT_SENTINEL: Final[str] = "XTEXTPII\u2620"
_STORAGE_KEY_SENTINEL: Final[str] = "SKEYPII\u2622"

# ---------------------------------------------------------------------------
# Smart generators for a Resume-like source row.
# ---------------------------------------------------------------------------

# UUIDv7 PKs/FKs are exposed as strings (conventions.md "IDs"); the schema types
# ``id`` as ``str``, so the source supplies string UUIDs.
_uuid_str = st.uuids().map(str)

# Lowercase ASCII + digits + space only: collision-free against both sentinels.
# Used for the *allowed* ``original_filename`` (which legitimately appears on the
# wire) and the opaque ``next_cursor``.
_safe_text = st.text(alphabet=ascii_lowercase + digits + " ", min_size=0, max_size=40)
_safe_filename = st.text(alphabet=ascii_lowercase + digits + " .", min_size=1, max_size=40)

# The validated media type: the schema types ``content_type`` as ``str``; the two
# real values are both all-lowercase, so they cannot collide with the sentinels.
_content_type = st.sampled_from(
    [
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ]
)

# ``extraction_status`` is a Literal in the schema, so the source must supply one
# of the three valid values or model_validate would reject it.
_extraction_status = st.sampled_from(["pending", "succeeded", "failed"])

# Timezone-aware datetimes, mirroring the timestamptz columns.
_aware_dt = st.datetimes(timezones=st.just(UTC))

# Sensitive values that MUST be excluded from every projection: each sentinel-
# prefixed so its presence on the wire is detectable, with arbitrary (incl.
# unicode) text appended so the exclusion is proven for varied content. Both are
# always non-null here so the sentinel is genuinely present in every source row.
_extracted_text = st.text(min_size=0, max_size=80).map(lambda s: _EXTRACTED_TEXT_SENTINEL + s)
_storage_key = st.text(min_size=0, max_size=40).map(lambda s: _STORAGE_KEY_SENTINEL + s)

# A full Resume-like source carrying EVERY ORM column (db/models.py): the seven
# response fields plus the non-response columns user_id, storage_key (internal),
# extracted_text (Restricted PII), extraction_char_count, and deleted_at. The
# projection must drop the latter five.
_resume_source = st.fixed_dictionaries(
    {
        "id": _uuid_str,
        "user_id": _uuid_str,
        "original_filename": _safe_filename,
        "storage_key": _storage_key,
        "content_type": _content_type,
        "byte_size": st.integers(min_value=0, max_value=5 * 1024 * 1024),
        "extracted_text": _extracted_text,
        "extraction_status": _extraction_status,
        "extraction_char_count": st.none() | st.integers(min_value=0, max_value=200_000),
        "created_at": _aware_dt,
        "updated_at": _aware_dt,
        "deleted_at": st.none() | _aware_dt,
    }
)

# An explicit example (a soft-deleted row with a successful extraction) so the
# canonical edge is always exercised alongside the generated space.
_EXAMPLE: Final[dict[str, Any]] = {
    "id": "0190aaaa-0000-7000-8000-000000000001",
    "user_id": "0190aaaa-0000-7000-8000-000000000002",
    "original_filename": "resume v2.pdf",
    "storage_key": _STORAGE_KEY_SENTINEL + "0190aaaa.pdf",
    "content_type": "application/pdf",
    "byte_size": 12345,
    "extracted_text": _EXTRACTED_TEXT_SENTINEL + " jane doe senior engineer",
    "extraction_status": "succeeded",
    "extraction_char_count": 25,
    "created_at": datetime(2024, 1, 1, tzinfo=UTC),
    "updated_at": datetime(2024, 1, 2, tzinfo=UTC),
    "deleted_at": datetime(2024, 1, 3, tzinfo=UTC),
}


def _assert_excludes_sensitive_values(*serialized_bodies: str) -> None:
    """Assert neither sensitive sentinel appears in any serialized body."""
    for body in serialized_bodies:
        assert _EXTRACTED_TEXT_SENTINEL not in body
        assert _STORAGE_KEY_SENTINEL not in body


def _assert_safe_projection(response: ResumeResponse) -> None:
    """Assert one ``ResumeResponse`` carries exactly the safe field set.

    Checks the exact field set on the Python-mode dump, confirms the literal
    wire shape (JSON-mode dump) carries the identical keys, asserts none of the
    forbidden column names appear, and proves neither sensitive *value* reaches
    the wire across all three serialization forms.
    """
    # Python-mode dump: exactly the seven fields, nothing else.
    dumped = response.model_dump()
    assert set(dumped) == EXPECTED_RESPONSE_FIELDS
    for forbidden in _FORBIDDEN_FIELD_NAMES:
        assert forbidden not in dumped

    # JSON-mode dump: identical key set (the literal wire shape).
    json_dumped = response.model_dump(mode="json")
    assert set(json_dumped) == EXPECTED_RESPONSE_FIELDS

    # The sensitive values never appear in any serialized form (model_dump,
    # model_dump(mode="json"), model_dump_json()).
    _assert_excludes_sensitive_values(
        str(dumped),
        str(json_dumped),
        response.model_dump_json(),
    )


@settings(max_examples=200, deadline=None)
@given(source=_resume_source)
@example(source=_EXAMPLE)
def test_resume_response_carries_exactly_the_safe_field_set(source: dict[str, Any]) -> None:
    """``ResumeResponse`` serializes to exactly the safe field set.

    Property 17 (Requirements 2.9, 4.2): for any Resume-like source carrying the
    full ORM column set, the single-item response projection contains exactly
    the seven enumerated fields and never ``extracted_text`` / ``storage_key``
    (nor ``user_id`` / ``deleted_at`` / ``extraction_char_count``) — validated
    through both the mapping and attribute paths and proven on the Python dump,
    the JSON-mode dump, and the serialized JSON.
    """
    # Both the mapping path (model_validate(dict)) and the attribute path
    # (model_validate(orm_row)) — the latter is exactly how the router builds
    # the body from a SQLAlchemy Resume row.
    responses = (
        ResumeResponse.model_validate(source),
        ResumeResponse.model_validate(SimpleNamespace(**source)),
    )

    for response in responses:
        _assert_safe_projection(response)


@settings(max_examples=200, deadline=None)
@given(
    sources=st.lists(_resume_source, min_size=0, max_size=8),
    next_cursor=st.none() | _safe_text,
)
@example(sources=[_EXAMPLE], next_cursor=None)
def test_resume_list_response_excludes_sensitive_fields_for_every_item(
    sources: list[dict[str, Any]], next_cursor: str | None
) -> None:
    """Every item of a ``ResumeListResponse`` carries only the safe field set.

    Property 17 (Requirement 4.2): for a list of Resume-like sources, the list
    response projects each item to exactly the seven enumerated fields and never
    exposes ``extracted_text`` / ``storage_key`` (nor the other internal
    columns) — proven per item across all three serialization forms, and proven
    once more on the whole serialized list body so a leak anywhere in the page is
    caught.
    """
    # Mirror the router: the list response is built from the ORM rows. Validate
    # through both the mapping path (dict items) and the attribute path
    # (SimpleNamespace items) so the nested ResumeResponse coercion is exercised
    # exactly as model_validate(orm_row) would drive it.
    list_responses = (
        ResumeListResponse.model_validate({"items": sources, "next_cursor": next_cursor}),
        ResumeListResponse.model_validate(
            {"items": [SimpleNamespace(**s) for s in sources], "next_cursor": next_cursor}
        ),
    )

    for list_response in list_responses:
        assert len(list_response.items) == len(sources)
        for item in list_response.items:
            _assert_safe_projection(item)

        # The whole serialized page body never carries either sensitive value,
        # regardless of how many items it holds.
        _assert_excludes_sensitive_values(
            str(list_response.model_dump()),
            str(list_response.model_dump(mode="json")),
            list_response.model_dump_json(),
        )
