"""Migration apply/rollback + schema/index assertions for 0005 (task 2.1).

Validates Requirements 12.1 and 10.5 of ``phase-4-agentic``.

Two layers of coverage, mirroring ``test_migration_0002_schema.py``:

* ``test_0005_revision_chain`` -- a structural check that the revision
  file declares ``revision = "0005_agent_tables"`` and chains off
  ``0004_llm_tables``. It imports the migration module by file path and
  inspects module-level identifiers only; importing does NOT execute
  ``upgrade()`` / ``downgrade()``, so it needs no database.

* ``test_0005_apply_rollback_and_schema`` -- the live apply/rollback
  cycle against the docker-compose Postgres, asserting the two tables,
  their full column sets, the status CHECK constraints (Requirement
  12.1's closed value sets), and the four documented indexes --
  including the **partial unique** index
  ``agent_jobs_match_user_inflight_uniq`` that provides the in-flight
  idempotency guarantee of Requirement 10.5 (design decision D5).
  Downgrade must remove everything the revision created.

Gating mirrors the sibling migration tests: the live test is skipped
when Postgres is unreachable, and the ``finally`` restores the shared
database to head so sibling integration tests see the full schema.
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
REV_0005 = "0005_agent_tables"
REV_0004 = "0004_llm_tables"

# Columns Requirement 12.1 enumerates for each table.
EXPECTED_JOB_COLUMNS = frozenset(
    {
        "id",
        "user_id",
        "match_id",
        "status",
        "attempts",
        "created_at",
        "started_at",
        "completed_at",
        "result_json",
        "error_json",
    }
)
EXPECTED_RUN_COLUMNS = frozenset(
    {
        "id",
        "job_id",
        "agent_name",
        "input_state_json",
        "output_state_json",
        "latency_ms",
        "status",
        "failure_reason_json",
        "created_at",
    }
)

# Named indexes the migration documents (conventions.md rationale).
EXPECTED_JOB_INDEXES = frozenset(
    {
        "agent_jobs_user_id_idx",
        "agent_jobs_match_user_status_idx",
        "agent_jobs_match_user_inflight_uniq",
    }
)
EXPECTED_RUN_INDEXES = frozenset({"agent_runs_job_id_idx"})


# ---------------------------------------------------------------------------
# Helpers (same shape as test_migration_0002_schema.py)
# ---------------------------------------------------------------------------


def _apps_api_dir() -> Path:
    """Return ``apps/api`` (parents: integration -> tests -> apps/api)."""
    return Path(__file__).resolve().parents[2]


def _load_migration_module() -> ModuleType:
    """Import the 0005 revision file by path without running its DDL."""
    path = _apps_api_dir() / "alembic" / "versions" / f"{REV_0005}.py"
    spec = importlib.util.spec_from_file_location("matchlayer_migration_0005", path)
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


def _snapshot_schema() -> dict[str, Any]:
    """Reflect the live schema into a plain dict for assertion."""
    engine = create_engine(_sync_database_url(), poolclass=NullPool)
    try:
        insp = sa.inspect(engine)
        tables = set(insp.get_table_names())
        snap: dict[str, Any] = {"tables": tables}
        for table in ("agent_jobs", "agent_runs"):
            if table in tables:
                snap[f"{table}_indexes"] = {ix["name"]: dict(ix) for ix in insp.get_indexes(table)}
                snap[f"{table}_columns"] = {
                    col["name"]: bool(col["nullable"]) for col in insp.get_columns(table)
                }
                snap[f"{table}_pk"] = list(
                    insp.get_pk_constraint(table).get("constrained_columns") or []
                )
                snap[f"{table}_checks"] = {
                    ck["name"]: ck["sqltext"] for ck in insp.get_check_constraints(table)
                }
            else:
                snap[f"{table}_indexes"] = {}
                snap[f"{table}_columns"] = {}
                snap[f"{table}_pk"] = []
                snap[f"{table}_checks"] = {}
        return snap
    finally:
        engine.dispose()


def _run_migration_cycle() -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply 0005, snapshot, roll back, snapshot; always restore to head."""
    cfg = _alembic_config()
    try:
        command.upgrade(cfg, "head")
        command.downgrade(cfg, REV_0004)
        command.upgrade(cfg, REV_0005)
        after_upgrade = _snapshot_schema()
        command.downgrade(cfg, REV_0004)
        after_downgrade = _snapshot_schema()
        return after_upgrade, after_downgrade
    finally:
        command.upgrade(cfg, "head")


