"""Migration 0003 (pgvector) apply/rollback + vector write semantics (task 1.4).

Validates Requirements 1.2, 1.3, 1.6, 1.8, 1.9, 2.10.

Coverage layers, mirroring ``test_migration_0002_schema.py``:

* ``test_0003_revision_chain`` -- structural, no database: the revision
  file declares ``revision = "0003_pgvector_embeddings"`` chained off
  ``0002_resumes_and_matches`` (the Phase 1 head), both directions are
  defined, and the ``vector(384)`` dimension literal is present in the
  DDL (the single declaration point Requirement 1.6 requires).

* ``test_0003_apply_rollback_and_schema`` -- live apply/rollback cycle
  against the docker-compose Postgres (Requirement 1.2): upgrading from
  the Phase 1 head creates the extension + both embedding tables with
  the declared ``vector(384)`` columns, NOT NULL model metadata,
  ownership FKs, unique source-entity constraints, and user_id indexes;
  downgrading removes everything the revision created.

* ``test_0003_failure_rolls_back_atomically_and_is_rerunnable`` -- a
  deliberately injected schema conflict (a pre-existing table named
  ``match_embeddings``) makes the upgrade fail AFTER ``CREATE
  EXTENSION`` and ``CREATE TABLE resume_embeddings`` have run inside
  the revision's transaction. Requirement 1.3 demands atomicity: the
  failure must leave no partial objects (``resume_embeddings`` must NOT
  survive) and the alembic version must still be the Phase 1 head, so
  the migration is safely re-runnable once the conflict is removed --
  which the test then does, asserting the rerun succeeds.

* ``test_0003_pgvector_unavailability_error`` -- Requirement 1.9 needs
  a Postgres server WITHOUT the pgvector extension available. The
  docker-compose image is ``pgvector/pgvector:pg16``, so this test
  self-skips whenever ``pg_available_extensions`` reports pgvector
  (i.e. always in normal local dev / CI). Pointing
  ``MATCHLAYER_DATABASE_URL`` at a plain ``postgres:16`` instance
  exercises it for real: the upgrade must fail with an error naming the
  missing ``vector`` extension and leave the schema unchanged.

* Write-semantics tests (Requirements 1.6, 1.8, 2.10) against the
  migrated schema through the ORM models (``ResumeEmbedding`` /
  ``MatchEmbedding``): a wrong-dimension vector is rejected with
  nothing persisted -- both at the pgvector-python bind layer (the ORM
  path) and at the Postgres DDL layer (a raw INSERT bypassing the ORM
  type, proving the ``vector(384)`` DDL literal itself rejects the
  statement); FK constraints reject embeddings whose owner or source
  entity does not exist; and ``model_name`` / ``model_revision`` are
  recorded and round-trip, which is what makes the Requirement 2.7
  reuse-vs-regenerate decision decidable from stored data.

Gating follows the integration-suite convention: live tests skip when
Postgres is unreachable (docker-compose not running) so the suite stays
green on a laptop without Docker while CI exercises them for real. The
assertions are never weakened to pass without a database.

DB-state assumption: like the 0002 migration test, cycle helpers begin
with ``upgrade head`` to normalize and always restore head in a
``finally`` so sibling integration tests see the full schema.
"""

from __future__ import annotations

import asyncio
import importlib.util
import warnings
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import DBAPIError, IntegrityError, StatementError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.pool import NullPool
from uuid_utils.compat import uuid7

from matchlayer_api.config import get_settings
from matchlayer_api.db.models import (
    EMBEDDING_DIMENSION,
    MatchEmbedding,
    MatchResult,
    Resume,
    ResumeEmbedding,
    User,
)

from .conftest import UserFactory, postgres_available

requires_postgres = pytest.mark.skipif(
    not postgres_available(),
    reason="Postgres not available (docker-compose not running)",
)

