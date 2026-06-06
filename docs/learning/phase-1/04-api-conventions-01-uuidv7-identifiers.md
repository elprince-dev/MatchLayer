# UUIDv7 time-ordered identifiers

## Introduction

This document explains how an application gives every record a unique name that
is both safe to show to the outside world and efficient to store, using a kind of
identifier called UUIDv7. A UUIDv7 — version 7 of the Universally Unique Identifier (UUID) standard — is a 128-bit value whose leading bits encode the time it was
created, so a pile of these values naturally sorts in the order they were made. The
application hands these values out across its Application Programming Interface (API) boundary as opaque strings (text that the receiver is meant to treat as a
meaningless label, not to parse or do arithmetic on), and it deliberately never
exposes the small counting numbers a database assigns rows internally.

This topic belongs to the API and data conventions track because the choice of
identifier shapes every Uniform Resource Locator (URL), every response body, and every foreign-key
relationship in the system, and a poor choice is expensive to reverse later.

**Learning outcomes** — after reading this document you will be able to:

- Explain what a UUIDv7 value is and how its time-ordered prefix differs from a fully random identifier. The time prefix is what makes the values sortable by creation order.
- Describe why a public identifier is exposed as an opaque string rather than as a database's internal counting integer. Exposing the counter would leak record volume and invite guessing.
- Recognise the common mistakes teams make when adopting time-ordered identifiers and recover from them. Most stem from treating the identifier as a number or from leaking the wrong column.

Prerequisites:

- [PostgreSQL fundamentals](07-database-01-postgresql-fundamentals.md) — covers primary keys, indexes, and the relational tables these identifiers live in.
- [SQLAlchemy async engine and the per-request session](03-backend-04-sqlalchemy-async-and-session-dependency.md) — covers the model layer where the identifier column and its default are declared.

## Problem it solves

Every row in a database needs a name that distinguishes it from every other row:
a primary key. The oldest and simplest approach is an auto-incrementing integer —
the database hands out 1, then 2, then 3, and so on. This is compact and fast, but
it carries three problems once those numbers escape the database. First, a number
in a web address (`/users/41`) tells anyone who sees it roughly how many records
exist and lets them walk the sequence by hand (`/users/42`, `/users/43`) to probe
for records that are not theirs. Second, the database is the only thing that can
safely allocate the next number, so two services or two database shards cannot
mint identifiers independently without coordinating. Third, the same small number
is reused as the key in many different tables, so a value seen in one place can be
guessed in another.

A later approach replaced the integer with a fully random identifier — version 4
of the UUID standard, written UUIDv4 — which fixes the guessing and the
coordination problems because the value carries no sequence and any machine can
generate one. But fully random keys create a new problem of their own: because each
new value lands in a random position, inserting them into the sorted on-disk index
that databases keep scatters writes across the whole index, fragments it, and slows
inserts as the table grows.

UUIDv7 keeps the good properties of a random identifier — unguessable, generatable
anywhere, unique without coordination — while restoring the insert-friendly
ordering of the counting integer. It does this by putting a timestamp at the front
of the value, so newly created identifiers are close together in sort order and land
near the end of the index rather than scattered through it.

## Mental model

Picture a large hotel handing out two kinds of guest identifiers. The first is the
room-key number printed in plain sight: room 41, room 42, room 43. Anyone glancing
at a key learns how full the hotel is and can name a neighbour's room without asking.
The second is a long badge code that looks random but quietly begins with the
check-in time. Two guests can never receive the same badge, an outsider cannot guess
the next one, yet if the front desk lines up all the badges they fall into check-in
order on their own. UUIDv7 is the second kind of badge: opaque to a stranger,
unique without a central clerk counting, and still naturally ordered by when it was
issued.

Walking through how one of these identifiers comes to exist and gets used:

1. The application asks its identifier generator for a new value at the instant a row is created.
2. The generator reads the current wall-clock time in milliseconds and writes it into the leading bits of a 128-bit number.
3. It fills the remaining bits with random data plus a small version marker, producing a value that is globally unique yet sorts in creation-time order.
4. That value becomes the row's primary key, and the outside world only ever sees it rendered as a hyphenated string — never the database's own internal counter.

The detail newcomers miss is step 4: the time-ordered value and the database's
hidden bookkeeping are two different things, and only the first is ever exposed.

## How it works

A UUID is a 128-bit number standardised so that values generated independently on
different machines are astronomically unlikely to collide. The standard defines
several versions that differ in how the 128 bits are filled. The governing
specification, Request for Comments (RFC) 9562, defines version 7 as the
time-ordered variant.

In a version 7 value the bits are laid out in three conceptual zones. The leading
48 bits hold a Unix timestamp in milliseconds — the count of milliseconds since a
fixed reference moment in 1970. A few bits in the middle record the version and a
variant marker so that software reading the value can tell which UUID version it is
looking at. The remaining bits are random. Because the timestamp sits at the most
significant end, comparing two values byte by byte compares their creation times
first, which is exactly what makes a set of these identifiers sort into the order
they were created.

