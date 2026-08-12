"""Feature: phase-3-llm-layer — Property 19.

Property 19: SSE streams terminate with exactly one correct terminal event.

    *For any* simulated stream outcome — a valid payload under any chunk
    split, a provider failure before or after the first delta, a
    validation failure at assembly, a timeout expiry, or a cache hit —
    the emitted SSE event sequence contains exactly one terminal event,
    of kind ``complete`` when and only when validation succeeded (or the
    result was cached) and ``degraded``/``error`` otherwise; no event
    follows the terminal event; the ``complete`` payload is identical to
    the persisted LLM_Result; and no ``delta`` event contains the
    planted system-prompt sentinel or API key sentinel.

**Validates: Requirements 8.5, 11.2, 11.3, 11.5, 11.6, 15.5**

The test drives the **real** SSE layer (``llm_stream_response`` /
``_event_stream``) over the **real** :class:`LLMOrchestrator` composed
with the real ``RESUME_COACH_SPEC`` — so the real prepare/execute
two-phase split, terminal validation, persistence, and cache write are
the unit under test — against the scripted in-memory fakes established
by ``tests/unit/test_llm_orchestrator.py`` and reused across
``tests/property/`` (``_FakeSession``, ``_RecordingRedis``,
``_FakeQuota``, ``_FakeBreaker``, scripted fake ``LLMClient``s). No
Postgres or Redis is required.

Each generated scenario is one of the design's simulated stream
outcomes, classified **by construction** (never by re-running the gate):

* ``valid`` — a CoachingReport assembled inside every schema bound,
  replayed under an arbitrary generated chunking (Req 8.5);
* ``invalid`` — one targeted mutation known to violate a bound
  (truncation/malformed JSON, count floor, priority ordering, extra
  key), also arbitrarily chunked — validation failure at assembly;
* ``provider_failure`` — the client raises ``LLMError`` (``timeout``
  expiry, Req 11.5, or a provider error/abort) after replaying zero or
  more prefix deltas — the before-/after-first-delta failure cases;
* ``unexpected_error`` — a non-LLM infrastructure exception whose
  message carries both planted sentinels, ending in the ``error``
  terminal (Req 11.3) that must not leak them;
* ``cache_hit`` — a priming call populates the real LLM_Cache; the
  second ``prepare`` resolves to an immediate outcome that streams one
  ``complete`` directly with no deltas (Req 15.5);
* ``pre_call_fallback`` — the key-absent pre-call path resolving to an
  immediate ``degraded`` terminal with no deltas.

The API-key sentinel is planted in the settings' ``llm_api_key`` and in
every scripted exception message; the system-prompt sentinel is the
plan's actual rendered system message. Neither may appear anywhere in
the raw SSE byte stream (Req 11.6).
"""

from __future__ import annotations

import asyncio
import itertools
import json
import string
from collections.abc import AsyncIterator, Callable, Coroutine
from dataclasses import dataclass
from decimal import Decimal
from types import TracebackType
from typing import Any, cast
from uuid import UUID

from fastapi.responses import StreamingResponse
from hypothesis import given, settings
from hypothesis import strategies as st
from uuid_utils.compat import uuid7

from matchlayer_api.api.matches.llm.sse import (
    EVENT_COMPLETE,
    EVENT_DEGRADED,
    EVENT_DELTA,
    EVENT_ERROR,
    llm_stream_response,
)
from matchlayer_api.config import Settings
from matchlayer_api.db.models import LLMResult, MatchResult
from matchlayer_api.ml.llm.client import (
    LLMCompletion,
    LLMError,
    LLMRequest,
    LLMStreamChunk,
    LLMUsage,
)
from matchlayer_api.services.llm.cache import LLMCache
from matchlayer_api.services.llm.coach import RESUME_COACH_SPEC, ResumeCoachInput
from matchlayer_api.services.llm.orchestrator import (
    LLMOrchestrator,
    LLMOutcome,
    ProviderCallPlan,
)
from matchlayer_api.services.llm.quota import QuotaDecision
from matchlayer_api.services.llm.spend import BreakerState

# ---------------------------------------------------------------------------
# Planted sentinels (Req 11.6). The key sentinel is deliberately shaped
# unlike any real credential — it exists to be *found* if the stream ever
# leaked it, not to look like a key.
# ---------------------------------------------------------------------------

# The leak-detection sentinel below is synthetic, not a credential.
_KEY_SENTINEL = "SENTINEL-API-KEY-MATERIAL-4242"  # gitleaks:allow — synthetic sentinel

