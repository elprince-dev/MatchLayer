"""Integration tests for the LLM sub-resource routers (task 10.1).

Validates the HTTP contract of ``api/matches/llm/router.py`` against the
docker-compose Postgres and Redis (phase-3-llm-layer Requirements 5.6,
6.3, 10.3, 13.5, 16.1, 16.2, 16.4, 16.5, 16.8, 16.9, 16.10):

* 401 before any existence check (16.8);
* the identical 404 RFC 7807 envelope for "not yours" and "not found"
  (5.6, 16.2, 16.9), including malformed ids;
* cursor pagination — newest-first order, opaque cursor round trip,
  limit bounds and malformed-cursor 422s (16.4, 16.10);
* Bullet_Rewriter request validation → 422 before any LLM work (6.3);
* the key-absent Fallback_Response with 200 (10.3, 9.1);
* the 429 Daily_Quota rejection with limit + UTC reset in ``detail`` and
  ``X-LLM-Quota-Remaining`` (13.2, 13.5, 16.5);
* the 503 spend-limit rejection with a spend-identifying ``type`` and no
  figures (10.3).

Reuses the integration harness: ``client_with_session`` (per-test session
override), ``factory_user``, ``unique_email``, and real access tokens via
``issue_access_token`` (the ``test_matches_api.py`` pattern). Match and
LLM_Result rows are inserted directly through ``db_session`` so the tests
focus on the router surface. The full streaming/availability matrix is
task 10.9; the pagination/ownership property tests are tasks 10.5-10.7.

LLM availability: the module autouse fixture pins the key-absent state so
no provider call can ever be attempted, independent of what other test
modules did to the process-wide availability flag.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from matchlayer_api.config import get_settings
from matchlayer_api.core.db import get_session
from matchlayer_api.core.security.jwt import issue_access_token
from matchlayer_api.db.models import User
from matchlayer_api.main import create_app
from matchlayer_api.services.llm.spend import (
    BreakerState,
    get_spend_circuit_breaker,
)

from .conftest import (
    LLM_TEST_COACH_PAYLOAD as _COACH_PAYLOAD,
)
from .conftest import (
    LLMResultFactory,
    MatchFactory,
    UserFactory,
    postgres_available,
    redis_available,
    unique_email,
)

# Both Postgres and Redis must be reachable: every LLM route reads the
# Redis-backed Daily_Quota for the X-LLM-Quota-Remaining header and the
# match/result rows live in Postgres. Mirrors test_matches_api.py.
pytestmark = pytest.mark.skipif(
    not (postgres_available() and redis_available()),
    reason="Postgres and Redis required (docker-compose not running)",
)

_QUOTA_HEADER = "X-LLM-Quota-Remaining"


@pytest.fixture(autouse=True)
def _llm_key_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the key-absent availability state for every test in this module.

    The flag is process-wide module state set by the lifespan; other test
    modules may have flipped it. Key absent guarantees the pipeline can
    never attempt a provider call (Requirement 1.8, 10.3).
    """
    monkeypatch.setattr("matchlayer_api.ml.llm.availability._key_present", False)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _make_user_and_token(factory_user: UserFactory, prefix: str) -> tuple[User, str]:
    user = await factory_user(email=unique_email(prefix))
    token = issue_access_token(sub=str(user.id))
    return user, token


# Row factories (``factory_match`` / ``factory_llm_result``) live in
# tests/integration/conftest.py, shared with the task 10.9 suite
# (test_llm_streaming_availability.py).


# ---------------------------------------------------------------------------
# 401 before any existence check (Requirement 16.8).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unauthenticated_requests_get_401(client_with_session: AsyncClient) -> None:
    """POST / GET-list / GET-one without auth → 401 ``unauthenticated``."""
    base = f"/api/v1/matches/{uuid4()}/coaching-reports"
    for res in (
        await client_with_session.post(base),
        await client_with_session.get(base),
        await client_with_session.get(f"{base}/{uuid4()}"),
    ):
        assert res.status_code == 401
        assert res.json()["type"] == "unauthenticated"


# ---------------------------------------------------------------------------
# Ownership indistinguishability (Requirements 5.6, 16.2).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_not_yours_and_missing_match_identical_404(
    client_with_session: AsyncClient,
    factory_user: UserFactory,
    factory_match: MatchFactory,
) -> None:
    """ "Not yours" and "does not exist" produce the same 404 envelope."""
    owner, _ = await _make_user_and_token(factory_user, "llmowner")
    _, other_token = await _make_user_and_token(factory_user, "llmother")
    match = await factory_match(user_id=owner.id)

    not_yours = await client_with_session.get(
        f"/api/v1/matches/{match.id}/coaching-reports", headers=_auth(other_token)
    )
    not_found = await client_with_session.get(
        f"/api/v1/matches/{uuid4()}/coaching-reports", headers=_auth(other_token)
    )
    malformed = await client_with_session.get(
        "/api/v1/matches/not-a-uuid/coaching-reports", headers=_auth(other_token)
    )

    assert not_yours.status_code == not_found.status_code == malformed.status_code == 404

    def _stable(body: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in body.items() if k != "request_id"}

    assert _stable(not_yours.json()) == _stable(not_found.json()) == _stable(malformed.json())
    assert not_yours.json()["type"] == "not_found"