# Revision identifiers under test.
REV_0003 = "0003_pgvector_embeddings"
REV_0002 = "0002_resumes_and_matches"

# Column sets the design ERD / migration declare for each table.
EXPECTED_RESUME_EMBEDDING_COLUMNS = frozenset(
    {
        "id",
        "resume_id",
        "user_id",
        "embedding",
        "model_name",
        "model_revision",
        "created_at",
        "updated_at",
    }
)
# match_embeddings has no updated_at: written once, never regenerated.
EXPECTED_MATCH_EMBEDDING_COLUMNS = frozenset(
    {
        "id",
        "match_result_id",
        "user_id",
        "embedding",
        "model_name",
        "model_revision",
        "created_at",
    }
)

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
MODEL_REVISION = "test-revision-sha"


def _vector(fill: float = 0.5) -> list[float]:
    """A valid 384-dimension vector for write tests."""
    return [fill] * EMBEDDING_DIMENSION


# ---------------------------------------------------------------------------
# Helpers (mirroring test_migration_0002_schema.py)
# ---------------------------------------------------------------------------


def _apps_api_dir() -> Path:
    """Return ``apps/api`` (parents: integration -> tests -> apps/api)."""
    return Path(__file__).resolve().parents[2]


def _migration_path() -> Path:
    return _apps_api_dir() / "alembic" / "versions" / f"{REV_0003}.py"


def _load_migration_module() -> ModuleType:
    """Import the 0003 revision file by path without running its DDL."""
    spec = importlib.util.spec_from_file_location("matchlayer_migration_0003", _migration_path())
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
    """Reflect the live schema (embedding tables + extension) into a dict.

    A fresh sync engine per call (``NullPool``) so reflection always
    queries the database. SQLAlchemy's inspector does not know the
    ``vector`` column type, which raises ``SAWarning`` -- promoted to an
    error by the project-wide ``filterwarnings = ["error"]`` -- so the
    reflection calls run under a scoped warning suppression.
    """
    engine = create_engine(_sync_database_url(), poolclass=NullPool)
    try:
        insp = sa.inspect(engine)
        tables = set(insp.get_table_names())
        snap: dict[str, Any] = {"tables": tables}
        with engine.connect() as conn:
            snap["extensions"] = {
                row[0] for row in conn.execute(sa.text("SELECT extname FROM pg_extension"))
            }
            snap["alembic_version"] = conn.execute(
                sa.text("SELECT version_num FROM alembic_version")
            ).scalar()
            for table in ("resume_embeddings", "match_embeddings"):
                if table not in tables:
                    snap[f"{table}_columns"] = {}
                    snap[f"{table}_indexes"] = set()
                    snap[f"{table}_uniques"] = set()
                    snap[f"{table}_fk_targets"] = set()
                    snap[f"{table}_embedding_type"] = None
                    continue
                with warnings.catch_warnings():
                    # 'vector' is not a type SQLAlchemy reflection knows.
                    warnings.simplefilter("ignore")
                    columns = insp.get_columns(table)
                    indexes = insp.get_indexes(table)
                    uniques = insp.get_unique_constraints(table)
                    fks = insp.get_foreign_keys(table)
                snap[f"{table}_columns"] = {col["name"]: bool(col["nullable"]) for col in columns}
                snap[f"{table}_indexes"] = {ix["name"] for ix in indexes}
                snap[f"{table}_uniques"] = {uc["name"] for uc in uniques}
                snap[f"{table}_fk_targets"] = {fk["referred_table"] for fk in fks}
                # The authoritative dimension check: the column's DDL type
                # as Postgres itself reports it (Requirement 1.6).
                snap[f"{table}_embedding_type"] = conn.execute(
                    sa.text(
                        "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
                        f"WHERE attrelid = '{table}'::regclass AND attname = 'embedding'"
                    )
                ).scalar()
        return snap
    finally:
        engine.dispose()


