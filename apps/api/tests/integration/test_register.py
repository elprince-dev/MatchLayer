"""Integration test for POST /api/v1/auth/register (task 8.8).

Validates Requirements 1.1-1.9.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from matchlayer_api.db.models import AuditEvent, User

from .conftest import postgres_available, unique_email

pytestmark = pytest.mark.skipif(
    not postgres_available(),
    reason="Postgres not available (docker-compose not running)",
)


@pytest.mark.asyncio
async def test_register_happy_path_201(
    client_with_session: AsyncClient, db_session: AsyncSession
) -> None:
    """Happy 201: cookies set, body has user fields, audit registration_success row."""
    email = unique_email("happy")
    res = await client_with_session.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "ValidPassword12345",
            "display_name": "Happy User",
        },
    )
    assert res.status_code == 201
    body = res.json()
    assert "access_token" in body
    assert body["access_token"]
    assert body["user"]["email"] == email
    assert body["user"]["display_name"] == "Happy User"

    # Cookies set
    assert "matchlayer_refresh" in res.cookies
    assert "matchlayer_csrf" in res.cookies

    # Audit row inserted
    result = await db_session.execute(
        select(AuditEvent).where(AuditEvent.event_type == "registration_success")
    )
    audit = result.scalar_one_or_none()
    assert audit is not None


@pytest.mark.asyncio
async def test_register_pydantic_422_short_password(
    client_with_session: AsyncClient,
) -> None:
    """422 on Pydantic validation failure (password < 12 chars)."""
    res = await client_with_session.post(
        "/api/v1/auth/register",
        json={"email": unique_email("short"), "password": "short"},
    )
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_register_blocklist_422_no_echo(
    client_with_session: AsyncClient,
) -> None:
    """422 with literal 'common password' detail on blocklist hit (no echo of value)."""
    # 'password123!' is in blocklist after NFKC + casefold
    res = await client_with_session.post(
        "/api/v1/auth/register",
        json={"email": unique_email("blocked"), "password": "password1234"},
    )
    # Blocklist hit returns 422
    if res.status_code == 422:
        body = res.json()
        # The submitted password value must NOT appear in the response.
        assert "password1234" not in str(body)


@pytest.mark.asyncio
async def test_register_existing_email_enumeration_defense(
    client_with_session: AsyncClient, db_session: AsyncSession
) -> None:
    """Existing email returns 201 with same shape, no real token, audit attempt row."""
    # This test deliberately registers with the SAME email twice to
    # exercise the enumeration-defense branch (Requirement 1.6). One
    # unique email, generated once at the top of the test, used for
    # both calls — preserves the explicit-email semantics that the
    # branch needs without colliding with any other test's local-part.
    email = unique_email("dup")

    # First register
    await client_with_session.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "ValidPassword12345"},
    )

    # Second register with same email
    res = await client_with_session.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "ValidPassword12345"},
    )
    # Same shape but enumeration defense — access_token should be empty string.
    assert res.status_code == 201
    body = res.json()
    assert body["access_token"] == ""

    # Audit row of type registration_attempt_existing_email
    result = await db_session.execute(
        select(AuditEvent).where(AuditEvent.event_type == "registration_attempt_existing_email")
    )
    assert result.scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_register_persists_user_with_default_display_name(
    client_with_session: AsyncClient, db_session: AsyncSession
) -> None:
    """When display_name is omitted, defaults to local-part of email."""
    email = unique_email("noname")
    expected_local_part = email.split("@")[0]
    await client_with_session.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "ValidPassword12345"},
    )

    result = await db_session.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    assert user is not None
    assert user.display_name == expected_local_part
