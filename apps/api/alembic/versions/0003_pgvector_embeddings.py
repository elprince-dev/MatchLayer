"""pgvector extension and embedding tables

Revision ID: 0003_pgvector_embeddings
Revises: 0002_resumes_and_matches
Create Date: 2026-06-10

Enables the pgvector extension (ADR 0004 — vectors live inside the
existing PostgreSQL database, not a dedicated vector DB) and creates
the two Vector_Store tables for ``phase-2-nlp-embeddings``:

* ``resume_embeddings`` — at most one current embedding per ``resumes``
  row (Requirements 1.1, 1.8, 2.5).
* ``match_embeddings`` — at most one job-description embedding per
  ``match_results`` row (Requirements 1.1, 1.8, 2.6).

Vector dimension — explicit 384 literal (Requirement 1.6)
---------------------------------------------------------
The embedding column is declared ``vector(384)`` with the dimension as
a DDL literal. 384 is the output dimension of the pinned
Embedding_Model ``sentence-transformers/all-MiniLM-L6-v2`` (design
decision D1; the alternative candidate ``bge-small-en-v1.5`` is also
384-dim, so a model swap needs no schema change). Declaring the
dimension in DDL makes Postgres reject any wrong-dimension write at
the statement level, persisting nothing for that write — exactly the
rejection semantics Requirement 1.6 requires. A future model with a
different output dimension requires a new reviewed migration by
construction (Requirement 1.7); nothing here auto-migrates.

No ANN index (deliberate)
-------------------------
No ivfflat/hnsw index is created. Phase 2 performs no vector search:
embeddings are read back by their primary-entity key only
(``resume_id`` / ``match_result_id``) and there are no cross-user
similarity queries (Requirement 9.6, scope boundary). An ANN index
would cost write amplification and memory for zero reads. Revisit only
when a phase introduces vector search, with its own reviewed migration.

Index justifications (conventions.md — every WHERE/ORDER BY on a
non-PK column gets an index, documented here)
-------------------------------------------------------------------
* ``UNIQUE (resume_id)`` / ``UNIQUE (match_result_id)`` — enforce "at
  most one current embedding per source row" (regeneration under a new
  model upserts, Requirement 2.7). The unique constraints' backing
  indexes double as the lookup indexes for the read-by-source-entity
  path, so no separate index on those columns is needed.
* ``resume_embeddings_user_id_idx`` / ``match_embeddings_user_id_idx``
  — every embedding read is scoped to the owning User_Account exactly
  as resume/match reads are scoped in Phase 1 (Requirement 1.8), so
  ``user_id`` appears in WHERE clauses and needs an index.

Ownership and association enforcement (Requirement 1.8)
-------------------------------------------------------
FKs to ``users`` and to the source entity (``resumes`` /
``match_results``), all ``NOT NULL`` with ``ON DELETE CASCADE``: a
write that does not reference an existing owning user and source
entity is rejected by the database, and deleting the owner or the
source row removes the derived embedding (embeddings are derived
Restricted PII and must not outlive their source).

Atomicity (Requirements 1.3, 1.9)
---------------------------------
Postgres DDL is transactional and Alembic runs this revision in a
single transaction: any failure — insufficient privileges, a schema
conflict, or pgvector unavailability (``CREATE EXTENSION`` raises,
naming the missing extension) — rolls back the whole revision,
leaving the schema unchanged with no partial objects, so a failed run
is safely re-runnable after the cause is fixed.

No fine-grained GRANT/REVOKE block is needed: ``0001``'s
``ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT,
UPDATE, DELETE ON TABLES TO matchlayer_app`` covers both new tables.

Design reference: Data Models / New tables (phase-2-nlp-embeddings);
Requirements 1.1, 1.2, 1.3, 1.6, 1.8, 1.9.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0003_pgvector_embeddings"
down_revision: str | Sequence[str] | None = "0002_resumes_and_matches"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


class _Vector384(sa.types.UserDefinedType[Any]):
    """Minimal DDL-only type emitting the literal ``vector(384)``.

    Defined locally so the migration has no import dependency on the
    ``pgvector`` Python package (the ORM models add that dependency
    separately). The 384 literal below is the single declaration point
    for the column dimension in this migration (Requirement 1.6).
    """

    cache_ok = True

    def get_col_spec(self, **kw: Any) -> str:
        return "vector(384)"


def upgrade() -> None:
    # 1. Extension first — the vector column type below depends on it.
    #    If pgvector is not installed on the server, this statement
    #    fails naming the missing extension and the transaction rolls
    #    back with the schema unchanged (Requirement 1.9).
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # 2. resume_embeddings — one stored embedding per resume (1.1, 1.8).
    op.create_table(
        "resume_embeddings",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "resume_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("resumes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Derived from Restricted PII — access-scoped to the owner,
        # vector values never logged (security.md).
        sa.Column("embedding", _Vector384(), nullable=False),
        # Model identity makes the reuse-vs-regenerate decision
        # decidable from stored data (Requirement 2.10).
        sa.Column("model_name", sa.Text(), nullable=False),
        sa.Column("model_revision", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("resume_id", name="resume_embeddings_resume_id_uniq"),
    )

    # 3. match_embeddings — one JD embedding per match result (1.1, 1.8).
    #    No updated_at: a match result's JD embedding is written once at
    #    match creation and never regenerated (design ERD).
    op.create_table(
        "match_embeddings",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "match_result_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("match_results.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("embedding", _Vector384(), nullable=False),
        sa.Column("model_name", sa.Text(), nullable=False),
        sa.Column("model_revision", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("match_result_id", name="match_embeddings_match_result_id_uniq"),
    )

    # 4. user_id indexes — owner-scoped reads (justification in the
    #    docstring above, per conventions.md).
    op.create_index("resume_embeddings_user_id_idx", "resume_embeddings", ["user_id"])
    op.create_index("match_embeddings_user_id_idx", "match_embeddings", ["user_id"])


def downgrade() -> None:
    # Drop in reverse creation order. The extension is intentionally
    # NOT dropped: CREATE EXTENSION IF NOT EXISTS means this revision
    # may not have created it, and dropping it could break objects
    # outside this migration's ownership. Removing the extension is an
    # explicit operator decision, not a side effect of a downgrade.
    op.drop_index("match_embeddings_user_id_idx", table_name="match_embeddings")
    op.drop_table("match_embeddings")

    op.drop_index("resume_embeddings_user_id_idx", table_name="resume_embeddings")
    op.drop_table("resume_embeddings")
