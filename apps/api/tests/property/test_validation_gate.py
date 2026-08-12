"""Feature: phase-3-llm-layer — Property 8.

# Feature: phase-3-llm-layer, Property 8: Validation gate — accept if and only if schema holds

Property 8: Validation gate — accept if and only if schema holds.

    *For any* complete raw LLM response (valid payloads, malformed JSON,
    truncations, out-of-bounds improvement/question counts, unordered
    priorities, over-length fields, bullet entries omitted, reordered, or
    with mutated originals) and any chunking of it over a stream, the
    outcome is exactly one of: (a) the response parses and satisfies the
    feature's Pydantic schema including field bounds and bullet alignment,
    and that validated object is what is returned, persisted, and cached;
    or (b) validation fails and the feature returns its Fallback_Response
    with a recorded failure category, persists nothing, caches nothing,
    never delivers truncated/padded/repaired content, and makes no second
    provider call.

**Validates: Requirements 4.5, 5.2, 6.2, 6.7, 7.2, 7.3, 7.7, 8.1, 8.2, 8.3, 8.5**

One test per feature service, each driving the **real** pipeline — the
real :class:`LLMOrchestrator` composed with the real feature spec
(``RESUME_COACH_SPEC`` / ``BULLET_REWRITE_SPEC`` /
``INTERVIEW_QUESTIONS_SPEC``, so the real result schemas, bound
validators, and the Bullet_Rewriter's alignment hook are the unit under
test) against the scripted fakes established by
``tests/unit/test_llm_orchestrator.py`` (``_FakeSession``,
``_RecordingRedis``, ``_FakeQuota``, ``_FakeBreaker``, scripted
``_FakeLLMClient``).

Each generated case is **valid or invalid by construction** — validity is
decided by the generator (a payload assembled inside the schema bounds,
or one targeted mutation known to violate exactly one bound), never by
re-running the validation gate inside the test, so the oracle is not
circular. The serialized response is then replayed under an arbitrary
generated chunking (Req 8.5: chunk boundaries never affect the terminal
outcome), and the two exhaustive outcomes are asserted:

* **valid** → ``is_fallback=False``, the returned/persisted/cached/logged
  payloads are all the byte-identical validated object (Req 8.2);
* **invalid** → the envelope is the feature's locally-built
  Fallback_Response (never a truncated/padded/repaired variant of the
  raw text, Req 8.3/7.7), ``fallback_reason`` records
  ``schema_validation_failed``, no LLM_Result row and no cache entry
  exist, the one invocation-log row carries the failure category, and
  exactly one provider call was made (never a second).
"""

from __future__ import annotations

import asyncio
import itertools
import json
import string
from collections.abc import AsyncIterator, Callable, Coroutine
from dataclasses import dataclass
from decimal import Decimal
from types import SimpleNamespace, TracebackType
from typing import Any
from unittest import mock
from uuid import UUID

from hypothesis import given, settings
from hypothesis import strategies as st
from uuid_utils.compat import uuid7

from matchlayer_api.config import Settings
from matchlayer_api.db.models import LLMInvocationLog, LLMResult, MatchResult
from matchlayer_api.ml.llm.client import (
    LLMCompletion,
    LLMRequest,
    LLMStreamChunk,
    LLMUsage,
)
from matchlayer_api.services.llm import questions as questions_module
from matchlayer_api.services.llm import schemas as schemas_module
from matchlayer_api.services.llm.bullets import BULLET_REWRITE_SPEC, BulletRewriteInput
from matchlayer_api.services.llm.cache import LLMCache
from matchlayer_api.services.llm.coach import RESUME_COACH_SPEC, ResumeCoachInput
from matchlayer_api.services.llm.orchestrator import LLMOrchestrator
from matchlayer_api.services.llm.questions import (
    INTERVIEW_QUESTIONS_SPEC,
    InterviewQuestionsInput,
)
from matchlayer_api.services.llm.quota import QuotaDecision
from matchlayer_api.services.llm.schemas import FailureReason, InterviewQuestionCategory
from matchlayer_api.services.llm.spend import BreakerState

