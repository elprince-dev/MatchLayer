"""Integration test for POST /api/v1/auth/logout (task 8.11).

Validates Requirements 4.1-4.6.

Cookie-API note (task 16.6): cookies are set on the
``httpx.AsyncClient`` instance via :mod:`._cookies` helpers rather
than per-request ``cookies=`` kwargs. httpx 0.27+ deprecated the
per-request shape and the API's pytest ``filterwarnings = ["error"]``
config escalates the ``DeprecationWarning`` into a teardown failure.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from matchlayer_api.core.security.jwt import issue_refresh_token
from matchlayer_api.db.models import AuditEvent

from ._cookies import set_auth_cookies
from .conftest import UserWithRefreshFactory, postgres_available, unique_email

pytestmark = pytest.mark.skipif(
    not postgres_available(),
    reason="Postgres not available (docker-compose not running)",
)


@pytest.mark.asyncio
async def test_logout_happy_204(
    client_with_session: AsyncClient,
    factory_user_with_refresh: UserWithRefreshFactory,
    db_session: AsyncSession,
) -> None:
    """Happy 204: row revoked, cookies cleared, logout audit row."""
    user, refresh_row = await factory_user_with_refresh(email=unique_email("logout"))
    refresh_jwt = issue_refresh_token(sub=str(user.id), jti=refresh_row.jti)
    csrf_value = set_auth_cookies(client_with_session, refresh=refresh_jwt)

    res = await client_with_session.post(
        "/api/v1/auth/logout",
        headers={"X-CSRF-Token": csrf_value},
    )
    assert res.status_code == 204

    # Row revoked
    await db_session.refresh(refresh_row)
    assert refresh_row.revoked_at is not None

    # Audit logout row
    result = await db_session.execute(
        select(AuditEvent).where(AuditEvent.event_type == "logout", AuditEvent.user_id == user.id)
    )
    assert result.scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_logout_missing_cookie_204(client_with_session: AsyncClient) -> None:
    """No refresh cookie → 204 with cookies cleared."""
    res = await client_with_session.post("/api/v1/auth/logout")
    assert res.status_code == 204


@pytest.mark.asyncio
async def test_logout_idempotent(
    client_with_session: AsyncClient,
    factory_user_with_refresh: UserWithRefreshFactory,
    db_session: AsyncSession,
) -> None:
    """Re-logout against already-revoked jti → 204, no duplicate audit."""
    user, refresh_row = await factory_user_with_refresh(email=unique_email("idem"))
    refresh_jwt = issue_refresh_token(sub=str(user.id), jti=refresh_row.jti)
    csrf_value = set_auth_cookies(client_with_session, refresh=refresh_jwt)

    # First logout.
    await client_with_session.post(
        "/api/v1/auth/logout",
        headers={"X-CSRF-Token": csrf_value},
    )

    # The router clears cookies on logout responses; re-set them so the
    # second request still presents the same (now-revoked) jti.
    set_auth_cookies(client_with_session, refresh=refresh_jwt, csrf=csrf_value)

    # Second logout with the same jti.
    res = await client_with_session.post(
        "/api/v1/auth/logout",
        headers={"X-CSRF-Token": csrf_value},
    )
    assert res.status_code == 204

    # Only one audit row.
    result = await db_session.execute(
        select(AuditEvent).where(AuditEvent.event_type == "logout", AuditEvent.user_id == user.id)
    )
    rows = result.scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_logout_csrf_mismatch_403(
    client_with_session: AsyncClient,
    factory_user_with_refresh: UserWithRefreshFactory,
) -> None:
    """CSRF mismatch → 403 csrf_mismatch."""
    user, refresh_row = await factory_user_with_refresh(email=unique_email("csrffail"))
    refresh_jwt = issue_refresh_token(sub=str(user.id), jti=refresh_row.jti)
    set_auth_cookies(client_with_session, refresh=refresh_jwt, csrf="value-A")

    res = await client_with_session.post(
        "/api/v1/auth/logout",
        headers={"X-CSRF-Token": "value-B"},
    )
    assert res.status_code == 403
    assert res.json()["type"] == "csrf_mismatch"
