# Cursor-based pagination

## Introduction

Lists of records are rarely sent back to a caller all at once. When a table can
grow without an upper bound, an Application Programming Interface (API) — the set
of endpoints one program exposes for another program to call — returns one _page_
of rows at a time and tells the caller how to fetch the page after it. This
document explains **cursor pagination**, the page-walking scheme this project
uses for its list endpoints, in which the caller asks for the next page with an
opaque marker that points at the last row it has already seen
(`?limit=&cursor=`), rather than with a numeric position counted from the start
of the list. The marker is called a _cursor_, and because it pins the query to a
specific row instead of a row number, the page boundaries stay correct even when
other rows are inserted or removed between two requests.

**Learning outcomes** — after reading this document you will be able to:

- Explain what page-walking means and why a list endpoint returns one page at a time instead of every row in a single response.
- Describe how a keyset cursor selects the next page and why it stays correct when rows are added or removed between requests.
- Explain why position-counted pagination can skip or repeat rows on data that changes between two page requests.
- Recognise the common mistakes when building cursor pagination and recover from them.

Prerequisites: this document builds on
[PostgreSQL 16 fundamentals](07-database-01-postgresql-fundamentals.md), which explains the
relational tables, row ordering, and indexes that a paged query reads, and on
[SQLAlchemy async engine and the per-request session](03-backend-04-sqlalchemy-async-and-session-dependency.md),
which explains how the project issues the database queries shown later in this
document.

## Problem it solves

A list endpoint that returned every matching row in one response would be slow,
memory-hungry, and fragile the moment a table held thousands of rows. The
concrete problem is therefore: how does a caller read a long, growing list in
manageable chunks, fetching only the next chunk when it needs one, without ever
seeing a row twice or missing a row entirely?

The prior approach most services reach for first is _offset pagination_. The
caller asks for "the next twenty rows starting after the first forty" and the
database is told to count forty rows from the start and discard them before
returning the next page. In Structured Query Language (SQL) — the language used
to query a relational database — that reads as `LIMIT 20 OFFSET 40`. Offset
pagination is easy to write and it works on a frozen dataset. The trouble is that
the offset is a _position counted from the start of the list_, and that position
shifts whenever the underlying rows change. If a new row is inserted near the top
of the list between the moment the caller reads page one and the moment it asks
for page two, every later row slides down by one position: the row that was at
position forty is now at position forty-one, so the row that has moved into
position forty is returned again on page two. The caller sees a duplicate. A
deletion causes the mirror-image fault — a row is skipped because everything
below the deletion shifted up by one. The more often the data changes, the more
often offset pages skip or repeat rows.

Cursor pagination removes the dependency on a counted position. Instead of "start
after the fortieth row", the caller says "start after _this specific row_", and
the database finds the next page relative to that row's stable sort values. An
insertion or deletion elsewhere in the list no longer moves the boundary, because
the boundary is anchored to a row, not to a number.

## Mental model

Think of reading a long book over several sittings. Offset pagination is like
remembering "I stopped on page 120" — but if someone inserts a new chapter near
the front overnight, page 120 is now different content, and you either re-read a
few pages or skip some. A cursor is like leaving a physical bookmark between two
specific pages: no matter how many pages are added or torn out elsewhere in the
book, the bookmark still sits in exactly the same place, and you resume precisely
where you left off.

The page-fetching cycle runs as a short sequence of steps:

1. The caller requests the first page with only a page size and no cursor; the database returns the rows in a fixed, deterministic order.
2. The server reads the sort values of the last row on that page and packs them into an opaque marker, which it returns alongside the page as the next cursor.
3. The caller asks for the following page, echoing that marker back unchanged.
4. The server unpacks the marker and selects only rows that sort strictly _after_ the marked row, returning the next page and a fresh marker.
5. When a page comes back with no further rows beyond it, the server returns no cursor, signalling that the list is exhausted.

Because every page boundary is defined by the sort values of a real row rather
than by a count, the cycle keeps returning each row exactly once even while the
list changes underneath it.

## How it works

Cursor pagination — often called _keyset pagination_ because the page boundary is
a database _key_ rather than a counted position — rests on three ideas: a total
ordering, a strict comparison against the last row's key, and an opaque token
that carries that key between requests.

