"""LangGraph checkpointer schema via PostgresSaver.setup()

Revision ID: 0006_checkpointer_schema
Revises: 0005_agent_tables
Create Date: 2026-07-08

Creates the LangGraph Postgres checkpointer tables for ``phase-4-agentic``
by invoking LangGraph's own setup routine from within this migration —
never hand-written DDL, and never runtime DDL (Requirement 2.5, design
decision D3): neither the API_App nor the Agent_Worker creates or alters
checkpointer schema at runtime; schema management stays in Alembic per
``conventions.md``.

What setup() creates (langgraph-checkpoint-postgres >=3.1,<4.0)
---------------------------------------------------------------
* ``checkpoint_migrations`` — the library's own schema-version ledger.
* ``checkpoints`` — one Agent_State snapshot per graph transition,
  keyed by ``thread_id`` (= ``agent_jobs.id``, Requirement 2.2).
* ``checkpoint_blobs`` — large channel values, split out of the
  snapshot row.
* ``checkpoint_writes`` — pending writes for in-flight supersteps.
* ``*_thread_id_idx`` indexes on all three data tables.

Content is serialized ``AgentState`` — Internal classification by
construction, since raw resume text can never enter graph state
(Requirement 2.3; see the design's Data Models section).

Sync saver, deliberate (D3 nuance)
----------------------------------
The design names ``AsyncPostgresSaver.setup()``; this migration invokes
the **sync** ``PostgresSaver.setup()`` instead. Both classes share the
identical ``MIGRATIONS`` DDL list in ``langgraph.checkpoint.postgres.base``
(``AsyncPostgresSaver`` is the same schema behind an async driver), so
the resulting objects are byte-for-byte what the runtime's async saver
expects. Alembic is synchronous by design here (``conventions.md``:
"Sync code only inside ML scripts and Alembic migrations"; ``env.py``
swaps ``+asyncpg`` for ``+psycopg``), and bridging an event loop inside
a migration buys nothing but fragility.

Dedicated autocommit connection, deliberate
-------------------------------------------
``setup()`` runs ``CREATE INDEX CONCURRENTLY`` statements, which
PostgreSQL refuses inside a transaction block — and Alembic runs each
revision inside one. We therefore open a short-lived dedicated psycopg
connection via ``PostgresSaver.from_conn_string`` (which sets
``autocommit=True`` and the ``dict_row`` row factory the saver requires)
against the same database URL Alembic is already connected to, run
``setup()``, and close it. Consequence: this DDL commits outside the
Alembic transaction. That is safe because ``setup()`` is idempotent —
every statement is ``IF NOT EXISTS``-guarded and the library tracks its
position in ``checkpoint_migrations`` — so a failure between setup()
and Alembic's version stamp simply re-runs as a no-op.

Offline mode is unsupported: ``alembic upgrade --sql`` renders SQL
without connecting, but the checkpointer DDL lives inside the library's
setup routine, not in this file. Rendering it here would mean
hand-copying library DDL — exactly what Requirement 2.5 forbids.

Downgrade drops the four tables (indexes fall with them) inside the
normal Alembic transaction; plain ``DROP TABLE`` is transactional.

No fine-grained GRANT/REVOKE block is needed: ``0001``'s
``ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT,
UPDATE, DELETE ON TABLES TO matchlayer_app`` covers the new tables.

Design reference: D3, "LangGraph checkpointer tables" (phase-4-agentic);
Requirement 2.5.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import context, op
from langgraph.checkpoint.postgres import PostgresSaver

revision: str = "0006_checkpointer_schema"
down_revision: str | Sequence[str] | None = "0005_agent_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The objects PostgresSaver.setup() owns, in FK-safe drop order (none of
# them reference each other, but data tables go before the version
# ledger so a partially-failed downgrade never strands a ledger that
# claims migrations which no longer have tables).
_CHECKPOINTER_TABLES: tuple[str, ...] = (
    "checkpoint_writes",
    "checkpoint_blobs",
    "checkpoints",
    "checkpoint_migrations",
)


def _libpq_conn_string() -> str:
    """Return a libpq-compatible URL for the database Alembic is using.

    Reuses the bind's engine URL (already Settings-derived and
    driver-swapped by ``env.py``) rather than re-reading Settings, so
    this migration targets exactly the database the rest of the
    revision chain runs against — including tests that point Alembic at
    a scratch database. psycopg's ``Connection.connect`` speaks libpq
    URLs (``postgresql://…``), not SQLAlchemy driver-qualified ones, so
    the ``+psycopg`` suffix is stripped.
    """
    url = op.get_bind().engine.url.render_as_string(hide_password=False)
    return url.replace("+psycopg", "", 1)


def upgrade() -> None:
    if context.is_offline_mode():
        msg = (
            "0006_checkpointer_schema cannot render offline SQL: the "
            "checkpointer DDL is owned by langgraph-checkpoint-postgres' "
            "setup() routine (Requirement 2.5). Run 'alembic upgrade' "
            "online against the target database."
        )
        raise NotImplementedError(msg)

    # LangGraph's setup routine on a dedicated autocommit connection —
    # see the module docstring for why Alembic's own transactional
    # connection cannot host CREATE INDEX CONCURRENTLY.
    with PostgresSaver.from_conn_string(_libpq_conn_string()) as saver:
        saver.setup()


def downgrade() -> None:
    if context.is_offline_mode():
        msg = (
            "0006_checkpointer_schema cannot render offline SQL; "
            "run 'alembic downgrade' online against the target database."
        )
        raise NotImplementedError(msg)

    # Plain DROP TABLE is transactional — Alembic's revision transaction
    # applies, so a failed downgrade rolls back atomically. IF EXISTS
    # keeps the downgrade re-runnable against a partially-created schema.
    for table in _CHECKPOINTER_TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table}")
