"""Unit tests for the phase-1-matching rate-limit dependency and idempotency
helpers added in task 9.1 (``core/dependencies.py``).

Task 9.2 pins three contracts the resume/match surface composes against:

* **Per-user rate-limit decision** (Requirements 11.1, 11.2, 11.3, 11.7).
  :func:`user_rate_limit` resolves the caller, checks the existing
  :class:`RateLimiter` with the key ``rl:{endpoint}:user:{user_id}``, a
  60-second window, and the per-endpoint per-minute budget from
  :class:`Settings`, then either allows the request, sets ``Retry-After``
  and raises :class:`RateLimited` (→ 429 ``rate_limited``), or sets
  ``Retry-After`` and raises :class:`RateLimiterUnavailableError`
  (→ 503 ``rate_limiter_unavailable``, fail-closed on a Redis outage).

* **Envelope mapping** (Requirements 11.3, 11.7). A request through a
  minimal FastAPI app wired with the *production*
  :func:`register_exception_handlers` confirms a by-policy rejection
  surfaces as HTTP 429 ``rate_limited`` and a Redis-outage decision as
  HTTP 503 ``rate_limiter_unavailable``.

* **Idempotency replay** (Requirements 2.8, 8.9). :class:`IdempotencyStore`
  stores the created resource id and the serialized 201 response under
  ``idem:{user_id}:{route}:{key}`` with a 24h TTL and returns the original
  record on replay; the first writer wins, and keys are scoped so one
  account's key can never replay another's response.

No real Redis is required: the per-user rate-limit decision logic of
:class:`RateLimiter` itself is already covered by
``tests/unit/test_rate_limit.py`` (fake-redis + freezegun) and the
property suite ``tests/property/test_rate_limit_window.py`` (real Redis).
Here the dependency is isolated with a canned/recording limiter so the
test exercises *the dependency's own* responsibilities (key/window/limit
selection, ``Retry-After`` placement, exception type), and the idempotency
store is driven against an in-memory fake implementing the ``get`` / ``set``
(``NX``/``EX``) subset the store touches — mirroring the fake-redis approach
the foundation rate-limit tests use.

Header note (verified during authoring): the ``Retry-After`` header
:func:`user_rate_limit` sets on its injected ``Response`` does NOT survive
to the wire when the dependency raises, because the reused foundation
handler (`core/errors.py`, kept unchanged per task 1.4) renders a fresh
``JSONResponse``. ``Retry-After`` is therefore asserted at the dependency
boundary where the production code actually sets it; the end-to-end
``Retry-After`` assertion against a real Redis is owned by task 12.8.

References: design.md §"Per-user rate limiting and idempotency",
Requirements 11.1, 11.2, 11.3, 11.7, 2.8, 8.9.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest
from fastapi import Depends, FastAPI, Response
from httpx import ASGITransport, AsyncClient
from uuid_utils.compat import uuid7

from matchlayer_api.config import get_settings
from matchlayer_api.core.dependencies import (
    IdempotencyRecord,
    IdempotencyStore,
    RateLimited,
    RateLimiterUnavailableError,
    _build_idempotency_key,
    get_current_user,
    user_rate_limit,
)
from matchlayer_api.core.errors import register_exception_handlers
from matchlayer_api.core.rate_limit import RateLimitDecision, get_rate_limiter

# 24h, mirroring ``dependencies._IDEMPOTENCY_TTL_SECONDS`` (kept as a literal
# here so a regression that quietly changes the production TTL is caught).
_EXPECTED_IDEMPOTENCY_TTL_SECONDS = 24 * 60 * 60


# ---------------------------------------------------------------------------
# Test doubles for the RateLimiter contract the dependency depends on.
#
# The dependency calls ``await rl.check(key, limit=..., window_seconds=...)``
# and acts on the returned :class:`RateLimitDecision`. These doubles expose
# exactly that one method so the dependency's behaviour is isolated from the
# Lua/Redis decision logic (covered elsewhere).
# ---------------------------------------------------------------------------


class _CannedRateLimiter:
    """Return a fixed :class:`RateLimitDecision` from every ``check``."""

    def __init__(self, decision: RateLimitDecision) -> None:
        self._decision = decision

    async def check(self, key: str, *, limit: int, window_seconds: int) -> RateLimitDecision:
        return self._decision


class _RecordingRateLimiter:
    """Record the ``check`` arguments and return a fixed decision.

    Lets the key-shape / window / limit-source assertions (Requirements
    11.1, 11.2) read back exactly what the dependency passed to the limiter.
    """

    def __init__(self, decision: RateLimitDecision) -> None:
        self._decision = decision
        self.calls: list[dict[str, Any]] = []

    async def check(self, key: str, *, limit: int, window_seconds: int) -> RateLimitDecision:
        self.calls.append({"key": key, "limit": limit, "window_seconds": window_seconds})
        return self._decision


def _stub_user(user_id: UUID | None = None) -> SimpleNamespace:
    """A minimal stand-in carrying only the ``.id`` the dependency reads."""
    return SimpleNamespace(id=user_id or uuid7())


def _allowed() -> RateLimitDecision:
    return RateLimitDecision(allowed=True, retry_after_seconds=0, redis_unavailable=False)


def _rejected(retry_after_seconds: int = 37) -> RateLimitDecision:
    return RateLimitDecision(
        allowed=False, retry_after_seconds=retry_after_seconds, redis_unavailable=False
    )


def _redis_down(retry_after_seconds: int = 60) -> RateLimitDecision:
    return RateLimitDecision(
        allowed=False, retry_after_seconds=retry_after_seconds, redis_unavailable=True
    )


# ---------------------------------------------------------------------------
# Per-user rate-limit dependency — driven directly (Requirements 11.1-11.3, 11.7)
# ---------------------------------------------------------------------------


async def test_user_rate_limit_allows_within_budget_sets_no_header() -> None:
    """An allowed decision passes through and sets no ``Retry-After``.

    The dependency must be transparent on the happy path: no exception, and
    no ``Retry-After`` header on the response it was handed.
    """
    dependency = user_rate_limit("resume")
    response = Response()
    limiter = _CannedRateLimiter(_allowed())

    # The dependency returns ``None`` on the happy path; it must not raise
    # and must leave the response header untouched.
    await dependency(
        response=response,
        settings=get_settings(),
        rl=limiter,
        user=_stub_user(),
    )

    assert "Retry-After" not in response.headers


async def test_user_rate_limit_rejection_sets_retry_after_and_raises() -> None:
    """A by-policy rejection sets ``Retry-After`` and raises :class:`RateLimited`.

    Validates Requirement 11.3: the rejecting dependency surfaces the
    integer seconds from the limiter on the ``Retry-After`` response header
    and raises the domain exception the foundation handler maps to a 429
    ``rate_limited`` envelope. The ``category`` is ``"user"`` (per-user key).
    """
    dependency = user_rate_limit("resume")
    response = Response()
    limiter = _CannedRateLimiter(_rejected(retry_after_seconds=37))

    with pytest.raises(RateLimited) as excinfo:
        await dependency(
            response=response,
            settings=get_settings(),
            rl=limiter,
            user=_stub_user(),
        )

    exc = excinfo.value
    assert exc.endpoint == "resume"
    assert exc.category == "user"
    assert exc.retry_after_seconds == 37
    # The header carries the same integer the limiter returned.
    assert response.headers["Retry-After"] == "37"


async def test_user_rate_limit_redis_outage_fails_closed_with_retry_after() -> None:
    """A Redis-unavailable decision raises :class:`RateLimiterUnavailableError`.

    Validates Requirement 11.7: the limiter's fail-closed decision
    (``redis_unavailable=True``) is mapped to the 503 exception, NOT the 429
    one, and ``Retry-After`` is still set so the client can back off.
    """
    dependency = user_rate_limit("match")
    response = Response()
    limiter = _CannedRateLimiter(_redis_down(retry_after_seconds=60))

    with pytest.raises(RateLimiterUnavailableError) as excinfo:
        await dependency(
            response=response,
            settings=get_settings(),
            rl=limiter,
            user=_stub_user(),
        )

    assert excinfo.value.retry_after_seconds == 60
    assert response.headers["Retry-After"] == "60"


@pytest.mark.parametrize(
    ("endpoint", "settings_attr"),
    [
        ("resume", "resume_rate_limit_per_min"),
        ("match", "match_rate_limit_per_min"),
    ],
)
async def test_user_rate_limit_uses_per_user_key_window_and_budget(
    endpoint: str, settings_attr: str
) -> None:
    """The dependency checks the per-user key with the configured budget.

    Validates Requirements 11.1 (resume) and 11.2 (match): the Redis key is
    ``rl:{endpoint}:user:{user_id}``, the window is the fixed 60 seconds, and
    the limit is the per-endpoint per-minute budget read from Settings.
    """
    settings = get_settings()
    user = _stub_user()
    limiter = _RecordingRateLimiter(_allowed())
    dependency = user_rate_limit(endpoint)  # type: ignore[arg-type]

    await dependency(response=Response(), settings=settings, rl=limiter, user=user)

    assert len(limiter.calls) == 1
    call = limiter.calls[0]
    assert call["key"] == f"rl:{endpoint}:user:{user.id}"
    assert call["window_seconds"] == 60
    assert call["limit"] == getattr(settings, settings_attr)


# ---------------------------------------------------------------------------
# Envelope mapping through the production error handlers (Req 11.3, 11.7)
# ---------------------------------------------------------------------------


def _build_app(decision: RateLimitDecision) -> FastAPI:
    """A minimal app whose single route is gated by ``user_rate_limit``.

    Uses the production :func:`register_exception_handlers` so the asserted
    envelope shape is the real one. ``get_current_user`` and
    ``get_rate_limiter`` are overridden so the route needs neither a JWT nor
    a live Redis; the limiter always returns ``decision``.
    """
    app = FastAPI()
    register_exception_handlers(app)

    limiter = _CannedRateLimiter(decision)

    app.dependency_overrides[get_current_user] = lambda: _stub_user()
    app.dependency_overrides[get_rate_limiter] = lambda: limiter

    @app.post("/_test/resumes", dependencies=[Depends(user_rate_limit("resume"))])
    async def _route() -> dict[str, str]:  # pragma: no cover - never reached on reject
        return {"status": "ok"}

    return app


@pytest.fixture
def rate_limited_app() -> Iterator[FastAPI]:
    app = _build_app(_rejected(retry_after_seconds=42))
    yield app
    app.dependency_overrides.clear()


@pytest.fixture
def redis_down_app() -> Iterator[FastAPI]:
    app = _build_app(_redis_down(retry_after_seconds=60))
    yield app
    app.dependency_overrides.clear()


async def _post(app: FastAPI) -> Any:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        return await ac.post("/_test/resumes")


async def test_router_maps_rejection_to_429_rate_limited(rate_limited_app: FastAPI) -> None:
    """A by-policy rejection surfaces as 429 ``rate_limited`` (Requirement 11.3)."""
    response = await _post(rate_limited_app)

    assert response.status_code == 429
    body = response.json()
    assert body["type"] == "rate_limited"
    assert body["status"] == 429


async def test_router_maps_redis_outage_to_503_unavailable(redis_down_app: FastAPI) -> None:
    """A Redis-outage decision surfaces as 503 ``rate_limiter_unavailable`` (Req 11.7)."""
    response = await _post(redis_down_app)

    assert response.status_code == 503
    body = response.json()
    assert body["type"] == "rate_limiter_unavailable"
    assert body["status"] == 503


# ---------------------------------------------------------------------------
# Idempotency store — replay, namespacing, TTL, first-writer-wins (Req 2.8, 8.9)
# ---------------------------------------------------------------------------


class _FakeKVRedis:
    """In-memory fake of the ``get`` / ``set`` (NX/EX) subset the store uses.

    Mirrors redis-py's contract closely enough for :class:`IdempotencyStore`:
    ``set(..., nx=True)`` is a no-op when the key already exists (returns
    ``None``), otherwise stores the value and returns ``True``. ``set_calls``
    records the TTL and NX flag so the 24h-TTL assertion can read them back.
    """

    def __init__(self) -> None:
        self.store: dict[str, Any] = {}
        self.set_calls: list[dict[str, Any]] = []

    async def get(self, name: str) -> Any:
        return self.store.get(name)

    async def set(
        self,
        name: str,
        value: Any,
        *,
        ex: int | None = None,
        px: int | None = None,
        nx: bool = False,
        **_kwargs: Any,
    ) -> bool | None:
        self.set_calls.append({"name": name, "ex": ex, "nx": nx})
        if nx and name in self.store:
            return None
        self.store[name] = value
        return True


def _record(
    resource_id: str | None = None, body: dict[str, Any] | None = None
) -> IdempotencyRecord:
    return IdempotencyRecord(
        resource_id=resource_id or str(uuid7()),
        status_code=201,
        body=body if body is not None else {"id": "r1", "extraction_status": "pending"},
    )


@pytest.fixture
def store() -> tuple[IdempotencyStore, _FakeKVRedis]:
    fake = _FakeKVRedis()
    return IdempotencyStore(fake), fake  # type: ignore[arg-type]


async def test_idempotency_put_then_get_returns_stored_response(
    store: tuple[IdempotencyStore, _FakeKVRedis],
) -> None:
    """A stored key replays the original serialized 201 response (Req 2.8, 8.9).

    The round-tripped record carries the same resource id, the 201 status,
    and the identical response body — this is the replay guarantee the
    Resumes/Matches routers rely on.
    """
    idem, _ = store
    user_id = uuid7()
    record = _record(body={"id": "abc", "score": 87, "scorer_version": "1+lex.1"})

    await idem.put(user_id=user_id, route="matches", key="key-1", record=record)
    replayed = await idem.get(user_id=user_id, route="matches", key="key-1")

    assert replayed == record
    assert replayed is not None
    assert replayed.status_code == 201
    assert replayed.resource_id == record.resource_id
    assert replayed.body == record.body


async def test_idempotency_get_miss_returns_none(
    store: tuple[IdempotencyStore, _FakeKVRedis],
) -> None:
    """An unseen key is a miss so the caller performs the mutation."""
    idem, _ = store
    assert await idem.get(user_id=uuid7(), route="resumes", key="never-seen") is None


async def test_idempotency_key_is_namespaced_with_24h_ttl(
    store: tuple[IdempotencyStore, _FakeKVRedis],
) -> None:
    """The stored key is ``idem:{user_id}:{route}:{key}`` with a 24h TTL.

    Validates the namespacing and 24h-persistence requirement (2.8, 8.9 and
    ``security.md`` "Persist keys for 24h").
    """
    idem, fake = store
    user_id = uuid7()

    await idem.put(user_id=user_id, route="resumes", key="k-9", record=_record())

    expected_key = f"idem:{user_id}:resumes:k-9"
    assert _build_idempotency_key(user_id=user_id, route="resumes", key="k-9") == expected_key
    assert expected_key in fake.store
    assert fake.set_calls[-1]["ex"] == _EXPECTED_IDEMPOTENCY_TTL_SECONDS
    assert fake.set_calls[-1]["nx"] is True


async def test_idempotency_key_is_scoped_per_user_and_route(
    store: tuple[IdempotencyStore, _FakeKVRedis],
) -> None:
    """One account's key never replays another account's (or route's) response.

    A key stored for ``(user_a, resumes)`` is invisible to ``user_b`` and to
    the same user's ``matches`` route, so the idempotency namespace cannot
    leak a stored response across the user or route boundary.
    """
    idem, _ = store
    user_a = uuid7()
    user_b = uuid7()
    record = _record()

    await idem.put(user_id=user_a, route="resumes", key="shared", record=record)

    assert await idem.get(user_id=user_b, route="resumes", key="shared") is None
    assert await idem.get(user_id=user_a, route="matches", key="shared") is None
    assert await idem.get(user_id=user_a, route="resumes", key="shared") == record


async def test_idempotency_first_writer_wins_on_replay(
    store: tuple[IdempotencyStore, _FakeKVRedis],
) -> None:
    """A second ``put`` under the same key never clobbers the first outcome.

    Models a concurrent replay racing the original write: ``SET ... NX`` keeps
    the first stored response authoritative, so every later replay returns the
    original record (Requirements 2.8, 8.9).
    """
    idem, _ = store
    user_id = uuid7()
    first = _record(resource_id="first", body={"id": "first"})
    second = _record(resource_id="second", body={"id": "second"})

    await idem.put(user_id=user_id, route="resumes", key="dup", record=first)
    await idem.put(user_id=user_id, route="resumes", key="dup", record=second)

    replayed = await idem.get(user_id=user_id, route="resumes", key="dup")
    assert replayed == first


async def test_idempotency_corrupt_payload_is_treated_as_miss(
    store: tuple[IdempotencyStore, _FakeKVRedis],
) -> None:
    """A corrupt / unexpected stored payload is a miss, never a request failure.

    The store fails soft: a non-JSON or schema-incomplete payload returns
    ``None`` so the caller re-runs the mutation rather than surfacing a 5xx.
    """
    idem, fake = store
    user_id = uuid7()

    # Non-JSON payload.
    fake.store[_build_idempotency_key(user_id=user_id, route="resumes", key="bad")] = "not-json"
    assert await idem.get(user_id=user_id, route="resumes", key="bad") is None

    # Well-formed JSON but missing the required ``resource_id`` field.
    partial_key = _build_idempotency_key(user_id=user_id, route="resumes", key="partial")
    fake.store[partial_key] = json.dumps({"status_code": 201, "body": {}})
    assert await idem.get(user_id=user_id, route="resumes", key="partial") is None


__all__: list[str] = []
