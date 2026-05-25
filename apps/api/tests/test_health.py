"""Tests for the ``GET /healthz`` endpoint.

Covers Task 3.11 (Requirements 4.7, 4.8, 4.9, 4.14 / Design §6.5):

* **Success path** — when the request-scoped session's ``SELECT 1``
  probe completes, the endpoint returns ``200`` with body
  ``{"status": "ok"}``. The probe runs through FastAPI's dependency
  override on :func:`~matchlayer_api.core.db.get_session` so no real
  Postgres is required.
* **Failure path** — when the probe raises any subclass of
  :class:`sqlalchemy.exc.SQLAlchemyError`, the endpoint returns
  ``503`` with body
  ``{"status": "unhealthy", "reason": "database_unreachable"}``.
* **Negative-leak assertion (Requirement 4.14)** — the response body
  on the failure path contains no DSN driver name, no committed dev
  password placeholder, no asyncpg/postgresql identifiers, and no
  trace of the simulated exception's message string. ``security.md``
  classifies DSN content and credentials as Confidential and forbids
  them from leaving the system through error responses.

The fixtures driving these tests live in :mod:`tests.conftest`. The
:class:`httpx.AsyncClient` returned from the ``client`` fixture uses
:class:`httpx.ASGITransport`, which deliberately does NOT trigger the
ASGI ``lifespan.startup`` event — meaning
:func:`~matchlayer_api.core.db.verify_database_connection` never runs
under these tests. That keeps the suite hermetic; lifespan-driven
coverage of the real probe lives in :mod:`tests.test_main` via
:class:`fastapi.testclient.TestClient`.
"""

from __future__ import annotations

from collections.abc import Callable

from httpx import AsyncClient
from sqlalchemy.exc import OperationalError, SQLAlchemyError

# Type alias mirroring :data:`tests.conftest.OverrideGetSession`. The
# ``tests/`` directory is not a Python package (no ``__init__.py``,
# per pytest's preferred ``rootdir + tests/`` convention), so a
# relative ``from .conftest import OverrideGetSession`` is not
# importable. Re-declaring the alias here is the simplest way to keep
# the fixture parameter typed without making ``tests/`` a package
# (which would also force the sibling test modules into the same
# package, with import-name knock-on effects).
OverrideGetSession = Callable[[SQLAlchemyError | None], None]

# A canary substring we attach to the simulated exception's message.
# Asserting its absence in the 503 response body is the load-bearing
# check for "the failure path does not echo the original exception
# message" (Requirement 4.14 / security.md "no PII / DSN / credentials
# in error responses"). The string deliberately does NOT look like a
# real DSN — using a real-looking DSN in test data would itself be a
# bad pattern.
_SIMULATED_EXCEPTION_DETAIL = "simulated failure"


async def test_healthz_returns_200_ok_when_db_probe_succeeds(
    client: AsyncClient,
    override_get_session: OverrideGetSession,
) -> None:
    """Requirements 4.7, 4.8: success path returns ``200 {"status": "ok"}``.

    The dependency override yields a stub session whose ``execute``
    resolves to a benign :class:`MagicMock` — exactly the shape the
    real handler expects from :py:meth:`AsyncSession.execute`.
    """
    override_get_session(None)

    response = await client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_healthz_returns_503_unhealthy_when_db_probe_raises(
    client: AsyncClient,
    override_get_session: OverrideGetSession,
) -> None:
    """Requirement 4.9: failure path returns the canonical 503 envelope.

    Drives the failure branch by overriding :func:`get_session` with
    a stub whose ``execute`` raises a :class:`SQLAlchemyError`.
    :class:`OperationalError` is the most representative subclass —
    it is what asyncpg surfaces for "DSN unreachable" or "credentials
    rejected", which is the precise condition Requirement 4.9 names.
    """
    override_get_session(SQLAlchemyError(_SIMULATED_EXCEPTION_DETAIL))

    response = await client.get("/healthz")

    assert response.status_code == 503
    assert response.json() == {
        "status": "unhealthy",
        "reason": "database_unreachable",
    }


async def test_healthz_failure_response_body_contains_no_dsn_or_credentials(
    client: AsyncClient,
    override_get_session: OverrideGetSession,
) -> None:
    """Requirement 4.14: 503 body must not echo DSN, credentials, or PII.

    Belt-and-braces negative assertion. The 503 contract test above
    locks down the exact body shape; this one defends against future
    regressions that might widen the body and accidentally pull in
    the connection-string or exception-message text. The substrings
    checked are:

    * ``asyncpg`` / ``postgresql`` — DSN driver/scheme identifiers
      that would only appear in the body if the handler started
      stringifying SQLAlchemy errors or the active engine URL.
    * ``dev_only_password`` — the committed ``.env.example``
      placeholder for the local Postgres password. Never a real
      secret, but this exact string IS the sentinel value the
      ``MATCHLAYER_DATABASE_URL`` in the test environment carries —
      so its absence here proves the handler isn't echoing the DSN.
    * ``matchlayer:`` — the ``user:`` form that would only appear
      in serialized DSN output (``postgresql+asyncpg://matchlayer:...``).
      The bare word ``matchlayer`` legitimately appears in the API
      title, so the trailing colon disambiguates from non-DSN uses.
    * The simulated exception's message (``_SIMULATED_EXCEPTION_DETAIL``)
      — proves the handler isn't ``str(exc)``-ing the original error.
    """
    # Use a real OperationalError (the type asyncpg raises for
    # connectivity failures) to make the test as faithful as possible
    # to the real failure mode. ``str(OperationalError)`` chains in
    # the SQL it tried to run AND the original DBAPI error message,
    # both of which can plausibly include credential or DSN-shaped
    # content in production — exactly what Requirement 4.14 wants
    # filtered out.
    boom = OperationalError(
        statement="SELECT 1",
        params=None,
        orig=Exception(_SIMULATED_EXCEPTION_DETAIL),
    )
    override_get_session(boom)

    response = await client.get("/healthz")

    assert response.status_code == 503

    # Inspect the raw response text rather than the parsed JSON so
    # the assertion catches accidental leakage through any field —
    # not just ones we know to look up — and through HTTP headers
    # rendered into the body of any future debug output.
    raw_body = response.text

    forbidden_substrings = [
        "asyncpg",
        "postgresql",
        "dev_only_password",
        "matchlayer:",
        _SIMULATED_EXCEPTION_DETAIL,
        "SELECT 1",  # part of the OperationalError repr — must not surface
    ]
    for needle in forbidden_substrings:
        assert needle not in raw_body, (
            f"Response body must not contain {needle!r}; got body={raw_body!r}"
        )
