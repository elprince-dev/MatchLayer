"""``Vector_Store`` access layer: reads and writes for the embedding tables.

This is the ONLY module in the API permitted to read or write the
``resume_embeddings`` and ``match_embeddings`` tables (Components and
Interfaces §6, phase-2-nlp-embeddings). Plain async functions rather than a
class: the store carries no configuration or collaborators — every call takes
the active request-scoped :class:`AsyncSession` plus explicit identifiers.

Scoping (Requirement 1.8): every read is scoped by ``user_id`` exactly as
Resume and Match_Result reads are scoped in Phase 1, so a stored Embedding
owned by a different User_Account can never be returned. Writes carry the
owning ``user_id`` and the source-entity id; the FK constraints in migration
``0003_pgvector_embeddings`` reject a write that references a non-existent
owner or source entity.

Model identity (Requirement 2.10): each stored Embedding records the
Embedding_Model name and revision that produced it, and
:class:`StoredEmbedding` carries them back to the caller so the
reuse-vs-regenerate decision (Requirement 2.7) is decidable from stored data
alone.

Transaction model (mirrors ``Scoring_Service`` / ``Resume_Service``): every
function stages its work on the caller's session and never commits — the
router owns the transaction boundary. Persistence failures are the *caller's*
concern (the Scoring_Service catches them, logs ``embedding_persist_failure``,
and never fails the request — Requirements 2.11, 2.12); nothing is caught or
logged here.

PRIVACY (``security.md`` "Data classification"): Embedding vectors are derived
from Restricted PII. No function in this module logs anything — vector values
must never appear in a log line, an error message, or any telemetry signal.

SQLAlchemy 2.x only, no raw SQL (``conventions.md`` "Database"): the upsert
uses the PostgreSQL-dialect ``insert().on_conflict_do_update()`` Core
construct, which is SQLAlchemy Core, not a ``text()`` SQL string.

Design reference: Components and Interfaces §6 "Vector_Store access".
Requirements covered: 1.8, 2.10, 12.4.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func
from uuid_utils.compat import uuid7

from matchlayer_api.db.models import MatchEmbedding, ResumeEmbedding

__all__ = [
    "StoredEmbedding",
    "get_resume_embedding",
    "insert_match_embedding",
    "upsert_resume_embedding",
]


@dataclass(frozen=True, slots=True)
class StoredEmbedding:
    """One persisted Embedding plus the model identity that produced it.

    Carries exactly what the Scoring_Service needs for the
    reuse-vs-regenerate decision (Requirement 2.7): the vector itself and
    the Embedding_Model name and revision recorded at write time
    (Requirement 2.10).

    Attributes:
        vector: The stored embedding as a plain ``list[float]``. pgvector
            reads come back as a numpy ``ndarray`` when numpy is installed;
            the read path normalizes to a list so callers never see a
            numpy type.
        model_name: The Embedding_Model name that produced the vector.
        model_revision: The pinned model revision that produced the vector.
    """

    vector: list[float]
    model_name: str
    model_revision: str


def _as_float_list(vector: Sequence[float]) -> list[float]:
    """Normalize a pgvector read (numpy ndarray or sequence) to ``list[float]``.

    ``float(x)`` coerces numpy scalar types to native Python floats, so the
    returned list is free of numpy types regardless of how the driver
    materialized the column.
    """
    return [float(component) for component in vector]


async def get_resume_embedding(
    session: AsyncSession,
    *,
    resume_id: UUID,
    user_id: UUID,
) -> StoredEmbedding | None:
    """Return the stored Embedding for the caller's resume, or ``None``.

    Scoped by ``user_id`` (Requirement 1.8): a row for *resume_id* owned by
    a different User_Account yields ``None``, indistinguishable from a
    resume that has no stored Embedding — so the caller's fallback is the
    same (generate at match time, Requirement 2.7) and cross-tenant
    existence is never disclosed.

    Args:
        session: Active request-scoped session.
        resume_id: The resume whose Embedding is requested.
        user_id: The authenticated owner's id; scopes the read.

    Returns:
        The :class:`StoredEmbedding` (vector + model name/revision), or
        ``None`` when no owned row exists.
    """
    result = await session.execute(
        select(
            ResumeEmbedding.embedding,
            ResumeEmbedding.model_name,
            ResumeEmbedding.model_revision,
        ).where(
            ResumeEmbedding.resume_id == resume_id,
            ResumeEmbedding.user_id == user_id,
        )
    )
    row = result.one_or_none()
    if row is None:
        return None
    embedding, model_name, model_revision = row
    return StoredEmbedding(
        vector=_as_float_list(embedding),
        model_name=model_name,
        model_revision=model_revision,
    )


async def upsert_resume_embedding(
    session: AsyncSession,
    *,
    resume_id: UUID,
    user_id: UUID,
    vector: Sequence[float],
    model_name: str,
    model_revision: str,
) -> None:
    """Insert or replace the single Embedding row for a resume.

    ``resume_embeddings`` holds at most one current Embedding per resume
    (``UNIQUE(resume_id)``); a conflict on that constraint replaces the
    stored vector and model identity in place (Requirement 2.7 — a stale
    Embedding produced by a different model name/revision is overwritten,
    never accumulated). Implemented with the SQLAlchemy Core
    ``insert().on_conflict_do_update()`` construct — no raw SQL.

    A wrong-dimension vector or a ``resume_id``/``user_id`` that references
    no existing row is rejected by the database (``vector(384)`` DDL and FK
    constraints, Requirements 1.6, 1.8); the resulting error propagates to
    the caller, which treats it as a best-effort persistence failure
    (Requirements 2.11, 2.12).

    Args:
        session: Active request-scoped session.
        resume_id: The resume the Embedding belongs to.
        user_id: The owning User_Account's id.
        vector: The embedding vector (dimension enforced by the DDL).
        model_name: The Embedding_Model name that produced *vector*.
        model_revision: The pinned model revision that produced *vector*.
    """
    stmt = pg_insert(ResumeEmbedding).values(
        id=uuid7(),
        resume_id=resume_id,
        user_id=user_id,
        embedding=list(vector),
        model_name=model_name,
        model_revision=model_revision,
    )
    stmt = stmt.on_conflict_do_update(
        constraint="resume_embeddings_resume_id_uniq",
        set_={
            "embedding": stmt.excluded.embedding,
            "model_name": stmt.excluded.model_name,
            "model_revision": stmt.excluded.model_revision,
            "updated_at": func.now(),
        },
    )
    await session.execute(stmt)


async def insert_match_embedding(
    session: AsyncSession,
    *,
    match_result_id: UUID,
    user_id: UUID,
    vector: Sequence[float],
    model_name: str,
    model_revision: str,
) -> None:
    """Insert the Job_Description Embedding for a newly created match.

    Written once at match creation and never regenerated (design ERD —
    ``match_embeddings`` has no update path), so this is a plain insert;
    the ``UNIQUE(match_result_id)`` constraint rejects a duplicate write
    rather than silently replacing it. Dimension and FK violations are
    rejected by the database (Requirements 1.6, 1.8) and propagate to the
    caller, which treats them as best-effort persistence failures
    (Requirement 2.12).

    Args:
        session: Active request-scoped session.
        match_result_id: The ``match_results`` row the Embedding belongs to.
        user_id: The owning User_Account's id.
        vector: The embedding vector (dimension enforced by the DDL).
        model_name: The Embedding_Model name that produced *vector*.
        model_revision: The pinned model revision that produced *vector*.
    """
    session.add(
        MatchEmbedding(
            id=uuid7(),
            match_result_id=match_result_id,
            user_id=user_id,
            embedding=list(vector),
            model_name=model_name,
            model_revision=model_revision,
        )
    )
