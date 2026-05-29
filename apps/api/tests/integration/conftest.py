"""Integration-test fixtures for the auth surface (task 8.1).

Provides:
  - ``db_session``: per-test transaction-scoped AsyncSession with rollback
    on teardown. Opened against the docker-compose Postgres.
  - ``redis_client``: per-test Redis client with a unique key prefix and
    post-test flush of that prefix.
  - ``factory_user``: build a User row in the active session.
  - ``factory_user_with_refresh``: build a User + an active RefreshToken row.
  - ``client_with_session``: AsyncClient wired so that the API uses the
    per-test session (via dependency override).
  - ``freeze_time``: helper for time-controlled tests.
  - ``unique_email``: helper returning a unique RFC 6761/2606-compliant
    test email so per-test DB isolation does not depend on transaction
    rollback unwinding committed rows.

Skip-if-no-infra: if Postgres or Redis isn't reachable, the entire
integration suite is skipped via ``pytestmark`` at module level on
each integration test file (see test_register.py etc.). This conftest
itself does not skip; it only fails fixture setup if a test actually
requests the fixture.

DB isolation note (task 16.3, fix-shape **option a + option c hybrid**)
-----------------------------------------------------------------------
Several auth flows commit through the API's ``AsyncSession`` *inside*
service / router code (``Auth_Service.register``, the rotate / logout
/ password-reset paths each end with ``await session.commit()`` in
``auth/router.py``). The per-test ``db_session.rollback()`` here can
only unwind work staged on its own session — it cannot reach commits
the API issued through its dependency-injected session. That meant
literal-email fixtures like ``"logout@test.local"`` survived between
tests and the next test that asked ``factory_user(email="logout@…")``
hit the functional unique index ``users_email_lower_uniq``. It also
meant ``audit_events`` and ``password_reset_tokens`` rows from prior
tests stayed visible to "select all rows of type X" assertions in
the next test, producing false ``MultipleResultsFound`` errors.

Chosen fix combines option (a) and option (c) from the task:

* **Option (a) — unique local-parts:** ``unique_email`` returns a
  fresh ``uuid4``-suffixed local-part on every call so the
  ``users_email_lower_uniq`` constraint can never collide between
  tests. Domain is the RFC 2606 ``example.com`` so Pydantic
  ``EmailStr`` (which delegates to ``email-validator``) accepts it
  on the default ``check_deliverability=False`` path. This is the
  primary email-validator hygiene fix folded in from 16.2's failure
  analysis: ``test.local`` is RFC 6761 reserved and 422s before the
  auth logic ever runs.
* **Option (c) — autouse truncate:** ``_truncate_auth_tables``
  (autouse, function-scoped) ``TRUNCATE``-with-``RESTART IDENTITY
  CASCADE`` the four auth tables in FK order before every test.
  This wipes the committed-state spillover that option (a) alone
  cannot reach — audit rows, refresh tokens, and password-reset
  tokens written through the API's own session.

The pair is "bluntest + cheapest" rather than the SAVEPOINT plumbing
of option (b) because it stays entirely in test fixtures: zero
production-code changes, no SQLAlchemy listener wiring, and the
``TRUNCATE`` runs against a docker-compose Postgres in millisecond
range. The case-insensitive uniqueness invariant in
``users_email_lower_uniq`` (Design §4.1, §16.2) is preserved — this
fix does not weaken any production constraint.
"""

from __future__ import annotations

import socket
import urllib.parse
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool
from uuid_utils.compat import uuid7

from matchlayer_api.config import get_settings
from matchlayer_api.core.db import get_session
from matchlayer_api.core.security.passwords import hash_password
from matchlayer_api.db.models import RefreshToken, User
from matchlayer_api.main import create_app


