"""Empty baseline revision.

This revision exists for one reason: future migrations need a parent.
``phase-1-foundation`` deliberately ships no domain tables (Design
§6.7, Requirement 4.11), so the upgrade and downgrade bodies are
no-ops. The first real migration — landing in ``phase-1-auth`` —
sets ``down_revision = "0000_baseline"`` and adds the auth schema.

Running ``alembic upgrade head`` against this revision creates the
``alembic_version`` bookkeeping table on a fresh database and stamps
it with revision ``0000_baseline``. No application tables are
created or modified.

Revision ID: 0000_baseline
Revises:
Create Date: 2025-05-24
"""

from __future__ import annotations

from collections.abc import Sequence

# Revision identifiers used by Alembic. ``down_revision = None`` marks
# this as the root of the migration graph; future revisions chain off
# the ``revision`` value below. ``branch_labels`` and ``depends_on``
# are unused in Phase 1 but kept here so ``script.py.mako`` matches
# the shape of every committed revision file.
revision: str = "0000_baseline"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """No-op: the baseline introduces no schema changes."""
    pass


def downgrade() -> None:
    """No-op: the baseline introduces no schema changes."""
    pass
