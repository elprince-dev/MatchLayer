"""users and auth tables

Revision ID: 0001_users_and_auth
Revises: 0000_baseline
Create Date: 2026-05-28

Task 16.4 deviation note
------------------------
The migration's GRANT/REVOKE block previously keyed on
``MATCHLAYER_DATABASE_APP_ROLE`` whose default was ``matchlayer`` —
the same name as the ``POSTGRES_USER`` in ``docker-compose.yml``.
That role is the database owner: in Postgres the table owner has
implicit ALL privileges that bypass the GRANT graph entirely, so
``REVOKE UPDATE, DELETE, TRUNCATE`` against it is silently a no-op.
The INV-1 invariant (Requirement 11.2 — "the application role cannot
rewrite the audit log") therefore did not actually hold, and the
matching tests `DID NOT RAISE`.

Fix shape: the migration now ensures the *least-privilege*
``matchlayer_app`` role exists (via ``DO ... CREATE ROLE IF NOT
EXISTS`` per the PostgreSQL idiom) before it grants/revokes on
``audit_events``. The default for ``MATCHLAYER_DATABASE_APP_ROLE``
flips to ``matchlayer_app`` to match the design's primary paragraph
(§4.5, §11.4). The dev-stack ``matchlayer`` superuser keeps
unrestricted DDL access; only the application's own runtime
connection runs as ``matchlayer_app`` and is bound by the GRANT
graph. The role is also provisioned by the
``infra/docker/postgres-init/01-create-app-role.sql`` script so a
fresh data volume gets the role at first boot.
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_users_and_auth"
down_revision: str | Sequence[str] | None = "0000_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Default flipped from ``matchlayer`` (the docker-compose superuser/
# owner) to ``matchlayer_app`` (the least-privilege role). The owner
# bypasses the GRANT graph in Postgres, so granting/revoking against
# it does not enforce INV-1. Override via the env var if a deployment
# uses a different role name.
_APP_ROLE = os.environ.get("MATCHLAYER_DATABASE_APP_ROLE", "matchlayer_app")
_APP_ROLE_PASSWORD = os.environ.get(
    "MATCHLAYER_DATABASE_APP_ROLE_PASSWORD", "dev_only_app_role_password"
)


def upgrade() -> None:
    # 0. Ensure the least-privilege role exists before granting on it
    #    (task 16.4). ``CREATE ROLE IF NOT EXISTS`` is not a vanilla
    #    Postgres construct; the standard idiom is the
    #    ``DO $$ ... pg_roles lookup ... $$`` block below. Idempotent
    #    so a manual replay of the migration is safe.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_APP_ROLE}') THEN
                CREATE ROLE {_APP_ROLE} LOGIN PASSWORD '{_APP_ROLE_PASSWORD}';
            END IF;
        END
        $$;
        """
    )
    # Schema-level grants so the role can read/write the auth tables
    # the migration creates below. The audit_events fine-grained
    # GRANT/REVOKE block at the bottom of upgrade() narrows the role's
    # privileges on that table only.
    op.execute(f"GRANT USAGE ON SCHEMA public TO {_APP_ROLE}")
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {_APP_ROLE}"
    )

    # 1. users
    op.create_table(
        "users",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("failed_login_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_failed_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute("CREATE UNIQUE INDEX users_email_lower_uniq ON users (lower(email))")

    # 2. refresh_tokens
    op.create_table(
        "refresh_tokens",
        sa.Column("jti", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("family_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "issued_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("refresh_tokens_family_id_idx", "refresh_tokens", ["family_id"])
    op.create_index("refresh_tokens_user_id_idx", "refresh_tokens", ["user_id"])

    # 3. password_reset_tokens
    op.create_table(
        "password_reset_tokens",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.LargeBinary(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("password_reset_tokens_user_id_idx", "password_reset_tokens", ["user_id"])
    op.create_index(
        "password_reset_tokens_token_hash_uniq",
        "password_reset_tokens",
        ["token_hash"],
        unique=True,
    )

    # 4. audit_events
    op.create_table(
        "audit_events",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("ip_address", sa.Text(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("payload", sa.dialects.postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("audit_events_user_id_idx", "audit_events", ["user_id"])
    op.create_index("audit_events_created_at_idx", "audit_events", ["created_at"])

    # 5. Role grants — defense in depth (§4.5, INV-1).
    #    Order matters: REVOKE first so the explicit GRANT below
    #    leaves only INSERT + SELECT in the privilege graph (the
    #    default-privileges ALTER above granted the full read/write
    #    set, including UPDATE + DELETE, to ``matchlayer_app`` on
    #    every newly created table).
    op.execute(f"REVOKE ALL ON TABLE audit_events FROM {_APP_ROLE}")
    op.execute(f"GRANT INSERT, SELECT ON TABLE audit_events TO {_APP_ROLE}")


def downgrade() -> None:
    # Reverse fine-grained grants. Re-grant the full default set so the
    # role can keep operating after the migration is rolled back.
    op.execute(f"REVOKE INSERT, SELECT ON TABLE audit_events FROM {_APP_ROLE}")
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON TABLE audit_events TO {_APP_ROLE}"
    )

    # Drop in reverse order
    op.drop_index("audit_events_created_at_idx", table_name="audit_events")
    op.drop_index("audit_events_user_id_idx", table_name="audit_events")
    op.drop_table("audit_events")

    op.drop_index("password_reset_tokens_token_hash_uniq", table_name="password_reset_tokens")
    op.drop_index("password_reset_tokens_user_id_idx", table_name="password_reset_tokens")
    op.drop_table("password_reset_tokens")

    op.drop_index("refresh_tokens_user_id_idx", table_name="refresh_tokens")
    op.drop_index("refresh_tokens_family_id_idx", table_name="refresh_tokens")
    op.drop_table("refresh_tokens")

    op.execute("DROP INDEX IF EXISTS users_email_lower_uniq")
    op.drop_table("users")