The first idea is a **total, deterministic ordering**. The list must be sorted by
a combination of columns that, taken together, are unique for every row, so that
no two rows ever tie. A common choice is a creation timestamp paired with the
row's unique identifier: order by the timestamp descending, and break any ties on
identical timestamps with the identifier descending. The identifier tiebreaker is
what guarantees there is exactly one "last row" on every page, with no ambiguity
about where the next page begins. Without that tiebreaker, several rows sharing a
timestamp could straddle a page boundary and be lost or duplicated.

The second idea is a **strict comparison against the last row's key**. To fetch
the page after a given row, the query keeps only rows whose sort key is strictly
"past" that row's key in the sort direction. When the sort is on two columns, the
correct predicate is a _row-value comparison_: treat the pair of columns as a
single composite value and compare it as a whole against the pair of values from
the cursor row. Comparing the columns one at a time with separate conditions is a
classic source of off-by-one bugs; comparing the pair as a tuple expresses "older
timestamp, or the same timestamp with a lower identifier" in one correct step.
This predicate maps directly onto a composite database index over the same
columns in the same direction, so the database can jump to the boundary and read
the page sequentially instead of scanning and discarding rows. That is the
performance reason keyset pagination scales where offset does not: an offset query
still has to walk and throw away every row before the offset, whereas a keyset
query seeks straight to the boundary.

A small refinement avoids a second round-trip to the database: the query asks for
one row more than the page size. If it gets back that extra row, the server knows
another page exists, so it trims the extra row off and emits a cursor; if it gets
back fewer rows than the page size plus one, this was the last page and no cursor
is emitted. This sidesteps a separate counting query that would otherwise be
needed to decide whether a "next page" cursor is warranted.

The third idea is the **opaque cursor token**. The server does not hand the raw
sort key back to the client as separate, meaningful fields, because the cursor is
an internal implementation detail the client should not construct or interpret.
Instead it serialises the key — for example by joining the timestamp and the
identifier with a separator — and encodes the result with Uniform Resource
Locator (URL)-safe base64, a text encoding whose output uses only characters that
travel cleanly inside a query parameter. The client treats the token as opaque:
it stores it and echoes it back, nothing more. A robust decoder also tolerates a
mangled token. If a client truncates or corrupts the cursor, the server can
either reject it with a validation error or fall back to returning the first
page, but it should never crash or leak an internal error; the choice between
those two behaviours is a deliberate design decision, not an accident.

## MatchLayer Phase 1 usage

In MatchLayer the two list endpoints — `GET /api/v1/matches` and
`GET /api/v1/resumes` — are both cursor-paginated, and the convention is fixed in
`.kiro/steering/conventions.md`: "cursor-based for lists that can grow unbounded;
`?limit=&cursor=`. Avoid offset pagination." The match listing lives in
`apps/api/src/matchlayer_api/services/matching.py`; the resume listing in
`apps/api/src/matchlayer_api/services/resumes.py` mirrors it line for line.

The cursor is produced by encoding the last row's sort key into a single opaque,
URL-safe token, exactly as the conceptual model describes:

Source: `apps/api/src/matchlayer_api/services/matching.py`

```python
def _encode_cursor(created_at: datetime, row_id: UUID) -> str:
    """Encode a ``(created_at, id)`` keyset position into an opaque token.

    The timestamp is serialised as an ISO-8601 string (timezone-aware) joined
    to the row id; the pair is URL-safe base64 encoded so it travels cleanly in
    a ``?cursor=`` query parameter.
    """
    raw = f"{created_at.isoformat()}|{row_id}".encode()
    return base64.urlsafe_b64encode(raw).decode("ascii")
```

The base query establishes the total ordering. It is scoped to the requesting
user, drops rows that were soft-deleted — rows marked with a `deleted_at`
timestamp instead of being physically removed — and orders by the creation
timestamp descending with the identifier descending as the deterministic
tiebreaker:

Source: `apps/api/src/matchlayer_api/services/matching.py`

```python
        stmt = (
            select(MatchResult)
            .where(
                MatchResult.user_id == user_id,
                MatchResult.deleted_at.is_(None),
            )
            .order_by(MatchResult.created_at.desc(), MatchResult.id.desc())
        )
```

When a cursor is supplied, the keyset predicate is a single row-value comparison
that compares the `(created_at, id)` pair as a whole against the decoded cursor
position, rather than comparing the two columns separately:

