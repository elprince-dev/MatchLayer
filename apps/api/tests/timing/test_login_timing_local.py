"""INV-5: Login timing for unknown vs known-but-wrong-password is indistinguishable.

Validates Requirement 2.4.

NOTE: Excluded from CI (pytest -m "not timing") because CI runners have
too much background noise for sub-30ms timing assertions. Run locally
with `pytest -m timing apps/api/tests/timing/`.
"""

from __future__ import annotations

import socket
import statistics
import time
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from uuid_utils.compat import uuid7

from matchlayer_api.config import get_settings
from matchlayer_api.core.db import get_session
from matchlayer_api.core.security.passwords import hash_password
from matchlayer_api.db.models import User
from matchlayer_api.main import create_app


def _postgres_available() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 5432), timeout=1):
            pass
        return True
    except OSError:
        return False


pytestmark = [
    pytest.mark.timing,
    pytest.mark.skipif(
        not _postgres_available(),
        reason="Postgres not available for timing test",
    ),
]


SAMPLE_COUNT = 100
MAX_MEDIAN_DELTA_MS = 25  # Requirement 2.4


@pytest.mark.asyncio
async def test_login_timing_unknown_vs_wrong_password() -> None:
    """Run >= 100 trials each, assert median wall-clock delta <= 25 ms."""
    settings = get_settings()
    engine = create_async_engine(str(settings.database_url), poolclass=NullPool)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        # Insert a known user with a real password hash.
        known_email = f"timing-{uuid.uuid4()}@example.com"
        user = User(
            id=uuid7(),
            email=known_email,
            password_hash=hash_password("CorrectPassword12345"),
            display_name="Timing Test",
            failed_login_count=0,
        )
        session.add(user)
        await session.flush()

        app = create_app()

        async def _override():
            yield session

        app.dependency_overrides[get_session] = _override

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            unknown_times: list[float] = []
            wrong_times: list[float] = []

            # Warm-up — first request often hits import / connection setup.
            for _ in range(5):
                await client.post(
                    "/api/v1/auth/login",
                    json={
                        "email": "warmup@example.com",
                        "password": "WarmupPassword12345",
                    },
                )

            # Unknown email trials.
            for _ in range(SAMPLE_COUNT):
                start = time.perf_counter()
                await client.post(
                    "/api/v1/auth/login",
                    json={
                        "email": f"unknown-{uuid.uuid4()}@example.com",
                        "password": "AttemptPassword12345",
                    },
                )
                unknown_times.append(time.perf_counter() - start)

            # Wrong password trials.
            for _ in range(SAMPLE_COUNT):
                start = time.perf_counter()
                await client.post(
                    "/api/v1/auth/login",
                    json={
                        "email": known_email,
                        "password": "WrongPassword12345",
                    },
                )
                wrong_times.append(time.perf_counter() - start)
                # Reset the user's lockout state between trials.
                user.failed_login_count = 0
                user.last_failed_login_at = None
                user.locked_until = None
                await session.flush()

        await session.rollback()
    await engine.dispose()

    median_unknown_ms = statistics.median(unknown_times) * 1000
    median_wrong_ms = statistics.median(wrong_times) * 1000
    delta_ms = abs(median_unknown_ms - median_wrong_ms)

    assert delta_ms <= MAX_MEDIAN_DELTA_MS, (
        f"Login timing delta {delta_ms:.1f} ms exceeds the {MAX_MEDIAN_DELTA_MS} ms "
        f"budget (Requirement 2.4). Unknown median: {median_unknown_ms:.1f} ms, "
        f"wrong-password median: {median_wrong_ms:.1f} ms."
    )
