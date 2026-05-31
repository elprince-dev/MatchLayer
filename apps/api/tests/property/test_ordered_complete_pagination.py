"""Feature: phase-1-matching — Property 22.

Property 22: Listing is correctly ordered and paginates completely.

    *For any* set of a user's resumes (or matches), walking the cursor-paginated
    list endpoint from the first page to exhaustion yields every non-deleted row
    owned by that user exactly once, in strictly non-increasing ``created_at``
    (ties broken by descending ``id``) order, and yields no soft-deleted row.

**Validates: Requirements 4.1, 9.1**

Why this is a *static* (no-DB) property test
---------------------------------------------
Requirements 4.1 (resumes) and 9.1 (matches) make both list endpoints
cursor-paginated, scoped to ``user_id``, filtered to ``deleted_at IS NULL``, and
ordered ``created_at`` descending with ``id`` descending as the deterministic
tiebreak. ``Resume_Service.list_resumes`` and ``Scoring_Service.list_matches``
implement this identically: fetch ``limit + 1`` rows under the keyset predicate
``(created_at, id) < (cursor_created_at, cursor_id)`` (the head when no cursor),
slice the page to ``limit``, and emit a ``next_cursor`` iff the ``(limit + 1)``th
row existed. The cursor is an opaque base64 token encoding ``(created_at, id)``.

The *runtime*, end-to-end proof — that real ``GET`` requests against a real
Postgres walk every row once in order — belongs to the integration suites
(tasks 10.7 / 11.5), which need a database. Per the task and the design's
Testing Strategy, this property test deliberately needs **no** Postgres/Redis:
it exercises the two pieces that carry the ordering/completeness guarantee in
isolation —

1. the **cursor codec** (each module's own private ``_encode_cursor`` /
   ``_decode_cursor``), driven for real; and
2. the **keyset/pagination logic**, modelled by a pure in-memory oracle that
   mirrors the service query exactly (global sort by ``(created_at DESC,
   id DESC)``; a page is the first ``limit`` rows strictly ``<`` the decoded
   cursor, or the head when no cursor; ``next_cursor`` iff a ``(limit + 1)``th
   row existed).

The model **drives the real codec**: at every page boundary it encodes the last
page row with the module-under-test's ``_encode_cursor`` and, on the next
iteration, decodes that exact token with the module-under-test's
``_decode_cursor`` to form the keyset predicate. So any codec defect that loses
precision or mangles the ``(created_at, id)`` key (a dropped microsecond, a lost
timezone offset, a bad separator split) surfaces directly as a dropped or
duplicated row at the boundary — the walk's output would no longer equal the
globally-sorted row set.

The two codecs are **not** the same code (resumes' ``_encode_cursor`` takes a
``Resume`` row and its ``_decode_cursor`` *raises* a 422 ``MatchLayerError`` on a
bad token; matching's takes ``(created_at, id)`` and its ``_decode_cursor``
*returns ``None``* → first page). Each is tested through its own module so a
divergence between the two is caught rather than masked.

Three properties are asserted, each over >=100 Hypothesis examples and across
**both** services:

* **(i) Round-trip** — ``decode(encode(created_at, id)) == (created_at, id)`` for
  arbitrary timezone-aware timestamps and arbitrary uuids, so a replayed cursor
  reconstructs the exact keyset position.
* **(ii) Ordered, complete pagination** — walking from the first page, following
  the *real* ``next_cursor`` until it is ``None``, yields every non-deleted row
  exactly once, in ``(created_at DESC, id DESC)`` order, and never a
  soft-deleted row — for arbitrary row sets (deliberately including duplicate
  ``created_at`` values so the ``id`` DESC tiebreak is exercised) and arbitrary
  ``limit >= 1``.
* **(iii) Malformed cursor → documented fallback** — a garbage token decodes to
  *each module's* documented behavior: resumes raises a 422
  ``validation_error`` ``MatchLayerError`` (whose ``detail`` never echoes the
  bad token), matching returns ``None`` (so a mangled cursor degrades to the
  first page rather than a 4xx).

No FastAPI app, database, network, or Redis is touched: the private codec
helpers are pure functions, and the keyset logic is a plain in-memory model, so
the suite is fast and deterministic.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta, timezone
from itertools import pairwise
from typing import Literal
from uuid import UUID

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from matchlayer_api.core.errors import MatchLayerError
from matchlayer_api.db.models import Resume
from matchlayer_api.services import matching as matching_module
from matchlayer_api.services import resumes as resumes_module

# The two list surfaces under test, identified by a small literal so the
# generated scenarios parametrize over both with one driver.
ServiceKind = Literal["resume", "match"]

# A row in the generated population: its sort key (created_at, id) plus whether
# it is soft-deleted (the service query filters ``deleted_at IS NULL``).
Row = tuple[datetime, UUID, bool]


# ---------------------------------------------------------------------------
# Codec adapters — drive each module's OWN private helpers (their signatures
# differ on purpose; see the module docstring).
# ---------------------------------------------------------------------------


def _encode_for(service: ServiceKind, created_at: datetime, row_id: UUID) -> str:
    """Encode a ``(created_at, id)`` keyset position with the real module codec.

    ``resumes._encode_cursor`` takes a ``Resume`` row (reading ``created_at`` /
    ``id`` off it); ``matching._encode_cursor`` takes the pair directly. A
    transient ORM ``Resume`` (no session) is enough to feed the resumes codec.
    """
    if service == "resume":
        return resumes_module._encode_cursor(Resume(id=row_id, created_at=created_at))
    return matching_module._encode_cursor(created_at, row_id)


def _decode_for(service: ServiceKind, cursor: str) -> tuple[datetime, UUID] | None:
    """Decode an opaque cursor with the real module codec.

    Returns the ``(created_at, id)`` pair for a well-formed token. The two
    modules diverge on a *bad* token — resumes raises ``MatchLayerError`` while
    matching returns ``None`` — which is exactly what property (iii) asserts; in
    the pagination walk only freshly-encoded (well-formed) tokens are decoded,
    so both return a pair there.
    """
    if service == "resume":
        return resumes_module._decode_cursor(cursor)
    return matching_module._decode_cursor(cursor)


# ---------------------------------------------------------------------------
# Smart generators.
#
# Timezone-aware timestamps with whole-quarter-hour fixed offsets (covering real
# zones like +05:30, +05:45, +12:45) so ``isoformat`` <-> ``fromisoformat`` round
# trips exactly without the sub-minute historical-LMT offsets that production
# (always UTC ``created_at``) never sees. Row ids are unique uuids, so the sort
# key ``(created_at, id)`` is unique even when ``created_at`` collides — which
# the small timestamp pool deliberately forces, exercising the ``id`` DESC
# tiebreak.
# ---------------------------------------------------------------------------

_quarter_hour_offsets = st.integers(min_value=-12 * 60, max_value=14 * 60).map(
    lambda minutes: timezone(timedelta(minutes=minutes - (minutes % 15)))
)

_aware_datetimes = st.datetimes(
    min_value=datetime(1970, 1, 1),
    max_value=datetime(2100, 1, 1),
    timezones=_quarter_hour_offsets,
)


@st.composite
def _row_sets(draw: st.DrawFn) -> list[Row]:
    """Generate a user's row population with duplicate ``created_at`` values.

    Ids are drawn unique (a real primary key), so the ``(created_at, id)`` sort
    key is unique. Each row's ``created_at`` is sampled from a *small* pool, so
    collisions on ``created_at`` are common and the descending-``id`` tiebreak is
    genuinely exercised. A per-row ``deleted`` flag models soft-deleted rows the
    service query must exclude.
    """
    ids = draw(st.lists(st.uuids(), min_size=0, max_size=24, unique=True))
    pool = draw(st.lists(_aware_datetimes, min_size=1, max_size=4))
    rows: list[Row] = []
    for row_id in ids:
        created_at = draw(st.sampled_from(pool))
        deleted = draw(st.booleans())
        rows.append((created_at, row_id, deleted))
    return rows


# A hand-built population whose three rows share one timestamp (``_TS_A``) so
# the ``id`` DESC tiebreak straddles a page boundary at ``limit=2``; one later
# row (``_TS_B``) sorts first and one soft-deleted row must never appear.
_TS_A = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
_TS_B = datetime(2025, 1, 2, 9, 30, 0, tzinfo=UTC)
_EXAMPLE_ROWS: list[Row] = [
    (_TS_A, UUID(int=10), False),
    (_TS_A, UUID(int=20), False),
    (_TS_A, UUID(int=30), False),
    (_TS_B, UUID(int=5), False),
    (_TS_A, UUID(int=40), True),  # soft-deleted: must never be walked
]


# ===========================================================================
# (i) Round-trip: decode(encode(created_at, id)) == (created_at, id).
# ===========================================================================


@settings(max_examples=200, deadline=None)
@given(
    service=st.sampled_from(("resume", "match")),
    created_at=_aware_datetimes,
    row_id=st.uuids(),
)
def test_cursor_roundtrips_to_the_same_keyset_position(
    service: ServiceKind,
    created_at: datetime,
    row_id: UUID,
) -> None:
    """A cursor round-trips to the exact ``(created_at, id)`` it encoded.

    Property 22 (Requirements 4.1, 9.1), codec facet: for either module's own
    codec, ``_decode_cursor(_encode_cursor(...))`` recovers the same composite
    keyset key. This exactness is what lets a ``next_cursor`` resume pagination
    at precisely the boundary row — the foundation the completeness walk in
    ``test_full_walk_*`` relies on.
    """
    decoded = _decode_for(service, _encode_for(service, created_at, row_id))
    assert decoded is not None, f"{service}: a well-formed cursor must decode to a pair"
    decoded_created_at, decoded_id = decoded
    assert decoded_id == row_id
    # Timezone-aware equality: same instant AND same fixed offset round-trip.
    assert decoded_created_at == created_at
    assert decoded_created_at.utcoffset() == created_at.utcoffset()


# ===========================================================================
# (ii) Ordered, complete pagination across the real cursor boundary.
# ===========================================================================


def _walk_all_pages(
    service: ServiceKind, rows: list[Row], limit: int
) -> tuple[list[tuple[datetime, UUID]], list[tuple[datetime, UUID]]]:
    """Walk the modelled list endpoint to exhaustion, driving the real codec.

    Mirrors the service query exactly: the pageable population is the
    non-deleted rows (``deleted_at IS NULL``); each page is the first ``limit``
    rows of the population sorted ``(created_at DESC, id DESC)`` and strictly
    ``<`` the decoded cursor (or the head when no cursor); ``next_cursor`` is the
    real ``_encode_cursor`` of the last page row iff a ``(limit + 1)``th row
    existed. On the following iteration that exact token is decoded with the real
    ``_decode_cursor`` to rebuild the keyset predicate, so both halves of the
    codec are exercised at every boundary.

    Returns the walked rows (page order, concatenated) and the non-deleted
    population, for the caller's completeness/order assertions.
    """
    population = [(created_at, row_id) for (created_at, row_id, deleted) in rows if not deleted]
    collected: list[tuple[datetime, UUID]] = []
    cursor: str | None = None
    iterations = 0
    max_iterations = len(population) + 2  # each non-final page yields >=1 row

    while True:
        iterations += 1
        assert iterations <= max_iterations, f"{service}: pagination failed to terminate"

        if cursor is None:
            candidate = population
        else:
            decoded = _decode_for(service, cursor)
            assert decoded is not None, (
                f"{service}: a freshly-encoded next_cursor must decode (got None)"
            )
            candidate = [key for key in population if key < decoded]

        ordered = sorted(candidate, key=lambda key: (key[0], key[1]), reverse=True)
        window = ordered[: limit + 1]
        has_more = len(window) > limit
        page = window[:limit]
        collected.extend(page)

        if has_more and page:
            last_created_at, last_id = page[-1]
            cursor = _encode_for(service, last_created_at, last_id)
        else:
            break

    return collected, population


@settings(max_examples=200, deadline=None)
@given(
    service=st.sampled_from(("resume", "match")),
    rows=_row_sets(),
    limit=st.integers(min_value=1, max_value=12),
)
@example(service="resume", rows=_EXAMPLE_ROWS, limit=2)
@example(service="match", rows=_EXAMPLE_ROWS, limit=2)
def test_full_walk_is_ordered_and_complete(
    service: ServiceKind,
    rows: list[Row],
    limit: int,
) -> None:
    """Walking every page yields each non-deleted row once, in order, none dropped.

    Property 22 (Requirements 4.1, 9.1): for an arbitrary owned row set and an
    arbitrary ``limit >= 1``, following the real ``next_cursor`` from the first
    page to ``None`` yields exactly the user's non-deleted rows, each exactly
    once (no row dropped or duplicated across a page boundary), in strictly
    non-increasing ``created_at`` with ``id`` descending as the tiebreak, and
    never a soft-deleted row. Both module codecs drive their own walk.
    """
    collected, population = _walk_all_pages(service, rows, limit)
    expected = sorted(population, key=lambda key: (key[0], key[1]), reverse=True)

    # Completeness + correct order + no duplication, in one structural equality.
    assert collected == expected

    collected_ids = [row_id for (_created_at, row_id) in collected]
    # Each non-deleted row appears exactly once (none dropped, none duplicated).
    assert len(collected_ids) == len(set(collected_ids)) == len(population)

    # No soft-deleted row ever surfaces (the ``deleted_at IS NULL`` filter).
    deleted_ids = {row_id for (_created_at, row_id, deleted) in rows if deleted}
    assert deleted_ids.isdisjoint(collected_ids)

    # Strictly descending adjacency on the composite (created_at, id) key, the
    # ordering clause of the property stated directly.
    for earlier, later in pairwise(collected):
        assert (earlier[0], earlier[1]) > (later[0], later[1])


# ===========================================================================
# (iii) Malformed cursor -> each module's documented fallback.
# ===========================================================================


def _b64(text: str) -> str:
    """URL-safe base64 of *text* (how the real codecs build their tokens)."""
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii")


# Letters-only parts can never parse as an ISO-8601 timestamp or a UUID.
_alpha_part = st.text(alphabet="GHIJKLMNOPQRSTUVWXYZghijklmnopqrstuvwxyz", min_size=1, max_size=12)

# (1) base64 of a payload with NO "|": both decoders hit the missing-separator
#     path (resumes' unpack raises; matching's ``partition`` reports no sep).
_no_separator_cursor = st.text(min_size=0, max_size=24).filter(lambda s: "|" not in s).map(_b64)

# (2) base64 of "<not-a-date>|<not-a-uuid>": both decoders parse-fail on the
#     timestamp/uuid even though the separator is present.
_bad_parts_cursor = st.builds(lambda left, right: _b64(f"{left}|{right}"), _alpha_part, _alpha_part)

_malformed_cursors = st.one_of(_no_separator_cursor, _bad_parts_cursor)


@settings(max_examples=200, deadline=None)
@given(cursor=_malformed_cursors)
@example(cursor="")  # empty token -> empty decode -> no separator
@example(cursor="A")  # invalid base64 length -> binascii.Error path
@example(cursor="!!")  # non-alphabet chars -> empty decode -> no separator
@example(cursor="not-a-valid-cursor")  # valid alphabet, bad length -> binascii.Error
@example(cursor="====")  # padding only -> empty decode -> no separator
def test_malformed_cursor_follows_each_modules_documented_fallback(cursor: str) -> None:
    """A garbage cursor decodes to each module's ACTUAL documented behavior.

    Property 22 (Requirements 4.1, 9.1), robustness facet. The two modules
    document *different* fallbacks for a malformed token, and both are asserted
    against the same garbage input so a divergence is caught:

    * ``resumes._decode_cursor`` raises a 422 ``validation_error``
      ``MatchLayerError`` whose ``detail`` never echoes the bad token (so a
      mangled cursor is a clean client error, not a leak); while
    * ``matching._decode_cursor`` returns ``None``, so the caller falls back to
      the first page rather than surfacing a 4xx.
    """
    # resumes: raises the 422 envelope, with no echo of the bad token.
    with pytest.raises(MatchLayerError) as exc_info:
        resumes_module._decode_cursor(cursor)
    error = exc_info.value
    assert error.status_code == 422
    assert error.error_type == "validation_error"
    # The malformed value is never reflected back in the detail.
    assert error.detail == "Invalid pagination cursor."

    # matching: returns None -> first page, never raises.
    assert matching_module._decode_cursor(cursor) is None