def _pgvector_available_on_server() -> bool:
    """True when the connected Postgres can CREATE EXTENSION vector."""
    engine = create_engine(_sync_database_url(), poolclass=NullPool)
    try:
        with engine.connect() as conn:
            count = conn.execute(
                sa.text("SELECT count(*) FROM pg_available_extensions WHERE name = 'vector'")
            ).scalar()
            return bool(count)
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# Migration cycle helpers (sync -- run via asyncio.to_thread from tests)
# ---------------------------------------------------------------------------


def _run_apply_rollback_cycle() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """upgrade head -> downgrade 0002 -> snapshot -> upgrade 0003 -> snapshot
    -> downgrade 0002 -> snapshot; always restore head.

    Returns ``(at_phase1_head, after_upgrade, after_downgrade)``.
    """
    cfg = _alembic_config()
    try:
        command.upgrade(cfg, "head")
        command.downgrade(cfg, REV_0002)
        at_phase1_head = _snapshot_schema()
        command.upgrade(cfg, REV_0003)
        after_upgrade = _snapshot_schema()
        command.downgrade(cfg, REV_0002)
        after_downgrade = _snapshot_schema()
        return at_phase1_head, after_upgrade, after_downgrade
    finally:
        command.upgrade(cfg, "head")


def _run_failure_rerun_cycle() -> dict[str, Any]:
    """Inject a schema conflict, assert-fail the upgrade, then re-run.

    A dummy table named ``match_embeddings`` is created while the DB is
    at the Phase 1 head. Upgrading to 0003 then fails at the third DDL
    statement -- AFTER ``CREATE EXTENSION`` and ``CREATE TABLE
    resume_embeddings`` executed inside the same transaction -- which is
    exactly the shape that exposes a non-atomic migration: if the
    rollback were partial, ``resume_embeddings`` would survive.

    Returns a dict with the failure outcome, post-failure snapshot, and
    post-rerun snapshot. Always restores head.
    """
    cfg = _alembic_config()
    engine = create_engine(_sync_database_url(), poolclass=NullPool)
    out: dict[str, Any] = {}
    try:
        command.upgrade(cfg, "head")
        command.downgrade(cfg, REV_0002)
        with engine.begin() as conn:
            conn.execute(sa.text("CREATE TABLE match_embeddings (dummy_marker integer)"))
        try:
            try:
                command.upgrade(cfg, REV_0003)
                out["upgrade_failed"] = False
            except Exception as exc:  # any DDL failure shape is valid here
                out["upgrade_failed"] = True
                out["error"] = str(exc)
            out["after_failure"] = _snapshot_schema()
        finally:
            # Remove the injected conflict so the rerun (and the outer
            # restore-to-head) can proceed. Only the dummy is dropped:
            # when the upgrade failed as expected, ``match_embeddings``
            # is still the single-column dummy table.
            if out.get("upgrade_failed", True):
                with engine.begin() as conn:
                    conn.execute(sa.text("DROP TABLE IF EXISTS match_embeddings"))
        # Re-runnable after the cause is fixed (Requirement 1.3).
        command.upgrade(cfg, REV_0003)
        out["after_rerun"] = _snapshot_schema()
        return out
    finally:
        try:
            command.upgrade(cfg, "head")
        finally:
            engine.dispose()


def _run_pgvector_unavailable_probe() -> dict[str, Any]:
    """Attempt the 0003 upgrade on a server without pgvector.

    Only called when ``pg_available_extensions`` lacks ``vector``, so
    the DB can never reach the 0003 head -- normalize to the Phase 1
    head instead and leave the DB there (nothing to restore: 0003 can
    never apply on this server).
    """
    cfg = _alembic_config()
    out: dict[str, Any] = {}
    command.upgrade(cfg, REV_0002)
    try:
        command.upgrade(cfg, REV_0003)
        out["upgrade_failed"] = False
    except Exception as exc:  # asserting on the message below
        out["upgrade_failed"] = True
        out["error"] = str(exc)
    out["after_failure"] = _snapshot_schema()
    return out