# ---------------------------------------------------------------------------
# Settings (the unit-harness convention).
# ---------------------------------------------------------------------------

_TEST_SECRET = "test-jwt-secret-32-byte-floor-pad"  # gitleaks:allow — synthetic test value

_BASE_SETTINGS_KWARGS: dict[str, Any] = {
    "environment": "development",
    "log_level": "info",
    "database_url": "postgresql+asyncpg://u:p@localhost:5432/db",
    "redis_url": "redis://localhost:6379/0",
    "s3_endpoint_url": None,
    "s3_region": "us-east-1",
    "s3_access_key_id": "test",
    "s3_secret_access_key": "test",
    "s3_bucket": "test-bucket",
    "cors_allowed_origins": [],
    "jwt_secret": _TEST_SECRET,
}

_SETTINGS = Settings(**_BASE_SETTINGS_KWARGS)

# Pinned Interview_Question_Set ceiling: the schema validator and the
# questions feature spec both read ``get_settings().llm_max_questions``
# at validation/build time (module-level bindings), so the test pins
# both bindings to one small known ceiling — mirroring the
# ``tests/unit/test_llm_questions.py`` convention — and sizes its
# generators against the same constant.
_QUESTIONS_CEILING = 8
_PINNED_SETTINGS = SimpleNamespace(llm_max_questions=_QUESTIONS_CEILING)


def _pinned_get_settings() -> Any:
    return _PINNED_SETTINGS


# ---------------------------------------------------------------------------
# Fakes — the exact surfaces the orchestrator touches, mirroring
# tests/unit/test_llm_orchestrator.py.
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
    """In-memory fake ``AsyncSession`` recording added rows.

    ``execute`` returns an always-empty result so the Resume_Coach's
    persisted-result reuse lookup (pipeline step 2) always misses and
    every case reaches the validation gate — reuse behavior itself is
    Property 12's concern, not this property's.
    """

    def __init__(self) -> None:
        self.added: list[Any] = []

    def add(self, row: Any) -> None:
        self.added.append(row)

    async def flush(self) -> None:
        return None

    async def execute(self, stmt: Any) -> _EmptyExecuteResult:
        return _EmptyExecuteResult()

    def begin_nested(self) -> _FakeSavepoint:
        return _FakeSavepoint()

    def rows(self, model: type) -> list[Any]:
        return [row for row in self.added if isinstance(row, model)]


class _FakeLLMClient:
    """Scripted ``LLMClient`` replaying the generated chunks verbatim."""

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
# Fixed pipeline inputs. Property 8 quantifies over the *response*, not the
# match context (that is Property 7/10 territory), so the Match_Result's
# stored fields and the PII-bearing texts are held constant — lowercase
# prose so redaction never varies the run.
# ---------------------------------------------------------------------------

_USER_ID = UUID("01890000-0000-7000-8000-000000000042")
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
# Case shape and shared generator pieces.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _GateCase:
    """One generated raw LLM response, pre-classified by construction.

    ``chunks`` reassemble to the exact raw response text; ``payload`` is
    the expected validated object (as a JSON-shaped dict) when ``valid``
    and ``None`` otherwise; ``mutation`` labels the construction for
    failure-message readability.
    """

    mutation: str
    chunks: list[str]
    valid: bool
    payload: dict[str, Any] | None


# Constrained-field text: letters only — non-empty after Pydantic's
# strip_whitespace normalization, and byte-stable through it, so the
# valid-case round-trip equality assertion is exact.
_LABEL = st.text(alphabet=string.ascii_letters, min_size=1, max_size=20)

# Unconstrained list[str] content (coach strengths/gaps): anything goes.
_FREE_TEXT = st.text(alphabet=string.ascii_letters + string.digits + " ,.", max_size=30)

_CATEGORY_VALUES = [category.value for category in InterviewQuestionCategory]


