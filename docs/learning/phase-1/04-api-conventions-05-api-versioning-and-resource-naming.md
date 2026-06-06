# Versioned API paths, plural resource names, and UTC timestamps

## Introduction

This document explains three small but load-bearing conventions that shape how a
web service names its addresses and reports its dates. The web service here is an
Application Programming Interface (API) — the set of network addresses one
program exposes so other programs can read and change its data over the web. The
web runs on the Hypertext Transfer Protocol (HTTP), the request-and-response
protocol behind every page load and every data call. The three conventions are:
putting a version label such as `v1` at the front of every address so the service
can evolve without breaking existing callers; naming each collection of things
with a plural noun (a resource) so addresses are predictable; and writing every
date as an International Organization for Standardization (ISO) 8601 string in
Coordinated Universal Time (UTC) — the single worldwide reference clock — with a
trailing `Z` so there is never any doubt about the time zone.

A _resource_ is one named collection of similar things the service manages, such
as the set of uploaded files or the set of scoring results. An _endpoint_ is one
addressable operation on a resource. A _base path_ is the fixed prefix every
endpoint of a service shares.

**Learning outcomes** — after reading this document you will be able to:

- Explain why a version label belongs in the address path and how it lets a service ship breaking changes without disrupting callers that still expect the old behaviour.
- Describe the plural-noun resource naming rule and why leaning on HTTP methods instead of verbs in the path keeps an API predictable.
- Explain what an ISO 8601 UTC timestamp with a `Z` suffix means and why storing and returning dates this way removes a whole class of time-zone bugs.
- Recognise the common mistakes in versioning, resource naming, and timestamp handling, and recover from each.

Prerequisites: this document builds on
[FastAPI and the application-factory pattern](03-backend-01-fastapi-application-factory.md),
which explains how routers are grouped and mounted onto the application, and on
[SQLAlchemy async engine and the session dependency](03-backend-04-sqlalchemy-async-and-session-dependency.md),
which explains the database model layer where the timestamp columns live.

## Problem it solves

Two distinct problems sit behind these conventions.

The first is change over time. An API is a promise to other programs: send this
request, get back this shape. The moment a second program depends on that
promise, the service can no longer freely change it — renaming a field or
dropping one breaks every caller that still expects the old shape. The naive
prior approach is to change the response in place and hope every caller updates
at the same time. In practice they do not, because the service author does not
control the callers' release schedules, so a "small" change becomes an outage for
whoever upgraded last.

The second is addressing. Early services often grew one address at a time, each
named however its author felt like that day — a Uniform Resource Locator (URL),
the full web address of one endpoint, might read like an action ("getUserList",
"create-resume", "fetchMatches"). With dozens of these, no caller can guess an
address without reading documentation, and two endpoints that do the same kind of
thing look nothing alike.

The third is time. A date written as "03/04/2024 14:30" is ambiguous on almost
every axis: which number is the month, and in whose time zone is 14:30? When one
machine writes a date in its local time and another reads it assuming a different
zone, the two disagree by hours without either noticing. The prior approach —
storing whatever local time the server happened to run in — turns every
cross-time-zone comparison into a latent bug.

Versioning the base path, naming resources as plurals, and pinning every
timestamp to one unambiguous format each remove one of these problems at the
design level rather than patching it case by case.

## Mental model

Think of a large public library.

1. Every book is shelved by a stable catalogue rule, not by whoever happened to
   put it away — so anyone can find a book from its catalogue number without
   asking a librarian. This is the resource-naming rule: addresses follow one
   predictable pattern instead of one-off names.
2. The shelves are grouped into named sections by category ("history",
   "science"), each holding many books — a plural collection, not one book per
   sign. This is the plural-resource rule: an address names a collection, and an
   individual item is found by its identifier within that collection.
3. When the library reorganises, it does not move every book overnight and hope
   nobody is mid-visit. It opens a new wing and keeps the old one running until
   readers have moved across. This is versioning: a new version of the API is a
   new wing, and the old one keeps serving its existing readers.
4. Every date stamp in the records room is written in one master clock and one
   fixed format, so a record filed by the morning shift and one filed by the
   night shift can be compared directly. This is the UTC timestamp rule.

The pattern across all four is the same: agree on one rule up front so that
neither the caller nor a future maintainer has to negotiate it per case.

## How it works

### Versioning the path

A versioned API places a version label as the first segment after the base of the
address — for example, a prefix beginning `/api/v1`. Every endpoint the service
offers lives under that prefix. When the service needs to make a change that would
break existing callers — removing a field, changing a field's type, altering the
meaning of a value — it does not edit the existing endpoints. Instead it
introduces a parallel set of endpoints under a new label such as `/api/v2`, while
the old set keeps behaving exactly as before. Callers migrate on their own
schedule, and the day the last caller has moved off the old version, the service
can retire it. The version label is therefore a contract boundary: everything
under one label is a stable promise.

Non-breaking changes — adding a new optional field, adding a whole new endpoint —
do not need a new version, because they cannot break a caller that ignores what it
does not understand. The version bumps only on breaking changes. This keeps the
number of live versions small.

### Plural resource names and HTTP methods

A resource-oriented address names a _collection of things_ with a plural noun, and
the protocol's own methods describe the action. Instead of one address per action,
there is one address per collection, and the verb lives in the HTTP method:

- a read of the whole collection and a read of one member share the same noun,
  differing only by whether an identifier follows it;
- creating a member, replacing one, and removing one reuse the same nouns with
  different HTTP methods (the protocol already defines a method for "create here",
  a method for "fetch", a method for "remove", and so on).