@pytest.mark.asyncio
async def test_get_one_missing_or_malformed_result_id_404(
    client_with_session: AsyncClient,
    factory_user: UserFactory,
    factory_match: MatchFactory,
) -> None:
    """Unknown and malformed result ids under an owned match → 404 (Req 16.9)."""
    user, token = await _make_user_and_token(factory_user, "llmresult404")
    match = await factory_match(user_id=user.id)
    base = f"/api/v1/matches/{match.id}/coaching-reports"

    missing = await client_with_session.get(f"{base}/{uuid4()}", headers=_auth(token))
    malformed = await client_with_session.get(f"{base}/not-a-uuid", headers=_auth(token))

    assert missing.status_code == malformed.status_code == 404
    assert missing.json()["type"] == malformed.json()["type"] == "not_found"


# ---------------------------------------------------------------------------
# Cursor pagination (Requirements 16.4, 16.10) + quota header (13.5).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_paginates_newest_first_with_opaque_cursor(
    client_with_session: AsyncClient,
    factory_user: UserFactory,
    factory_match: MatchFactory,
    factory_llm_result: LLMResultFactory,
) -> None:
    """Three rows, limit=2 → newest-first page + cursor to the final row."""
    user, token = await _make_user_and_token(factory_user, "llmpage")
    match = await factory_match(user_id=user.id)
    base_time = datetime.now(UTC) - timedelta(minutes=10)
    rows = [
        await factory_llm_result(
            user_id=user.id,
            match_result_id=match.id,
            created_at=base_time + timedelta(minutes=i),
        )
        for i in range(3)
    ]

    first = await client_with_session.get(
        f"/api/v1/matches/{match.id}/coaching-reports",
        headers=_auth(token),
        params={"limit": 2},
    )
    assert first.status_code == 200
    assert first.headers.get(_QUOTA_HEADER) is not None
    body = first.json()
    assert [item["id"] for item in body["items"]] == [str(rows[2].id), str(rows[1].id)]
    assert body["next_cursor"] is not None

    second = await client_with_session.get(
        f"/api/v1/matches/{match.id}/coaching-reports",
        headers=_auth(token),
        params={"limit": 2, "cursor": body["next_cursor"]},
    )
    assert second.status_code == 200
    second_body = second.json()
    assert [item["id"] for item in second_body["items"]] == [str(rows[0].id)]
    assert second_body["next_cursor"] is None

    # Every listed item is a persisted (non-fallback) envelope (Req 17.9).
    for item in body["items"] + second_body["items"]:
        assert item["is_fallback"] is False
        assert item["prompt_template_version"] == 1
        assert item["created_at"] is not None


@pytest.mark.asyncio
async def test_list_rejects_bad_limit_and_malformed_cursor_422(
    client_with_session: AsyncClient,
    factory_user: UserFactory,
    factory_match: MatchFactory,
) -> None:
    """limit outside 1..100 and an undecodable cursor → 422 (Req 16.10)."""
    user, token = await _make_user_and_token(factory_user, "llm422")
    match = await factory_match(user_id=user.id)
    base = f"/api/v1/matches/{match.id}/coaching-reports"

    for params in ({"limit": 0}, {"limit": 101}, {"cursor": "%%%not-base64%%%"}):
        res = await client_with_session.get(base, headers=_auth(token), params=params)
        assert res.status_code == 422, params
        assert res.json()["type"] == "validation_error"


# ---------------------------------------------------------------------------
# Bullet request validation (Requirement 6.3).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bullet_rewrite_invalid_bodies_422(
    client_with_session: AsyncClient,
    factory_user: UserFactory,
    factory_match: MatchFactory,
) -> None:
    """Count/emptiness/length violations → 422 before any LLM work."""
    user, token = await _make_user_and_token(factory_user, "llmbullets")
    match = await factory_match(user_id=user.id)
    settings = get_settings()
    url = f"/api/v1/matches/{match.id}/bullet-rewrites"

    invalid_bodies = [
        {"bullets": []},  # below the 1-bullet floor
        {"bullets": ["   "]},  # whitespace-only
        {"bullets": ["ok"] * (settings.llm_max_bullets + 1)},  # over the ceiling
        {"bullets": ["x" * (settings.llm_max_bullet_chars + 1)]},  # over char cap
    ]
    for body in invalid_bodies:
        res = await client_with_session.post(url, headers=_auth(token), json=body)
        assert res.status_code == 422, body
        assert res.json()["type"] == "validation_error"


