"""Integration test for password-reset endpoints (task 8.12).

Validates Requirements 5.1-5.11, 8.5.
"""

from __future__ import annotations

import hashlib
import secrets

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from matchlayer_api.db.models import AuditEvent, PasswordResetToken, RefreshToken

from .conftest import UserFactory, UserWithRefreshFactory, postgres_available, unique_email

pytestmark = pytest.mark.skipif(
    not postgres_available(),
    reason="Postgres not available (docker-compose not running)",
)


@pytest.mark.asyncio
async def test_password_reset_request_unknown_email_silent_202(
    client_with_session: AsyncClient, db_session: AsyncSession
) -> None:
    """Unknown email → 202 with no row inserted."""
    res = await client_with_session.post(
        "/api/v1/auth/password-reset/request",
        json={"email": unique_email("ghost")},
    )
    assert res.status_code == 202

    # No password_reset_tokens row
    result = await db_session.execute(select(PasswordResetToken))
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_password_reset_request_known_email_inserts_row_with_hash(
    client_with_session: AsyncClient,
    factory_user: UserFactory,
    db_session: AsyncSession,
) -> None:
    """Known email → 202 with SHA-256 hashed token row + audit."""
    email = unique_email("reset")
    user = await factory_user(email=email)

    res = await client_with_session.post(
        "/api/v1/auth/password-reset/request",
        json={"email": email},
    )
    assert res.status_code == 202

    # Row inserted with token_hash that is 32 bytes (SHA-256).
    result = await db_session.execute(
        select(PasswordResetToken).where(PasswordResetToken.user_id == user.id)
    )
    row = result.scalar_one_or_none()
    assert row is not None
    assert len(row.token_hash) == 32

    # Audit row
    result = await db_session.execute(
        select(AuditEvent).where(
            AuditEvent.event_type == "password_reset_requested",
            AuditEvent.user_id == user.id,
        )
    )
    assert result.scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_password_reset_confirm_happy_204(
    client_with_session: AsyncClient,
    factory_user_with_refresh: UserWithRefreshFactory,
    db_session: AsyncSession,
) -> None:
    """Happy confirm: 204, password updated, used_at set, refresh tokens revoked."""
    user, _ = await factory_user_with_refresh(email=unique_email("confirm"))

    # Manually insert a reset token.
    plaintext = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(plaintext.encode()).digest()
    from datetime import UTC, datetime, timedelta

    pr_row = PasswordResetToken(
        user_id=user.id,
        token_hash=token_hash,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        used_at=None,
    )
    db_session.add(pr_row)
    await db_session.flush()

    old_hash = user.password_hash

    res = await client_with_session.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": plaintext, "new_password": "BrandNewPassword12345"},
    )
    assert res.status_code == 204

    # password_hash updated
    await db_session.refresh(user)
    assert user.password_hash != old_hash

    # used_at set
    await db_session.refresh(pr_row)
    assert pr_row.used_at is not None

    # All refresh tokens revoked
    result = await db_session.execute(select(RefreshToken).where(RefreshToken.user_id == user.id))
    for row in result.scalars():
        assert row.revoked_at is not None

    # Audit
    result = await db_session.execute(
        select(AuditEvent).where(
            AuditEvent.event_type == "password_reset_confirmed",
            AuditEvent.user_id == user.id,
        )
    )
    assert result.scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_password_reset_confirm_invalid_token_400(
    client_with_session: AsyncClient,
) -> None:
    """Missing/invalid token → 400 invalid_reset_token."""
    res = await client_with_session.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": "nonexistent-token-value", "new_password": "ValidNewPassword12345"},
    )
    assert res.status_code == 400
    assert res.json()["type"] == "invalid_reset_token"


@pytest.mark.asyncio
async def test_password_reset_confirm_expired_token_400(
    client_with_session: AsyncClient,
    factory_user: UserFactory,
    db_session: AsyncSession,
) -> None:
    """Expired token → 400 invalid_reset_token (same envelope)."""
    from datetime import UTC, datetime, timedelta

    user = await factory_user(email=unique_email("exp"))
    plaintext = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(plaintext.encode()).digest()

    db_session.add(
        PasswordResetToken(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=datetime.now(UTC) - timedelta(hours=1),  # expired
            used_at=None,
        )
    )
    await db_session.flush()

    res = await client_with_session.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": plaintext, "new_password": "ValidNewPassword12345"},
    )
    assert res.status_code == 400
    assert res.json()["type"] == "invalid_reset_token"


@pytest.mark.asyncio
async def test_password_reset_confirm_used_token_400(
    client_with_session: AsyncClient,
    factory_user: UserFactory,
    db_session: AsyncSession,
) -> None:
    """Already-used token → 400 invalid_reset_token."""
    from datetime import UTC, datetime, timedelta

    user = await factory_user(email=unique_email("used"))
    plaintext = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(plaintext.encode()).digest()

    db_session.add(
        PasswordResetToken(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            used_at=datetime.now(UTC),  # already used
        )
    )
    await db_session.flush()

    res = await client_with_session.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": plaintext, "new_password": "ValidNewPassword12345"},
    )
    assert res.status_code == 400
    assert res.json()["type"] == "invalid_reset_token"