# ---------------------------------------------------------------------------
# Structural test (no database required)
# ---------------------------------------------------------------------------


def test_0003_revision_chain() -> None:
    """0003 chains off the Phase 1 head and declares vector(384) (1.2, 1.6)."""
    module = _load_migration_module()
    assert module.revision == REV_0003
    assert module.down_revision == REV_0002
    assert callable(module.upgrade)
    assert callable(module.downgrade)
    # The dimension is a DDL literal in the revision source -- the single
    # declaration point Requirement 1.6 requires (matching the ORM's
    # EMBEDDING_DIMENSION constant).
    source = _migration_path().read_text(encoding="utf-8")
    assert f"vector({EMBEDDING_DIMENSION})" in source


# ---------------------------------------------------------------------------
# Live migration tests (real Postgres)
# ---------------------------------------------------------------------------


@requires_postgres
@pytest.mark.asyncio
async def test_0003_apply_rollback_and_schema() -> None:
    """Upgrade from the Phase 1 head applies cleanly; downgrade removes it (1.2).

    Asserts the full DDL surface: extension enabled, both tables with
    the declared ``vector(384)`` column (1.6), NOT NULL model metadata
    (2.10), ownership/source FKs and unique constraints (1.8), and the
    user_id lookup indexes.
    """
    at_phase1_head, after_upgrade, after_downgrade = await asyncio.to_thread(
        _run_apply_rollback_cycle
    )

    # --- baseline: the Phase 1 head has no embedding tables ------------
    assert at_phase1_head["alembic_version"] == REV_0002
    assert "resume_embeddings" not in at_phase1_head["tables"]
    assert "match_embeddings" not in at_phase1_head["tables"]

    # --- after upgrade: at the new head, no manual SQL steps (1.2) -----
    assert after_upgrade["alembic_version"] == REV_0003
    assert "resume_embeddings" in after_upgrade["tables"]
    assert "match_embeddings" in after_upgrade["tables"]
    assert "vector" in after_upgrade["extensions"]

    # --- column sets per the design ERD --------------------------------
    assert set(after_upgrade["resume_embeddings_columns"]) == EXPECTED_RESUME_EMBEDDING_COLUMNS
    assert set(after_upgrade["match_embeddings_columns"]) == EXPECTED_MATCH_EMBEDDING_COLUMNS

    # --- the declared dimension is 384, as DDL, per table (1.6) --------
    assert after_upgrade["resume_embeddings_embedding_type"] == f"vector({EMBEDDING_DIMENSION})"
    assert after_upgrade["match_embeddings_embedding_type"] == f"vector({EMBEDDING_DIMENSION})"

    # --- model identity columns are NOT NULL (2.10) --------------------
    for table in ("resume_embeddings", "match_embeddings"):
        columns: dict[str, bool] = after_upgrade[f"{table}_columns"]
        assert columns["model_name"] is False  # nullable=False
        assert columns["model_revision"] is False
        assert columns["embedding"] is False

    # --- ownership + source-entity FKs (1.8) ---------------------------
    assert after_upgrade["resume_embeddings_fk_targets"] == {"users", "resumes"}
    assert after_upgrade["match_embeddings_fk_targets"] == {"users", "match_results"}

    # --- at-most-one-embedding-per-source uniques + user_id indexes ----
    assert "resume_embeddings_resume_id_uniq" in (
        after_upgrade["resume_embeddings_uniques"] | after_upgrade["resume_embeddings_indexes"]
    )
    assert "match_embeddings_match_result_id_uniq" in (
        after_upgrade["match_embeddings_uniques"] | after_upgrade["match_embeddings_indexes"]
    )
    assert "resume_embeddings_user_id_idx" in after_upgrade["resume_embeddings_indexes"]
    assert "match_embeddings_user_id_idx" in after_upgrade["match_embeddings_indexes"]

    # --- after downgrade: both tables removed --------------------------
    assert after_downgrade["alembic_version"] == REV_0002
    assert "resume_embeddings" not in after_downgrade["tables"]
    assert "match_embeddings" not in after_downgrade["tables"]