def _chunked(draw: st.DrawFn, text: str) -> list[str]:
    """Split *text* at arbitrary drawn cut points (Req 8.5).

    Any multiset of cut positions — including none, and including cuts
    producing empty deltas — yields chunks that concatenate back to the
    exact response text, mirroring how the adapter accumulates a stream.
    """
    cuts = draw(st.lists(st.integers(min_value=0, max_value=len(text)), max_size=6))
    bounds = sorted({0, len(text), *cuts})
    chunks = [text[start:stop] for start, stop in itertools.pairwise(bounds)]
    return chunks or [""]


def _truncate(draw: st.DrawFn, text: str) -> str:
    """A strict prefix of *text* — always malformed JSON.

    ``json.dumps`` output carries no trailing whitespace and every strict
    prefix leaves the outermost object unbalanced, so any cut point in
    ``0..len-1`` produces an unparseable document (truncation and
    malformed JSON in one mutation).
    """
    return text[: draw(st.integers(min_value=0, max_value=len(text) - 1))]


# ---------------------------------------------------------------------------
# Per-feature case strategies. Validity is decided here, by construction:
# either the payload is assembled strictly inside every schema bound, or
# exactly one targeted mutation known to violate a specific bound is
# applied. The test never re-runs the gate to classify a case.
# ---------------------------------------------------------------------------

_COACH_MUTATIONS = (
    "valid",
    "truncated",
    "too_few_improvements",
    "too_many_improvements",
    "unordered_priorities",
    "empty_summary",
    "extra_key",
)


@st.composite
def _coach_cases(draw: st.DrawFn) -> _GateCase:
    """A CoachingReport response: valid, or one Requirement 5.2 violation."""
    mutation = draw(st.sampled_from(_COACH_MUTATIONS))
    if mutation == "too_few_improvements":
        count = draw(st.integers(min_value=0, max_value=2))
    elif mutation == "too_many_improvements":
        count = draw(st.integers(min_value=11, max_value=13))
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
        "strengths": draw(st.lists(_FREE_TEXT, max_size=3)),
        "gaps": draw(st.lists(_FREE_TEXT, max_size=3)),
        "improvements": [{"priority": priority, "action": draw(_LABEL)} for priority in priorities],
    }
    if mutation == "unordered_priorities":
        # Two equal adjacent ranks break the strictly-increasing order.
        payload["improvements"][1]["priority"] = payload["improvements"][0]["priority"]
    elif mutation == "empty_summary":
        payload["summary"] = draw(st.sampled_from(["", "   "]))
    elif mutation == "extra_key":
        payload["unexpected"] = "surprise"  # extra="forbid" (Req 8.3)
    text = json.dumps(payload)
    if mutation == "truncated":
        text = _truncate(draw, text)
    valid = mutation == "valid"
    return _GateCase(
        mutation=mutation,
        chunks=_chunked(draw, text),
        valid=valid,
        payload=payload if valid else None,
    )


_QUESTION_MUTATIONS = (
    "valid",
    "truncated",
    "too_few_questions",
    "too_many_questions",
    "over_length_question",
    "over_length_reason",
    "bad_category",
)


@st.composite
def _question_cases(draw: st.DrawFn) -> _GateCase:
    """An InterviewQuestionSet response: valid, or one Req 7.2/7.3 violation."""
    mutation = draw(st.sampled_from(_QUESTION_MUTATIONS))
    if mutation == "too_few_questions":
        count = draw(st.integers(min_value=0, max_value=4))
    elif mutation == "too_many_questions":
        count = _QUESTIONS_CEILING + 1
    else:
        count = draw(st.integers(min_value=5, max_value=_QUESTIONS_CEILING))
    payload: dict[str, Any] = {
        "questions": [
            {
                "question": draw(_LABEL),
                "category": draw(st.sampled_from(_CATEGORY_VALUES)),
                "reason": draw(_LABEL),
            }
            for _ in range(count)
        ]
    }
    if mutation == "over_length_question":
        payload["questions"][0]["question"] = "q" * 301  # > 300 chars (Req 7.2)
    elif mutation == "over_length_reason":
        payload["questions"][0]["reason"] = "r" * 501  # > 500 chars (Req 7.2)
    elif mutation == "bad_category":
        payload["questions"][0]["category"] = "quizzical"  # outside the enum
    text = json.dumps(payload)
    if mutation == "truncated":
        text = _truncate(draw, text)
    valid = mutation == "valid"
    return _GateCase(
        mutation=mutation,
        chunks=_chunked(draw, text),
        valid=valid,
        payload=payload if valid else None,
    )


