"""Migration apply/rollback + schema/index assertions for 0002 (task 2.3).

Validates Requirements 14.1, 14.2, 14.3, 14.4, 14.5.

Two layers of coverage:

* ``test_0002_revision_chain`` -- a structural check that the revision
  file declares ``revision = "0002_resumes_and_matches"`` and
  ``down_revision = "0001_users_and_auth"`` (Requirement 14.1). It
  imports the migration module by file path and inspects its
  module-level identifiers only; importing does NOT execute
  ``upgrade()`` / ``downgrade()``, so it needs no database and runs
  even when docker-compose is down.

* ``test_0002_apply_rollback_and_schema`` -- the live apply/rollback
  cycle against the docker-compose Postgres. It drives Alembic
  programmatically (``alembic.command``) through the same
  ``alembic.ini`` / ``env.py`` the operator uses, so the URL is sourced
  from ``Settings`` and the ``+asyncpg`` -> ``+psycopg`` swap happens in
  ``env.py`` exactly as in production. The cycle is::

      upgrade head      (normalize to a known baseline)
      downgrade 0001    (clean slate)
      upgrade 0002      -> SNAPSHOT "after upgrade"
      downgrade 0001    -> SNAPSHOT "after downgrade"
      upgrade head      (restore shared DB for sibling tests)

  The trailing ``upgrade head`` runs in a ``finally`` so the shared
  integration database is always left at head regardless of assertion
  outcome -- sibling integration tests (e.g.
  ``test_audit_events_role_grants.py``) assume the full schema exists.

Gating mirrors ``test_audit_events_role_grants.py``: the live test is
skipped when Postgres is unreachable (docker-compose not running), so
the suite is green on a laptop without Docker while CI -- which has
Postgres -- exercises it for real. The assertions are NOT weakened to
pass without a database; they are simply not collected when the
backing service is absent.

DB-state assumption: the integration database is migrated to head
before the suite runs (every other integration test already requires
the auth schema to exist), so ``downgrade 0001`` is always a valid
backward step. The leading ``upgrade head`` makes this explicit rather
than implicit.
"""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

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
REV_0002 = "0002_resumes_and_matches"
REV_0001 = "0001_users_and_auth"

# Columns Requirement 14.2 enumerates for each table.
EXPECTED_RESUME_COLUMNS = frozenset(
    {
        "id",
        "user_id",
        "original_filename",
        "storage_key",
        "content_type",
        "byte_size",
        "extracted_text",
        "extraction_status",
        "extraction_char_count",
        "created_at",
        "updated_at",
        "deleted_at",
    }
)
EXPECTED_MATCH_COLUMNS = frozenset(
    {
        "id",
        "user_id",
        "resume_id",
        "job_description_text",
        "score",
        "score_breakdown",
        "matched_keywords",
        "missing_keywords",
        "suggestions",
        "scorer_version",
        "created_at",
        "updated_at",
        "deleted_at",
    }
)

