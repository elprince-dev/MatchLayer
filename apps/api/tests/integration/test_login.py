"""Integration test for POST /api/v1/auth/login (task 8.9).

Validates Requirements 2.1-2.10.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from matchlayer_api.db.models import AuditEvent

from .conftest import UserFactory, postgres_available, unique_email

pytestmark = pytest.mark.skipif(
    not postgres_available(),
    reason="Postgres not available (docker-compose not running)",
)


@pytest.mark.asyncio
async def test_login_happy_200(
    client_with_session: AsyncClient,
    factory_user: UserFactory,
    db_session: AsyncSession,
) -> None:
    """Happy 200 with token + audit login_success."""
    email = unique_email("login")
    user = await factory_user(email=email, password="MyPassword!12345")
    res = await client_with_session.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "MyPassword!12345"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["access_token"]
    assert body["user"]["email"] == email

    # Audit row
    result = await db_session.execute(
        select(AuditEvent).where(
            AuditEvent.event_type == "login_success",
            AuditEvent.user_id == user.id,
        )
    )
    assert result.scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_login_unknown_email_401_literal_detail(
    client_with_session: AsyncClient,
) -> None:
    """Unknown email → 401 with literal 'Email or password is incorrect.' detail."""
    res = await client_with_session.post(
        "/api/v1/auth/login",
        json={"email": unique_email("unknown"), "password": "AnyPassword12345"},
    )
    assert res.status_code == 401
    body = res.json()
    assert body["detail"] == "Email or password is incorrect."


@pytest.mark.asyncio
async def test_login_wrong_password_same_envelope(
    client_with_session: AsyncClient, factory_user: UserFactory
) -> None:
    """Wrong password → 401 with byte-for-byte identical envelope to unknown email."""
    email = unique_email("real")
    await factory_user(email=email, password="CorrectPassword12345")
    res = await client_with_session.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "WrongPassword12345"},
    )
    assert res.status_code == 401
    body = res.json()
    assert body["detail"] == "Email or password is incorrect."


@pytest.mark.asyncio
async def test_login_failed_counter_increments_and_locks(
    client_with_session: AsyncClient,
    factory_user: UserFactory,
    db_session: AsyncSession,
) -> None:
    """Failed login increments counter; threshold triggers lockout."""
    email = unique_email("lockme")
    user = await factory_user(email=email, password="CorrectPassword12345")

    # Get the threshold from settings (default 10).
    from matchlayer_api.config import get_settings

    threshold = get_settings().auth_lockout_threshold

    # Submit threshold failed attempts.
    for _ in range(threshold):
        await client_with_session.post(
            "/api/v1/auth/login",
            json={"email": email, "password": "WrongPassword12345"},
        )

    # Now the account should be locked.
    await db_session.refresh(user)
    assert user.locked_until is not None

    # Audit account_locked row
    result = await db_session.execute(
        select(AuditEvent).where(
            AuditEvent.event_type == "account_locked",
            AuditEvent.user_id == user.id,
        )
    )
    assert result.scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_login_locked_account_returns_423(
    client_with_session: AsyncClient,
    factory_user: UserFactory,
    db_session: AsyncSession,
) -> None:
    """Locked account → 423 without incrementing counter."""
    from datetime import UTC, datetime, timedelta

    email = unique_email("alreadylocked")
    user = await factory_user(email=email, password="CorrectPassword12345")
    # Manually lock the account.
    user.locked_until = datetime.now(UTC) + timedelta(minutes=15)
    await db_session.flush()

    res = await client_with_session.post(
        "/api/v1/auth/login",
        json={
            "email": email,
            "password": "CorrectPassword12345",
        },
    )
    assert res.status_code == 423
