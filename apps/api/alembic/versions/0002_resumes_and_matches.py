"""resumes and match_results tables

Revision ID: 0002_resumes_and_matches
Revises: 0001_users_and_auth
Create Date: 2026-06-04

Creates the two persistence tables for ``phase-1-matching``:

* ``resumes`` — one uploaded resume file per row (Requirements 2, 3).
* ``match_results`` — one scoring of a resume against a job description
  per row (Requirements 5 through 8).

Both tables mirror the conventions established by ``0001``: UUIDv7
primary keys (generated application-side by the ORM ``_uuid7`` default,
so the column carries no server default), ``timestamptz`` columns with a
``now()`` server default, a nullable ``deleted_at`` soft-delete column
(``conventions.md``), and ``JSONB`` for the structured scoring columns.

No fine-grained GRANT/REVOKE block is needed here. ``0001``'s
``ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT,
UPDATE, DELETE ON TABLES TO matchlayer_app`` already covers any table
created afterwards; the ``audit_events`` REVOKE in ``0001`` is specific
to that table and does not affect these.

Design reference: Data Models / Alembic migration (phase-1-matching);
Requirements 14.1, 14.2, 14.3, 14.4, 14.5.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_resumes_and_matches"
down_revision: str | Sequence[str] | None = "0001_users_and_auth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. resumes — columns required by Requirements 2 and 3.
    op.create_table(
        "resumes",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Restricted PII — retained for display only, never logged.
        sa.Column("original_filename", sa.Text(), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),  # <uuidv7>.<ext>
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        # Restricted PII — nullable because extraction may fail (3.5).
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column("extraction_status", sa.Text(), nullable=False),  # pending|succeeded|failed
        sa.Column("extraction_char_count", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )

    # 2. match_results — columns required by Requirements 5 through 8.
    op.create_table(
        "match_results",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "resume_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("resumes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Restricted PII — never logged or placed in an audit payload (8.8).
        sa.Column("job_description_text", sa.Text(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),  # 0..100
        sa.Column("score_breakdown", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column("matched_keywords", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column("missing_keywords", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column("suggestions", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column("scorer_version", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )

    # 3. Indexes (Requirement 14.3 — document why each exists, per
    #    conventions.md "add an index any time you add a WHERE or
    #    ORDER BY on a non-PK column").

    # Every resume list/get query filters by user_id (per-user scoping,
    # Requirement 1.4).
    op.create_index("resumes_user_id_idx", "resumes", ["user_id"])

    # Every match list/get query filters by user_id (per-user scoping,
    # Requirement 1.4).
    op.create_index("match_results_user_id_idx", "match_results", ["user_id"])

    # The Requirement 9.6 lookup joins a match back to its resume; the
    # resume_id FK is also the natural filter for that relationship.
    op.create_index("match_results_resume_id_idx", "match_results", ["resume_id"])

    # Composite indexes backing keyset (cursor) pagination. The list
    # endpoints page with ORDER BY created_at DESC, id DESC scoped to a
    # single user_id (Requirements 4.1, 9.1, conventions.md cursor
    # pagination). Built with explicit DESC ordering via raw SQL —
    # mirroring 0001's use of op.execute for its expression index — so
    # the index ordering matches the query's ORDER BY exactly and the
    # planner can satisfy the sort directly.
    op.execute(
        "CREATE INDEX resumes_user_created_idx ON resumes (user_id, created_at DESC, id DESC)"
    )
    op.execute(
        "CREATE INDEX match_results_user_created_idx "
        "ON match_results (user_id, created_at DESC, id DESC)"
    )


def downgrade() -> None:
    # Drop every index then table in reverse creation order
    # (Requirement 14.4). match_results is dropped before resumes
    # because it carries a FK to resumes.id.
    op.execute("DROP INDEX IF EXISTS match_results_user_created_idx")
    op.execute("DROP INDEX IF EXISTS resumes_user_created_idx")

    op.drop_index("match_results_resume_id_idx", table_name="match_results")
    op.drop_index("match_results_user_id_idx", table_name="match_results")
    op.drop_table("match_results")

    op.drop_index("resumes_user_id_idx", table_name="resumes")
    op.drop_table("resumes")
