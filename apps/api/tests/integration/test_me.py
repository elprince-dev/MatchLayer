"""Integration test for /me endpoints (task 8.13).

Validates Requirements 6.1-6.8.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from matchlayer_api.core.security.jwt import issue_access_token
from matchlayer_api.db.models import AuditEvent

from .conftest import UserFactory, postgres_available, unique_email

pytestmark = pytest.mark.skipif(
    not postgres_available(),
    reason="Postgres not available (docker-compose not running)",
)


@pytest.mark.asyncio
async def test_get_me_happy_200(
    client_with_session: AsyncClient, factory_user: UserFactory
) -> None:
    """GET /me with valid token → 200 with user fields."""
    email = unique_email("me")
    user = await factory_user(email=email)
    token = issue_access_token(sub=str(user.id))

    res = await client_with_session.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 200
    body = res.json()
    assert body["email"] == email
    assert "password_hash" not in body
    assert "failed_login_count" not in body
    assert "locked_until" not in body


@pytest.mark.asyncio
async def test_get_me_missing_token_401(client_with_session: AsyncClient) -> None:
    """GET /me without token → 401 unauthenticated."""
    res = await client_with_session.get("/api/v1/auth/me")
    assert res.status_code == 401
    assert res.json()["type"] == "unauthenticated"


@pytest.mark.asyncio
async def test_get_me_invalid_token_401(client_with_session: AsyncClient) -> None:
    """GET /me with invalid token → 401 unauthenticated."""
    res = await client_with_session.get(
        "/api/v1/auth/me", headers={"Authorization": "Bearer not.a.valid.jwt"}
    )
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_get_me_soft_deleted_user_401(
    client_with_session: AsyncClient, factory_user: UserFactory
) -> None:
    """User with deleted_at set → 401 unauthenticated."""
    user = await factory_user(email=unique_email("deleted"), deleted_at=datetime.now(UTC))
    token = issue_access_token(sub=str(user.id))

    res = await client_with_session.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_patch_me_display_name_200(
    client_with_session: AsyncClient,
    factory_user: UserFactory,
    db_session: AsyncSession,
) -> None:
    """PATCH /me with valid display_name → 200 with audit row."""
    user = await factory_user(email=unique_email("patch"), display_name="OldName")
    token = issue_access_token(sub=str(user.id))

    res = await client_with_session.patch(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
        json={"display_name": "NewName"},
    )
    assert res.status_code == 200
    assert res.json()["display_name"] == "NewName"

    # Audit display_name_changed
    result = await db_session.execute(
        select(AuditEvent).where(
            AuditEvent.event_type == "display_name_changed",
            AuditEvent.user_id == user.id,
        )
    )
    audit = result.scalar_one_or_none()
    assert audit is not None
    # Payload contains only length fields, not the strings.
    payload = audit.payload
    assert "previous_display_name_length" in payload
    assert "new_display_name_length" in payload
    assert "OldName" not in str(payload)
    assert "NewName" not in str(payload)


@pytest.mark.asyncio
async def test_patch_me_validation_422(
    client_with_session: AsyncClient, factory_user: UserFactory
) -> None:
    """PATCH /me with invalid display_name (too long) → 422."""
    user = await factory_user(email=unique_email("patchfail"))
    token = issue_access_token(sub=str(user.id))

    res = await client_with_session.patch(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
        json={"display_name": "x" * 100},  # > 64 chars
    )
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_patch_me_empty_after_strip_422(
    client_with_session: AsyncClient, factory_user: UserFactory
) -> None:
    """PATCH /me with whitespace-only display_name → 422."""
    user = await factory_user(email=unique_email("patchempty"))
    token = issue_access_token(sub=str(user.id))

    res = await client_with_session.patch(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
        json={"display_name": "   "},
    )
    assert res.status_code == 422