That ordering matters because of how databases store indexes. A typical index is a
balanced tree kept in sorted order on disk, organised into fixed-size pages. When a
new key is larger than every existing key, it appends to the rightmost page, which
stays warm in memory and rarely needs reorganising. When new keys arrive in random
positions — as with a fully random identifier — inserts touch pages all over the
tree, evict useful pages from memory, and trigger page splits that fragment the
index. A time-ordered key behaves like the append-friendly case while still looking
random to an outsider, so it captures most of the insert performance of a counting
integer without the counting integer's drawbacks.

Two properties explain why such a value is exposed as an opaque string rather than
as an internal counter. The first is non-enumerability: an auto-incrementing counter
reveals both the approximate number of records and a trivially guessable neighbour,
whereas a value with 122 effectively unpredictable bits cannot be walked or counted.
The second is decoupling: the internal counter is an implementation detail of one
storage engine, while the public identifier is part of the contract the application
promises to callers. Keeping them separate means the storage can change without
breaking every saved link, and the random suffix means a caller cannot treat the
identifier as an integer to do arithmetic on. The canonical text form is 36
characters — 32 hexadecimal digits split into five hyphen-separated groups — and the
receiver is expected to pass it back unchanged rather than interpret it.

## MatchLayer Phase 1 usage

In MatchLayer's Phase 1 backend, every table's primary key is a UUIDv7. The
identifier and its column default are declared in the SQLAlchemy model base at
`apps/api/src/matchlayer_api/db/models.py`. A small helper, `_uuid7`, generates a
time-ordered value, and each table sets that helper as the column `default`, so a
fresh value is minted whenever a row is created without the calling code passing one
in:

Source: `apps/api/src/matchlayer_api/db/models.py`

```python
def _uuid7() -> UUID:
    """Generate a UUIDv7 (time-ordered) primary key as a stdlib ``uuid.UUID``."""
    return uuid7()


class Base(DeclarativeBase):
    """Shared declarative base for all MatchLayer models."""


class User(Base):
    """The ``users`` table (4.1)."""

    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid7)
```

The `default=_uuid7` argument is the anchor of this whole topic: the column stores
a real `uuid.UUID` value (the Postgres `uuid` column type, requested here via
`PG_UUID(as_uuid=True)`), and the default callable stamps a time-ordered value at
insert time. Notice there is no separate auto-incrementing integer column anywhere
in the table — the UUIDv7 is the only key, so there is no internal counter that
could accidentally leak.

Exposing that key as an opaque string happens one layer out, in the response schema.
The user projection at `apps/api/src/matchlayer_api/auth/schemas.py` declares its
`id` field as a `uuid.UUID` annotated with a serializer that renders it as text on
the way out:

Source: `apps/api/src/matchlayer_api/auth/schemas.py`

```python
    id: Annotated[uuid.UUID, PlainSerializer(str, return_type=str)] = Field(
        description="UUIDv7 of the User_Account, encoded as a string.",
    )
```

The `PlainSerializer(str, return_type=str)` wrapper is how the value crosses the
wire as a hyphenated string rather than as a raw number or a structured object. The
field description states the contract in one line: the value is a UUIDv7, encoded as
a string. Because the same pattern is repeated for every resource's `id`, callers
across the whole surface receive opaque string identifiers and never see a database
sequence integer.

## Common pitfalls

- **Mistake:** Treating the identifier as a number — storing it in an integer column, sorting on it expecting numeric order, or trying to do arithmetic on it.
  **Symptom:** Values get truncated or rejected on insert, or a list that should be in creation order comes back shuffled because the text was compared the wrong way.
  **Recovery:** Store it in the database's native `uuid` column type and let the database compare it; if you need creation order, sort on the timestamp column rather than parsing the identifier yourself.

- **Mistake:** Exposing the internal auto-incrementing counter alongside the UUIDv7, for example by adding a serial column "for convenience" and returning it in a response.
  **Symptom:** Record counts and guessable neighbours leak in URLs or payloads even though the primary key is a UUIDv7, defeating the reason the UUIDv7 was chosen.
  **Recovery:** Remove the serial column from the public schema; keep the UUIDv7 as the only identifier callers ever see, and never place an internal counter in a response model.

- **Mistake:** Using a fully random version 4 identifier as the primary key and assuming it behaves like a time-ordered one.
  **Symptom:** Insert throughput degrades as the table grows and index fragmentation climbs, because random keys scatter writes across the index instead of appending.
  **Recovery:** Generate version 7 values for the primary key so inserts stay near the end of the index, and reserve fully random values for cases where ordering must not be inferable.

## External reading

- [Request for Comments (RFC) 9562: Universally Unique Identifiers, including version 7](https://datatracker.ietf.org/doc/html/rfc9562)
- [Python `uuid` module documentation](https://docs.python.org/3/library/uuid.html)
- [PostgreSQL `uuid` data type](https://www.postgresql.org/docs/current/datatype-uuid.html)
- [SQLAlchemy type basics, including the `Uuid` type](https://docs.sqlalchemy.org/en/20/core/type_basics.html)
