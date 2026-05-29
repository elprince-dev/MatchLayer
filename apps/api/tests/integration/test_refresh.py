"""Integration test for POST /api/v1/auth/refresh (task 8.10).

Validates Requirements 3.1-3.10, 8.2-8.4.

Cookie-API note (task 16.6): cookies are set on the
``httpx.AsyncClient`` instance via :mod:`._cookies` helpers rather
than per-request ``cookies=`` kwargs (httpx 0.27+ deprecation).
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from matchlayer_api.core.security.jwt import issue_refresh_token
from matchlayer_api.db.models import AuditEvent, RefreshToken

from ._cookies import set_auth_cookies
from .conftest import UserWithRefreshFactory, postgres_available, unique_email

pytestmark = pytest.mark.skipif(
    not postgres_available(),
    reason="Postgres not available (docker-compose not running)",
)


@pytest.mark.asyncio
async def test_refresh_happy_rotation(
    client_with_session: AsyncClient,
    factory_user_with_refresh: UserWithRefreshFactory,
    db_session: AsyncSession,
) -> None:
    """Happy rotation: same family_id, predecessor revoked, fresh CSRF, audit row."""
    user, refresh_row = await factory_user_with_refresh(email=unique_email("rot"))
    family_id = refresh_row.family_id
    refresh_jwt = issue_refresh_token(sub=str(user.id), jti=refresh_row.jti)
    csrf_value = set_auth_cookies(client_with_session, refresh=refresh_jwt)

    res = await client_with_session.post(
        "/api/v1/auth/refresh",
        headers={"X-CSRF-Token": csrf_value},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["access_token"]

    # Predecessor revoked
    await db_session.refresh(refresh_row)
    assert refresh_row.revoked_at is not None

    # New row in same family
    result = await db_session.execute(
        select(RefreshToken).where(
            RefreshToken.family_id == family_id,
            RefreshToken.revoked_at.is_(None),
        )
    )
    assert result.scalar_one_or_none() is not None

    # Audit refresh_token_rotated
    result = await db_session.execute(
        select(AuditEvent).where(AuditEvent.event_type == "refresh_token_rotated")
    )
    assert result.scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_refresh_missing_cookie_401(client_with_session: AsyncClient) -> None:
    """Missing refresh cookie → 401 missing_refresh_cookie."""
    res = await client_with_session.post("/api/v1/auth/refresh")
    assert res.status_code == 401
    assert res.json()["type"] == "missing_refresh_cookie"


@pytest.mark.asyncio
async def test_refresh_csrf_mismatch_403(
    client_with_session: AsyncClient,
    factory_user_with_refresh: UserWithRefreshFactory,
) -> None:
    """CSRF mismatch → 403 csrf_mismatch."""
    user, refresh_row = await factory_user_with_refresh(email=unique_email("csrf"))
    refresh_jwt = issue_refresh_token(sub=str(user.id), jti=refresh_row.jti)
    set_auth_cookies(client_with_session, refresh=refresh_jwt, csrf="value-A")

    res = await client_with_session.post(
        "/api/v1/auth/refresh",
        headers={"X-CSRF-Token": "value-B"},
    )
    assert res.status_code == 403
    assert res.json()["type"] == "csrf_mismatch"


@pytest.mark.asyncio
async def test_refresh_invalid_token_401(client_with_session: AsyncClient) -> None:
    """Invalid refresh token → 401 invalid_refresh_token."""
    set_auth_cookies(client_with_session, refresh="not.a.valid.jwt", csrf="csrf-value")

    res = await client_with_session.post(
        "/api/v1/auth/refresh",
        headers={"X-CSRF-Token": "csrf-value"},
    )
    assert res.status_code == 401
    assert res.json()["type"] == "invalid_refresh_token"


@pytest.mark.asyncio
async def test_refresh_reuse_revokes_family(
    client_with_session: AsyncClient,
    factory_user_with_refresh: UserWithRefreshFactory,
    db_session: AsyncSession,
) -> None:
    """Reuse of revoked jti → 401 refresh_token_reused, all family revoked."""
    user, refresh_row = await factory_user_with_refresh(email=unique_email("reuse"))
    refresh_jwt = issue_refresh_token(sub=str(user.id), jti=refresh_row.jti)
    csrf_value = set_auth_cookies(client_with_session, refresh=refresh_jwt)

    # First rotation succeeds.
    await client_with_session.post(
        "/api/v1/auth/refresh",
        headers={"X-CSRF-Token": csrf_value},
    )

    # The router clears cookies on a successful rotation and sets a
    # fresh pair; replay the *original* (now-revoked) jti to exercise
    # the reuse-detection branch.
    set_auth_cookies(client_with_session, refresh=refresh_jwt, csrf=csrf_value)

    res = await client_with_session.post(
        "/api/v1/auth/refresh",
        headers={"X-CSRF-Token": csrf_value},
    )
    assert res.status_code == 401
    assert res.json()["type"] == "refresh_token_reused"

    # All tokens in the family revoked.
    result = await db_session.execute(select(RefreshToken).where(RefreshToken.user_id == user.id))
    for row in result.scalars():
        assert row.revoked_at is not None

    # Audit refresh_token_reuse_detected
    result = await db_session.execute(
        select(AuditEvent).where(AuditEvent.event_type == "refresh_token_reuse_detected")
    )
    assert result.scalar_one_or_none() is not None