def _service_available(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
        return True
    except OSError:
        return False


def postgres_available() -> bool:
    return _service_available("127.0.0.1", 5432)


def redis_available() -> bool:
    return _service_available("127.0.0.1", 6379)


# ---------------------------------------------------------------------------
# Email helper (task 16.3 — DB isolation via unique local-parts).
# ---------------------------------------------------------------------------


# RFC 2606 reserves ``example.com`` for documentation/testing and
# ``email-validator`` (the package Pydantic ``EmailStr`` delegates to)
# accepts it on the default ``check_deliverability=False`` path.
# ``test.local`` (the prior choice) is RFC 6761 reserved and rejected
# by ``email-validator`` as an undeliverable TLD, so any test that
# routes its email through ``EmailStr`` (register, login, password-
# reset request) used to fail with HTTP 422 before the auth logic ran.
TEST_EMAIL_DOMAIN: Final[str] = "example.com"


def unique_email(prefix: str = "user") -> str:
    """Return a fresh email guaranteed to be unique across the suite.

    The local-part carries an 8-char random suffix from
    :func:`uuid.uuid4` so two consecutive calls — including across
    separate tests, including when the same test is repeated — never
    collide on the ``users_email_lower_uniq`` functional index. The
    domain is the RFC 2606 reserved ``example.com`` so Pydantic
    ``EmailStr`` accepts it without a real-world MX record.

    Why ``uuid4`` and not ``uuid7`` for the suffix: a UUIDv7's
    leading bits are a millisecond-precision timestamp, so taking the
    *first* 8 hex chars yields identical prefixes across an entire
    test run (the high-order timestamp bits roll over only every
    ~16 minutes). UUIDv4 is fully random and a 32-bit suffix gives
    ~4 billion distinct values — comfortable headroom for a test
    suite that runs hundreds of cases.

    Args:
        prefix: Optional human-readable prefix for log readability
            (e.g. ``"logout"``, ``"reset"``). Lower-cased to keep the
            stored email column visually consistent — case-insensitive
            lookup still works on any case (PBT-5).

    Returns:
        ``"<prefix>-<8 hex chars>@example.com"``.
    """
    return f"{prefix.lower()}-{uuid.uuid4().hex[:8]}@{TEST_EMAIL_DOMAIN}"


# ---------------------------------------------------------------------------
# DB fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(autouse=True)
async def _truncate_auth_tables() -> AsyncIterator[None]:
    """Wipe the four auth tables before every integration test (task 16.3).

    Because the API commits through its own ``AsyncSession`` inside
    ``Auth_Service`` / ``auth/router.py``, the per-test ``db_session``
    rollback cannot reach those rows. Without this fixture, a previous
    test's committed ``users``, ``refresh_tokens``,
    ``password_reset_tokens``, or ``audit_events`` rows leak into the
    next test and either trip the ``users_email_lower_uniq`` index
    (when the next test reuses an email) or produce
    ``MultipleResultsFound`` on global table queries.

    ``TRUNCATE ... RESTART IDENTITY CASCADE`` issued in FK order
    (children → parent) is the cheapest reset that stays entirely in
    the test layer and survives ``pytest --count=N`` re-runs without
    accumulating state. Issued through a fresh short-lived connection
    so it commits before the test's own ``db_session`` opens its
    transaction.

    Skips silently when Postgres is unreachable; tests that need
    Postgres surface that requirement through the connection failure
    they raise downstream.
    """
    if not postgres_available():
        yield
        return
    settings = get_settings()
    engine = create_async_engine(
        str(settings.database_url),
        echo=False,
        poolclass=NullPool,
    )
    try:
        async with engine.begin() as conn:
            # Order matches FK dependency: refresh_tokens, password_reset_tokens,
            # and audit_events all reference users.
            await conn.exec_driver_sql(
                "TRUNCATE TABLE refresh_tokens, password_reset_tokens, "
                "audit_events, users RESTART IDENTITY CASCADE"
            )
        yield
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """Per-test session opened against the docker-compose Postgres.

    Wraps the test in a transaction that is rolled back on teardown so
    no test pollutes another. Uses ``expire_on_commit=False`` so detached
    ORM instances stay readable after flush.

    Pool note (task 16.9): ``poolclass=NullPool`` is REQUIRED here.
    Without it, asyncpg's default pool keeps idle ``Connection`` objects
    whose ``StreamReader`` / ``StreamWriter`` socket pairs (AF_UNIX
    self-pipes for the running ``_UnixSelectorEventLoop``) bind to the
    per-test event loop. ``engine.dispose()`` releases the engine but
    ``__del__`` on the pooled connections can race the loop's close,
    surfacing as ``ResourceWarning: unclosed <socket.socket fd=N,
    family=1, type=1, proto=0>`` and ``ResourceWarning: unclosed event
    loop`` after the session ends. ``filterwarnings = ["error"]`` then
    promotes those into a teardown ``ExceptionGroup`` that fails the
    backend CI check. ``NullPool`` opens and closes a connection per
    checkout, eliminating the leak surface.
    """
    settings = get_settings()
    engine = create_async_engine(
        str(settings.database_url),
        echo=False,
        poolclass=NullPool,
    )
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        try:
            yield session
        finally:
            await session.rollback()
    await engine.dispose()


# ---------------------------------------------------------------------------
# Audit-role engine (task 16.4 / INV-1).
# ---------------------------------------------------------------------------


def _build_audit_role_dsn() -> str:
    """Return a DSN authenticated as ``MATCHLAYER_DATABASE_APP_ROLE``.

    The integration suite's default :func:`db_session` connects as the
    table owner (``MATCHLAYER_DATABASE_URL`` user), which by Postgres
    semantics has implicit ALL privileges that bypass the GRANT graph.
    INV-1 (Requirement 11.2 — "the application role cannot rewrite the
    audit log") therefore *cannot* be exercised against that
    connection: the migration's ``REVOKE UPDATE, DELETE, TRUNCATE`` is
    a no-op against an owner.

    This helper rewrites the configured DSN to use the dedicated
    least-privilege role (``MATCHLAYER_DATABASE_APP_ROLE`` /
    ``MATCHLAYER_DATABASE_APP_ROLE_PASSWORD``) instead. The role is
    provisioned by
    ``infra/docker/postgres-init/01-create-app-role.sql`` and re-asserted
    by the auth migration's ``DO $$ ... CREATE ROLE IF NOT EXISTS $$``
    block, so a fresh data volume gets it at first boot.
    """
    settings = get_settings()
    parsed = urllib.parse.urlparse(str(settings.database_url))
    role = settings.database_app_role
    password = urllib.parse.quote(settings.database_app_role_password.get_secret_value())
    netloc = f"{role}:{password}@{parsed.hostname}"
    if parsed.port:
        netloc += f":{parsed.port}"
    return urllib.parse.urlunparse(
        (parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment)
    )


@pytest_asyncio.fixture
async def audit_role_engine() -> AsyncIterator[Any]:
    """Async engine authenticated as the app role for INV-1.

    INV-1 (test_audit_events_role_grants.py) connects through this
    engine so the GRANT/REVOKE graph the migration emits is actually
    enforced. Function-scoped so it shares the per-test event loop —
    pytest-asyncio's default ``asyncio_default_fixture_loop_scope =
    "function"`` config means a session-scoped async fixture would
    bind to the first test's loop and explode in every subsequent
    test.
    """
    if not postgres_available():
        yield None
        return
    engine = create_async_engine(
        _build_audit_role_dsn(),
        echo=False,
        poolclass=NullPool,
    )
    try:
        yield engine
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Redis fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(autouse=True)
async def _flush_rate_limiter_keys() -> AsyncIterator[None]:
    """Wipe rate-limiter keys before each integration test (task 16.2).

    The production ``Rate_Limiter`` writes real keys
    (``rl:auth:login:email:…``, ``rl:auth:register:ip:…``, etc.) into
    the same Redis instance every integration test shares. Without
    isolation, counts from a prior test leak into the next one
    whenever two tests reuse the same email or IP, producing a 429
    on the *first* request of the second test even though the
    sliding-window cap was never reached for that test in isolation.

    Chosen shape: a function-scoped autouse fixture that issues a
    targeted ``SCAN MATCH rl:* | DEL`` (rather than ``FLUSHDB``) so
    the sibling ``redis_client`` fixture's per-test prefixed keys are
    not collateral damage. No production-code change is required —
    Option A from task 16.2's "fix shape" alternatives — because the
    boundary tests' import rule scopes to ``src/matchlayer_api/`` and
    ``tests/integration/conftest.py`` may import ``redis.asyncio``
    directly. Skips silently when Redis is unreachable; tests that
    need Redis surface that requirement through the connection
    failure they raise downstream.
    """
    if not redis_available():
        yield
        return
    settings = get_settings()
    client = Redis.from_url(str(settings.redis_url), decode_responses=False)
    try:
        async for key in client.scan_iter(match="rl:*"):
            await client.delete(key)
        yield
    finally:
        # Mirrors ``core/rate_limit.py::get_rate_limiter``'s teardown
        # discipline (task 16.9): a bare ``aclose()`` releases the
        # *borrowed* connection but leaves the underlying
        # ``ConnectionPool``'s idle ``Connection`` objects holding
        # their ``StreamReader``/``StreamWriter`` socket pairs. Those
        # streams are bound to the per-test event loop; without an
        # explicit pool ``disconnect()`` they fall through to ``__del__``
        # after the loop closes and pytest's
        # ``filterwarnings = ["error"]`` config promotes the resulting
        # ``ResourceWarning: unclosed <socket.socket ...>`` /
        # ``ResourceWarning: unclosed event loop`` into a session-end
        # ``ExceptionGroup`` that fails the ``backend`` CI check.
        await client.aclose(close_connection_pool=True)
        await client.connection_pool.disconnect()


@pytest_asyncio.fixture
async def redis_client() -> AsyncIterator[Redis]:
    """Per-test Redis client. Cleans up keys with the test's prefix on teardown."""
    settings = get_settings()
    prefix = f"test:{uuid.uuid4()}:"
    client = Redis.from_url(str(settings.redis_url), decode_responses=False)
    try:
        # Attach the prefix as a context attr so tests can use it.
        client._test_prefix = prefix  # type: ignore[attr-defined]
        yield client
    finally:
        # Flush any key with this test's prefix.
        async for key in client.scan_iter(match=f"{prefix}*"):
            await client.delete(key)
        # Drain the connection pool deterministically (task 16.9). See
        # the matching note on ``_flush_rate_limiter_keys`` above:
        # ``aclose()`` alone leaks ``Connection`` objects whose socket
        # streams are bound to the per-test event loop, surfacing as a
        # session-end ``ResourceWarning`` ExceptionGroup under
        # ``filterwarnings = ["error"]``.
        await client.aclose(close_connection_pool=True)
        await client.connection_pool.disconnect()


# ---------------------------------------------------------------------------
# Factory fixtures
# ---------------------------------------------------------------------------


UserFactory = Callable[..., Awaitable[User]]


@pytest_asyncio.fixture
async def factory_user(db_session: AsyncSession) -> UserFactory:
    """Return a builder that inserts a User row into ``db_session``.

    Usage::

        user = await factory_user(email="alice@example.com", password="Password!12345")
    """

    async def _build(
        *,
        email: str | None = None,
        password: str = "Password!12345",
        display_name: str | None = None,
        deleted_at: datetime | None = None,
    ) -> User:
        # Default to a unique local-part on a deliverable-domain
        # (``example.com``) so two consecutive calls cannot collide on
        # the ``users_email_lower_uniq`` index (task 16.3) and Pydantic
        # ``EmailStr`` accepts the resulting value (task 16.3 follow-up
        # to 16.2: ``email-validator`` rejects ``test.local`` as RFC
        # 6761 reserved).
        resolved_email = email or unique_email()
        user = User(
            id=uuid7(),
            email=resolved_email,
            password_hash=hash_password(password),
            display_name=display_name or resolved_email.split("@")[0],
            failed_login_count=0,
            last_failed_login_at=None,
            locked_until=None,
            deleted_at=deleted_at,
        )
        db_session.add(user)
        await db_session.flush()
        return user

    return _build


UserWithRefreshFactory = Callable[..., Awaitable[tuple[User, RefreshToken]]]


@pytest_asyncio.fixture
async def factory_user_with_refresh(
    db_session: AsyncSession, factory_user: UserFactory
) -> UserWithRefreshFactory:
    """Return a builder that creates a User + an active RefreshToken row."""

    async def _build(
        *,
        email: str | None = None,
        password: str = "Password!12345",
        family_id: Any = None,
    ) -> tuple[User, RefreshToken]:
        user = await factory_user(email=email, password=password)
        now = datetime.now(UTC)
        refresh = RefreshToken(
            jti=uuid7(),
            family_id=family_id or uuid7(),
            user_id=user.id,
            issued_at=now,
            expires_at=now + timedelta(days=7),
            revoked_at=None,
        )
        db_session.add(refresh)
        await db_session.flush()
        return user, refresh

    return _build


# ---------------------------------------------------------------------------
# HTTP client fixture wired to use the per-test session
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def client_with_session(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """AsyncClient where the API's get_session is overridden to yield ``db_session``."""
    app = create_app()

    async def _override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _override

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac

    app.dependency_overrides.pop(get_session, None)


# ---------------------------------------------------------------------------
# Time freezing helper (no external lib — simple monkey-patch)
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _frozen_time_ctx(target: datetime) -> AsyncIterator[None]:
    """Freeze ``services.auth._now`` and yield."""
    from matchlayer_api.services import auth as auth_module

    original = auth_module._now
    auth_module._now = lambda: target  # type: ignore[assignment]
    try:
        yield
    finally:
        auth_module._now = original  # type: ignore[assignment]


@pytest.fixture
def freeze_time() -> Callable[[datetime], Any]:
    """Return a context manager builder that freezes ``services.auth._now``."""
    return _frozen_time_ctx