@requires_postgres
@pytest.mark.asyncio
async def test_0003_failure_rolls_back_atomically_and_is_rerunnable() -> None:
    """A schema-conflict failure leaves no partial objects and re-runs (1.3).

    The injected conflict makes the upgrade fail AFTER the extension and
    ``resume_embeddings`` DDL executed in the revision's transaction, so
    a surviving ``resume_embeddings`` table would prove a broken
    rollback. After removing the conflict, the same migration applies
    cleanly.
    """
    out = await asyncio.to_thread(_run_failure_rerun_cycle)

    # The conflicting table made the upgrade fail.
    assert out["upgrade_failed"] is True

    # Atomic rollback: no partial objects, version still the Phase 1
    # head (so the migration is re-runnable, not stuck half-applied).
    after_failure = out["after_failure"]
    assert "resume_embeddings" not in after_failure["tables"]
    assert after_failure["alembic_version"] == REV_0002

    # Re-runnable once the cause is fixed.
    after_rerun = out["after_rerun"]
    assert after_rerun["alembic_version"] == REV_0003
    assert "resume_embeddings" in after_rerun["tables"]
    assert "match_embeddings" in after_rerun["tables"]


@requires_postgres
@pytest.mark.asyncio
async def test_0003_pgvector_unavailability_error() -> None:
    """Without pgvector the migration fails cleanly, naming the extension (1.9).

    Self-skips on the docker-compose ``pgvector/pgvector:pg16`` image
    (pgvector is always available there). Point
    ``MATCHLAYER_DATABASE_URL`` at a plain ``postgres:16`` instance to
    exercise this for real.
    """
    if await asyncio.to_thread(_pgvector_available_on_server):
        pytest.skip(
            "pgvector is available on the connected server; requires a "
            "non-pgvector Postgres image to exercise Requirement 1.9"
        )

    out = await asyncio.to_thread(_run_pgvector_unavailable_probe)

    assert out["upgrade_failed"] is True
    # The error names the missing extension (Postgres: 'extension
    # "vector" is not available' / 'could not open extension control file').
    assert "vector" in out["error"].lower()

    # Schema unchanged: no partial objects, still at the Phase 1 head.
    after_failure = out["after_failure"]
    assert "resume_embeddings" not in after_failure["tables"]
    assert "match_embeddings" not in after_failure["tables"]
    assert after_failure["alembic_version"] == REV_0002


# ---------------------------------------------------------------------------
# Vector write semantics (real Postgres at head, via the ORM models)
# ---------------------------------------------------------------------------


async def _create_owned_resume(session: AsyncSession, user: User) -> Resume:
    """Insert a minimal succeeded-extraction resume owned by ``user``."""
    resume = Resume(
        id=uuid7(),
        user_id=user.id,
        original_filename="resume.pdf",
        storage_key=f"resumes/{uuid7()}.pdf",
        content_type="application/pdf",
        byte_size=1024,
        extracted_text="Python engineer with FastAPI experience.",
        extraction_status="succeeded",
        extraction_char_count=40,
    )
    session.add(resume)
    await session.flush()
    return resume


async def _create_match_result(session: AsyncSession, user: User, resume: Resume) -> MatchResult:
    """Insert a minimal match_results row owned by ``user``."""
    match = MatchResult(
        id=uuid7(),
        user_id=user.id,
        resume_id=resume.id,
        job_description_text="Python role.",
        score=50,
        score_breakdown={},
        matched_keywords=[],
        missing_keywords=[],
        suggestions=[],
        scorer_version="1.0.0+lex.v1",
    )
    session.add(match)
    await session.flush()
    return match


