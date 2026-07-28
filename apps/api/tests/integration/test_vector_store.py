"""Vector_Store access-layer integration tests (task 9.2).

Validates Requirements 1.8, 2.7, 2.10 against the real docker-compose
Postgres through the ``services/vector_store.py`` functions — the only
module permitted to touch the embedding tables.

Coverage:

* **Owner-scoped reads (1.8)** — ``get_resume_embedding`` returns the
  stored Embedding for the owning User_Account and returns ``None`` for
  any other user's id, indistinguishable from "no embedding stored", so
  cross-tenant existence is never disclosed.

* **Upsert replaces in place (2.7)** — two ``upsert_resume_embedding``
  calls for the same resume leave exactly one row whose vector and
  model identity are the latest write's; stale Embeddings are
  overwritten via ``ON CONFLICT (resume_embeddings_resume_id_uniq)``,
  never accumulated.

* **Model identity round-trip (2.10)** — the Embedding_Model name and
  revision recorded at write time come back on ``StoredEmbedding``, and
  comparing them against the currently configured identity is exactly
  the reuse-vs-regenerate decision Requirement 2.7 requires: matching
  identity → reuse; differing revision → regenerate and upsert.

* **Match embedding round-trip** — ``insert_match_embedding`` persists
  the JD Embedding with its model identity readable back intact.

Gating follows the integration-suite convention: live tests skip when
Postgres is unreachable (docker-compose not running) so the suite stays
green on a laptop without Docker while CI exercises them for real.

DB-state assumption: the schema is at the migration head (sibling
migration tests always restore head in a ``finally``). Everything here
runs inside the per-test ``db_session`` transaction and is rolled back
on teardown; no commits are issued, mirroring the production contract
that the Vector_Store never owns the transaction boundary.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils.compat import uuid7

from matchlayer_api.db.models import (
    EMBEDDING_DIMENSION,
    MatchEmbedding,
    MatchResult,
    Resume,
    ResumeEmbedding,
    User,
)
from matchlayer_api.services.vector_store import (
    StoredEmbedding,
    get_resume_embedding,
    insert_match_embedding,
    upsert_resume_embedding,
)

from .conftest import UserFactory, postgres_available

requires_postgres = pytest.mark.skipif(
    not postgres_available(),
    reason="Postgres not available (docker-compose not running)",
)

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
MODEL_REVISION_V1 = "revision-sha-aaaa"
MODEL_REVISION_V2 = "revision-sha-bbbb"


def _vector(fill: float = 0.5) -> list[float]:
    """A valid 384-dimension vector with float32-exact components."""
    return [fill] * EMBEDDING_DIMENSION


async def _create_owned_resume(session: AsyncSession, user: User) -> Resume:
    """Insert a minimal succeeded-extraction resume owned by ``user``.

    Duplicated from ``test_migration_0003_pgvector.py`` rather than
    imported so the two test modules stay independently runnable.
    """
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


async def _resume_embedding_row_count(session: AsyncSession, resume_id: object) -> int:
    result = await session.execute(
        select(func.count())
        .select_from(ResumeEmbedding)
        .where(ResumeEmbedding.resume_id == resume_id)
    )
    return int(result.scalar_one())


async def _resume_embedding_timestamps(
    session: AsyncSession, resume_id: object
) -> tuple[datetime, datetime]:
    """Read (created_at, updated_at) for the single row of ``resume_id``."""
    result = await session.execute(
        select(ResumeEmbedding.created_at, ResumeEmbedding.updated_at).where(
            ResumeEmbedding.resume_id == resume_id
        )
    )
    created_at, updated_at = result.one()
    return created_at, updated_at


# ---------------------------------------------------------------------------
# Owner-scoped reads (Requirement 1.8)
# ---------------------------------------------------------------------------


@requires_postgres
@pytest.mark.asyncio
async def test_read_is_scoped_to_owner(db_session: AsyncSession, factory_user: UserFactory) -> None:
    """User A reads their own Embedding; user B's id yields None (1.8).

    The cross-user read returns ``None`` — the same result as a resume
    with no stored Embedding — so a caller can never learn whether a
    foreign resume has an Embedding at all.
    """
    user_a = await factory_user()
    user_b = await factory_user()
    resume_a = await _create_owned_resume(db_session, user_a)

    await upsert_resume_embedding(
        db_session,
        resume_id=resume_a.id,
        user_id=user_a.id,
        vector=_vector(0.25),
        model_name=MODEL_NAME,
        model_revision=MODEL_REVISION_V1,
    )

    # Owner read: the stored Embedding comes back complete.
    owned = await get_resume_embedding(db_session, resume_id=resume_a.id, user_id=user_a.id)
    assert owned is not None
    assert isinstance(owned, StoredEmbedding)
    assert owned.model_name == MODEL_NAME
    assert owned.model_revision == MODEL_REVISION_V1
    assert len(owned.vector) == EMBEDDING_DIMENSION
    assert owned.vector[0] == pytest.approx(0.25, abs=1e-6)
    # The read path normalizes to plain Python floats (no numpy leakage).
    assert all(type(component) is float for component in owned.vector)

    # Cross-user read: nothing, despite the row existing.
    foreign = await get_resume_embedding(db_session, resume_id=resume_a.id, user_id=user_b.id)
    assert foreign is None


@requires_postgres
@pytest.mark.asyncio
async def test_read_returns_none_when_no_embedding_stored(
    db_session: AsyncSession, factory_user: UserFactory
) -> None:
    """A resume without a stored Embedding reads as None for its owner (1.8).

    Together with the cross-user case above this proves the two states
    are indistinguishable to the caller: both fall through to the same
    generate-at-match-time path (Requirement 2.7).
    """
    user = await factory_user()
    resume = await _create_owned_resume(db_session, user)

    assert await get_resume_embedding(db_session, resume_id=resume.id, user_id=user.id) is None


# ---------------------------------------------------------------------------
# Upsert replaces the single row per resume (Requirement 2.7)
# ---------------------------------------------------------------------------


@requires_postgres
@pytest.mark.asyncio
async def test_upsert_replaces_existing_row(
    db_session: AsyncSession, factory_user: UserFactory
) -> None:
    """A second upsert for the same resume replaces, never accumulates (2.7).

    Two writes with different vectors and model revisions leave exactly
    one ``resume_embeddings`` row carrying the latest vector and model
    identity — the ``ON CONFLICT`` path of
    ``resume_embeddings_resume_id_uniq`` overwrote the stale Embedding
    in place — and the row's ``updated_at`` is bumped by the conflict
    update while ``created_at`` stays put.

    Transaction note: the two upserts are separated by a real commit
    because both ``server_default=now()`` and the ``ON CONFLICT``
    ``set_={"updated_at": func.now()}`` resolve to the *transaction*
    start time in Postgres — inside a single transaction the two writes
    would carry identical timestamps and the bump would be unobservable.
    Committed rows are wiped by the autouse truncate fixture before the
    next test (users CASCADEs through resumes to resume_embeddings).
    """
    user = await factory_user()
    resume = await _create_owned_resume(db_session, user)

    await upsert_resume_embedding(
        db_session,
        resume_id=resume.id,
        user_id=user.id,
        vector=_vector(0.25),
        model_name=MODEL_NAME,
        model_revision=MODEL_REVISION_V1,
    )
    await db_session.commit()

    created_before, updated_before = await _resume_embedding_timestamps(db_session, resume.id)
    await db_session.commit()  # end the read transaction so the next now() advances

    await upsert_resume_embedding(
        db_session,
        resume_id=resume.id,
        user_id=user.id,
        vector=_vector(0.75),
        model_name=MODEL_NAME,
        model_revision=MODEL_REVISION_V2,
    )
    await db_session.commit()

    # Exactly one row survives.
    assert await _resume_embedding_row_count(db_session, resume.id) == 1

    # And it carries the second write's vector and model identity.
    stored = await get_resume_embedding(db_session, resume_id=resume.id, user_id=user.id)
    assert stored is not None
    assert stored.model_revision == MODEL_REVISION_V2
    assert stored.vector[0] == pytest.approx(0.75, abs=1e-6)

    # updated_at bumped by the ON CONFLICT update; created_at untouched.
    created_after, updated_after = await _resume_embedding_timestamps(db_session, resume.id)
    assert created_after == created_before
    assert updated_after > updated_before


# ---------------------------------------------------------------------------
# Model identity round-trip drives reuse-vs-regenerate (Requirement 2.10)
# ---------------------------------------------------------------------------


@requires_postgres
@pytest.mark.asyncio
async def test_model_identity_round_trip_drives_reuse_decision(
    db_session: AsyncSession, factory_user: UserFactory
) -> None:
    """Stored model name/revision decide reuse-vs-regenerate (2.10, 2.7).

    Plays out the Scoring_Service's decision exactly as Requirement 2.7
    words it, using only what ``StoredEmbedding`` carries back:

    1. Configured identity matches the stored one → reuse.
    2. Configured revision moves on → the comparison says regenerate;
       the regenerated Embedding is upserted and the next read returns
       the new identity, so the following request reuses again.
    """
    user = await factory_user()
    resume = await _create_owned_resume(db_session, user)

    await upsert_resume_embedding(
        db_session,
        resume_id=resume.id,
        user_id=user.id,
        vector=_vector(0.25),
        model_name=MODEL_NAME,
        model_revision=MODEL_REVISION_V1,
    )

    # Step 1: configured identity == stored identity → reuse.
    stored = await get_resume_embedding(db_session, resume_id=resume.id, user_id=user.id)
    assert stored is not None
    assert (stored.model_name, stored.model_revision) == (MODEL_NAME, MODEL_REVISION_V1)

    # Step 2: configuration pins a new revision → stored identity no
    # longer matches → regenerate and upsert.
    configured = (MODEL_NAME, MODEL_REVISION_V2)
    assert (stored.model_name, stored.model_revision) != configured
    await upsert_resume_embedding(
        db_session,
        resume_id=resume.id,
        user_id=user.id,
        vector=_vector(0.5),
        model_name=configured[0],
        model_revision=configured[1],
    )

    # The next read sees the regenerated identity → reuse holds again.
    refreshed = await get_resume_embedding(db_session, resume_id=resume.id, user_id=user.id)
    assert refreshed is not None
    assert (refreshed.model_name, refreshed.model_revision) == configured
    assert refreshed.vector[0] == pytest.approx(0.5, abs=1e-6)


# ---------------------------------------------------------------------------
# Match embedding write path
# ---------------------------------------------------------------------------


@requires_postgres
@pytest.mark.asyncio
async def test_insert_match_embedding_round_trip(
    db_session: AsyncSession, factory_user: UserFactory
) -> None:
    """insert_match_embedding persists vector + model identity (2.10).

    ``match_embeddings`` has no read path in the Vector_Store (written
    once at match creation, never regenerated), so the round-trip is
    asserted through the ORM model directly.
    """
    user = await factory_user()
    resume = await _create_owned_resume(db_session, user)
    match = await _create_match_result(db_session, user, resume)
    # ``expire_all()`` below expires ``match`` as well, so capture the id
    # first — touching ``match.id`` afterwards would lazy-refresh the row
    # synchronously on an async engine (``MissingGreenlet``).
    match_id = match.id

    await insert_match_embedding(
        db_session,
        match_result_id=match_id,
        user_id=user.id,
        vector=_vector(0.125),
        model_name=MODEL_NAME,
        model_revision=MODEL_REVISION_V1,
    )
    await db_session.flush()
    db_session.expire_all()

    stored = (
        await db_session.execute(
            select(MatchEmbedding).where(MatchEmbedding.match_result_id == match_id)
        )
    ).scalar_one()
    assert stored.model_name == MODEL_NAME
    assert stored.model_revision == MODEL_REVISION_V1
    assert len(list(stored.embedding)) == EMBEDDING_DIMENSION
    assert float(next(iter(stored.embedding))) == pytest.approx(0.125, abs=1e-6)


@requires_postgres
@pytest.mark.asyncio
async def test_insert_match_embedding_rejects_duplicate(
    db_session: AsyncSession, factory_user: UserFactory
) -> None:
    """A second JD Embedding for the same match result is rejected (1.8).

    ``match_embeddings`` has no update path — written once at match
    creation, never regenerated — so ``insert_match_embedding`` is a
    plain insert and the ``match_embeddings_match_result_id_uniq``
    constraint rejects a duplicate write rather than silently
    replacing it.
    """
    user = await factory_user()
    resume = await _create_owned_resume(db_session, user)
    match = await _create_match_result(db_session, user, resume)

    await insert_match_embedding(
        db_session,
        match_result_id=match.id,
        user_id=user.id,
        vector=_vector(0.125),
        model_name=MODEL_NAME,
        model_revision=MODEL_REVISION_V1,
    )
    await db_session.flush()

    await insert_match_embedding(
        db_session,
        match_result_id=match.id,
        user_id=user.id,
        vector=_vector(0.875),
        model_name=MODEL_NAME,
        model_revision=MODEL_REVISION_V2,
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()