_BULLET_MUTATIONS = (
    "valid",
    "truncated",
    "omit_entry",
    "reorder_entries",
    "mutate_original",
    "no_alternatives",
    "too_many_alternatives",
    "empty_rationale",
)


@st.composite
def _bullet_cases(draw: st.DrawFn) -> tuple[_GateCase, tuple[str, ...]]:
    """A BulletRewrite response paired with the submission it must align to.

    Covers the alignment violations the schema cannot see (Req 6.7):
    entries omitted, reordered, or with mutated originals — plus the
    schema-visible bounds (1..3 alternatives, non-empty rationale).
    """
    mutation = draw(st.sampled_from(_BULLET_MUTATIONS))
    min_bullets = 2 if mutation == "reorder_entries" else 1
    bullets = tuple(draw(st.lists(_LABEL, min_size=min_bullets, max_size=4, unique=True)))
    entries: list[dict[str, Any]] = [
        {
            "original": bullet,
            "alternatives": draw(st.lists(_LABEL, min_size=1, max_size=3)),
            "rationale": draw(_LABEL),
        }
        for bullet in bullets
    ]
    if mutation == "omit_entry":
        entries = entries[:-1]  # count mismatch (or empty list) — Req 6.7
    elif mutation == "reorder_entries":
        entries[0], entries[1] = entries[1], entries[0]  # bullets are unique
    elif mutation == "mutate_original":
        index = draw(st.integers(min_value=0, max_value=len(bullets) - 1))
        entries[index]["original"] = entries[index]["original"] + "X"
    elif mutation == "no_alternatives":
        entries[0]["alternatives"] = []  # below the 1-alternative floor
    elif mutation == "too_many_alternatives":
        entries[0]["alternatives"] = [draw(_LABEL) for _ in range(4)]  # > 3
    elif mutation == "empty_rationale":
        entries[0]["rationale"] = draw(st.sampled_from(["", "  "]))
    payload: dict[str, Any] = {"entries": entries}
    text = json.dumps(payload)
    if mutation == "truncated":
        text = _truncate(draw, text)
    valid = mutation == "valid"
    case = _GateCase(
        mutation=mutation,
        chunks=_chunked(draw, text),
        valid=valid,
        payload=payload if valid else None,
    )
    return case, bullets


# ---------------------------------------------------------------------------
# Shared pipeline runner + the exhaustive two-outcome assertion.
# ---------------------------------------------------------------------------


