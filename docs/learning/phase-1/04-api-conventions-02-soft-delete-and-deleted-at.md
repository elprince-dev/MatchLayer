# Soft delete and the `deleted_at` timestamp

## Introduction

Soft delete is the practice of marking a record as deleted by setting a
timestamp column — conventionally named `deleted_at` — instead of physically
removing the row from the database. A row whose `deleted_at` is empty is
"live"; a row whose `deleted_at` holds a moment in time is "deleted" and is
hidden from ordinary reads, even though its bytes remain on disk. This document
explains why a system that holds people's data prefers this reversible marker
over an irreversible removal, and how every read, write, and delete path has to
cooperate for the pattern to be safe.

The core idea is that deletion becomes a state change rather than destruction.
Because the row survives, an accidental delete can be undone, an audit log (a
durable, time-ordered record of who did what) still points at a real row, and
the genuine erasure of bytes can be deferred to a deliberate, separate process
that runs only when a person actually asks for it.

**Learning outcomes** — after reading this document you will be able to:

- Explain what a `deleted_at` timestamp column is and how a null versus non-null value distinguishes a live row from a deleted one.
- Describe why every read query must filter out soft-deleted rows for the pattern to behave like real deletion.
- Explain why user data is retained on a soft delete and only purged later in response to an explicit request.
- Recognise the common mistakes when adopting soft delete and recover from them.

Prerequisites:

- [PostgreSQL 16 fundamentals](07-database-01-postgresql-fundamentals.md) — covers the relational model, rows, columns, and the timestamp data type this pattern is built on.
- [SQLAlchemy async engine and the per-request session](03-backend-04-sqlalchemy-async-and-session-dependency.md) — covers the query layer where the soft-delete filter is applied.
- [The append-only audit log](06-auth-06-append-only-audit-log.md) — covers the event record that a delete writes alongside the timestamp change.

## Problem it solves

Applications that store data owned by people routinely receive a "delete this"
request — remove a resume, close an account, drop a saved result. The naive
response is to issue a `DELETE` statement that physically removes the row. That
choice creates three concrete problems at once.

The first is that the action is irreversible. A misclick by a user, or a bug
that deletes the wrong rows, destroys data that nobody can get back. The prior
approach to that risk was to lean on database backups and hope a recent enough
snapshot existed — slow to restore, coarse-grained, and useless for recovering a
single row that a user removed by mistake an hour ago.

The second problem is referential damage. Other records often point at the row
being removed: an audit entry references the account that performed an action, a
scoring result references the resume it analysed. Physically deleting the
referenced row either breaks those links or, with cascading deletes, silently
removes the dependent history too, which defeats the point of keeping a record
in the first place.

The third problem is that immediate, total erasure is rarely what the law or the
product actually wants. Data-protection expectations distinguish "hide it from
the user now" from "destroy every byte". Doing both in a single irreversible
step removes any window to honour the first without the risk of the second.
Soft delete solves all three by separating the _intent_ to delete (set a
timestamp now, hide the row immediately) from the _destruction_ of bytes (a
later, deliberate purge).

## Mental model

Think of the recycle bin (the holding area for deleted files) on a desktop
computer. Dragging a file to the bin does not erase it: the file is hidden from
the folder you were looking at but still occupies disk space, and you can drag
it back out to restore it. Only "empty the bin" — a separate, explicit action —
actually frees the space. The `deleted_at` timestamp column is the bin: setting
it moves the row out of view, and a later purge job is the act of emptying.

Walk through the full lifecycle of one deleted-then-restored record:

1. A row is created with its timestamp empty, so every ordinary read treats it as live and shows it to the user.
2. The user asks to delete the row, and the system writes the current moment into the timestamp column rather than removing the row.
3. From that point on, every read query that keeps only rows with an empty timestamp skips this row, so the user sees it as gone even though all its data remains on disk.
4. An administrator or the user decides the delete was a mistake and clears the timestamp back to empty, and the row reappears in every filtered read, fully intact.
5. Much later, a deliberate purge job physically removes rows whose timestamp has been set for longer than the retention window, and only then are the bytes actually destroyed.

Steps 2 and 3 are the heart of the pattern — hiding without destroying — and
step 5 is the only place real erasure happens.

## How it works

Soft delete adds one nullable timestamp column to a table. The column has two
meanings packed into it: whether the row is deleted, and — when it is — the exact
moment it happened. A null value means the row is active. A non-null value is
both the boolean "this is deleted" and the timestamp "this is when".

For this single column to behave like real deletion, three groups of operations
must agree on its meaning:

1. **Reads must exclude deleted rows.** Every query that lists or fetches "the
   data" adds a condition keeping only rows whose timestamp is null. If even one
   read path forgets the condition, deleted rows leak back into view and the
   marker stops behaving like deletion.
