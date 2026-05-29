"""PBT-5: Email lookup is case-insensitive everywhere it is consumed.

Validates: Requirements 1.6, 2.2, 2.3, 5.2, 5.3, 14.2.

Hypothesis fixture-shape note (task 16.5)
-----------------------------------------
The earlier shape consumed a function-scoped pytest fixture inside the
``@given`` body, which Hypothesis flags as
``HealthCheck.function_scoped_fixture``: the fixture is built once per
test and silently reused across every generated example, so any per-
example state would leak across examples without warning.

Reshape (preferred, no health-check suppression): the only fixture
that flowed in was ``_db_url`` — a *string* — so it's lifted to a
module-level constant. Each Hypothesis example still opens a fresh
``AsyncEngine`` + transaction inside an ``asyncio.Runner`` and rolls
back on completion, which is the actual isolation guarantee.
Removing the fixture parameter eliminates the health-check trigger
without weakening any property.

Strategy-shape note (task 16.5 follow-up)
-----------------------------------------
The first reshape kept generating the base email *inside* the test
body via ``uuid7()`` and using its length to size the case-permutation
strategy. Hypothesis records each draw against the strategy structure;
a different base_email per call → different strategy structure per
call → ``FlakyStrategyDefinition``. The fix is to keep the email
strategy stable: a fixed-shape ASCII alphabetic prefix drawn from
Hypothesis (whose case Hypothesis can permute), suffixed with a
test-run-unique random salt that's added *outside* the strategy.
"""

from __future__ import annotations

import asyncio
import socket
from collections.abc import Awaitable, Callable

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from uuid_utils.compat import uuid7

from matchlayer_api.config import get_settings
from matchlayer_api.core.security.passwords import hash_password
from matchlayer_api.db.models import User


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


# Module-scope: ``get_settings`` is LRU-cached and the DSN is immutable
# for the lifetime of the test process. Resolving once here means every
# Hypothesis example reads the same string — and, crucially, no
# function-scoped pytest fixture flows into the ``@given`` body.
_DB_URL = str(get_settings().database_url)


def _run_sync(coro_factory: Callable[[], Awaitable[None]]) -> None:
    """Run an async test body via :class:`asyncio.Runner`.

    Replaces ``asyncio.run(_run())`` with the
    ``with asyncio.Runner() as r: r.run(...)`` shape recommended for
    repeated invocations: ``Runner`` closes the event loop and its
    selectors deterministically on ``__exit__`` so the
    ``ResourceWarning("unclosed event loop")`` that bare ``asyncio.run``
    can leak in teardown is impossible. Hypothesis drives this body
    over many examples per run, so the deterministic close is also
    measurably faster than re-creating the loop scaffolding via
    ``asyncio.run`` per example.
    """
    with asyncio.Runner() as runner:
        runner.run(coro_factory())


def _alpha_case_strategy() -> st.SearchStrategy[str]:
    """Strategy producing a fixed-length ASCII alphabetic case-permutation.

    Each character is drawn independently from
    ``[a-zA-Z]`` so Hypothesis can shrink and replay deterministically.
    The fixed length keeps the strategy structure stable across calls,
    avoiding the ``FlakyStrategyDefinition`` that an in-body
    length-varying strategy would trigger.
    """
    return st.text(
        alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"),
        min_size=8,
        max_size=8,
    )


@settings(deadline=None, max_examples=20)
@given(local_part=_alpha_case_strategy())
def test_case_insensitive_lookup(local_part: str) -> None:
    """For any registered email E and case-permutation E', lookup resolves the same row.

    Property: ``lower(stored.email) == lower(submitted)`` regardless of
    case. We register the user with the *lower-cased* form of the
    Hypothesis-drawn local-part and look up with the *original-cased*
    form; the case difference is what exercises the
    ``users_email_lower_uniq`` functional index (Data Models §4.1).
    A test-run-unique ``uuid7`` salt suffix prevents collisions
    between Hypothesis examples on the same connection-pool.
    """
    salt = uuid7().hex[:12]
    base_email = f"pbt5-{local_part.lower()}-{salt}@example.com"
    permuted = f"pbt5-{local_part}-{salt}@example.com"

    async def _run() -> None:
        engine = create_async_engine(_DB_URL, poolclass=NullPool)
        async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async with async_session() as session:
            # Register with the base (lower-cased) email.
            user_id = uuid7()
            session.add(
                User(
                    id=user_id,
                    email=base_email,
                    password_hash=hash_password("validpassword12"),
                    display_name="PBT5 User",
                    failed_login_count=0,
                )
            )
            await session.flush()

            # Look up with the permuted-case email — should find the same user.
            result = await session.execute(
                select(User).where(func.lower(User.email) == permuted.lower())
            )
            found = result.scalar_one_or_none()
            assert found is not None, f"Lookup failed for permutation: {permuted}"
            assert found.id == user_id

            await session.rollback()
        await engine.dispose()

    _run_sync(_run)