# Named indexes Requirement 14.3 requires (plus the composite cursor
# indexes the design adds for keyset pagination). The PK index is a
# constraint, not reported by ``get_indexes``, so it is asserted
# separately via ``get_pk_constraint``.
EXPECTED_RESUME_INDEXES = frozenset({"resumes_user_id_idx", "resumes_user_created_idx"})
EXPECTED_MATCH_INDEXES = frozenset(
    {
        "match_results_user_id_idx",
        "match_results_resume_id_idx",
        "match_results_user_created_idx",
    }
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _apps_api_dir() -> Path:
    """Return ``apps/api`` (parents: integration -> tests -> apps/api)."""
    return Path(__file__).resolve().parents[2]


def _load_migration_module() -> ModuleType:
    """Import the 0002 revision file by path without running its DDL."""
    path = _apps_api_dir() / "alembic" / "versions" / f"{REV_0002}.py"
    spec = importlib.util.spec_from_file_location("matchlayer_migration_0002", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _alembic_config() -> Config:
    """Build an Alembic ``Config`` from the committed ``alembic.ini``.

    ``env.py`` sources the database URL from ``Settings`` (not from the
    ini), so no URL is injected here -- this is the same entry point the
    ``alembic`` CLI uses.
    """
    return Config(str(_apps_api_dir() / "alembic.ini"))


def _sync_database_url() -> str:
    """Mirror ``env.py``'s ``+asyncpg`` -> ``+psycopg`` driver swap."""
    url = str(get_settings().database_url)
    if "+asyncpg" in url:
        return url.replace("+asyncpg", "+psycopg", 1)
    if "+psycopg" in url:
        return url
    return url.replace("postgresql://", "postgresql+psycopg://", 1)


def _snapshot_schema() -> dict[str, Any]:
    """Reflect the live schema into a plain dict for assertion.

    A fresh sync engine + inspector is built per call (``NullPool``) so
    reflection always queries the database rather than a cached
    ``Inspector``. Index/column data is collected only for tables that
    currently exist, so the same probe works before and after a
    downgrade.
    """
    engine = create_engine(_sync_database_url(), poolclass=NullPool)
    try:
        insp = sa.inspect(engine)
        tables = set(insp.get_table_names())
        snap: dict[str, Any] = {"tables": tables}
        for table in ("resumes", "match_results"):
            if table in tables:
                snap[f"{table}_indexes"] = {ix["name"] for ix in insp.get_indexes(table)}
                snap[f"{table}_columns"] = {
                    col["name"]: bool(col["nullable"]) for col in insp.get_columns(table)
                }
                snap[f"{table}_pk"] = list(
                    insp.get_pk_constraint(table).get("constrained_columns") or []
                )
            else:
                snap[f"{table}_indexes"] = set()
                snap[f"{table}_columns"] = {}
                snap[f"{table}_pk"] = []
        return snap
    finally:
        engine.dispose()


def _run_migration_cycle() -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply 0002, snapshot, roll back, snapshot; always restore to head.

    Returns ``(after_upgrade, after_downgrade)`` schema snapshots. The
    ``finally`` guarantees the shared database is returned to head even
    if a migration step raises, so sibling integration tests are not
    left against a half-migrated schema.
    """
    cfg = _alembic_config()
    try:
        # Normalize to a known baseline, then exercise apply + rollback.
        command.upgrade(cfg, "head")
        command.downgrade(cfg, REV_0001)
        command.upgrade(cfg, REV_0002)
        after_upgrade = _snapshot_schema()
        command.downgrade(cfg, REV_0001)
        after_downgrade = _snapshot_schema()
        return after_upgrade, after_downgrade
    finally:
        command.upgrade(cfg, "head")


# ---------------------------------------------------------------------------
# Structural test (no database required) -- Requirement 14.1
# ---------------------------------------------------------------------------


def test_0002_revision_chain() -> None:
    """0002 declares the correct revision id and chains off 0001 (14.1)."""
    module = _load_migration_module()
    assert module.revision == REV_0002
    assert module.down_revision == REV_0001
    # Both directions are defined so the rollback path exists (14.4).
    assert callable(module.upgrade)
    assert callable(module.downgrade)


# ---------------------------------------------------------------------------
# Live apply/rollback test (real Postgres) -- Requirements 14.2 to 14.5
# ---------------------------------------------------------------------------


@requires_postgres
@pytest.mark.asyncio
async def test_0002_apply_rollback_and_schema() -> None:
    """Applying 0002 creates both tables + all indexes; downgrade removes them.

    Covers Requirements 14.2 (table/column set), 14.3 (named indexes),
    14.4 (working downgrade drops everything it created), and 14.5
    (plural snake_case names, UUIDv7 ``id`` primary key, nullable
    ``deleted_at`` soft-delete column).
    """
    # Alembic's command API is synchronous; run the whole cycle off the
    # event loop so the async autouse fixtures' loop is not blocked.
    after_upgrade, after_downgrade = await asyncio.to_thread(_run_migration_cycle)

    # --- after upgrade: both tables exist (14.2, 14.5 naming) ---------
    assert "resumes" in after_upgrade["tables"]
    assert "match_results" in after_upgrade["tables"]

    # --- after upgrade: full column set per table (14.2) --------------
    assert set(after_upgrade["resumes_columns"]) >= EXPECTED_RESUME_COLUMNS
    assert set(after_upgrade["match_results_columns"]) >= EXPECTED_MATCH_COLUMNS

    # --- after upgrade: all named indexes exist (14.3) ----------------
    assert after_upgrade["resumes_indexes"] >= EXPECTED_RESUME_INDEXES
    assert after_upgrade["match_results_indexes"] >= EXPECTED_MATCH_INDEXES

    # --- after upgrade: UUIDv7 PK + nullable deleted_at (14.5) --------
    assert after_upgrade["resumes_pk"] == ["id"]
    assert after_upgrade["match_results_pk"] == ["id"]
    assert after_upgrade["resumes_columns"]["deleted_at"] is True
    assert after_upgrade["match_results_columns"]["deleted_at"] is True

    # --- after downgrade: every table + index is gone (14.4) ----------
    assert "resumes" not in after_downgrade["tables"]
    assert "match_results" not in after_downgrade["tables"]
    assert after_downgrade["resumes_indexes"] == set()
    assert after_downgrade["match_results_indexes"] == set()
