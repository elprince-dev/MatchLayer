"""Feature: phase-1-matching — Property 18.

Property 18: Match creation response carries the full field set.

    *For any* successfully created ``Match_Result``, the 201 response body
    contains exactly ``{id, resume_id, score, score_breakdown,
    matched_keywords, missing_keywords, suggestions, scorer_version,
    created_at, updated_at}``, and the match-list item shape contains
    ``{id, resume_id, score, created_at}`` and never ``job_description_text``.

**Validates: Requirements 8.7, 9.2**

This module is the universal companion to the integration coverage of the match
endpoints (task 11.5). Where those tests drive concrete HTTP requests, this file
asserts the *shape* invariants of the two response projections hold across a
wide, generated input space using Hypothesis (>=100 examples), driving the
:class:`~matchlayer_api.api.matches.schemas.MatchResponse` and
:class:`~matchlayer_api.api.matches.schemas.MatchListItem` Pydantic models
directly. No FastAPI app, database, or storage is touched: the schemas are the
single source of truth for the wire shape (``conventions.md`` "Shared schemas"),
so verifying them in isolation verifies the contract the router and the
generated frontend types depend on.

The source of every projection in production is a ``MatchResult`` ORM row
(``db/models.py``) carrying the full column set — including the Restricted PII
column ``job_description_text`` (``security.md`` "Data classification") and the
internal-only ``user_id`` / ``deleted_at`` columns. The router builds each
response via ``model_validate(match)`` (``from_attributes=True``). The danger
this property guards against is a schema change that lets one of those
non-response columns leak into a serialized body. So each generated example
builds a ``MatchResult``-like source that deliberately carries **all** ORM
columns (with a ``job_description_text`` value that must be excluded) and
asserts the projection drops everything outside its declared field set.

Two assertions, one per clause of Property 18:

* **Requirement 8.7 — the full field set.** ``MatchResponse`` serializes to
  *exactly* the ten enumerated fields, and never to ``job_description_text``
  (nor ``user_id`` / ``deleted_at``), regardless of the source values.

* **Requirement 9.2 — the trimmed list item.** ``MatchListItem`` serializes to
  *exactly* ``{id, resume_id, score, created_at}`` and never to
  ``job_description_text`` (nor any of the heavier detail-only columns).

Both clauses are checked through three lenses so the exclusion is proven for the
real wire form, not just the Python object: ``model_dump()`` (Python mode),
``model_dump(mode="json")`` (JSON mode), and the serialized ``model_dump_json()``
string. Each source is validated through **both** the mapping path
(``model_validate(dict)``) and the attribute path
(``model_validate(stub_object)``), the latter mirroring the router's
``model_validate(orm_row)`` usage exactly.

To prove the ``job_description_text`` value itself never reaches the wire (not
merely that no field is *named* for it), every generated job description is
prefixed with a sentinel built from uppercase letters and a non-ASCII glyph
(``JDPII\u2620``). Every other generated string field is constrained to
lowercase ASCII, digits, and spaces, so the sentinel is collision-free: if it
appears anywhere in a serialized body, the JD text leaked. The assertion that
the sentinel is absent from both serialized projections is therefore a direct,
non-fragile check of the PII-exclusion guarantee.
"""

from __future__ import annotations

from datetime import UTC, datetime
from string import ascii_lowercase, digits
from types import SimpleNamespace
from typing import Any, Final

from hypothesis import example, given, settings
from hypothesis import strategies as st

from matchlayer_api.api.matches.schemas import MatchListItem, MatchResponse

# ---------------------------------------------------------------------------
# The exact field sets the two projections must carry.
# ---------------------------------------------------------------------------

# Requirement 8.7: the full Match_Result projection returned by
# POST /api/v1/matches (201) and GET /api/v1/matches/{id}.
EXPECTED_RESPONSE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "id",
        "resume_id",
        "score",
        "score_breakdown",
        "matched_keywords",
        "missing_keywords",
        "suggestions",
        "scorer_version",
        "created_at",
        "updated_at",
    }
)

