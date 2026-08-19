"""Migration apply/rollback + schema assertions for 0006 (task 2.2).

Validates Requirement 2.5 of ``phase-4-agentic``: the LangGraph
checkpointer schema is created by invoking LangGraph's setup routine
from within an Alembic migration — never hand-written DDL, never
runtime DDL.

Two layers of coverage, mirroring ``test_migration_0005_agent_tables.py``:

* ``test_0006_revision_chain`` — a structural check that the revision
  file declares ``revision = "0006_checkpointer_schema"`` and chains off
  ``0005_agent_tables``. It imports the migration module by file path
  and inspects module-level identifiers only; importing does NOT execute
  ``upgrade()`` / ``downgrade()``, so it needs no database.

* ``test_0006_upgrade_delegates_to_langgraph_setup`` — a structural
  guarantee for the heart of Requirement 2.5: the migration source
  invokes the library's ``setup()`` and contains no hand-written
  ``CREATE TABLE`` for checkpointer objects.

* ``test_0006_apply_rollback_and_schema`` — the live apply/rollback
  cycle against the docker-compose Postgres, asserting the four tables
  ``setup()`` owns (``checkpoint_migrations``, ``checkpoints``,
  ``checkpoint_blobs``, ``checkpoint_writes``) exist after upgrade and
  are gone after downgrade. Column-level shape belongs to the library's
  own versioned migrations, not to us — we assert presence/absence, not
  internals, so a library minor bump doesn't break the suite.

Gating mirrors the sibling migration tests: the live test is skipped
when Postgres is unreachable, and the ``finally`` restores the shared
database to head so sibling integration tests see the full schema.
"""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from matchlayer_api.config import get_settings

from .conftest import postgres_available

requires_postgres = pytest.mark.skipif(
    not postgres_available(),
    reason="Postgres not available (docker-compose not running)",
)

# Revision identifiers under test.
REV_0006 = "0006_checkpointer_schema"
REV_0005 = "0005_agent_tables"

# The tables langgraph-checkpoint-postgres' setup() creates.
CHECKPOINTER_TABLES = frozenset(
    {
        "checkpoint_migrations",
        "checkpoints",
        "checkpoint_blobs",
        "checkpoint_writes",
    }
)


# ---------------------------------------------------------------------------
# Helpers (same shape as test_migration_0005_agent_tables.py)
# ---------------------------------------------------------------------------


def _apps_api_dir() -> Path:
    """Return ``apps/api`` (parents: integration -> tests -> apps/api)."""
    return Path(__file__).resolve().parents[2]


def _migration_path() -> Path:
    return _apps_api_dir() / "alembic" / "versions" / f"{REV_0006}.py"


def _load_migration_module() -> ModuleType:
    """Import the 0006 revision file by path without running its DDL."""
    spec = importlib.util.spec_from_file_location("matchlayer_migration_0006", _migration_path())
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _alembic_config() -> Config:
    """Build an Alembic ``Config`` from the committed ``alembic.ini``."""
    return Config(str(_apps_api_dir() / "alembic.ini"))


def _sync_database_url() -> str:
    """Mirror ``env.py``'s ``+asyncpg`` -> ``+psycopg`` driver swap."""
    url = str(get_settings().database_url)
    if "+asyncpg" in url:
        return url.replace("+asyncpg", "+psycopg", 1)
    if "+psycopg" in url:
        return url
    return url.replace("postgresql://", "postgresql+psycopg://", 1)


def _snapshot_tables() -> set[str]:
    """Reflect the live table set for presence/absence assertions."""
    engine = create_engine(_sync_database_url(), poolclass=NullPool)
    try:
        return set(sa.inspect(engine).get_table_names())
    finally:
        engine.dispose()


def _run_migration_cycle() -> tuple[set[str], set[str]]:
    """Apply 0006, snapshot, roll back, snapshot; always restore to head."""
    cfg = _alembic_config()
    try:
        command.upgrade(cfg, "head")
        command.downgrade(cfg, REV_0005)
        command.upgrade(cfg, REV_0006)
        after_upgrade = _snapshot_tables()
        command.downgrade(cfg, REV_0005)
        after_downgrade = _snapshot_tables()
        return after_upgrade, after_downgrade
    finally:
        command.upgrade(cfg, "head")


# ---------------------------------------------------------------------------
# Structural tests (no database required)
# ---------------------------------------------------------------------------


def test_0006_revision_chain() -> None:
    """0006 declares the correct revision id and chains off 0005."""
    module = _load_migration_module()
    assert module.revision == REV_0006
    assert module.down_revision == REV_0005
    # Both directions are defined so the rollback path exists.
    assert callable(module.upgrade)
    assert callable(module.downgrade)


def test_0006_upgrade_delegates_to_langgraph_setup() -> None:
    """Requirement 2.5's core: the DDL comes from LangGraph's setup().

    The migration must invoke the library's setup routine rather than
    hand-copying checkpointer DDL. Source-level assertions: the module
    imports ``PostgresSaver`` and calls ``.setup()``, and contains no
    hand-written ``CREATE TABLE`` statement.
    """
    source = _migration_path().read_text(encoding="utf-8")
    assert "PostgresSaver" in source
    assert ".setup()" in source
    assert "CREATE TABLE" not in source.upper()


# ---------------------------------------------------------------------------
# Live apply/rollback test (real Postgres)
# ---------------------------------------------------------------------------


@requires_postgres
@pytest.mark.asyncio
async def test_0006_apply_rollback_and_schema() -> None:
    """Applying 0006 creates the checkpointer tables; downgrade removes them.

    Covers Requirement 2.5: the schema exists after ``alembic upgrade``
    (created by LangGraph's setup routine, not runtime code) and the
    downgrade path removes every checkpointer table.
    """
    after_upgrade, after_downgrade = await asyncio.to_thread(_run_migration_cycle)

    # --- after upgrade: all four setup()-owned tables exist ------------
    assert after_upgrade >= CHECKPOINTER_TABLES

    # --- after downgrade: every checkpointer table is gone -------------
    assert not (CHECKPOINTER_TABLES & after_downgrade)
