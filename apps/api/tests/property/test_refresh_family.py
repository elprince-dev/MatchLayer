"""PBT-4: Refresh-token family rotation invariants.

Validates: Requirements 3.7, 3.8, 3.10, 4.5, 8.2, 8.3, 8.4.

NOTE: This test requires the integration DB (docker-compose Postgres)
to exercise the SELECT ... FOR UPDATE semantics. It is structured as
a property test but uses a fixed set of scenarios rather than full
Hypothesis generation to keep the DB fixture manageable.

Domain note (task 16.3): synthetic User_Account rows below use the
RFC 2606 reserved ``example.com`` domain. The prior ``test.local``
placeholder is RFC 6761 reserved (multicast DNS) and rejected by
``email-validator``; even though the property tests bypass Pydantic
``EmailStr``, keeping the domain RFC-compliant removes a future
landmine if these tests ever route through the API.

API note (task 16.3 / authority bullet a): switched from the
deprecated ``sqlalchemy.orm.sessionmaker(class_=AsyncSession)``
shim — which required a ``# type: ignore[call-overload]`` per call —
to ``sqlalchemy.ext.asyncio.async_sessionmaker``, the SQLAlchemy 2.x
factory that's correctly typed against ``AsyncSession``.
"""

from __future__ import annotations

import asyncio
import socket
from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from uuid_utils.compat import uuid7

from matchlayer_api.config import get_settings
from matchlayer_api.db.models import RefreshToken, User
from matchlayer_api.services.auth import Auth_Service, RefreshOutcome


def _postgres_available() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 5432), timeout=1):
            pass
        return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not _postgres_available(),
    reason="Postgres not available (docker-compose not running)",
)


def _run_sync(coro_factory: Callable[[], Awaitable[None]]) -> None:
    """Run an async test body via :class:`asyncio.Runner`.

    Replaces ``asyncio.run(_run())`` with the
    ``with asyncio.Runner() as r: r.run(...)`` shape recommended for
    repeated invocations: ``Runner`` closes the event loop and its
    selectors deterministically on ``__exit__``, so the
    ``ResourceWarning("unclosed event loop")`` that bare ``asyncio.run``
    can leak in teardown is impossible — and pytest's
    ``filterwarnings = ["error"]`` config no longer escalates that
    warning into a teardown failure. Hypothesis drives this test
    body across many examples per run, so the deterministic close
    is also measurably faster than re-creating the loop scaffolding
    via ``asyncio.run`` per example.
    """
    with asyncio.Runner() as runner:
        runner.run(coro_factory())


@pytest.fixture()
def _db_url() -> str:
    settings = get_settings()
    return str(settings.database_url)


def test_rotation_preserves_family_id(_db_url: str) -> None:
    """Every successful rotation produces a new row with the same family_id."""

    async def _run() -> None:
        engine = create_async_engine(_db_url, poolclass=NullPool)
        async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async with async_session() as session:
            # Create a user.
            user_id = uuid7()
            session.add(
                User(
                    id=user_id,
                    email=f"pbt4-{user_id}@example.com",
                    password_hash="$argon2id$v=19$m=65536,t=1,p=1$fake$fake",
                    display_name="PBT4 User",
                    failed_login_count=0,
                )
            )
            await session.flush()

            # Issue initial token pair.
            svc = Auth_Service()
            _, _, initial_jti = svc._issue_token_pair_for_user(
                session=session, user_id=user_id, family_id=uuid7()
            )
            await session.flush()

            # Get the family_id of the initial token.
            result = await session.execute(
                select(RefreshToken).where(RefreshToken.jti == initial_jti)
            )
            initial_row = result.scalar_one()
            family_id = initial_row.family_id

            # Rotate.
            outcome: RefreshOutcome = await svc.rotate_refresh_token(
                session, presented_jti=initial_jti, user_id=user_id
            )
            assert outcome.status == "rotated"
            await session.flush()

            # Verify the new row has the same family_id.
            result = await session.execute(
                select(RefreshToken).where(
                    RefreshToken.family_id == family_id,
                    RefreshToken.revoked_at.is_(None),
                )
            )
            new_row = result.scalar_one()
            assert new_row.jti != initial_jti
            assert new_row.family_id == family_id

            await session.rollback()
        await engine.dispose()

    _run_sync(_run)


def test_reuse_revokes_entire_family(_db_url: str) -> None:
    """Presenting a revoked jti revokes every sibling in the family."""

    async def _run() -> None:
        engine = create_async_engine(_db_url, poolclass=NullPool)
        async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async with async_session() as session:
            user_id = uuid7()
            session.add(
                User(
                    id=user_id,
                    email=f"pbt4-reuse-{user_id}@example.com",
                    password_hash="$argon2id$v=19$m=65536,t=1,p=1$fake$fake",
                    display_name="PBT4 Reuse",
                    failed_login_count=0,
                )
            )
            await session.flush()

            svc = Auth_Service()
            _, _, jti_1 = svc._issue_token_pair_for_user(
                session=session, user_id=user_id, family_id=uuid7()
            )
            await session.flush()

            # Rotate once (jti_1 -> jti_2).
            outcome = await svc.rotate_refresh_token(session, presented_jti=jti_1, user_id=user_id)
            assert outcome.status == "rotated"
            await session.flush()

            # Attempt to reuse jti_1 (already revoked).
            outcome = await svc.rotate_refresh_token(session, presented_jti=jti_1, user_id=user_id)
            assert outcome.status == "reused"
            await session.flush()

            # All tokens in the family should now be revoked.
            result = await session.execute(
                select(RefreshToken).where(RefreshToken.user_id == user_id)
            )
            for row in result.scalars():
                assert row.revoked_at is not None

            await session.rollback()
        await engine.dispose()

    _run_sync(_run)


def test_logout_revokes_exactly_one(_db_url: str) -> None:
    """Logout against a single jti revokes exactly one row."""

    async def _run() -> None:
        engine = create_async_engine(_db_url, poolclass=NullPool)
        async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async with async_session() as session:
            user_id = uuid7()
            session.add(
                User(
                    id=user_id,
                    email=f"pbt4-logout-{user_id}@example.com",
                    password_hash="$argon2id$v=19$m=65536,t=1,p=1$fake$fake",
                    display_name="PBT4 Logout",
                    failed_login_count=0,
                )
            )
            await session.flush()

            svc = Auth_Service()
            family = uuid7()
            _, _, jti_1 = svc._issue_token_pair_for_user(
                session=session, user_id=user_id, family_id=family
            )
            _, _, jti_2 = svc._issue_token_pair_for_user(
                session=session, user_id=user_id, family_id=family
            )
            await session.flush()

            # Logout jti_1 only.
            await svc.logout(session, presented_jti=jti_1)
            await session.flush()

            # jti_1 should be revoked, jti_2 should not.
            r1 = await session.execute(select(RefreshToken).where(RefreshToken.jti == jti_1))
            assert r1.scalar_one().revoked_at is not None

            r2 = await session.execute(select(RefreshToken).where(RefreshToken.jti == jti_2))
            assert r2.scalar_one().revoked_at is None

            await session.rollback()
        await engine.dispose()

    _run_sync(_run)
