"""Tests for the ``GET /healthz`` endpoint.

Covers Task 3.11 (Requirements 4.7, 4.8, 4.9, 4.14 / Design §6.5):

* **Success path** — when the request-scoped session's ``SELECT 1``
  probe completes, the endpoint returns ``200`` with body
  ``{"status": "ok", "semantic_scoring": ...}``. The probe runs
  through FastAPI's dependency override on
  :func:`~matchlayer_api.core.db.get_session` so no real Postgres is
  required.
* **Semantic availability (Phase 2, Requirement 7.5)** — the
  ``semantic_scoring`` field maps
  :func:`~matchlayer_api.ml.semantic_adapter.semantic_available` to
  exactly ``"available"`` / ``"unavailable"`` without changing the
  status-code semantics.
* **LLM availability (Phase 3, Requirements 10.1, 10.2, 10.4, 10.5,
  10.6)** — the additive ``llm`` field reports ``"unavailable"`` iff
  the provider API key was absent at startup or the
  Spend_Circuit_Breaker is open, ``"available"`` otherwise; the value
  never changes the 200 status and never exposes the key or spend
  figures.
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

import pytest
from httpx import AsyncClient
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from matchlayer_api.services.llm.spend import BreakerCause, BreakerState

# Type alias mirroring :data:`tests.conftest.OverrideGetSession`. The
# ``tests/`` directory is not a Python package (no ``__init__.py``,
# per pytest's preferred ``rootdir + tests/`` convention), so a
# relative ``from .conftest import OverrideGetSession`` is not
# importable. Re-declaring the alias here is the simplest way to keep
# the fixture parameter typed without making ``tests/`` a package
# (which would also force the sibling test modules into the same
# package, with import-name knock-on effects).
OverrideGetSession = Callable[[SQLAlchemyError | None], None]


class _StubJobQueue:
    """Minimal stand-in for the process-wide JobQueue (phase-4).

    The health handler only awaits ``healthcheck()`` (which never
    raises), so a canned boolean is the entire surface these tests need.
    """

    def __init__(self, *, healthy: bool) -> None:
        self._healthy = healthy

    async def healthcheck(self) -> bool:
        return self._healthy


@pytest.fixture(autouse=True)
def _stub_agents_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep every healthz test hermetic w.r.t. the Job_Queue probe.

    Resets the module-level ~10 s memo (so no value leaks across tests)
    and substitutes a stub queue reporting unreachable — the natural
    state of a test environment with no LocalStack. Individual tests
    override ``get_job_queue`` again to drive the ``available`` branch.
    """
    monkeypatch.setattr("matchlayer_api.api.health._agents_cache", None)
    monkeypatch.setattr(
        "matchlayer_api.api.health.get_job_queue",
        lambda: _StubJobQueue(healthy=False),
    )


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

    Phase 2 (Requirement 7.5): the body also carries the
    ``semantic_scoring`` availability field. No model artifact loads in
    the test environment, so it reports ``"unavailable"`` — while the
    status code stays 200, proving Degraded_Mode never flips the
    instance to unhealthy.
    """
    override_get_session(None)

    response = await client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "semantic_scoring": "unavailable",
        "llm": "unavailable",
        "agents": "unavailable",
    }


async def test_healthz_semantic_scoring_reports_available_when_pipeline_loaded(
    client: AsyncClient,
    override_get_session: OverrideGetSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requirement 7.5: a loaded semantic pipeline maps to ``"available"``.

    Patches :func:`matchlayer_api.ml.semantic_adapter.semantic_available`
    at the name the health router imported, simulating a successfully
    loaded Phase 2 pipeline without requiring the real model artifact.
    Status-code semantics are identical in both states — only the field
    value changes.
    """
    monkeypatch.setattr("matchlayer_api.api.health.semantic_available", lambda: True)
    override_get_session(None)

    response = await client.get("/healthz")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["semantic_scoring"] == "available"


class _StubBreaker:
    """Minimal stand-in for the process-wide SpendCircuitBreaker.

    The health handler only reads the memoized ``.state`` snapshot
    (design decision D5), so a frozen :class:`BreakerState` is the
    entire surface these tests need to control.
    """

    def __init__(self, *, is_open: bool, cause: BreakerCause | None = None) -> None:
        self.state = BreakerState(
            is_open=is_open,
            tracked_spend=None,
            limit=None,
            cause=cause,
        )