# Requirement 9.2: the trimmed projection in the GET /api/v1/matches list.
EXPECTED_LIST_ITEM_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "id",
        "resume_id",
        "score",
        "created_at",
    }
)

# ---------------------------------------------------------------------------
# Sentinel proving the Restricted job_description_text value never reaches the
# wire. Built from uppercase ASCII + a non-ASCII glyph so it can never be
# produced by the lowercase-ASCII-only generators used for every other string
# field below; if it shows up in a serialized body, the JD text leaked.
# ---------------------------------------------------------------------------
_JD_SENTINEL: Final[str] = "JDPII\u2620"

# ---------------------------------------------------------------------------
# Smart generators for a MatchResult-like source row.
# ---------------------------------------------------------------------------

# UUIDv7 PKs/FKs are exposed as strings (conventions.md "IDs"); the schemas type
# ``id``/``resume_id`` as ``str``, so the source supplies string UUIDs.
_uuid_str = st.uuids().map(str)

# Lowercase ASCII + digits + space only: collision-free against ``_JD_SENTINEL``.
_safe_text = st.text(alphabet=ascii_lowercase + digits + " ", min_size=0, max_size=40)
_safe_term = st.text(alphabet=ascii_lowercase + digits, min_size=1, max_size=24)

# Scorer_Version surface form (e.g. "1.0.0+lex.v1"): lowercase/digits/./+ only.
_scorer_version = st.text(alphabet=ascii_lowercase + digits + ".+", min_size=1, max_size=24)

# Timezone-aware datetimes, mirroring the timestamptz columns.
_aware_dt = st.datetimes(timezones=st.just(UTC))

# A finite weight (never NaN/inf, so model_dump_json cannot raise).
_weight = st.floats(min_value=0.0, max_value=1000.0, allow_nan=False, allow_infinity=False)
# A component fraction in [0, 1] (never NaN/inf).
_unit = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)

# One {term, weight} keyword, mirroring the matched/missing JSONB shape.
_keyword = st.fixed_dictionaries({"term": _safe_term, "weight": _weight})
# One {keyword, text} suggestion, mirroring the suggestions JSONB shape.
_suggestion = st.fixed_dictionaries({"keyword": _safe_term, "text": _safe_text})

# The explainable score breakdown, mirroring the score_breakdown JSONB shape.
_breakdown = st.fixed_dictionaries(
    {
        "similarity_component": _unit,
        "keyword_coverage_component": _unit,
        "weight_similarity": _unit,
        "weight_keyword": _unit,
        "final_score": st.integers(min_value=0, max_value=100),
    }
)

# A JD value that MUST be excluded from every projection: sentinel-prefixed so
# its presence on the wire is detectable, with arbitrary (incl. unicode) text
# appended so the exclusion is proven for varied content.
_job_description_text = st.text(min_size=0, max_size=80).map(lambda s: _JD_SENTINEL + s)

# A full MatchResult-like source carrying EVERY ORM column (db/models.py): the
# ten response fields plus the non-response columns user_id, job_description_text
# (Restricted PII), and deleted_at. The projections must drop the latter three.
_match_source = st.fixed_dictionaries(
    {
        "id": _uuid_str,
        "user_id": _uuid_str,
        "resume_id": _uuid_str,
        "job_description_text": _job_description_text,
        "score": st.integers(min_value=0, max_value=100),
        "score_breakdown": _breakdown,
        "matched_keywords": st.lists(_keyword, max_size=6),
        "missing_keywords": st.lists(_keyword, max_size=6),
        "suggestions": st.lists(_suggestion, max_size=6),
        "scorer_version": _scorer_version,
        "created_at": _aware_dt,
        "updated_at": _aware_dt,
        "deleted_at": st.none() | _aware_dt,
    }
)

