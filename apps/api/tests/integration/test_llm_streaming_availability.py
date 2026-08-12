"""Router, SSE, and availability integration tests (task 10.9).

Completes the integration matrix that ``test_llm_api.py`` (task 10.1's
contract suite) left to this task, against the docker-compose Postgres
and Redis (phase-3-llm-layer Requirements 1.8, 5.1, 5.7, 6.1, 7.1, 10.3,
11.1, 11.4, 11.7, 14.3, 16.7):

* **Happy path per feature** (5.1, 6.1, 7.1): key present + a scripted
  provider returning a schema-valid payload → 200 validated envelope,
  persisted LLM_Result, exactly one invocation-log row, and the
  ``X-Robots-Tag: noindex, nofollow`` header (16.7).
* **Coach version/model-change regeneration** (5.7): a persisted report
  under a superseded model misses the reuse lookup → fresh provider
  call; the old row is retained; a repeat request reuses the fresh row
  with no further call (5.4 contrast).
* **Non-streaming vs ``stream=true``** (11.1): the same POST returns a
  single JSON body without the flag and a ``text/event-stream`` of
  ``delta`` events plus exactly one ``complete`` terminal with it.
* **Gate rejections never open a stream** (11.4): the 429 quota and 503
  spend-limit rejections on ``stream=true`` requests are plain RFC 7807
  JSON, byte-shaped like their non-streaming twins.
* **Client disconnect aborts the adapter** (11.7): cancelling the SSE
  consumer mid-stream propagates into the provider client's stream.
* **Key-absent startup** (1.8): ``initialize_llm_availability`` with no
  key returns normally; the app serves requests with ``llm:
  unavailable`` on ``/healthz``.
* **The three 10.3 cases**: breaker-cause → 503 and key-absent →
  fallback live in ``test_llm_api.py``; the both-causes case (absent key
  wins → Fallback_Response, never 503) lands here.
* **Breaker-open blocks new calls while in-flight calls complete and
  log** (14.3): a paused in-flight provider call survives the breaker
  opening — it completes with a 200 validated result and its
  invocation-log row — while a concurrent new request gets the 503.
* **``/healthz`` in both states** (10.1, 10.2, 10.5, 10.6): ``llm:
  available`` iff key present and breaker closed; the value never
  changes the 200 status and the body carries no key/spend details;
  a below-limit breaker evaluation flips it back (10.4).

Harness: the shared integration fixtures (``client_with_session``,
``factory_user``, ``factory_match``, ``factory_llm_result``) plus a
scripted ``LLMClient`` installed by monkeypatching the router's
``build_llm_client`` composition seam — the provider boundary is the
only fake; quota, cache, breaker, orchestrator, SSE layer, and
persistence are all real.

LLM availability: the module autouse fixture pins the key-absent state
and a fresh closed breaker; tests that need the key flip the flag
explicitly via ``monkeypatch``.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from matchlayer_api.api.matches.llm.sse import (
    EVENT_COMPLETE,
    EVENT_DELTA,
    llm_stream_response,
)
from matchlayer_api.config import Settings, get_settings
from matchlayer_api.core.db import get_session
from matchlayer_api.core.security.jwt import issue_access_token
from matchlayer_api.db.models import LLMInvocationLog, LLMResult, User
from matchlayer_api.main import create_app
from matchlayer_api.ml.llm import availability as availability_module
from matchlayer_api.ml.llm.client import (
    LLMCompletion,
    LLMRequest,
    LLMStreamChunk,
    LLMUsage,
)
from matchlayer_api.services.llm.cache import LLMCache
from matchlayer_api.services.llm.coach import RESUME_COACH_SPEC, ResumeCoachInput
from matchlayer_api.services.llm.orchestrator import LLMOrchestrator, ProviderCallPlan
from matchlayer_api.services.llm.quota import DailyQuota
from matchlayer_api.services.llm.spend import (
    BreakerState,
    get_spend_circuit_breaker,
    reset_spend_circuit_breaker,
)

from .conftest import (
    LLM_TEST_COACH_PAYLOAD,
    LLM_TEST_RESUME_TEXT,
    LLMResultFactory,
    MatchFactory,
    UserFactory,
    postgres_available,
    redis_available,
    unique_email,
)

# Both Postgres and Redis must be reachable: quota/cache live in Redis,
# match/result/log rows in Postgres. Mirrors test_llm_api.py.
pytestmark = pytest.mark.skipif(
    not (postgres_available() and redis_available()),
    reason="Postgres and Redis required (docker-compose not running)",
)

_QUOTA_HEADER = "X-LLM-Quota-Remaining"
_ROBOTS_HEADER = "X-Robots-Tag"
_KEY_PRESENT_ATTR = "matchlayer_api.ml.llm.availability._key_present"


@pytest.fixture(autouse=True)
def _llm_state(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Pin key-absent and a fresh closed breaker for every test.

    Both are process-wide module state (the availability flag and the
    breaker singleton); other test modules may have flipped them. Tests
    that need the key present monkeypatch the flag themselves — the
    fixture's ``setattr`` guarantees teardown restores the original.
    """
    monkeypatch.setattr(_KEY_PRESENT_ATTR, False)
    reset_spend_circuit_breaker()
    yield
    reset_spend_circuit_breaker()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _make_user_and_token(factory_user: UserFactory, prefix: str) -> tuple[User, str]:
    user = await factory_user(email=unique_email(prefix))
    token = issue_access_token(sub=str(user.id))
    return user, token