async def _run_and_assert(spec: Any, feature_input: Any, case: _GateCase) -> None:
    """Run the real pipeline on *case* and assert exactly one outcome holds."""
    match = _make_match()
    session = _FakeSession()
    redis = _RecordingRedis()
    client = _FakeLLMClient(chunks=case.chunks)
    orchestrator = LLMOrchestrator(
        session=session,  # type: ignore[arg-type]
        quota=_FakeQuota(),  # type: ignore[arg-type]
        cache=LLMCache(redis, ttl_seconds=60),  # type: ignore[arg-type]
        breaker=_FakeBreaker(),  # type: ignore[arg-type]
        client_factory=lambda: client,
        settings=_SETTINGS,
        key_present=lambda: True,
    )

    outcome = await orchestrator.run(
        spec, user_id=_USER_ID, match=match, feature_input=feature_input
    )
    envelope = outcome.envelope

    # Exactly one provider call, never a second (Req 9.6 within this
    # property's scope: validation failure never triggers a retry).
    assert client.stream_calls == 1, case.mutation

    # Exactly one invocation-log row either way (success or failure).
    logs = session.rows(LLMInvocationLog)
    assert len(logs) == 1, case.mutation

    if case.valid:
        # (a) The response satisfied the schema: the validated object is
        # what is returned, persisted, cached, and logged (Req 8.2).
        assert envelope.is_fallback is False, case.mutation
        assert envelope.fallback_reason is None, case.mutation
        assert envelope.result.model_dump(mode="json") == case.payload, case.mutation

        results = session.rows(LLMResult)
        assert len(results) == 1, case.mutation
        assert results[0].payload == case.payload, case.mutation
        assert len(redis.store) == 1, case.mutation
        assert logs[0].failure_category is None, case.mutation
        assert logs[0].output == case.payload, case.mutation
    else:
        # (b) Validation failed: the locally-built Fallback_Response with
        # a recorded failure category — never truncated/padded/repaired
        # content (Req 8.3, 7.7) — persists nothing, caches nothing.
        assert envelope.is_fallback is True, case.mutation
        assert envelope.fallback_reason is FailureReason.SCHEMA_VALIDATION_FAILED, case.mutation
        assert envelope.id is None and envelope.created_at is None, case.mutation
        expected_fallback = spec.build_fallback(
            match, feature_input, FailureReason.SCHEMA_VALIDATION_FAILED
        )
        assert envelope.result == expected_fallback, case.mutation

        assert session.rows(LLMResult) == [], case.mutation
        assert redis.store == {}, case.mutation
        assert logs[0].failure_category == "schema_validation_failed", case.mutation
        assert logs[0].output is None, case.mutation


# ---------------------------------------------------------------------------
# The property, once per feature service.
# ---------------------------------------------------------------------------


# Feature: phase-3-llm-layer, Property 8: Validation gate — accept if and only if schema holds
@settings(max_examples=100, deadline=None)
@given(case=_coach_cases())
def test_coach_validation_gate_accepts_iff_schema_holds(case: _GateCase) -> None:
    """CoachingReport responses are accepted iff every Req 5.2 bound holds.

    Improvement count 3..10, strictly increasing priority ranks, and a
    non-empty summary — under arbitrary stream chunking — decide between
    the validated-object outcome and the fallback outcome exactly.
    """
    feature_input = ResumeCoachInput(resume_text=_RESUME_TEXT)
    _run_sync(lambda: _run_and_assert(RESUME_COACH_SPEC, feature_input, case))


# Feature: phase-3-llm-layer, Property 8: Validation gate — accept if and only if schema holds
@settings(max_examples=100, deadline=None)
@given(case=_question_cases())
def test_questions_validation_gate_accepts_iff_schema_holds(case: _GateCase) -> None:
    """InterviewQuestionSet responses are accepted iff Req 7.2/7.3 hold.

    Question count 5..llm_max_questions, the closed category enum, and
    the 300/500-character field ceilings — an out-of-bounds count is a
    validation failure, never truncated or padded (Req 7.7).
    """
    feature_input = InterviewQuestionsInput(resume_text=_RESUME_TEXT)
    with (
        mock.patch.object(schemas_module, "get_settings", _pinned_get_settings),
        mock.patch.object(questions_module, "get_settings", _pinned_get_settings),
    ):
        _run_sync(lambda: _run_and_assert(INTERVIEW_QUESTIONS_SPEC, feature_input, case))


# Feature: phase-3-llm-layer, Property 8: Validation gate — accept if and only if schema holds
@settings(max_examples=100, deadline=None)
@given(case_and_bullets=_bullet_cases())
def test_bullets_validation_gate_accepts_iff_schema_and_alignment_hold(
    case_and_bullets: tuple[_GateCase, tuple[str, ...]],
) -> None:
    """BulletRewrite responses are accepted iff schema AND alignment hold.

    On top of the schema bounds (1..3 alternatives, non-empty rationale —
    Req 6.2), the ``validate_extra`` alignment hook rejects omitted,
    reordered, or mutated-original entries as schema-validation failures
    (Req 6.7).
    """
    case, bullets = case_and_bullets
    feature_input = BulletRewriteInput(bullets=bullets)
    _run_sync(lambda: _run_and_assert(BULLET_REWRITE_SPEC, feature_input, case))
