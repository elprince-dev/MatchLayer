"""SQLAlchemy 2.x declarative models for the MatchLayer tables.

Auth tables (``users``, ``refresh_tokens``, ``password_reset_tokens``,
``audit_events``), matching tables (``resumes``, ``match_results``), and
the Phase 2 Vector_Store tables (``resume_embeddings``,
``match_embeddings``). UUIDv7 primary keys via ``uuid_utils``. All
timestamps are ``TIMESTAMP WITH TIME ZONE`` (Postgres ``timestamptz``).

Design reference: Data Models 4.1-4.4 (phase-1-auth); Data Models
(phase-1-matching); Data Models / New tables (phase-2-nlp-embeddings).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from uuid_utils.compat import uuid7


def _uuid7() -> UUID:
    """Generate a UUIDv7 (time-ordered) primary key as a stdlib ``uuid.UUID``."""
    return uuid7()


class Base(DeclarativeBase):
    """Shared declarative base for all MatchLayer models."""


class User(Base):
    """The ``users`` table (4.1)."""

    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid7)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    failed_login_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_failed_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )


class RefreshToken(Base):
    """The ``refresh_tokens`` table (4.2)."""

    __tablename__ = "refresh_tokens"

    jti: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid7)
    family_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )


class PasswordResetToken(Base):
    """The ``password_reset_tokens`` table (4.3)."""

    __tablename__ = "password_reset_tokens"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid7)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class AuditEvent(Base):
    """The ``audit_events`` table (4.4). Append-only."""

    __tablename__ = "audit_events"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid7)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    ip_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class Resume(Base):
    """The ``resumes`` table.

    One uploaded resume file owned by a ``users`` row. ``original_filename``
    and ``extracted_text`` are Restricted PII (display/analysis only) and are
    never logged or placed in audit payloads.

    Design reference: Data Models (phase-1-matching); Requirements 14.2, 14.5.
    """

    __tablename__ = "resumes"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid7)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    original_filename: Mapped[str] = mapped_column(Text, nullable=False)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(Text, nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    extraction_status: Mapped[str] = mapped_column(Text, nullable=False)
    extraction_char_count: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )


class MatchResult(Base):
    """The ``match_results`` table.

    One scoring of one ``resumes`` row against one job description, owned by a
    ``users`` row. ``job_description_text`` is Restricted PII and is never
    logged or placed in audit payloads.

    Design reference: Data Models (phase-1-matching); Requirements 14.2, 14.5.
    """

    __tablename__ = "match_results"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid7)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    resume_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("resumes.id", ondelete="CASCADE"),
        nullable=False,
    )
    job_description_text: Mapped[str] = mapped_column(Text, nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    score_breakdown: Mapped[dict] = mapped_column(JSONB, nullable=False)
    matched_keywords: Mapped[list] = mapped_column(JSONB, nullable=False)
    missing_keywords: Mapped[list] = mapped_column(JSONB, nullable=False)
    suggestions: Mapped[list] = mapped_column(JSONB, nullable=False)
    scorer_version: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )


# Output dimension of the pinned Embedding_Model (all-MiniLM-L6-v2). Must
# match the ``vector(384)`` DDL literal in migration 0003 (Requirement 1.6);
# a model with a different dimension requires a new reviewed migration.
EMBEDDING_DIMENSION = 384


class ResumeEmbedding(Base):
    """The ``resume_embeddings`` table.

    At most one current Embedding per ``resumes`` row (upserted when the
    Embedding_Model changes, Requirement 2.7). The vector is derived from
    Restricted PII: access is scoped to the owning user via ``user_id``
    (Requirement 1.8) and vector values are never logged. ``model_name`` /
    ``model_revision`` record the exact model identity so the
    reuse-vs-regenerate decision is decidable from stored data
    (Requirement 2.10).

    Mirrors migration ``0003_pgvector_embeddings``.
    Design reference: Data Models / New tables (phase-2-nlp-embeddings).
    """

    __tablename__ = "resume_embeddings"
    __table_args__ = (
        UniqueConstraint("resume_id", name="resume_embeddings_resume_id_uniq"),
        Index("resume_embeddings_user_id_idx", "user_id"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid7)
    resume_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("resumes.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    # pgvector accepts list[float] on write; reads come back as a float
    # sequence (numpy ndarray when numpy is installed).
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMENSION), nullable=False)
    model_name: Mapped[str] = mapped_column(Text, nullable=False)
    model_revision: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class MatchEmbedding(Base):
    """The ``match_embeddings`` table.

    One job-description Embedding per ``match_results`` row, written once at
    match creation and never regenerated (hence no ``updated_at``, per the
    design ERD). Derived from Restricted PII: access is scoped to the owning
    user via ``user_id`` (Requirement 1.8) and vector values are never
    logged. ``model_name`` / ``model_revision`` record the exact model
    identity (Requirement 2.10).

    Mirrors migration ``0003_pgvector_embeddings``.
    Design reference: Data Models / New tables (phase-2-nlp-embeddings).
    """

    __tablename__ = "match_embeddings"
    __table_args__ = (
        UniqueConstraint("match_result_id", name="match_embeddings_match_result_id_uniq"),
        Index("match_embeddings_user_id_idx", "user_id"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid7)
    match_result_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("match_results.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    # pgvector accepts list[float] on write; reads come back as a float
    # sequence (numpy ndarray when numpy is installed).
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMENSION), nullable=False)
    model_name: Mapped[str] = mapped_column(Text, nullable=False)
    model_revision: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