# ---------------------------------------------------------------------------
# Scripted provider clients — the only fake in these tests. Installed by
# monkeypatching the router's ``build_llm_client`` composition seam.
# ---------------------------------------------------------------------------


def _usage() -> LLMUsage:
    return LLMUsage(
        input_tokens=120,
        output_tokens=80,
        cost_usd=Decimal("0.000150"),
        cost_basis="provider_reported",
    )


class _ScriptedLLMClient:
    """Replays fixed chunks, recording its completion like the adapter."""

    def __init__(self, chunks: list[str]) -> None:
        self.chunks = chunks
        self.stream_calls = 0
        self._completion: LLMCompletion | None = None

    async def validate_credentials(self) -> None:
        return None

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMStreamChunk]:
        self.stream_calls += 1
        parts: list[str] = []
        try:
            for chunk in self.chunks:
                parts.append(chunk)
                yield LLMStreamChunk(delta=chunk)
        finally:
            self._completion = LLMCompletion(text="".join(parts), usage=_usage(), latency_ms=42)

    async def result(self) -> LLMCompletion:
        assert self._completion is not None
        return self._completion


class _PausingLLMClient:
    """Signals when its stream starts, then waits to be released.

    Models an in-flight provider call for the Requirement 14.3 test: the
    call pauses mid-stream (``started`` set, awaiting ``release``) while
    the test opens the breaker and probes a second request.
    """

    def __init__(self, text: str) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self._text = text
        self._completion: LLMCompletion | None = None

    async def validate_credentials(self) -> None:
        return None

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMStreamChunk]:
        try:
            self.started.set()
            await self.release.wait()
            yield LLMStreamChunk(delta=self._text)
        finally:
            self._completion = LLMCompletion(text=self._text, usage=_usage(), latency_ms=80)

    async def result(self) -> LLMCompletion:
        assert self._completion is not None
        return self._completion


class _HangingLLMClient:
    """Yields one delta, then hangs until cancelled (Requirement 11.7)."""

    def __init__(self) -> None:
        self.cancelled = False

    async def validate_credentials(self) -> None:
        return None

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMStreamChunk]:
        yield LLMStreamChunk(delta="partial")
        try:
            await asyncio.Event().wait()  # never set: simulates a slow provider
        except asyncio.CancelledError:
            self.cancelled = True
            raise

    async def result(self) -> LLMCompletion:
        return LLMCompletion(
            text="partial",
            usage=LLMUsage(
                input_tokens=None, output_tokens=None, cost_usd=None, cost_basis="unavailable"
            ),
            latency_ms=0,
        )


def _install_provider(monkeypatch: pytest.MonkeyPatch, client: object) -> None:
    """Point the router's ``build_llm_client`` seam at a scripted client."""
    monkeypatch.setattr(
        "matchlayer_api.api.matches.llm.router.build_llm_client",
        lambda settings=None: client,
    )