_TEST_SECRET = "test-jwt-secret-32-byte-floor-pad"  # gitleaks:allow — synthetic test value

_SETTINGS = Settings(
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
    llm_api_key=_KEY_SENTINEL,
)

# ---------------------------------------------------------------------------
# Fakes — the exact surfaces the orchestrator and SSE generator touch,
# mirroring tests/unit/test_llm_orchestrator.py / test_validation_gate.py.
# ---------------------------------------------------------------------------


class _FakeQuota:
    """Always-allowing DailyQuota: both gates pass, remaining fixed."""

    async def gate(self, user_id: str) -> QuotaDecision:
        return QuotaDecision(allowed=True, remaining=5)

    async def reserve(self, user_id: str) -> QuotaDecision:
        return QuotaDecision(allowed=True, remaining=4)


class _RecordingRedis:
    """In-memory redis fake covering the get/set surface LLMCache uses."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value


class _FakeBreaker:
    """Closed SpendCircuitBreaker."""

    async def evaluate(self, session: Any) -> BreakerState:
        return BreakerState(
            is_open=False,
            tracked_spend=Decimal("0"),
            limit=Decimal("10"),
            cause=None,
        )

    def record_persist_failure(self) -> BreakerState:
        return BreakerState(is_open=True, tracked_spend=None, limit=Decimal("10"), cause=None)


class _FakeSavepoint:
    """Async context manager standing in for ``AsyncSessionTransaction``."""

    async def __aenter__(self) -> _FakeSavepoint:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        return False


class _EmptyScalars:
    """A ``ScalarResult`` stand-in whose query always finds nothing."""

    def first(self) -> None:
        return None


class _EmptyExecuteResult:
    """An ``execute()`` result stand-in: every SELECT comes back empty."""

    def scalars(self) -> _EmptyScalars:
        return _EmptyScalars()


class _FakeSession:
    """In-memory fake ``AsyncSession`` recording added rows and commits.

    ``execute`` returns an always-empty result so the Resume_Coach's
    persisted-result reuse lookup always misses — the cache-hit scenario
    therefore exercises the LLM_Cache path (Req 15.5), and reuse behavior
    stays Property 12's concern. ``commit`` is counted because the SSE
    generator owns the commit (design transaction model).
    """

    def __init__(self) -> None:
        self.added: list[Any] = []
        self.commits = 0

    def add(self, row: Any) -> None:
        self.added.append(row)

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        self.commits += 1

    async def execute(self, stmt: Any) -> _EmptyExecuteResult:
        return _EmptyExecuteResult()

    def begin_nested(self) -> _FakeSavepoint:
        return _FakeSavepoint()

    def rows(self, model: type) -> list[Any]:
        return [row for row in self.added if isinstance(row, model)]


class _ScriptedLLMClient:
    """Scripted ``LLMClient``: replays chunks, then optionally raises.

    Mirrors the adapter's contract: the accumulated completion is
    recorded in ``finally`` so ``result()`` is available even after a
    mid-stream failure (the orchestrator's ``_completion_of`` relies on
    this for the failure invocation-log row).
    """

    def __init__(self, chunks: list[str], error: Exception | None = None) -> None:
        self.chunks = chunks
        self.error = error
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
            if self.error is not None:
                raise self.error
        finally:
            self._completion = LLMCompletion(
                text="".join(parts),
                usage=LLMUsage(
                    input_tokens=10,
                    output_tokens=5,
                    cost_usd=Decimal("0.000123"),
                    cost_basis="provider_reported",
                ),
                latency_ms=42,
            )

    async def result(self) -> LLMCompletion:
        assert self._completion is not None
        return self._completion


def _run_sync(coro_factory: Callable[[], Coroutine[Any, Any, None]]) -> None:
    """Drive an async body from a sync Hypothesis example via ``Runner``."""
    with asyncio.Runner() as runner:
        runner.run(coro_factory())


# ---------------------------------------------------------------------------
# Fixed pipeline inputs: Property 19 quantifies over the *stream outcome*,
# not the match context, so the PII-bearing texts are held constant —
# lowercase prose so redaction never varies the run.
# ---------------------------------------------------------------------------

_USER_ID = UUID("01890000-0000-7000-8000-000000000019")
_RESUME_TEXT = "worked on backend services and infrastructure for five years"
_JD_TEXT = "we need a senior backend engineer comfortable with containers"


def _make_match() -> MatchResult:
    return MatchResult(
        id=uuid7(),
        user_id=_USER_ID,
        job_description_text=_JD_TEXT,
        matched_keywords=["python", "postgresql"],
        missing_keywords=["docker"],
        suggestions=["Add docker to your skills section."],
    )


# ---------------------------------------------------------------------------
# Scenario shape and generators. Every scenario is classified by
# construction — the test never re-runs the validation gate to decide
# which terminal is correct.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Scenario:
    """One simulated stream outcome, pre-classified by construction.

    ``chunks`` are the provider fragments the scripted client replays
    (empty for the no-provider-call kinds); ``payload`` is the expected
    validated CoachingReport (as a JSON-shaped dict) for the kinds whose
    terminal is ``complete``; ``category`` is the ``LLMError`` category
    for ``provider_failure``.
    """

    kind: str
    mutation: str
    chunks: tuple[str, ...]
    payload: dict[str, Any] | None
    category: str | None


# Constrained-field text: letters only — non-empty after Pydantic's
# strip_whitespace normalization and byte-stable through it.
_LABEL = st.text(alphabet=string.ascii_letters, min_size=1, max_size=20)

# Arbitrary prefix deltas replayed before a scripted failure.
_FRAGMENT = st.text(alphabet=string.ascii_letters + string.digits + ' {}:,"', max_size=20)

_INVALID_MUTATIONS = (
    "truncated",
    "too_few_improvements",
    "unordered_priorities",
    "extra_key",
)

_FAILURE_CATEGORIES = ("timeout", "provider_error", "abort")


def _chunked(draw: st.DrawFn, text: str) -> tuple[str, ...]:
    """Split *text* at arbitrary drawn cut points (Req 8.5)."""
    cuts = draw(st.lists(st.integers(min_value=0, max_value=len(text)), max_size=6))
    bounds = sorted({0, len(text), *cuts})
    chunks = tuple(text[start:stop] for start, stop in itertools.pairwise(bounds))
    return chunks or ("",)


def _coach_payload(draw: st.DrawFn, mutation: str) -> dict[str, Any]:
    """A CoachingReport payload: valid, or carrying one targeted violation."""
    if mutation == "too_few_improvements":
        count = draw(st.integers(min_value=0, max_value=2))
    else:
        count = draw(st.integers(min_value=3, max_value=6))
    priorities = sorted(
        draw(
            st.lists(
                st.integers(min_value=1, max_value=99), min_size=count, max_size=count, unique=True
            )
        )
    )
    payload: dict[str, Any] = {
        "summary": draw(_LABEL),
        "strengths": draw(st.lists(_LABEL, max_size=3)),
        "gaps": draw(st.lists(_LABEL, max_size=3)),
        "improvements": [{"priority": priority, "action": draw(_LABEL)} for priority in priorities],
    }
    if mutation == "unordered_priorities" and count >= 2:
        payload["improvements"][1]["priority"] = payload["improvements"][0]["priority"]
    elif mutation == "extra_key":
        payload["unexpected"] = "surprise"  # extra="forbid"
    return payload


@st.composite
def _scenarios(draw: st.DrawFn) -> _Scenario:
    kind = draw(
        st.sampled_from(
            (
                "valid",
                "invalid",
                "provider_failure",
                "unexpected_error",
                "cache_hit",
                "pre_call_fallback",
            )
        )
    )
    if kind in ("valid", "cache_hit"):
        payload = _coach_payload(draw, "valid")
        text = json.dumps(payload)
        return _Scenario(
            kind=kind,
            mutation="valid",
            chunks=_chunked(draw, text),
            payload=payload,
            category=None,
        )
    if kind == "invalid":
        mutation = draw(st.sampled_from(_INVALID_MUTATIONS))
        if mutation == "unordered_priorities":
            # Needs two entries to break the strictly-increasing order.
            payload = _coach_payload(draw, mutation)
            while len(payload["improvements"]) < 2:  # pragma: no cover — count >= 3
                payload = _coach_payload(draw, mutation)
            text = json.dumps(payload)
        else:
            payload = _coach_payload(draw, mutation)
            text = json.dumps(payload)
            if mutation == "truncated":
                # A strict prefix of compact json.dumps output is always
                # unparseable (truncation and malformed JSON in one).
                text = text[: draw(st.integers(min_value=0, max_value=len(text) - 1))]
        return _Scenario(
            kind=kind,
            mutation=mutation,
            chunks=_chunked(draw, text),
            payload=None,
            category=None,
        )
    if kind == "provider_failure":
        # Zero prefix chunks = failure before the first delta (Req 11.3);
        # one or more = failure after deltas already flowed.
        prefix = tuple(draw(st.lists(_FRAGMENT, max_size=3)))
        category = draw(st.sampled_from(_FAILURE_CATEGORIES))
        return _Scenario(
            kind=kind, mutation=category, chunks=prefix, payload=None, category=category
        )
    if kind == "unexpected_error":
        prefix = tuple(draw(st.lists(_FRAGMENT, max_size=3)))
        return _Scenario(
            kind=kind, mutation="unexpected_error", chunks=prefix, payload=None, category=None
        )
    return _Scenario(
        kind="pre_call_fallback",
        mutation="pre_call_fallback",
        chunks=(),
        payload=None,
        category=None,
    )


# ---------------------------------------------------------------------------
# Stream consumption.
# ---------------------------------------------------------------------------


async def _collect_events(
    response: StreamingResponse,
) -> tuple[str, list[tuple[str, dict[str, Any]]]]:
    """Consume the stream; return the raw text and (event, payload) pairs."""
    raw = "".join([cast("str", chunk) async for chunk in response.body_iterator])
    events: list[tuple[str, dict[str, Any]]] = []
    for block in raw.split("\n\n"):
        if not block.strip():
            continue
        lines = block.split("\n")
        assert lines[0].startswith("event: ")
        event_type = lines[0][len("event: ") :]
        data = "\n".join(line[len("data: ") :] for line in lines[1:] if line.startswith("data: "))
        events.append((event_type, json.loads(data)))
    return raw, events


# ---------------------------------------------------------------------------
# The property.
# ---------------------------------------------------------------------------

_TERMINAL_KINDS = frozenset({EVENT_COMPLETE, EVENT_DEGRADED, EVENT_ERROR})


async def _run_and_assert(scenario: _Scenario) -> None:
    """Stream *scenario* through the real SSE layer; assert Property 19."""
    match = _make_match()
    session = _FakeSession()
    redis = _RecordingRedis()
    feature_input = ResumeCoachInput(resume_text=_RESUME_TEXT)

    if scenario.kind == "provider_failure":
        assert scenario.category is not None
        error: Exception | None = LLMError(
            scenario.category, detail=f"scripted failure carrying {_KEY_SENTINEL}"
        )
    elif scenario.kind == "unexpected_error":
        error = RuntimeError(f"infrastructure failure carrying {_KEY_SENTINEL}")
    else:
        error = None
    client = _ScriptedLLMClient(chunks=list(scenario.chunks), error=error)

    orchestrator = LLMOrchestrator(
        session=session,  # type: ignore[arg-type]
        quota=_FakeQuota(),  # type: ignore[arg-type]
        cache=LLMCache(redis, ttl_seconds=60),  # type: ignore[arg-type]
        breaker=_FakeBreaker(),  # type: ignore[arg-type]
        client_factory=lambda: client,
        settings=_SETTINGS,
        key_present=lambda: scenario.kind != "pre_call_fallback",
    )

    primed_row: LLMResult | None = None
    if scenario.kind == "cache_hit":
        # Prime the real LLM_Cache: one full non-streaming run persists the
        # validated result and writes the cache entry (Req 15.4).
        primed = await orchestrator.run(
            RESUME_COACH_SPEC, user_id=_USER_ID, match=match, feature_input=feature_input
        )
        assert primed.envelope.is_fallback is False
        (primed_row,) = session.rows(LLMResult)
        assert redis.store, "priming call must have written the cache entry"

    prepared = await orchestrator.prepare(
        RESUME_COACH_SPEC, user_id=_USER_ID, match=match, feature_input=feature_input
    )

    system_sentinel: str | None = None
    if scenario.kind in ("cache_hit", "pre_call_fallback"):
        # Resolved without a provider call (Req 15.5 / pre-call fallback).
        assert isinstance(prepared, LLMOutcome), scenario.kind
    else:
        assert isinstance(prepared, ProviderCallPlan), scenario.kind
        # The planted system-prompt sentinel is the plan's actual rendered
        # system message — the exact text a leak would emit (Req 11.6).
        system_sentinel = prepared.request.messages[0].content
        assert system_sentinel

    commits_before = session.commits
    response = llm_stream_response(orchestrator, prepared, session=session)  # type: ignore[arg-type]
    raw, events = await _collect_events(response)

    # --- Exactly one terminal event, nothing after it (Req 11.2, 11.3) ---
    assert events, scenario.kind
    event_types = [event_type for event_type, _ in events]
    terminal_positions = [
        index for index, event_type in enumerate(event_types) if event_type in _TERMINAL_KINDS
    ]
    assert terminal_positions == [len(events) - 1], (scenario.kind, event_types)
    assert all(event_type == EVENT_DELTA for event_type in event_types[:-1]), event_types
    terminal_type, terminal_payload = events[-1]

    # --- Deltas carry only the provider's display fragments (Req 11.6) ---
    # For cache_hit the chunks were consumed by the priming call, and the
    # pre-call fallback never calls the provider: the *streamed* request
    # relays no deltas at all in both cases (Req 15.5).
    streamed_chunks = "" if isinstance(prepared, LLMOutcome) else "".join(scenario.chunks)
    delta_texts = [payload["text"] for event_type, payload in events[:-1]]
    assert "".join(delta_texts) == streamed_chunks, scenario.kind
    assert _KEY_SENTINEL not in raw, scenario.kind
    if system_sentinel is not None:
        assert system_sentinel not in raw, scenario.kind
        assert all(system_sentinel not in text for text in delta_texts), scenario.kind

    # --- The terminal kind is decided by the outcome alone ---
    if scenario.kind == "valid":
        assert terminal_type == EVENT_COMPLETE, scenario.mutation
        assert terminal_payload["is_fallback"] is False
        # The ``complete`` payload is identical to the persisted LLM_Result.
        (row,) = session.rows(LLMResult)
        assert terminal_payload["id"] == str(row.id)
        assert terminal_payload["result"] == row.payload == scenario.payload
        assert terminal_payload["prompt_template_version"] == row.prompt_template_version
        # Staged rows were committed before the terminal event.
        assert session.commits == commits_before + 1
    elif scenario.kind == "cache_hit":
        # Cache hits emit ``complete`` directly — no deltas, no provider
        # call for the streamed request, no commit (Req 15.5).
        assert event_types == [EVENT_COMPLETE], event_types
        assert terminal_payload["is_fallback"] is False
        assert primed_row is not None
        assert terminal_payload["id"] == str(primed_row.id)
        assert terminal_payload["result"] == primed_row.payload == scenario.payload
        assert client.stream_calls == 1  # the priming call only
        assert session.commits == commits_before
    elif scenario.kind == "invalid":
        # Validation failure at assembly → ``degraded`` (Req 8.5, 11.3).
        assert terminal_type == EVENT_DEGRADED, scenario.mutation
        assert terminal_payload["is_fallback"] is True
        assert terminal_payload["fallback_reason"] == "schema_validation_failed"
        assert session.rows(LLMResult) == []
        # The failure invocation-log row was staged: still committed.
        assert session.commits == commits_before + 1
    elif scenario.kind == "provider_failure":
        # Provider failure / timeout expiry → ``degraded`` (Req 11.3, 11.5).
        assert terminal_type == EVENT_DEGRADED, scenario.mutation
        assert terminal_payload["is_fallback"] is True
        expected_reason = "timeout" if scenario.category == "timeout" else "provider_error"
        assert terminal_payload["fallback_reason"] == expected_reason
        assert session.rows(LLMResult) == []
        assert session.commits == commits_before + 1
    elif scenario.kind == "pre_call_fallback":
        assert event_types == [EVENT_DEGRADED], event_types
        assert terminal_payload["is_fallback"] is True
        assert terminal_payload["fallback_reason"] == "llm_unavailable"
        assert client.stream_calls == 0
        assert session.commits == commits_before
    else:  # unexpected_error
        # A non-LLM exception after the stream opened still terminates the
        # stream — with the ``error`` RFC 7807 event, never the exception
        # text (Req 11.3).
        assert terminal_type == EVENT_ERROR
        assert terminal_payload["type"] == "internal_server_error"
        assert terminal_payload["status"] == 500
        assert "infrastructure failure" not in raw
        assert session.commits == commits_before


# Feature: phase-3-llm-layer, Property 19: SSE streams terminate with exactly one correct terminal event  # noqa: E501
@settings(max_examples=100, deadline=None)
@given(scenario=_scenarios())
def test_sse_streams_terminate_with_exactly_one_correct_terminal_event(
    scenario: _Scenario,
) -> None:
    """Every simulated stream outcome ends in exactly one correct terminal.

    Zero or more ``delta`` events, then exactly one terminal —
    ``complete`` iff validation succeeded or the result was cached,
    ``degraded`` for every LLM failure (timeout included), ``error`` for
    an unexpected infrastructure exception — with nothing after it, the
    ``complete`` payload byte-identical to the persisted LLM_Result, and
    no delta carrying the planted system-prompt or API-key sentinel.
    """
    _run_sync(lambda: _run_and_assert(scenario))