Because the noun is always plural and the action is always a method, a caller who
has seen one collection can predict the shape of every other collection's
addresses without reading more documentation. Putting an action verb in the path
("create", "fetch", "delete") is treated as a smell: it duplicates what the method
already says and makes two similar operations look different.

### ISO 8601 timestamps in UTC

ISO 8601 is an international standard for writing dates and times as text in a
fixed, sortable order: year, then month, then day, then the time, largest unit
first. A widely used internet profile of that standard, Request for Comments (RFC)
3339, nails down the exact spelling services use on the wire. The key piece is the
zone designator at the end. A trailing `Z` (spoken "Zulu") means the time is in
UTC, the worldwide reference clock from which every local time is an offset. So a
value like `2024-03-04T14:30:00Z` is unambiguous: it is that instant in UTC, and
any reader anywhere converts it to local time the same way.

Two habits make this reliable. First, store the instant with its zone information
preserved rather than as a bare local time, so the value never silently inherits
whatever zone a particular machine runs in. Second, serialise it on the wire in
the ISO 8601 form with the `Z` suffix, and give the date fields consistent names
across every resource, so a caller writes one date-parsing routine and reuses it
everywhere. Because the textual format sorts in the same order as the underlying
instants, these timestamps are also directly comparable and sortable as plain
strings.

## MatchLayer Phase 1 usage

In MatchLayer every feature router declares its own versioned, plural base path,
so the version label and the resource noun are defined in one place per feature
rather than scattered across handlers. The resumes router, defined in
`apps/api/src/matchlayer_api/api/resumes/router.py`, registers its base path on
the router object itself — the version segment and the plural noun `resumes` are
both visible on this single line:

Source: `apps/api/src/matchlayer_api/api/resumes/router.py`

```python
router = APIRouter(prefix="/api/v1/resumes", tags=["resumes"])
```

The matches router, defined in
`apps/api/src/matchlayer_api/api/matches/router.py`, follows the identical
convention with its own plural noun `matches`:

Source: `apps/api/src/matchlayer_api/api/matches/router.py`

```python
router = APIRouter(prefix="/api/v1/matches", tags=["matches"])
```

Because each router carries its own version-and-resource prefix, the application
factory in `apps/api/src/matchlayer_api/main.py` mounts them without adding any
further prefix — the version label is owned by the routers, in one obvious spot
each. Individual members are addressed by appending an identifier path parameter
to the same plural noun (the single-resume and single-match read, update, and
delete handlers all hang off `{resume_id}` and `{match_id}` under those same
prefixes), and the action is always carried by the HTTP method rather than by a
verb in the path.

The timestamp convention lives in the database model layer at
`apps/api/src/matchlayer_api/db/models.py`. Every table that records when a row
was created and last changed uses the same two column names, each typed as a
timezone-aware column so the stored instant keeps its zone rather than degrading
to a bare local time:

Source: `apps/api/src/matchlayer_api/db/models.py`

```python
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
```

`DateTime(timezone=True)` maps to the PostgreSQL `timestamptz` type, which stores
the instant in UTC; the `created_at` and `updated_at` names are reused on every
user-facing table (the users, resumes, and match-results models all repeat the
same pair). When these values are serialised back to callers they are rendered in
the ISO 8601 form with the `Z` suffix, so the wire format matches the convention
described above.

## Common pitfalls

- **Mistake:** Shipping an API with no version segment in the path, intending to "add versioning later".
  **Symptom:** The first breaking change — a renamed or removed field — silently breaks every existing caller, because there is no parallel path to migrate them onto.
  **Recovery:** Introduce the `/api/v1` prefix from the very first endpoint; place all routes under it so a future `/api/v2` can be added beside it without disturbing live callers.

- **Mistake:** Putting action verbs and singular nouns in the path, such as an address that reads like "create-resume" or "getMatch".
  **Symptom:** Endpoints that do similar things look unrelated, and callers cannot guess a new address without reading documentation for each one.
  **Recovery:** Name each collection with a plural noun and let the HTTP method carry the action; address a single member by appending its identifier to the same plural noun.

- **Mistake:** Storing timestamps as naive local times with no zone, or returning them without the `Z` suffix.
  **Symptom:** Two services or a service and a browser disagree about what instant a date refers to, producing off-by-hours errors that appear only across time zones or around daylight-saving changes.
  **Recovery:** Store the instant in a timezone-aware column (`timestamptz`) so it is kept in UTC, and serialise it on the wire as ISO 8601 with the trailing `Z`.

- **Mistake:** Using inconsistent date field names across resources (`createdAt` on one, `created` on another, `date_added` on a third).
  **Symptom:** A caller cannot write one generic routine to read timestamps and must special-case every resource, and client code drifts out of sync as new resources appear.
  **Recovery:** Standardise on one pair of names — `created_at` and `updated_at` — and reuse them on every resource that records creation and modification times.

## External reading

- [Mozilla Developer Network: Date.prototype.toISOString()](https://developer.mozilla.org/docs/Web/JavaScript/Reference/Global_Objects/Date/toISOString)
- [Date and Time on the Internet: Timestamps (the RFC 3339 specification)](https://datatracker.ietf.org/doc/html/rfc3339)
- [FastAPI documentation: Bigger Applications – Multiple Files](https://fastapi.tiangolo.com/tutorial/bigger-applications/)
- [Python documentation: datetime — basic date and time types](https://docs.python.org/3/library/datetime.html)