def _chunks_of(text: str) -> list[str]:
    """Split *text* into three chunks so deltas genuinely stream."""
    third = max(1, len(text) // 3)
    return [text[:third], text[third : 2 * third], text[2 * third :]]


def _parse_sse(raw: str) -> list[tuple[str, dict[str, Any]]]:
    """Parse an SSE body into ordered (event_type, payload) pairs."""
    events: list[tuple[str, dict[str, Any]]] = []
    for block in raw.split("\n\n"):
        if not block.strip():
            continue
        lines = block.split("\n")
        assert lines[0].startswith("event: ")
        event_type = lines[0][len("event: ") :]
        data = "\n".join(line[len("data: ") :] for line in lines[1:] if line.startswith("data: "))
        events.append((event_type, json.loads(data)))
    return events


# ---------------------------------------------------------------------------
# Schema-valid provider payloads per feature.
# ---------------------------------------------------------------------------

_BULLET = "Built REST APIs in Python."
_BULLET_REQUEST_BODY: dict[str, Any] = {"bullets": [_BULLET]}
_BULLET_PAYLOAD: dict[str, Any] = {
    "entries": [
        {
            "original": _BULLET,
            "alternatives": [
                "Designed and shipped Python REST APIs that cut partner integration time."
            ],
            "rationale": "Mirrors the job description's emphasis on API development.",
        }
    ]
}

_QUESTION_CATEGORIES = ("technical", "behavioral", "experience-gap", "technical", "behavioral")
_QUESTIONS_PAYLOAD: dict[str, Any] = {
    "questions": [
        {
            "question": f"Question {index} about your Python and FastAPI work?",
            "category": category,
            "reason": "The job description emphasizes this area of the stack.",
        }
        for index, category in enumerate(_QUESTION_CATEGORIES, start=1)
    ]
}

_FEATURE_CASES = (
    pytest.param("coaching-reports", "resume_coach", None, LLM_TEST_COACH_PAYLOAD, id="coach"),
    pytest.param(
        "bullet-rewrites", "bullet_rewrite", _BULLET_REQUEST_BODY, _BULLET_PAYLOAD, id="bullets"
    ),
    pytest.param(
        "interview-question-sets",
        "interview_questions",
        None,
        _QUESTIONS_PAYLOAD,
        id="questions",
    ),
)


# ---------------------------------------------------------------------------
# Happy path per feature (Requirements 5.1, 6.1, 7.1, 11.1, 12.1, 16.7).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(("resource", "feature", "body", "payload"), _FEATURE_CASES)
async def test_post_happy_path_returns_validated_persisted_result(
    client_with_session: AsyncClient,
    db_session: AsyncSession,
    factory_user: UserFactory,
    factory_match: MatchFactory,
    monkeypatch: pytest.MonkeyPatch,
    resource: str,
    feature: str,
    body: dict[str, Any] | None,
    payload: dict[str, Any],
) -> None:
    """Key present + valid provider output → 200 validated envelope.

    The non-streaming mode of Requirement 11.1: a single JSON body
    carrying the schema-validated LLM_Result, persisted (16.3) with one
    invocation-log row for the one provider call (12.1), stamped with
    ``X-Robots-Tag`` (16.7) and the quota header (13.5).
    """
    monkeypatch.setattr(_KEY_PRESENT_ATTR, True)
    provider = _ScriptedLLMClient(chunks=_chunks_of(json.dumps(payload)))
    _install_provider(monkeypatch, provider)

    user, token = await _make_user_and_token(factory_user, f"llmhappy{feature}")
    match = await factory_match(user_id=user.id)
    url = f"/api/v1/matches/{match.id}/{resource}"

    res = await client_with_session.post(url, headers=_auth(token), json=body)

    assert res.status_code == 200
    assert res.headers["content-type"].startswith("application/json")  # Req 11.1
    assert res.headers[_ROBOTS_HEADER] == "noindex, nofollow"  # Req 16.7
    assert res.headers.get(_QUOTA_HEADER) is not None  # Req 13.5
    envelope = res.json()
    assert envelope["is_fallback"] is False
    assert envelope["fallback_reason"] is None
    assert envelope["id"] is not None
    assert envelope["prompt_template_version"] == 1
    assert envelope["created_at"] is not None
    assert envelope["result"] == payload
    assert provider.stream_calls == 1  # exactly one attempt (Req 1.12)

    # Persisted round trip (Req 16.3): the GET returns the same result.
    fetched = await client_with_session.get(f"{url}/{envelope['id']}", headers=_auth(token))
    assert fetched.status_code == 200
    assert fetched.json()["result"] == payload

    # Exactly one invocation-log row for the one provider call (Req 12.1).
    logs = (await db_session.execute(select(LLMInvocationLog))).scalars().all()
    assert len(logs) == 1
    assert logs[0].feature == feature
    assert logs[0].output == payload
    assert logs[0].failure_category is None


# ---------------------------------------------------------------------------
# Coach version/model-change regeneration (Requirements 5.7, 5.4).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_coach_model_change_regenerates_and_retains_old_row(
    client_with_session: AsyncClient,
    db_session: AsyncSession,
    factory_user: UserFactory,
    factory_match: MatchFactory,
    factory_llm_result: LLMResultFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stored report under a superseded model triggers a fresh call.

    Requirement 5.7: the reuse lookup misses on a model change, so the
    request is a fresh provider call; the previously persisted row is
    retained, never deleted. A repeat request then reuses the fresh row
    without another call (Req 5.4).
    """
    monkeypatch.setattr(_KEY_PRESENT_ATTR, True)
    provider = _ScriptedLLMClient(chunks=_chunks_of(json.dumps(LLM_TEST_COACH_PAYLOAD)))
    _install_provider(monkeypatch, provider)

    user, token = await _make_user_and_token(factory_user, "llmregen")
    match = await factory_match(user_id=user.id)
    superseded = await factory_llm_result(
        user_id=user.id,
        match_result_id=match.id,
        llm_model=f"{get_settings().llm_model}-superseded",
        created_at=datetime.now(UTC) - timedelta(minutes=10),
    )
    url = f"/api/v1/matches/{match.id}/coaching-reports"

    first = await client_with_session.post(url, headers=_auth(token))

    assert first.status_code == 200
    fresh = first.json()
    assert fresh["is_fallback"] is False
    assert fresh["id"] != str(superseded.id)  # a fresh call, not the old row
    assert provider.stream_calls == 1

    # The superseded row is retained alongside the fresh one (Req 5.7).
    rows = (await db_session.execute(select(LLMResult))).scalars().all()
    assert {str(row.id) for row in rows} == {str(superseded.id), fresh["id"]}

    # A repeat request under the now-current version + model reuses the
    # fresh row: same id, no further provider call (Req 5.4).
    second = await client_with_session.post(url, headers=_auth(token))
    assert second.status_code == 200
    assert second.json()["id"] == fresh["id"]
    assert provider.stream_calls == 1


# ---------------------------------------------------------------------------
# stream=true SSE delivery (Requirements 11.1, 11.2, 16.7).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_true_delivers_deltas_then_single_complete(
    client_with_session: AsyncClient,
    db_session: AsyncSession,
    factory_user: UserFactory,
    factory_match: MatchFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``stream=true`` → ``text/event-stream`` of deltas + one terminal.

    The streaming mode of Requirement 11.1: incremental ``delta`` events
    carrying the provider fragments, then exactly one ``complete``
    terminal whose envelope is identical in content to the persisted row
    (Req 11.2).
    """
    monkeypatch.setattr(_KEY_PRESENT_ATTR, True)
    payload_text = json.dumps(LLM_TEST_COACH_PAYLOAD)
    provider = _ScriptedLLMClient(chunks=_chunks_of(payload_text))
    _install_provider(monkeypatch, provider)

    user, token = await _make_user_and_token(factory_user, "llmsse")
    match = await factory_match(user_id=user.id)

    res = await client_with_session.post(
        f"/api/v1/matches/{match.id}/coaching-reports",
        headers=_auth(token),
        params={"stream": "true"},
    )

    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")
    assert res.headers[_ROBOTS_HEADER] == "noindex, nofollow"  # Req 16.7
    assert res.headers.get(_QUOTA_HEADER) is not None

    events = _parse_sse(res.text)
    event_types = [event_type for event_type, _ in events]
    assert event_types == [EVENT_DELTA] * (len(events) - 1) + [EVENT_COMPLETE]
    assert len(events) > 1  # deltas genuinely streamed before the terminal
    assert "".join(data["text"] for _, data in events[:-1]) == payload_text

    terminal = events[-1][1]
    assert terminal["is_fallback"] is False
    assert terminal["result"] == LLM_TEST_COACH_PAYLOAD
    # Identical in content to the persisted LLM_Result (Req 11.2).
    (row,) = (await db_session.execute(select(LLMResult))).scalars().all()
    assert terminal["id"] == str(row.id)
    assert terminal["result"] == row.payload


# ---------------------------------------------------------------------------
# Gate rejections never open a stream (Requirement 11.4).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_true_quota_rejection_is_plain_json_429(
    client_with_session: AsyncClient,
    factory_user: UserFactory,
    factory_match: MatchFactory,
    redis_client: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An exhausted quota on a ``stream=true`` request → plain 429 JSON."""
    monkeypatch.setattr(_KEY_PRESENT_ATTR, True)
    user, token = await _make_user_and_token(factory_user, "llmssequota")
    match = await factory_match(user_id=user.id)
    settings = get_settings()

    today = datetime.now(UTC).strftime("%Y%m%d")
    key = f"llm:quota:{user.id}:{today}"
    await redis_client.set(key, settings.llm_daily_quota, ex=300)
    try:
        res = await client_with_session.post(
            f"/api/v1/matches/{match.id}/coaching-reports",
            headers=_auth(token),
            params={"stream": "true"},
        )
    finally:
        await redis_client.delete(key)

    assert res.status_code == 429
    assert res.headers["content-type"].startswith("application/json")  # no stream opened
    assert "event:" not in res.text
    body = res.json()
    assert body["type"] == "llm_quota_exceeded"
    assert res.headers.get(_QUOTA_HEADER) == "0"


class _OpenBreaker:
    """Duck-typed SpendCircuitBreaker whose evaluation is always open."""

    def __init__(self) -> None:
        self.state = BreakerState(is_open=True, tracked_spend=None, limit=None, cause=None)

    async def evaluate(self, session: AsyncSession) -> BreakerState:
        return self.state

    def record_persist_failure(self) -> BreakerState:  # pragma: no cover - unused
        return self.state


@pytest.mark.asyncio
async def test_stream_true_breaker_rejection_is_plain_json_503(
    db_session: AsyncSession,
    factory_user: UserFactory,
    factory_match: MatchFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An open breaker on a ``stream=true`` request → plain 503 JSON."""
    monkeypatch.setattr(_KEY_PRESENT_ATTR, True)
    user, token = await _make_user_and_token(factory_user, "llmssespend")
    match = await factory_match(user_id=user.id)

    app = create_app()

    async def _override_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_spend_circuit_breaker] = _OpenBreaker

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        res = await ac.post(
            f"/api/v1/matches/{match.id}/coaching-reports",
            headers=_auth(token),
            params={"stream": "true"},
        )

    assert res.status_code == 503
    assert res.headers["content-type"].startswith("application/json")  # no stream opened
    assert "event:" not in res.text
    assert res.json()["type"] == "llm_spend_limit_reached"