# An explicit minimal example (empty keyword/suggestion lists, soft-deleted row)
# and a populated one, so the canonical edges are always exercised alongside the
# generated space.
_EXAMPLE_EMPTY: Final[dict[str, Any]] = {
    "id": "0190aaaa-0000-7000-8000-000000000001",
    "user_id": "0190aaaa-0000-7000-8000-000000000002",
    "resume_id": "0190aaaa-0000-7000-8000-000000000003",
    "job_description_text": _JD_SENTINEL,
    "score": 0,
    "score_breakdown": {
        "similarity_component": 0.0,
        "keyword_coverage_component": 0.0,
        "weight_similarity": 0.6,
        "weight_keyword": 0.4,
        "final_score": 0,
    },
    "matched_keywords": [],
    "missing_keywords": [],
    "suggestions": [],
    "scorer_version": "1.0.0+lex.v1",
    "created_at": datetime(2024, 1, 1, tzinfo=UTC),
    "updated_at": datetime(2024, 1, 2, tzinfo=UTC),
    "deleted_at": datetime(2024, 1, 3, tzinfo=UTC),
}


def _assert_excludes_jd(serialized_json: str) -> None:
    """Assert the Restricted JD sentinel never reaches a serialized body."""
    assert _JD_SENTINEL not in serialized_json


@settings(max_examples=200, deadline=None)
@given(source=_match_source)
@example(source=_EXAMPLE_EMPTY)
def test_match_response_carries_exactly_the_full_field_set(source: dict[str, Any]) -> None:
    """``MatchResponse`` serializes to exactly the Requirement 8.7 field set.

    Property 18 (Requirement 8.7): for any MatchResult-like source carrying the
    full ORM column set, the response projection contains exactly the ten
    enumerated fields and never ``job_description_text`` (nor ``user_id`` /
    ``deleted_at``) — validated through both the mapping and attribute paths and
    proven on the Python dump, the JSON-mode dump, and the serialized JSON.
    """
    # Both the mapping path (model_validate(dict)) and the attribute path
    # (model_validate(orm_row)) — the latter is exactly how the router builds
    # the body from a SQLAlchemy MatchResult row.
    responses = (
        MatchResponse.model_validate(source),
        MatchResponse.model_validate(SimpleNamespace(**source)),
    )

    for response in responses:
        # Python-mode dump: exactly the ten fields, nothing else.
        dumped = response.model_dump()
        assert set(dumped) == EXPECTED_RESPONSE_FIELDS
        assert "job_description_text" not in dumped
        assert "user_id" not in dumped
        assert "deleted_at" not in dumped

        # JSON-mode dump: identical key set (the literal wire shape).
        json_dumped = response.model_dump(mode="json")
        assert set(json_dumped) == EXPECTED_RESPONSE_FIELDS

        # Serialized JSON: the Restricted JD value never appears anywhere.
        _assert_excludes_jd(response.model_dump_json())


@settings(max_examples=200, deadline=None)
@given(source=_match_source)
@example(source=_EXAMPLE_EMPTY)
def test_match_list_item_carries_only_the_safe_minimum_fields(source: dict[str, Any]) -> None:
    """``MatchListItem`` serializes to exactly ``{id, resume_id, score, created_at}``.

    Property 18 (Requirement 9.2): for any MatchResult-like source, the list-item
    projection contains exactly the four enumerated fields and never
    ``job_description_text`` (nor the detail-only columns score_breakdown,
    matched_keywords, missing_keywords, suggestions, scorer_version,
    updated_at) — validated through both the mapping and attribute paths and
    proven on the Python dump, the JSON-mode dump, and the serialized JSON.
    """
    items = (
        MatchListItem.model_validate(source),
        MatchListItem.model_validate(SimpleNamespace(**source)),
    )

    for item in items:
        # Python-mode dump: exactly the four list-item fields, nothing else.
        dumped = item.model_dump()
        assert set(dumped) == EXPECTED_LIST_ITEM_FIELDS
        assert "job_description_text" not in dumped

        # JSON-mode dump: identical key set (the literal wire shape).
        json_dumped = item.model_dump(mode="json")
        assert set(json_dumped) == EXPECTED_LIST_ITEM_FIELDS

        # Serialized JSON: the Restricted JD value never appears anywhere.
        _assert_excludes_jd(item.model_dump_json())
