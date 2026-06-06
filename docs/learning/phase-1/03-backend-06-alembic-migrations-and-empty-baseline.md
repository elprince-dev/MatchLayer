# Alembic migrations and the empty baseline

## Introduction

This document explains how a database schema — the set of tables, columns, and
constraints that define where data lives — is changed over time in small, ordered,
reviewable steps, and a specific starting move called the empty baseline. The tool
is Alembic, a database migration library for Python: a migration is one
versioned change to the schema, and Alembic keeps the migrations in order and
records which ones a given database has already applied. The empty baseline is a
first migration that changes nothing — it exists only to give later migrations a
parent to build on. This belongs in the Backend track because every future change
to the database lands as one of these ordered migrations.

**Learning outcomes** — after reading this document you will be able to:

- Explain what a database migration is and why schema changes are tracked as ordered, versioned steps.
- Describe how a migration tool records which migrations a database has already applied.
- Explain why a project starts with an empty baseline migration and what it accomplishes.
- Recognise the common mistakes around migrations and recover from them.

Prerequisites: this document builds on
[SQLAlchemy async engine and the per-request session](03-backend-04-sqlalchemy-async-and-session-dependency.md),
which introduces the database access layer, and on
[Pydantic and typed settings](03-backend-02-pydantic-and-pydantic-settings.md), which explains
the configuration object the migration environment reads its connection string
from.

## Problem it solves

A database schema is not fixed: as an application grows, tables get added,
columns change, and constraints are introduced. The schema in a developer's local
database, in the test environment, and in production must all reach the same shape
in the same way. The naive approach is to edit the database by hand, running
ad-hoc statements wherever a change is needed. That approach fails as soon as more
than one environment or more than one person is involved.

The common prior approach — hand-edited schemas and shared ad-hoc statements —
has real costs:

- Two environments drift apart because a change was applied in one and forgotten in another, and nobody can tell which statements a given database has actually seen.
- There is no ordering, so a change that depends on an earlier change can be applied first and fail in a confusing way.
- A change cannot be reviewed like code or undone cleanly, because it exists only as a statement someone typed once.

Alembic solves this by turning each schema change into a versioned migration
file that lives in source control, applies in a defined order, and is recorded in
the database once applied. Any database can be brought up to the latest schema by
applying the migrations it has not yet seen, in order.

## Mental model

Think of migrations as a numbered chain of save points in a game, where each save
point records exactly what changed since the previous one. To get any save file
to the latest state, you replay the save points it has not reached yet, in order.
The game also writes down, inside each save file, which save point it last
reached, so it knows where to resume.

Here is how a tool applies that chain to a database:

1. Each migration file declares its own version and names the version that comes immediately before it, forming a chain.
2. The database holds a small bookkeeping record of the latest migration version it has applied.
3. To upgrade, the tool compares the latest available version against the database's recorded version.
4. It applies each not-yet-applied migration in chain order, running that migration's change.
5. After each one it updates the database's recorded version, so an interrupted run can resume rather than repeat.

The chain plus the recorded version is the whole mechanism: it lets any database,
however far behind, catch up deterministically.

## How it works

A migration is a file containing two directions of one schema change: an upgrade
that applies the change and a downgrade that reverses it. Each file carries a
unique version identifier and a reference to the version that precedes it, so the
files form a single ordered chain from the first migration to the most recent.
This chain is what gives migrations their deterministic order: a migration that
depends on an earlier one names that earlier one as its parent and therefore
always runs after it.

The tool tracks progress with a small bookkeeping table it maintains inside the
database itself, holding the version identifier of the most recently applied
migration. When asked to upgrade, the tool reads that recorded version, finds the
chain of migrations after it, and applies each upgrade in order, updating the
recorded version as it goes. Because the database remembers what it has applied,
running the upgrade again is safe — there is nothing left to do — and an
interrupted upgrade can resume from where it stopped. Running the tool against a
brand-new, empty database creates the bookkeeping table and applies the whole
chain from the start.

A migration environment file configures how the tool connects to the database and
where it finds the migration files. The migration files themselves are run
synchronously, even in an otherwise asynchronous application, because a migration
is a short, one-shot administrative task with no need for concurrency. The empty
baseline is the first link in the chain: a migration whose upgrade and downgrade
do nothing. It seems pointless, but it serves a real purpose — it establishes the
root of the chain and creates the bookkeeping table on a fresh database, so the
first migration that _does_ change the schema has a parent to attach to and a
clean, already-initialised version history to extend. Starting with an empty
baseline keeps the very first real change reviewable as an ordinary migration
rather than a special case.

## MatchLayer Phase 1 usage

In MatchLayer the migration environment lives in `apps/api/alembic/env.py`, and
the committed migrations live under `apps/api/alembic/versions/`. The environment
reads the connection string from the application's settings and switches it to a
synchronous driver, because migrations run synchronously:

Source: `apps/api/alembic/env.py`

```python
    settings = get_settings()
    url = str(settings.database_url)
    if "+asyncpg" in url:
        return url.replace("+asyncpg", "+psycopg", 1)
```

The first migration, `apps/api/alembic/versions/0000_baseline.py`, is the empty
baseline: both directions are deliberately no-ops, and it names no predecessor so
it is the root of the chain:

Source: `apps/api/alembic/versions/0000_baseline.py`

```python
revision: str = "0000_baseline"
down_revision: str | Sequence[str] | None = None
```

Source: `apps/api/alembic/versions/0000_baseline.py`

```python
def upgrade() -> None:
    """No-op: the baseline introduces no schema changes."""
    pass
```

This phase ships no domain tables of its own, so the baseline only creates the
bookkeeping table on a fresh database and stamps it with the baseline version.
Later migrations in the chain — the ones that add real tables — set their
predecessor to this baseline, so the whole history is reviewable as an ordered
sequence starting from a clean root.

## Common pitfalls

- **Mistake:** Editing the database schema by hand instead of writing a migration.
  **Symptom:** Environments drift apart and a later migration fails because the live schema does not match what the chain assumes.
  **Recovery:** Capture every schema change as a migration file in the chain, and bring each database up to date by applying migrations rather than editing it directly.

- **Mistake:** Editing or renaming a migration that has already been applied somewhere.
  **Symptom:** A database whose recorded version points at the old identifier can no longer find its place in the chain, and the upgrade breaks.
  **Recovery:** Treat applied migrations as immutable; make a new migration for the further change rather than altering one already in use.

- **Mistake:** Pointing the migration tool at the wrong database connection string, separate from the application's configuration.
  **Symptom:** Migrations apply to a different database than the one the application uses, so the application still sees an unmigrated schema.
  **Recovery:** Source the migration tool's connection string from the same single configuration object the application uses, so both target the same database.

- **Mistake:** Dismissing the empty baseline as unnecessary and deleting it.
  **Symptom:** The first real migration has no parent to attach to, and the version history loses its defined root.
  **Recovery:** Keep the empty baseline as the chain's root and attach the first schema-changing migration to it as its predecessor.

## External reading

- [Alembic: tutorial and core concepts](https://alembic.sqlalchemy.org/en/latest/tutorial.html)
- [Alembic: creating and running migrations (autogenerate)](https://alembic.sqlalchemy.org/en/latest/autogenerate.html)
- [Alembic: working with branches and dependencies](https://alembic.sqlalchemy.org/en/latest/branches.html)
- [SQLAlchemy: schema definition (MetaData and tables)](https://docs.sqlalchemy.org/en/20/core/metadata.html)