async def test_healthz_llm_reports_available_when_key_present_and_breaker_closed(
    client: AsyncClient,
    override_get_session: OverrideGetSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requirement 10.6: not LLM_Unavailable → ``llm: "available"``.

    Patches both availability sources at the names the health router
    imported: a validated key recorded at startup and a closed
    Spend_Circuit_Breaker. This is also the Requirement 10.4 recovery
    shape — both sources are re-read per probe, so a breaker that
    closes (month rollover / raised limit) flips the field back with
    no code change.
    """
    monkeypatch.setattr("matchlayer_api.api.health.llm_key_present", lambda: True)
    monkeypatch.setattr(
        "matchlayer_api.api.health.get_spend_circuit_breaker",
        lambda: _StubBreaker(is_open=False),
    )
    override_get_session(None)

    response = await client.get("/healthz")

    assert response.status_code == 200
    assert response.json()["llm"] == "available"


async def test_healthz_llm_reports_unavailable_when_key_absent(
    client: AsyncClient,
    override_get_session: OverrideGetSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requirement 10.2: key absent at startup → ``llm: "unavailable"``.

    A closed breaker does not rescue an absent key — either cause of
    LLM_Unavailable is sufficient. The status code stays 200: an
    instance without LLM features is still serving (Requirement 10.1).
    """
    monkeypatch.setattr("matchlayer_api.api.health.llm_key_present", lambda: False)
    monkeypatch.setattr(
        "matchlayer_api.api.health.get_spend_circuit_breaker",
        lambda: _StubBreaker(is_open=False),
    )
    override_get_session(None)

    response = await client.get("/healthz")

    assert response.status_code == 200
    assert response.json()["llm"] == "unavailable"


async def test_healthz_llm_reports_unavailable_when_breaker_open(
    client: AsyncClient,
    override_get_session: OverrideGetSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requirement 10.2: Spend_Circuit_Breaker open → ``llm: "unavailable"``.

    Key present, breaker open — the second cause of LLM_Unavailable.
    The 200 status is unchanged (Requirement 10.1) and the body carries
    no spend figures or breaker internals (Requirement 10.5): only the
    two-value ``llm`` field distinguishes the states.
    """
    monkeypatch.setattr("matchlayer_api.api.health.llm_key_present", lambda: True)
    monkeypatch.setattr(
        "matchlayer_api.api.health.get_spend_circuit_breaker",
        lambda: _StubBreaker(is_open=True, cause=BreakerCause.LIMIT_REACHED),
    )
    override_get_session(None)

    response = await client.get("/healthz")

    assert response.status_code == 200
    body = response.json()
    assert body["llm"] == "unavailable"
    # Requirement 10.5: no key material, spend figures, or provider
    # account details — the body is exactly the four known fields.
    assert set(body) == {"status", "semantic_scoring", "llm", "agents"}
    raw_body = response.text
    for needle in ("spend", "limit", "cost", "sk-", "openrouter"):
        assert needle not in raw_body.lower(), (
            f"healthz body must not contain {needle!r}; got body={raw_body!r}"
        )


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


# ---------------------------------------------------------------------------
# Phase 4 (phase-4-agentic task 11.8, Requirements 16.1, 16.6): the
# ``agents`` field's available branch and the ~10 s probe memo. The
# unavailable branch is pinned by the autouse ``_stub_agents_queue``
# fixture above (every prior success-path test asserts
# ``agents: "unavailable"``).
# ---------------------------------------------------------------------------


class _CountingJobQueue:
    """JobQueue stub counting ``healthcheck`` probes for the cache tests."""

    def __init__(self, *, healthy: bool) -> None:
        self._healthy = healthy
        self.probes = 0

    async def healthcheck(self) -> bool:
        self.probes += 1
        return self._healthy


async def test_healthz_agents_reports_available_when_queue_reachable(
    client: AsyncClient,
    override_get_session: OverrideGetSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requirement 16.1: a reachable Job_Queue maps to ``agents: "available"``.

    Patches ``get_job_queue`` at the name the health router imported with
    a stub whose ``healthcheck`` resolves True. The 200 status semantics
    are identical in both states — only the field value changes — and
    the body stays exactly the four known fields, never a queue URL,
    endpoint address, or credential (Requirement 16.6).
    """
    monkeypatch.setattr(
        "matchlayer_api.api.health.get_job_queue",
        lambda: _StubJobQueue(healthy=True),
    )
    override_get_session(None)

    response = await client.get("/healthz")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["agents"] == "available"
    assert set(body) == {"status", "semantic_scoring", "llm", "agents"}
    raw_body = response.text.lower()
    for needle in ("sqs", "queue-url", "amazonaws", "localstack", "aws_", "secret"):
        assert needle not in raw_body, (
            f"healthz body must not contain {needle!r}; got body={response.text!r}"
        )


async def test_healthz_agents_probe_is_cached_within_the_ttl_window(
    client: AsyncClient,
    override_get_session: OverrideGetSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requirement 16.1 (design §7): the probe result is memoized ~10 s.

    Two back-to-back probes inside one cache window hit the Job_Queue
    exactly once; both responses carry the same cached value, so healthz
    stays cheap under orchestration-frequency polling.
    """
    queue = _CountingJobQueue(healthy=True)
    monkeypatch.setattr("matchlayer_api.api.health.get_job_queue", lambda: queue)
    override_get_session(None)

    first = await client.get("/healthz")
    second = await client.get("/healthz")

    assert first.json()["agents"] == "available"
    assert second.json()["agents"] == "available"
    assert queue.probes == 1, "the second probe within the TTL must be served from the memo"


async def test_healthz_agents_probe_reprobes_after_cache_expiry(
    client: AsyncClient,
    override_get_session: OverrideGetSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An expired memo re-probes, so recovery is observed within one window.

    The cached entry is backdated past the ~10 s TTL between requests
    (rather than sleeping); the second request must consult the queue
    again — and a queue whose reachability changed flips the field
    without any code change (the Requirement 10.4-style recovery shape,
    here for agents).
    """
    import matchlayer_api.api.health as health_module

    queue = _CountingJobQueue(healthy=True)
    monkeypatch.setattr("matchlayer_api.api.health.get_job_queue", lambda: queue)
    override_get_session(None)

    first = await client.get("/healthz")
    assert first.json()["agents"] == "available"
    assert queue.probes == 1

    # Backdate the memo beyond the TTL and flip the queue's health: the
    # next probe must observe the new state.
    recorded_at, reachable = health_module._agents_cache
    monkeypatch.setattr(
        health_module,
        "_agents_cache",
        (recorded_at - health_module._AGENTS_HEALTH_CACHE_TTL_SECONDS - 1.0, reachable),
    )
    queue._healthy = False

    second = await client.get("/healthz")

    assert queue.probes == 2, "an expired memo must re-probe the Job_Queue"
    assert second.json()["agents"] == "unavailable"