# ---------------------------------------------------------------------------
# Client disconnect aborts the adapter (Requirement 11.7).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_client_disconnect_aborts_provider_stream(
    db_session: AsyncSession,
    factory_user: UserFactory,
    factory_match: MatchFactory,
    redis_client: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancelling the SSE consumer cancels the provider client's stream.

    Drives the real orchestrator (real Postgres rows, real Redis quota
    and cache) through the real SSE generator against a hanging provider
    client, then models Starlette's disconnect handling by cancelling
    the pending read — the cancellation must propagate into the
    adapter's stream so no further tokens are consumed (Req 11.7).
    (The HTTP transport buffers streamed bodies, so the disconnect is
    driven at the generator boundary Starlette itself cancels.)
    """
    monkeypatch.setattr(_KEY_PRESENT_ATTR, True)
    user, _ = await _make_user_and_token(factory_user, "llmdisconnect")
    match = await factory_match(user_id=user.id)
    settings = get_settings()

    provider = _HangingLLMClient()
    orchestrator = LLMOrchestrator(
        session=db_session,
        quota=DailyQuota(redis_client, limit=settings.llm_daily_quota),
        cache=LLMCache(redis_client, ttl_seconds=60),
        breaker=get_spend_circuit_breaker(),
        client_factory=lambda: provider,
    )
    prepared = await orchestrator.prepare(
        RESUME_COACH_SPEC,
        user_id=user.id,
        match=match,
        feature_input=ResumeCoachInput(resume_text=LLM_TEST_RESUME_TEXT),
    )
    assert isinstance(prepared, ProviderCallPlan)  # every gate passed

    response = llm_stream_response(orchestrator, prepared, session=db_session)
    stream = aiter(cast("AsyncIterator[str]", response.body_iterator))
    first = await anext(stream)
    assert "partial" in first  # the delta reached the client

    # The next read suspends on the internal queue; cancelling it models
    # Starlette's disconnect handling (CancelledError in the generator).
    async def _read_next() -> str:
        return await anext(stream)

    next_read = asyncio.create_task(_read_next())
    await asyncio.sleep(0)  # let the read reach its suspension point
    next_read.cancel()
    with pytest.raises(asyncio.CancelledError):
        await next_read

    assert provider.cancelled is True  # the adapter's stream was aborted
    # The aborted call persisted no LLM_Result.
    assert (await db_session.execute(select(LLMResult))).scalars().all() == []


# ---------------------------------------------------------------------------
# Breaker opens mid-flight (Requirement 14.3).
# ---------------------------------------------------------------------------


class _ControllableBreaker:
    """Closed until the test opens it; never touches storage."""

    def __init__(self) -> None:
        self._open = False

    def open(self) -> None:
        self._open = True

    @property
    def state(self) -> BreakerState:
        return BreakerState(is_open=self._open, tracked_spend=None, limit=None, cause=None)

    async def evaluate(self, session: AsyncSession) -> BreakerState:
        return self.state

    def record_persist_failure(self) -> BreakerState:  # pragma: no cover - unused
        return self.state


@pytest.mark.asyncio
async def test_breaker_open_blocks_new_calls_while_in_flight_completes_and_logs(
    db_session: AsyncSession,
    factory_user: UserFactory,
    factory_match: MatchFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Opening the breaker mid-flight blocks new calls only (Req 14.3).

    Request A's provider call pauses mid-stream; the breaker then opens.
    Request B — arriving while the breaker is open — is rejected with
    the 503 and never reaches the provider. Released, request A runs to
    completion: a 200 validated result whose cost is recorded in the
    invocation log, so overshoot is bounded by the in-flight calls.
    """
    monkeypatch.setattr(_KEY_PRESENT_ATTR, True)
    provider = _PausingLLMClient(json.dumps(LLM_TEST_COACH_PAYLOAD))
    _install_provider(monkeypatch, provider)
    breaker = _ControllableBreaker()

    user, token = await _make_user_and_token(factory_user, "llminflight")
    match_a = await factory_match(user_id=user.id)
    match_b = await factory_match(user_id=user.id)

    app = create_app()

    async def _override_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_spend_circuit_breaker] = lambda: breaker

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        in_flight = asyncio.create_task(
            ac.post(f"/api/v1/matches/{match_a.id}/coaching-reports", headers=_auth(token))
        )
        # Wait until A's provider call is genuinely in flight, then trip
        # the breaker.
        await asyncio.wait_for(provider.started.wait(), timeout=5)
        breaker.open()

        # A new request while the breaker is open: 503, no provider call.
        blocked = await ac.post(
            f"/api/v1/matches/{match_b.id}/coaching-reports", headers=_auth(token)
        )
        assert blocked.status_code == 503
        assert blocked.json()["type"] == "llm_spend_limit_reached"

        # Release the in-flight call: it may run to completion (Req 14.3).
        provider.release.set()
        completed = await in_flight

    assert completed.status_code == 200
    body = completed.json()
    assert body["is_fallback"] is False
    assert body["result"] == LLM_TEST_COACH_PAYLOAD

    # Exactly one invocation-log row — the in-flight call's, with its
    # cost recorded; the blocked request never initiated a call.
    logs = (await db_session.execute(select(LLMInvocationLog))).scalars().all()
    assert len(logs) == 1
    assert logs[0].failure_category is None
    assert logs[0].cost_usd is not None