# ---------------------------------------------------------------------------
# Structural test (no database required)
# ---------------------------------------------------------------------------


def test_0005_revision_chain() -> None:
    """0005 declares the correct revision id and chains off 0004."""
    module = _load_migration_module()
    assert module.revision == REV_0005
    assert module.down_revision == REV_0004
    # Both directions are defined so the rollback path exists.
    assert callable(module.upgrade)
    assert callable(module.downgrade)


# ---------------------------------------------------------------------------
# Live apply/rollback test (real Postgres)
# ---------------------------------------------------------------------------


@requires_postgres
@pytest.mark.asyncio
async def test_0005_apply_rollback_and_schema() -> None:
    """Applying 0005 creates both tables + indexes; downgrade removes them.

    Covers Requirement 12.1 (table/column sets, closed status value
    sets via CHECK constraints, documented indexes) and Requirement
    10.5 (the partial unique in-flight idempotency index).
    """
    after_upgrade, after_downgrade = await asyncio.to_thread(_run_migration_cycle)

    # --- after upgrade: both tables exist (plural snake_case) ---------
    assert "agent_jobs" in after_upgrade["tables"]
    assert "agent_runs" in after_upgrade["tables"]

    # --- after upgrade: full column set per table (12.1) ---------------
    assert set(after_upgrade["agent_jobs_columns"]) >= EXPECTED_JOB_COLUMNS
    assert set(after_upgrade["agent_runs_columns"]) >= EXPECTED_RUN_COLUMNS

    # --- after upgrade: UUIDv7 id PKs ----------------------------------
    assert after_upgrade["agent_jobs_pk"] == ["id"]
    assert after_upgrade["agent_runs_pk"] == ["id"]

    # --- after upgrade: nullability of the lifecycle/result columns ----
    jobs_cols = after_upgrade["agent_jobs_columns"]
    assert jobs_cols["started_at"] is True  # NULL until running
    assert jobs_cols["completed_at"] is True  # NULL until terminal
    assert jobs_cols["result_json"] is True  # set iff completed
    assert jobs_cols["error_json"] is True  # null unless failed
    assert jobs_cols["status"] is False
    assert jobs_cols["attempts"] is False
    runs_cols = after_upgrade["agent_runs_columns"]
    assert runs_cols["failure_reason_json"] is True  # null iff completed
    assert runs_cols["input_state_json"] is False
    assert runs_cols["output_state_json"] is False

    # --- after upgrade: closed status sets via CHECK (12.1) ------------
    job_checks = after_upgrade["agent_jobs_checks"]
    assert "agent_jobs_status_check" in job_checks
    for value in ("queued", "running", "completed", "failed"):
        assert value in job_checks["agent_jobs_status_check"]
    run_checks = after_upgrade["agent_runs_checks"]
    assert "agent_runs_status_check" in run_checks
    for value in ("completed", "degraded", "failed"):
        assert value in run_checks["agent_runs_status_check"]

    # --- after upgrade: all documented indexes exist --------------------
    job_indexes = after_upgrade["agent_jobs_indexes"]
    assert set(job_indexes) >= EXPECTED_JOB_INDEXES
    assert set(after_upgrade["agent_runs_indexes"]) >= EXPECTED_RUN_INDEXES

    # --- the in-flight idempotency index is partial AND unique (10.5) --
    inflight = job_indexes["agent_jobs_match_user_inflight_uniq"]
    assert inflight["unique"] is True
    assert inflight["column_names"] == ["match_id", "user_id"]
    predicate = str(inflight.get("dialect_options", {}).get("postgresql_where", ""))
    assert "queued" in predicate and "running" in predicate

    # --- after downgrade: every table + index is gone -------------------
    assert "agent_jobs" not in after_downgrade["tables"]
    assert "agent_runs" not in after_downgrade["tables"]
    assert after_downgrade["agent_jobs_indexes"] == {}
    assert after_downgrade["agent_runs_indexes"] == {}
