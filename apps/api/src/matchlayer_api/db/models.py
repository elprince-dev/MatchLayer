"""SQLAlchemy 2.x declarative models for the MatchLayer tables.

Auth tables (``users``, ``refresh_tokens``, ``password_reset_tokens``,
``audit_events``), matching tables (``resumes``, ``match_results``),
the Phase 2 Vector_Store tables (``resume_embeddings``,
``match_embeddings``), the Phase 3 LLM tables (``llm_results``,
``llm_invocation_logs``), and the Phase 4 agent tables (``agent_jobs``,
``agent_runs``). UUIDv7 primary keys via ``uuid_utils``. All
timestamps are ``TIMESTAMP WITH TIME ZONE`` (Postgres ``timestamptz``).

Design reference: Data Models 4.1-4.4 (phase-1-auth); Data Models
(phase-1-matching); Data Models / New tables (phase-2-nlp-embeddings);
Data Models / New tables (phase-3-llm-layer); Data Models /
``agent_jobs`` and ``agent_runs`` (phase-4-agentic).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
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


class LLMResult(Base):
    """The ``llm_results`` table.

    One persisted, schema-validated LLM_Feature output (Coaching_Report,
    Bullet_Rewrite, or Interview_Question_Set) owned by a ``users`` row and
    anchored to a ``match_results`` row. ``payload`` is derived from
    Restricted PII (redacted before the provider call, but the validated
    output is user-facing content): access is scoped to the owning user and
    payload content is never logged. Fallback_Responses are never persisted
    here (Requirement 9.5) — every row is a validated LLM output.

    No soft delete (no ``deleted_at``): rows cascade with their match, which
    itself soft-deletes; a hard delete of the match removes its LLM results.

    Design reference: Data Models / New tables (phase-3-llm-layer);
    Requirements 16.3, 5.4, 16.4.
    """

    __tablename__ = "llm_results"
    __table_args__ = (
        # Newest-first list reads and the coach persisted-result reuse
        # lookup both filter on (match_result_id, feature) and order by
        # created_at DESC (Requirements 16.4, 5.4).
        Index(
            "llm_results_match_feature_created_idx",
            "match_result_id",
            "feature",
            text("created_at DESC"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid7)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    match_result_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("match_results.id", ondelete="CASCADE"),
        nullable=False,
    )
    # 'resume_coach' | 'bullet_rewrite' | 'interview_questions'
    feature: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_template_version: Mapped[int] = mapped_column(Integer, nullable=False)
    llm_model: Mapped[str] = mapped_column(Text, nullable=False)
    # Validated CoachingReport / BulletRewrite / InterviewQuestionSet.
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class LLMInvocationLog(Base):
    """The ``llm_invocation_logs`` table.

    Exactly one row per LLM_Provider call (streaming or not, success or
    failure). Never contains raw PII: the prompt input is represented only
    by ``input_hash`` (sha256 over the redacted input, same digest as the
    LLM_Cache key, Requirements 12.3, 12.6). ``output`` holds the validated
    structured output XOR ``failure_category`` holds a FailureReason value.

    Token counts and cost are nullable — NULL means "unavailable", which is
    deliberately distinct from a recorded value of zero (Requirement 12.2).
    ``cost_basis`` records how cost was resolved:
    'provider_reported' | 'computed' | 'unavailable'.

    No soft delete: append-only operational records retained for Phase 5
    evaluation replay; the API never deletes, expires, or overwrites them
    (Requirement 12.4).

    Design reference: Data Models / New tables (phase-3-llm-layer);
    Requirements 12.1, 12.2, 12.3, 12.4, 14.1.
    """

    __tablename__ = "llm_invocation_logs"
    __table_args__ = (
        # Phase 5 evaluation replay selects comparable invocation sets by
        # feature + prompt version + model over a time range (Req 12.4).
        Index(
            "llm_invocation_logs_replay_idx",
            "feature",
            "prompt_template_version",
            "llm_model",
            "created_at",
        ),
        # The Spend_Circuit_Breaker sums cost_usd over the current UTC
        # calendar month — a created_at range scan (Requirement 14.1).
        Index("llm_invocation_logs_created_at_idx", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid7)
    # No ON DELETE CASCADE: invocation logs are append-only records that
    # must not be silently removed by a parent-row delete (Req 12.4).
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
    )
    match_result_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("match_results.id"),
        nullable=False,
    )
    feature: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_template_version: Mapped[int] = mapped_column(Integer, nullable=False)
    llm_model: Mapped[str] = mapped_column(Text, nullable=False)
    redactor_version: Mapped[str] = mapped_column(Text, nullable=False)
    # sha256 over the redacted prompt input — same digest as the LLM_Cache
    # key (Requirement 12.6). Never the raw input (Requirement 12.3).
    input_hash: Mapped[str] = mapped_column(Text, nullable=False)
    # Validated structured output; NULL on failure.
    output: Mapped[dict | None] = mapped_column(JSONB, nullable=True, default=None)
    # FailureReason enum value; NULL on success.
    failure_category: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    # NULL = unavailable, distinct from a recorded 0 (Requirement 12.2).
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 6), nullable=True, default=None)
    # 'provider_reported' | 'computed' | 'unavailable' (Requirement 12.2).
    cost_basis: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class AgentJob(Base):
    """The ``agent_jobs`` table.

    One asynchronous multi-agent analysis job per row, owned by a ``users``
    row and anchored to a ``match_results`` row. Job_Status lifecycle is
    ``queued → running → completed | failed`` (Requirement 12.1); the only
    permitted mutations after creation are the status transitions and their
    associated timestamp/error fields (Requirement 12.6) — enforced by the
    single writer (``services/agent_jobs/service.py``), not the schema.

    ``attempts`` is the SQS redelivery counter (Requirement 11.5).
    ``result_json`` holds the AnalysisResult and is set iff ``completed``;
    ``error_json`` holds a structured PII-free error and is null unless
    ``failed``. No soft delete: jobs are operational records retained for
    Phase 5 evaluation consumption — no Phase 4 code path deletes them.

    FKs deliberately carry no cascade (Postgres ``NO ACTION``): a parent
    hard-delete must not silently destroy retained job records.

    Mirrors migration ``0005_agent_tables`` (index rationale documented
    there per ``conventions.md``).
    Design reference: Data Models / ``agent_jobs`` (phase-4-agentic);
    Requirements 12.1, 10.5.
    """

    __tablename__ = "agent_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed')",
            name="agent_jobs_status_check",
        ),
        # Owner-scoped job reads (Requirements 10.4, 12.4).
        Index("agent_jobs_user_id_idx", "user_id"),
        # In-flight idempotency lookup path (Requirement 10.5, D5).
        Index("agent_jobs_match_user_status_idx", "match_id", "user_id", "status"),
        # Partial unique index: at most one non-terminal job per
        # (match, user) pair, even under concurrency (Requirement 10.5, D5).
        Index(
            "agent_jobs_match_user_inflight_uniq",
            "match_id",
            "user_id",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid7)
    # No ON DELETE CASCADE: retained operational records (Requirement 12.6).
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
    )
    match_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("match_results.id"),
        nullable=False,
    )
    # Job_Status: 'queued' | 'running' | 'completed' | 'failed'.
    status: Mapped[str] = mapped_column(Text, nullable=False)
    # SQS redelivery counter (Requirement 11.5).
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    # NULL until the worker records the corresponding transition (11.4).
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    # AnalysisResult; set iff status = 'completed'.
    result_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True, default=None)
    # Structured PII-free error; null unless status = 'failed'.
    error_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True, default=None)


class AgentRun(Base):
    """The ``agent_runs`` table.

    Exactly one immutable row per Agent node invocation (Requirements 12.2,
    12.6) — completed, degraded, or failed. ``input_state_json`` and
    ``output_state_json`` hold the JSON-serialized agent input/output state,
    which contains only redacted or derived content by construction — never
    raw resume text (Requirement 12.3, Internal classification).

    ``latency_ms`` is measured node-invocation-start → output-return — the
    same boundary as the per-node timeout and the span duration
    (Requirements 8.3, 12.2, 13.1). ``failure_reason_json`` is a structured
    PII-free failure reason, null iff status is ``completed`` (enforced by
    the single writer, ``services/agent_jobs/runs.py``).

    No soft delete and no updated_at: rows are append-only and immutable
    once written, retained for Phase 5 evaluation replay. The FK carries no
    cascade for the same retention reason as ``agent_jobs``.

    Mirrors migration ``0005_agent_tables``.
    Design reference: Data Models / ``agent_runs`` (phase-4-agentic);
    Requirement 12.1.
    """

    __tablename__ = "agent_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('completed', 'degraded', 'failed')",
            name="agent_runs_status_check",
        ),
        # Per-agent step statuses for one job are derived by selecting all
        # runs for that job_id (Requirements 10.2, 12.1).
        Index("agent_runs_job_id_idx", "job_id"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid7)
    # No ON DELETE CASCADE: immutable retained records (Requirement 12.6).
    job_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("agent_jobs.id"),
        nullable=False,
    )
    # One of the five node names: resume_analysis, ats, skill_gap,
    # improvement, synthesizer.
    agent_name: Mapped[str] = mapped_column(Text, nullable=False)
    # Redacted/derived content only — never raw resume text (12.3).
    input_state_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    output_state_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # Node-invocation-start → output-return (Requirement 12.2).
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    # 'completed' | 'degraded' | 'failed'.
    status: Mapped[str] = mapped_column(Text, nullable=False)
    # Structured failure reason; null iff status = 'completed' (12.1).
    failure_reason_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