async def _resume_embedding_count(session: AsyncSession, resume_id: Any) -> int:
    result = await session.execute(
        select(func.count())
        .select_from(ResumeEmbedding)
        .where(ResumeEmbedding.resume_id == resume_id)
    )
    return int(result.scalar_one())


@requires_postgres
@pytest.mark.asyncio
async def test_wrong_dimension_write_rejected_nothing_persisted(
    db_session: AsyncSession, factory_user: UserFactory
) -> None:
    """A wrong-dimension vector write fails and persists nothing (1.6).

    The owner rows are committed first so the post-failure rollback
    cannot mask the assertion: if anything from the failed write had
    persisted, the count query would see it.
    """
    user = await factory_user()
    resume = await _create_owned_resume(db_session, user)
    await db_session.commit()
    # Bind the identifiers to locals BEFORE the failed write: the rollback
    # below expires every instance in the session, so a later
    # ``resume.id`` would trigger a lazy refresh — synchronous IO on an
    # async engine, i.e. ``MissingGreenlet``.
    resume_id = resume.id
    user_id = user.id

    wrong = [0.5] * (EMBEDDING_DIMENSION - 1)
    db_session.add(
        ResumeEmbedding(
            id=uuid7(),
            resume_id=resume_id,
            user_id=user_id,
            embedding=wrong,
            model_name=MODEL_NAME,
            model_revision=MODEL_REVISION,
        )
    )
    # StatementError covers both rejection layers: pgvector-python's
    # dimension validation at bind time and the server's vector(384)
    # DDL check (DBAPIError is a StatementError subclass).
    with pytest.raises(StatementError):
        await db_session.flush()
    await db_session.rollback()

    assert await _resume_embedding_count(db_session, resume_id) == 0


@requires_postgres
@pytest.mark.asyncio
async def test_wrong_dimension_rejected_by_ddl(
    db_session: AsyncSession, factory_user: UserFactory
) -> None:
    """The vector(384) DDL itself rejects a wrong-dimension INSERT (1.6).

    Bypasses the ORM ``Vector`` type (which validates client-side) with
    a raw INSERT casting a 10-dimension literal, proving the rejection
    is enforced by the database schema, not only by the Python driver.
    """
    user = await factory_user()
    resume = await _create_owned_resume(db_session, user)
    await db_session.commit()
    # Read the ids before the failed write: the rollback expires the ORM
    # instances, and touching them afterwards would lazy-load (sync IO on
    # an async engine → ``MissingGreenlet``).
    resume_id = resume.id
    user_id = user.id

    wrong_literal = "[" + ",".join(["0.5"] * 10) + "]"
    stmt = sa.text(
        "INSERT INTO resume_embeddings "
        "(id, resume_id, user_id, embedding, model_name, model_revision) "
        "VALUES (:id, :resume_id, :user_id, CAST(:embedding AS vector(384)), "
        ":model_name, :model_revision)"
    )
    with pytest.raises(DBAPIError) as exc_info:
        await db_session.execute(
            stmt,
            {
                "id": uuid7(),
                "resume_id": resume_id,
                "user_id": user_id,
                "embedding": wrong_literal,
                "model_name": MODEL_NAME,
                "model_revision": MODEL_REVISION,
            },
        )
    await db_session.rollback()

    # Postgres names the expected dimension in the error.
    assert str(EMBEDDING_DIMENSION) in str(exc_info.value)
    assert await _resume_embedding_count(db_session, resume_id) == 0