# ---------------------------------------------------------------------------
# Key-absent startup (Requirement 1.8) and both-causes precedence (10.3).
# ---------------------------------------------------------------------------

_TEST_SECRET = "test-jwt-secret-32-byte-floor-pad"  # gitleaks:allow — synthetic test value


def _keyless_settings() -> Settings:
    """A Settings instance with no LLM API key configured."""
    return Settings(
        environment="development",
        log_level="info",
        database_url="postgresql+asyncpg://u:p@localhost:5432/db",
        redis_url="redis://localhost:6379/0",
        s3_endpoint_url=None,
        s3_region="us-east-1",
        s3_access_key_id="test",
        s3_secret_access_key="test",
        s3_bucket="test-bucket",
        cors_allowed_origins=[],
        jwt_secret=_TEST_SECRET,
        llm_api_key=None,
    )


@pytest.mark.asyncio
async def test_key_absent_startup_starts_normally_and_reports_unavailable(
    client_with_session: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No key at startup → normal start, ``llm: unavailable`` (Req 1.8).

    ``initialize_llm_availability`` returns without error and without a
    validation call; the app serves requests with the LLM subsystem in
    LLM_Unavailable and non-LLM functionality unchanged (10.2).
    """
    # Pre-set so the startup call's write is observed and restored.
    monkeypatch.setattr(_KEY_PRESENT_ATTR, True)

    await availability_module.initialize_llm_availability(_keyless_settings())

    assert availability_module.llm_key_present() is False
    # The app is serving normally: /healthz is 200 with llm unavailable.
    res = await client_with_session.get("/healthz")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"
    assert res.json()["llm"] == "unavailable"


@pytest.mark.asyncio
async def test_key_absent_and_breaker_open_serves_fallback_not_503(
    client_with_session: AsyncClient,
    factory_user: UserFactory,
    factory_match: MatchFactory,
) -> None:
    """Both LLM_Unavailable causes at once → the fallback wins (Req 10.3).

    With the key absent AND the breaker open, the absent-key behavior
    applies — a 200 Fallback_Response, never the 503 — since no provider
    call is possible regardless of spend state.
    """
    # Key absent via the autouse fixture; force the singleton breaker open.
    get_spend_circuit_breaker().record_persist_failure()
    assert get_spend_circuit_breaker().state.is_open is True

    user, token = await _make_user_and_token(factory_user, "llmbothcauses")
    match = await factory_match(user_id=user.id)

    res = await client_with_session.post(
        f"/api/v1/matches/{match.id}/coaching-reports", headers=_auth(token)
    )

    assert res.status_code == 200
    body = res.json()
    assert body["is_fallback"] is True
    assert body["fallback_reason"] == "llm_unavailable"


# ---------------------------------------------------------------------------
# /healthz in both states (Requirements 10.1, 10.2, 10.4, 10.5, 10.6).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_healthz_llm_available_when_key_present_and_breaker_closed(
    client_with_session: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Key present + breaker closed → ``llm: available`` on a 200 (10.6)."""
    monkeypatch.setattr(_KEY_PRESENT_ATTR, True)

    res = await client_with_session.get("/healthz")

    assert res.status_code == 200
    body = res.json()
    assert body["llm"] == "available"
    # The body carries exactly the documented fields — no key material,
    # spend figures, or provider account details (Req 10.5).
    assert set(body) == {"status", "semantic_scoring", "llm"}


@pytest.mark.asyncio
async def test_healthz_llm_unavailable_when_key_absent_still_200(
    client_with_session: AsyncClient,
) -> None:
    """Key absent → ``llm: unavailable`` without changing the 200 (10.1)."""
    res = await client_with_session.get("/healthz")

    assert res.status_code == 200
    assert res.json() == {
        "status": "ok",
        "semantic_scoring": res.json()["semantic_scoring"],
        "llm": "unavailable",
    }


@pytest.mark.asyncio
async def test_healthz_llm_unavailable_when_breaker_open_and_recovers(
    client_with_session: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Breaker open → ``llm: unavailable``; a below-limit evaluation
    flips it back without a code change (10.2, 10.4)."""
    monkeypatch.setattr(_KEY_PRESENT_ATTR, True)
    breaker = get_spend_circuit_breaker()
    breaker.record_persist_failure()

    res = await client_with_session.get("/healthz")
    assert res.status_code == 200
    assert res.json()["llm"] == "unavailable"

    # Recovery: the next successful below-limit evaluation closes the
    # breaker (no rows this month → spend 0 < limit), and the very next
    # probe reports available again (Req 10.4).
    state = await breaker.evaluate(db_session)
    assert state.is_open is False

    recovered = await client_with_session.get("/healthz")
    assert recovered.status_code == 200
    assert recovered.json()["llm"] == "available"