# ---------------------------------------------------------------------------
# Key-absent fallback (Requirements 9.1, 10.3) + quota header (13.5).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_coach_key_absent_returns_fallback_200(
    client_with_session: AsyncClient,
    factory_user: UserFactory,
    factory_match: MatchFactory,
) -> None:
    """Key absent → 200 Fallback_Response envelope, never an error."""
    user, token = await _make_user_and_token(factory_user, "llmfallback")
    match = await factory_match(user_id=user.id)

    res = await client_with_session.post(
        f"/api/v1/matches/{match.id}/coaching-reports", headers=_auth(token)
    )

    assert res.status_code == 200
    assert res.headers.get(_QUOTA_HEADER) is not None
    body = res.json()
    assert body["is_fallback"] is True
    assert body["fallback_reason"] == "llm_unavailable"
    # Fallbacks are never persisted (Req 9.5): no id / version / timestamp.
    assert body["id"] is None
    assert body["prompt_template_version"] is None
    assert body["created_at"] is None
    # The fallback conforms to the same CoachingReport result schema.
    assert len(body["result"]["improvements"]) >= 3


# ---------------------------------------------------------------------------
# 429 Daily_Quota rejection (Requirements 13.2, 13.5, 16.5).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_quota_exhausted_429_with_header_and_reset_detail(
    client_with_session: AsyncClient,
    factory_user: UserFactory,
    factory_match: MatchFactory,
    redis_client: Any,
) -> None:
    """Exhausted counter → 429 stating the limit and UTC reset, header 0."""
    user, token = await _make_user_and_token(factory_user, "llmquota")
    match = await factory_match(user_id=user.id)
    settings = get_settings()

    today = datetime.now(UTC).strftime("%Y%m%d")
    key = f"llm:quota:{user.id}:{today}"
    await redis_client.set(key, settings.llm_daily_quota, ex=300)
    try:
        res = await client_with_session.post(
            f"/api/v1/matches/{match.id}/coaching-reports", headers=_auth(token)
        )
    finally:
        await redis_client.delete(key)

    assert res.status_code == 429
    assert res.headers.get(_QUOTA_HEADER) == "0"
    body = res.json()
    assert body["type"] == "llm_quota_exceeded"
    assert body["status"] == 429
    # The detail states the configured daily limit and the UTC reset (13.2).
    assert str(settings.llm_daily_quota) in body["detail"]
    assert "reset" in body["detail"].lower()
    assert "00:00:00" in body["detail"]


# ---------------------------------------------------------------------------
# 503 spend-limit rejection (Requirement 10.3).
# ---------------------------------------------------------------------------


class _OpenBreaker:
    """Duck-typed SpendCircuitBreaker whose evaluation is always open."""

    def __init__(self) -> None:
        self.state = BreakerState(
            is_open=True,
            tracked_spend=None,
            limit=None,
            cause=None,
        )

    async def evaluate(self, session: AsyncSession) -> BreakerState:
        return self.state

    def record_persist_failure(self) -> None:  # pragma: no cover - unused
        pass


@pytest.mark.asyncio
async def test_post_breaker_open_returns_503_spend_type_no_figures(
    db_session: AsyncSession,
    factory_user: UserFactory,
    factory_match: MatchFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Breaker open with a key present → 503 whose type names the spend limit.

    The detail is user-safe fixed copy carrying no spend figures. Built on
    a dedicated app instance so the breaker dependency can be overridden.
    """
    # Key present so the absent-key fallback does not preempt the breaker
    # (Requirement 10.3's precedence rule).
    monkeypatch.setattr("matchlayer_api.ml.llm.availability._key_present", True)

    user, token = await _make_user_and_token(factory_user, "llmspend")
    match = await factory_match(user_id=user.id)

    app = create_app()

    async def _override_session() -> Any:
        yield db_session

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_spend_circuit_breaker] = _OpenBreaker

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        res = await ac.post(f"/api/v1/matches/{match.id}/coaching-reports", headers=_auth(token))

    assert res.status_code == 503
    body = res.json()
    assert body["type"] == "llm_spend_limit_reached"
    assert body["status"] == 503
    # No spend figures, key material, or provider details (Req 10.3).
    assert not any(ch.isdigit() for ch in body["detail"])


# ---------------------------------------------------------------------------
# GET-one round trip (Requirement 16.3).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_one_returns_persisted_result(
    client_with_session: AsyncClient,
    factory_user: UserFactory,
    factory_match: MatchFactory,
    factory_llm_result: LLMResultFactory,
) -> None:
    """GET of a persisted result → 200 with id, version, timestamp, payload."""
    user, token = await _make_user_and_token(factory_user, "llmgetone")
    match = await factory_match(user_id=user.id)
    row = await factory_llm_result(user_id=user.id, match_result_id=match.id)

    res = await client_with_session.get(
        f"/api/v1/matches/{match.id}/coaching-reports/{row.id}", headers=_auth(token)
    )

    assert res.status_code == 200
    assert res.headers.get(_QUOTA_HEADER) is not None
    body = res.json()
    assert body["id"] == str(row.id)
    assert body["is_fallback"] is False
    assert body["fallback_reason"] is None
    assert body["prompt_template_version"] == 1
    assert body["created_at"] is not None
    assert body["result"]["summary"] == _COACH_PAYLOAD["summary"]
