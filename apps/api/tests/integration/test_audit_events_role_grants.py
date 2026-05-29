"""INV-1: The application role cannot rewrite the audit log (task 8.14).

Validates Requirement 11.2.

Connects to Postgres as ``MATCHLAYER_DATABASE_APP_ROLE`` (a *separate*
least-privilege role distinct from the docker-compose ``POSTGRES_USER``
table owner — task 16.4) and asserts:

* ``INSERT`` and ``SELECT`` against ``audit_events`` succeed.
* ``UPDATE``, ``DELETE``, ``TRUNCATE`` raise ``InsufficientPrivilege``.

Why a separate role: in Postgres the table *owner* has implicit ALL
privileges that bypass the GRANT graph. The migration's
``REVOKE UPDATE, DELETE, TRUNCATE`` therefore has no effect against the
owner — INV-1 simply does not hold against the owner connection. The
session-scoped ``audit_role_engine`` fixture in ``conftest.py`` opens
the connection as the dedicated app role so the migration's grants
are actually enforced.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from .conftest import postgres_available

pytestmark = pytest.mark.skipif(
    not postgres_available(),
    reason="Postgres not available (docker-compose not running)",
)


@pytest.mark.asyncio
async def test_app_role_can_insert_and_select(audit_role_engine: Any) -> None:
    """The app role SHALL be able to INSERT and SELECT on audit_events."""
    async with audit_role_engine.connect() as conn:
        # INSERT should succeed.
        await conn.execute(
            text(
                "INSERT INTO audit_events (id, event_type, user_id, payload) "
                "VALUES (gen_random_uuid(), 'login_success', NULL, '{}'::jsonb)"
            )
        )
        # SELECT should succeed.
        result = await conn.execute(text("SELECT COUNT(*) FROM audit_events"))
        count = result.scalar_one()
        assert count >= 0
        await conn.rollback()


@pytest.mark.asyncio
async def test_app_role_cannot_update_audit_events(audit_role_engine: Any) -> None:
    """The app role SHALL NOT be able to UPDATE audit_events.

    SQLAlchemy wraps psycopg's :class:`InsufficientPrivilege` (SQLSTATE
    42501) as :class:`sqlalchemy.exc.ProgrammingError`; asserting on the
    SQLSTATE string keeps the test resilient to the wrapper choice
    (psycopg vs asyncpg) without coupling to a specific exception
    class hierarchy.
    """
    async with audit_role_engine.connect() as conn:
        with pytest.raises(ProgrammingError) as excinfo:
            await conn.execute(text("UPDATE audit_events SET event_type = 'tampered'"))
        assert "permission denied" in str(excinfo.value).lower() or "42501" in str(excinfo.value)
        await conn.rollback()


@pytest.mark.asyncio
async def test_app_role_cannot_delete_audit_events(audit_role_engine: Any) -> None:
    """The app role SHALL NOT be able to DELETE from audit_events."""
    async with audit_role_engine.connect() as conn:
        with pytest.raises(ProgrammingError) as excinfo:
            await conn.execute(text("DELETE FROM audit_events"))
        assert "permission denied" in str(excinfo.value).lower() or "42501" in str(excinfo.value)
        await conn.rollback()


@pytest.mark.asyncio
async def test_app_role_cannot_truncate_audit_events(audit_role_engine: Any) -> None:
    """The app role SHALL NOT be able to TRUNCATE audit_events."""
    async with audit_role_engine.connect() as conn:
        with pytest.raises(ProgrammingError) as excinfo:
            await conn.execute(text("TRUNCATE audit_events"))
        assert "permission denied" in str(excinfo.value).lower() or "42501" in str(excinfo.value)
        await conn.rollback()
