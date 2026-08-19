"""Feature: phase-4-agentic — Property 2.

Property 2: Provider-bound text is always redacted.

    *For any* resume text containing generated PII, every request captured
    at the mocked LLM provider boundary during an agent run contains the
    PII_Redactor's typed placeholders and none of the raw PII values.

**Validates: Requirements 9.2, 3.1**

How the redaction chain is enforced in Phase 4
----------------------------------------------

The Agent_Worker runs the Phase 3 PII_Redactor over the Resume's
``extracted_text`` **before** building ``AgentState`` — the state
deliberately has no field for raw text (Requirement 1.3), only
``redacted_resume_text``. The Resume_Analysis_Agent's
``build_prompt_input`` returns that field verbatim, and its feature spec
passes it through prompt assembly with ``redaction=None`` because the text
is redacted by construction (Requirement 3.1). Every provider interaction
flows through the injected orchestrator (the final ``LLMAgent.run``,
Requirement 9.2), so the text handed to ``prepare`` *is* the
provider-bound text at the agent boundary.

This property closes that chain without duplicating the Phase 3 redactor
coverage (``test_redaction_placeholder_correctness`` proves exact
placeholder indexing; ``test_redaction_determinism`` proves stability):

1. **Redactor link** — for any raw resume text with planted emails and
   phone numbers, ``redact(text, kind="resume")`` yields text containing
   typed ``[EMAIL_n]`` / ``[PHONE_n]`` placeholders and none of the raw
   planted values.
2. **Agent link** — driving the ResumeAnalysisAgent through its full
   ``__call__`` lifecycle with a capturing fake orchestrator, the text
   captured at the orchestrator boundary equals
   ``state.redacted_resume_text`` **verbatim** (never any other state
   field — a decoy marker planted in every other content-bearing field
   must not appear), exactly one prepare call occurs, and the captured
   text carries the placeholders and no raw PII value.
3. **Prompt-assembly link** — the feature spec's ``build_inputs`` wraps
   that same text verbatim in a single delimited ``resume`` section with
   ``redaction=None`` and no placeholder values, which is the exact
   handoff the Phase 3 prompt-assembly property tests cover from there to
   the wire.

Generator notes: filler is vowel-free lowercase words, so it can never
match the committed email regex (no ``@``), the phone regex (no digits),
or form a capitalized name run; emails are letter-only and phones
fixed-format, so detections never merge and every planted occurrence is
redactable. Consecutive PII tokens are always filler-separated.

Async note: each example drives its coroutine inside an
:class:`asyncio.Runner` (the suite's established pattern) so the event
loop closes deterministically under ``filterwarnings = ["error"]``.
"""

# Feature: phase-4-agentic, Property 2: Provider-bound text is always redacted

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import cast
from uuid import uuid4

from hypothesis import given, settings
from hypothesis import strategies as st
from opentelemetry.trace import NoOpTracer
from pydantic import BaseModel

from matchlayer_api.db.models import MatchResult
from matchlayer_api.ml.agents.base import AgentDeps
from matchlayer_api.ml.agents.resume_analysis_agent import ResumeAnalysisAgent
from matchlayer_api.ml.agents.state import (
    AgentCompletion,
    AgentState,
    AgentStatusFlag,
    CandidateProfile,
    FailureDetail,
    MatchSnapshot,
)
from matchlayer_api.services.llm.orchestrator import (
    LLMFeatureSpec,
    LLMOutcome,
    ProviderCallPlan,
)
from matchlayer_api.services.llm.redaction import redact
from matchlayer_api.services.llm.schemas import LLMResultEnvelope

# ---------------------------------------------------------------------------
# Fakes: pure lifecycle dependencies plus the capturing orchestrator.
# ---------------------------------------------------------------------------


class _FakeClock:
    def monotonic(self) -> float:
        return 0.0


async def _persist_noop(
    agent_name: str,
    state: AgentState,
    output: BaseModel,
    status: AgentCompletion,
    reason: FailureDetail | None,
    latency_ms: int,
) -> None:
    del agent_name, state, output, status, reason, latency_ms


def _deps() -> AgentDeps:
    return AgentDeps(
        node_timeout_s=5.0,
        persist_agent_run=_persist_noop,
        tracer=NoOpTracer(),
        clock=_FakeClock(),
    )


class _CapturingOrchestrator:
    """AgentLLMOrchestrator fake capturing the provider-bound text.

    ``prepare`` is the single gateway every LLM agent request flows
    through (Requirement 9.2), so the ``feature_input`` it receives is the
    text bound for the provider. It resolves without a provider call
    (a non-fallback outcome), so ``execute`` must never run.
    """

    def __init__(self) -> None:
        self.captured: list[str] = []

    @property
    def model(self) -> str:
        return "test-model"

    async def prepare[TResult: BaseModel](
        self,
        spec: LLMFeatureSpec[str, TResult],
        *,
        user_id: str,
        feature_input: str,
    ) -> LLMOutcome[TResult] | ProviderCallPlan[str, TResult]:
        del spec, user_id
        self.captured.append(feature_input)
        envelope = LLMResultEnvelope[CandidateProfile](
            is_fallback=False,
            result=CandidateProfile(skills=["python"]),
        )
        return cast("LLMOutcome[TResult]", LLMOutcome(envelope=envelope, quota_remaining=None))

    async def execute[TResult: BaseModel](
        self, plan: ProviderCallPlan[str, TResult]
    ) -> LLMOutcome[TResult]:
        raise AssertionError("execute must not be reached in this fake")


# ---------------------------------------------------------------------------
# Generators: raw resume text with planted, filler-separated PII values.
# ---------------------------------------------------------------------------