Source: `apps/api/src/matchlayer_api/services/matching.py`

```python
            stmt = stmt.where(
                tuple_(MatchResult.created_at, MatchResult.id) < (cursor_created_at, cursor_id)
            )
```

The "fetch one extra row" refinement decides whether a next-page cursor is
warranted without a second counting query — it requests `limit + 1` rows, trims
the surplus, and only emits a cursor when a further row was actually present:

Source: `apps/api/src/matchlayer_api/services/matching.py`

```python
        # Fetch one extra row to detect whether a further page exists.
        stmt = stmt.limit(effective_limit + 1)
        rows = list((await session.execute(stmt)).scalars().all())

        has_more = len(rows) > effective_limit
        page_rows = rows[:effective_limit]
        next_cursor: str | None = None
        if has_more and page_rows:
            last = page_rows[-1]
            next_cursor = _encode_cursor(last.created_at, last.id)
```

The endpoint surface exposes the two query parameters from the convention. The
page size is constrained to the range 1 to 100 by the framework, and the cursor
is an optional opaque string that defaults to none for the first page:

Source: `apps/api/src/matchlayer_api/api/matches/router.py`

```python
async def list_matches(
    user: _CurrentUser,
    session: _SessionDep,
    limit: Annotated[int, Query(ge=_MIN_LIST_LIMIT, le=_MAX_LIST_LIMIT)] = _DEFAULT_LIST_LIMIT,
    cursor: Annotated[str | None, Query()] = None,
) -> MatchListResponse:
```

The seek-to-the-boundary performance property is not free; it depends on a
composite index whose column order and sort direction match the query's ordering
exactly. The database migration (the versioned script that changes the database
schema) at `apps/api/alembic/versions/0002_resumes_and_matches.py` creates that
index so the planner can satisfy the ordering directly:

Source: `apps/api/alembic/versions/0002_resumes_and_matches.py`

```python
    op.execute(
        "CREATE INDEX match_results_user_created_idx "
        "ON match_results (user_id, created_at DESC, id DESC)"
    )
```

## Common pitfalls

- **Mistake:** Ordering the list by a non-unique column alone, such as a creation timestamp with no identifier tiebreaker.
  **Symptom:** Rows that share the same timestamp intermittently vanish from a page or appear twice across two adjacent pages, especially under bulk inserts that land many rows in the same instant.
  **Recovery:** Add a unique column (the row identifier) as the final sort key and include it in both the ordering and the cursor, so every row has a single unambiguous position.

- **Mistake:** Comparing the two sort columns with separate conditions instead of one row-value comparison.
  **Symptom:** The page boundary is off by one — the first row of a new page repeats the last row of the previous page, or one row between pages is skipped.
  **Recovery:** Replace the per-column conditions with a single tuple comparison of the whole sort key against the cursor values, so the comparison matches the composite ordering precisely.

- **Mistake:** Building the keyset predicate but leaving the table without a matching composite index.
  **Symptom:** Listing stays correct but grows slower as the table fills, and the query plan shows a full scan with a sort step rather than an index range read.
  **Recovery:** Create a composite index whose columns and descending directions match the query ordering exactly, then confirm the plan seeks the index instead of sorting.

- **Mistake:** Crashing or returning a server error when a client sends a truncated or corrupted cursor.
  **Symptom:** A mangled `cursor` value produces a 500 response, or an internal error message leaks the decoder's exception text back to the caller.
  **Recovery:** Decode defensively and decide one deliberate behaviour for an unparseable token — either reject it with a validation error or fall back to the first page — never an unhandled crash.

## External reading

- [PostgreSQL documentation: LIMIT and OFFSET](https://www.postgresql.org/docs/16/queries-limit.html)
- [PostgreSQL documentation: indexes and ORDER BY](https://www.postgresql.org/docs/16/indexes-ordering.html)
- [PostgreSQL documentation: row and array comparisons](https://www.postgresql.org/docs/16/functions-comparisons.html)
- [SQLAlchemy documentation: tuple\_ row-value construct](https://docs.sqlalchemy.org/en/20/core/sqlelement.html)
- [MDN Web Docs: Base64 encoding](https://developer.mozilla.org/en-US/docs/Glossary/Base64)