2. **The delete operation sets the timestamp instead of removing the row.** What
   the outside world calls "delete" becomes an update: write the current time
   into the column. The row, and every byte it holds, stays exactly where it was.
3. **A later purge performs the real removal.** Genuine erasure of the bytes is a
   separate, deliberate job — driven by a retention policy or an explicit erasure
   request — that physically removes rows whose timestamp is older than some
   threshold, or that a person has specifically asked to be destroyed.

A useful way to picture the cost is to remember that the hiding works only if
_every_ read remembers to filter. Two properties follow from this design.
Deletes become reversible: clearing the timestamp restores the row to every
query that filters on it. And deletes become auditable: because the row
survives, a separate event record can reference it by identifier long after the
user can no longer see it. The cost is discipline — the row stays hidden only
while every read path applies the filter, which is the single most important
rule of the pattern.

## MatchLayer Phase 1 usage

In Phase 1 the soft-delete marker is a nullable `deleted_at` column declared on
every user-facing table in the SQLAlchemy models at
`apps/api/src/matchlayer_api/db/models.py`. The column is a timezone-aware
timestamp that defaults to nothing, so a freshly inserted row starts life
active:

Source: `apps/api/src/matchlayer_api/db/models.py`

```python
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
```

The "delete" path never issues a physical `DELETE`. In the resume service at
`apps/api/src/matchlayer_api/services/resumes.py`, soft-deleting an owned resume
stamps the current time into `deleted_at`, bumps `updated_at`, and stages an
audit row — all inside the caller's transaction, so the marker and the audit
event commit together:

Source: `apps/api/src/matchlayer_api/services/resumes.py`

```python
        now = _now()
        resume.deleted_at = now
        resume.updated_at = now
```

The other half of the contract lives in the read paths. Every query that returns
a user's data adds a `deleted_at IS NULL` condition, so a soft-deleted row
disappears from listings and lookups even though its bytes are untouched. The
list query in the same service is representative:

Source: `apps/api/src/matchlayer_api/services/resumes.py`

```python
        stmt = select(Resume).where(
            Resume.user_id == user.id,
            Resume.deleted_at.is_(None),
        )
```

The scoring service at `apps/api/src/matchlayer_api/services/matching.py` follows
the same rule for match results, with one deliberate exception: a stored match
result is still returned even after its underlying resume is soft-deleted,
because the score and analysis are retained independently of the resume's
lifecycle. Crucially, Phase 1 stops at the marker. The stored file bytes and the
extracted text are intentionally retained after a soft delete; physical erasure
of bytes and a scheduled purge job are deferred to a later phase and run only in
response to an explicit account-deletion request, never automatically. This is
the project-wide convention: user data is not hard-deleted without an explicit
request.

## Common pitfalls

- **Mistake:** Adding the `deleted_at` column and the delete path, but forgetting to add the `deleted_at IS NULL` filter to one or more read queries.
  **Symptom:** A row a user "deleted" reappears in a list, a search result, or a detail page, while other screens correctly hide it — the deletion seems to half-work.
  **Recovery:** Audit every query that reads the table and add the null-timestamp condition; centralise the filter in a shared query helper or a base query so a new read path cannot silently omit it.

- **Mistake:** Treating the soft delete as the end of the story and never building the purge step, so deleted rows accumulate forever.
  **Symptom:** The table grows without bound, queries slow down as the engine scans ever more hidden rows, and personal data that should have been erased lingers indefinitely.
  **Recovery:** Add a retention or erasure job that physically removes rows whose `deleted_at` is older than the agreed window, or that a user has explicitly asked to be destroyed, and add an index that keeps the live-row filter fast.

- **Mistake:** Enforcing soft delete only at the application layer while leaving a unique constraint that ignores the timestamp, so a "deleted" value still blocks reuse.
  **Symptom:** A user deletes a record and then cannot recreate one with the same natural key — for example re-registering a previously deleted email — because the hidden row still occupies the unique slot.
  **Recovery:** Make the uniqueness rule aware of soft delete (for instance a partial unique index that applies only where the timestamp is null) so a hidden row no longer collides with a new live one.

- **Mistake:** Writing the marker and the audit event in separate transactions, committing the delete first and emitting the event afterward.
  **Symptom:** During an investigation you find rows marked deleted with no matching audit entry, or audit entries for deletes that were rolled back, because a crash landed between the two commits.
  **Recovery:** Stage the timestamp change and the audit row on the same session and let them commit together, so the marker and its record share one all-or-nothing transaction.

## External reading

- [PostgreSQL 16 documentation: date/time types (`timestamptz`)](https://www.postgresql.org/docs/16/datatype-datetime.html)
- [SQLAlchemy 2.0 querying guide: filtering rows](https://docs.sqlalchemy.org/en/20/orm/queryguide/select.html)
- [Alembic operations reference: adding and altering columns](https://alembic.sqlalchemy.org/en/latest/ops.html)