# Vowel-free lowercase filler: never matches the email regex (no "@"),
# never matches the phone regex (no digits), never forms a name run.
_CONSONANTS = "bcdfghjklmnpqrstvwxz"

_filler_word = st.text(alphabet=_CONSONANTS, min_size=2, max_size=8)

# Letter-only email values (no digits, so the phone regex never fires
# inside them); the committed email regex matches each exactly.
_email_value = st.builds(
    lambda local, domain: f"{local}@{domain}.com",
    st.text(alphabet=_CONSONANTS, min_size=1, max_size=8),
    st.text(alphabet=_CONSONANTS, min_size=1, max_size=8),
)

# Fixed-format US-style phone values the committed phone regex matches.
_phone_value = st.builds(
    lambda digits: f"{digits[0:3]}-{digits[3:6]}-{digits[6:10]}",
    st.text(alphabet="0123456789", min_size=10, max_size=10),
)


@st.composite
def _pii_resume_texts(draw: st.DrawFn) -> tuple[str, list[str], list[str]]:
    """Build ``(raw text, planted emails, planted phones)``.

    Every planted value occurs 1-2 times; occurrences are shuffled and
    always filler-separated so detections never merge or overlap.
    """
    emails = draw(st.lists(_email_value, min_size=1, max_size=3, unique=True))
    phones = draw(st.lists(_phone_value, min_size=1, max_size=3, unique=True))

    occurrences: list[str] = []
    for email in emails:
        occurrences.extend(email for _ in range(draw(st.integers(1, 2))))
    for phone in phones:
        occurrences.extend(phone for _ in range(draw(st.integers(1, 2))))
    occurrences = list(draw(st.permutations(occurrences)))

    parts: list[str] = [draw(_filler_word)]
    for token in occurrences:
        parts.append(token)
        parts.append(draw(_filler_word))
    return " ".join(parts), emails, phones


# ---------------------------------------------------------------------------
# Driving the agent lifecycle.
# ---------------------------------------------------------------------------


def _run_sync[T](coro_factory: Callable[[], Awaitable[T]]) -> T:
    """Drive an async body from a sync Hypothesis example via ``Runner``."""
    with asyncio.Runner() as runner:
        return runner.run(coro_factory())


async def _invoke_agent(
    state: AgentState,
) -> tuple[_CapturingOrchestrator, AgentStatusFlag]:
    orchestrator = _CapturingOrchestrator()
    agent = ResumeAnalysisAgent(_deps(), orchestrator)
    update = await agent(state)
    status_map = update["agent_status"]
    assert isinstance(status_map, dict)
    flag = status_map[agent.name]
    assert isinstance(flag, AgentStatusFlag)
    return orchestrator, flag


# ---------------------------------------------------------------------------
# The property.
# ---------------------------------------------------------------------------

# A marker planted in every other content-bearing state field: if any of
# those fields leaked into the provider-bound text, the marker would show.
_DECOY = "DECOY-FIELD-CONTENT"


# Feature: phase-4-agentic, Property 2: Provider-bound text is always redacted
@settings(max_examples=100, deadline=None)
@given(case=_pii_resume_texts())
def test_provider_bound_text_is_always_redacted(
    case: tuple[str, list[str], list[str]],
) -> None:
    """The text reaching the provider boundary has passed through the
    PII_Redactor: the redactor removes every planted email/phone in favor
    of typed placeholders (Requirement 3.1), and the agent hands the
    orchestrator exactly ``state.redacted_resume_text`` — verbatim, from
    no other state field — through the one sanctioned gateway
    (Requirement 9.2)."""
    raw_text, emails, phones = case

    # ---- redactor link (Requirement 3.1) ----------------------------------
    redacted = redact(raw_text, kind="resume").text
    for value in (*emails, *phones):
        assert value not in redacted, f"raw PII value {value!r} survived redaction"
    assert "[EMAIL_" in redacted
    assert "[PHONE_" in redacted

    # ---- agent link (Requirements 3.1, 9.2) -------------------------------
    state = AgentState(
        job_id=str(uuid4()),
        match_id=str(uuid4()),
        user_id=str(uuid4()),
        redacted_resume_text=redacted,
        job_description_skills=[_DECOY],
        match_snapshot=MatchSnapshot(
            score=50.0,
            scorer_version=_DECOY,
            matched_skills=[_DECOY],
            missing_skills=[_DECOY],
            suggestions=[_DECOY],
        ),
    )
    orchestrator, flag = _run_sync(lambda: _invoke_agent(state))

    # The normal path ran: the capture happened at the provider boundary,
    # not on a degraded no-call path.
    assert flag.status is AgentCompletion.COMPLETED
    # Exactly one orchestrator request per execution (Requirement 3.1).
    assert len(orchestrator.captured) == 1
    provider_bound = orchestrator.captured[0]

    # Verbatim ``redacted_resume_text`` — never any other state field.
    assert provider_bound == redacted
    assert _DECOY not in provider_bound
    # Redaction evidence at the boundary: placeholders in, raw values out.
    assert "[EMAIL_" in provider_bound
    assert "[PHONE_" in provider_bound
    for value in (*emails, *phones):
        assert value not in provider_bound

    # ---- prompt-assembly link (Requirement 3.1) ---------------------------
    # The feature spec wraps the provider-bound text verbatim in a single
    # delimited resume section with ``redaction=None`` (already redacted
    # by construction); Phase 3 prompt-assembly properties cover the rest
    # of the path to the wire.
    agent = ResumeAnalysisAgent(_deps(), orchestrator)
    spec = agent.feature_spec()
    inputs = spec.build_inputs(cast("MatchResult", object()), provider_bound)
    assert inputs.values == {}
    assert len(inputs.sections) == 1
    section = inputs.sections[0]
    assert section.kind == "resume"
    assert section.text == provider_bound
    assert section.redaction is None