@requires_postgres
@pytest.mark.asyncio
async def test_fk_rejects_embedding_without_source_entity(
    db_session: AsyncSession, factory_user: UserFactory
) -> None:
    """An embedding referencing a nonexistent resume is rejected (1.8)."""
    user = await factory_user()
    await db_session.commit()

    db_session.add(
        ResumeEmbedding(
            id=uuid7(),
            resume_id=uuid7(),  # fresh UUID -- no such resumes row
            user_id=user.id,
            embedding=_vector(),
            model_name=MODEL_NAME,
            model_revision=MODEL_REVISION,
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


@requires_postgres
@pytest.mark.asyncio
async def test_fk_rejects_embedding_without_owner(
    db_session: AsyncSession, factory_user: UserFactory
) -> None:
    """An embedding referencing a nonexistent owning user is rejected (1.8)."""
    user = await factory_user()
    resume = await _create_owned_resume(db_session, user)
    await db_session.commit()

    db_session.add(
        ResumeEmbedding(
            id=uuid7(),
            resume_id=resume.id,
            user_id=uuid7(),  # fresh UUID -- no such users row
            embedding=_vector(),
            model_name=MODEL_NAME,
            model_revision=MODEL_REVISION,
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


@requires_postgres
@pytest.mark.asyncio
async def test_fk_rejects_match_embedding_without_match_result(
    db_session: AsyncSession, factory_user: UserFactory
) -> None:
    """A JD embedding referencing a nonexistent match result is rejected (1.8)."""
    user = await factory_user()
    await db_session.commit()

    db_session.add(
        MatchEmbedding(
            id=uuid7(),
            match_result_id=uuid7(),  # fresh UUID -- no such match_results row
            user_id=user.id,
            embedding=_vector(),
            model_name=MODEL_NAME,
            model_revision=MODEL_REVISION,
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


@requires_postgres
@pytest.mark.asyncio
async def test_model_name_and_revision_round_trip(
    db_session: AsyncSession, factory_user: UserFactory
) -> None:
    """model_name / model_revision are recorded and read back intact (2.10).

    Exercises both tables: the stored model identity is what makes the
    Requirement 2.7 reuse-vs-regenerate decision decidable from stored
    data alone.
    """
    user = await factory_user()
    resume = await _create_owned_resume(db_session, user)
    match = await _create_match_result(db_session, user, resume)
    # ``expire_all()`` below expires these instances too, so capture the
    # identifiers now — reading ``resume.id`` afterwards would lazy-refresh
    # the row synchronously on an async engine (``MissingGreenlet``).
    resume_id = resume.id
    match_id = match.id
    user_id = user.id

    db_session.add(
        ResumeEmbedding(
            id=uuid7(),
            resume_id=resume_id,
            user_id=user_id,
            embedding=_vector(0.25),
            model_name=MODEL_NAME,
            model_revision=MODEL_REVISION,
        )
    )
    db_session.add(
        MatchEmbedding(
            id=uuid7(),
            match_result_id=match_id,
            user_id=user_id,
            embedding=_vector(0.75),
            model_name=MODEL_NAME,
            model_revision=MODEL_REVISION,
        )
    )
    await db_session.flush()
    # Force a real re-read from the database rather than identity-map hits.
    db_session.expire_all()

    stored_resume_emb = (
        await db_session.execute(
            select(ResumeEmbedding).where(ResumeEmbedding.resume_id == resume_id)
        )
    ).scalar_one()
    assert stored_resume_emb.model_name == MODEL_NAME
    assert stored_resume_emb.model_revision == MODEL_REVISION
    assert len(list(stored_resume_emb.embedding)) == EMBEDDING_DIMENSION
    assert float(next(iter(stored_resume_emb.embedding))) == pytest.approx(0.25, abs=1e-6)

    stored_match_emb = (
        await db_session.execute(
            select(MatchEmbedding).where(MatchEmbedding.match_result_id == match_id)
        )
    ).scalar_one()
    assert stored_match_emb.model_name == MODEL_NAME
    assert stored_match_emb.model_revision == MODEL_REVISION
    assert len(list(stored_match_emb.embedding)) == EMBEDDING_DIMENSION
    assert float(next(iter(stored_match_emb.embedding))) == pytest.approx(0.75, abs=1e-6)
