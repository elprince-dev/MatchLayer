"""Feature: phase-4-agentic — Property 1.

Property 1: No raw resume PII on any persistence or telemetry surface.

    *For any* resume text containing generated PII (emails, phone
    numbers), and any graph execution over it, the serialized
    ``AgentState`` at every node boundary, every persisted ``agent_runs``
    input/output state, and every emitted span's name, attributes, and
    events contain neither the raw ``extracted_text`` nor any unredacted
    generated PII value.

**Validates: Requirements 1.3, 2.3, 12.3, 13.3**

How the chain is closed
-----------------------

The Agent_Worker runs the Phase 3 PII_Redactor over the Resume's
``extracted_text`` **before** building ``AgentState`` — the state
deliberately has no field for raw text (Requirement 1.3), so every
serialized snapshot (checkpoints per Requirement 2.3, ``agent_runs`` rows
per Requirement 12.3, spans per Requirement 13.3) is PII-free by
construction. This property validates that structural guarantee end to
end: raw resume text with planted PII values goes through the real
:func:`redact` (exactly as the worker does), the **real five agents** run
through the real ``build_agents`` / ``compile_graph`` composition under a
locally constructed ``TracerProvider`` + ``InMemorySpanExporter``
(injected via ``AgentDeps`` — the global provider is never mutated, the
``test_tracing.py`` discipline) inside a mirror of the worker's
``agent.job`` span, and then every surface is scanned:

* the JSON-serialized ``AgentState`` handed to every ``persist_agent_run``
  callback (``model_dump(mode="json")`` — byte-for-byte what
  ``services/agent_jobs/runs.py`` stores as ``input_state_json``);
* every persisted output document (``output_state_json``) and structured
  failure reason;
* every captured span's name, attribute keys/values, and events —
  including the job span.

Run shapes vary at the collaborator seams (LLM agents: resolved-without-
call, genuine provider call, or injected failure; ATS: normal or scorer
failure) so degraded outputs and structured failure reasons land on the
scanned surfaces too.

The planted-PII generator follows ``test_provider_bound_redaction.py``:
vowel-free lowercase filler that can never match the email regex (no
``@``), the phone regex (no digits), or a capitalized name run; letter-only
emails and fixed-format phones so detections never merge and every planted
occurrence is redactable.

Async note: each example drives its coroutine inside an
:class:`asyncio.Runner` (the suite's established pattern) so the event
loop closes deterministically under ``filterwarnings = ["error"]``.
"""

# Feature: phase-4-agentic, Property 1: No raw resume PII on any persistence
# or telemetry surface

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any, cast
from uuid import UUID, uuid4

from hypothesis import given, settings
from hypothesis import strategies as st
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import BaseModel

from matchlayer_api.db.models import MatchResult
from matchlayer_api.ml.agents.ats_agent import ScoredMatch
from matchlayer_api.ml.agents.base import AgentDeps
from matchlayer_api.ml.agents.graph import build_agents, compile_graph
from matchlayer_api.ml.agents.state import (
    AgentCompletion,
    AgentState,
    FailureDetail,
    MatchSnapshot,
)
from matchlayer_api.ml.llm.client import LLMRequest
from matchlayer_api.ml.prompts.registry import LLMFeature
from matchlayer_api.services.llm.orchestrator import (
    LLMFeatureSpec,
    LLMOutcome,
    ProviderCallPlan,
)
from matchlayer_api.services.llm.redaction import redact
from matchlayer_api.services.llm.schemas import LLMResultEnvelope

# ---------------------------------------------------------------------------
# Fakes.
# ---------------------------------------------------------------------------


class _FakeClock:
    def monotonic(self) -> float:
        return 0.0


class _PersistedRun(BaseModel):
    """One captured agent_runs row, serialized exactly as runs.py stores it."""

    agent_name: str
    input_state_json: dict[str, Any]
    output_state_json: dict[str, Any]
    status: str
    failure_reason_json: dict[str, Any] | None


class _RunRecorder:
    """Captures every persist_agent_run callback as its JSON documents."""

    def __init__(self) -> None:
        self.rows: list[_PersistedRun] = []

    async def __call__(
        self,
        agent_name: str,
        state: AgentState,
        output: BaseModel,
        status: AgentCompletion,
        reason: FailureDetail | None,
        latency_ms: int,
    ) -> None:
        del latency_ms
        self.rows.append(
            _PersistedRun(
                agent_name=agent_name,
                input_state_json=state.model_dump(mode="json"),
                output_state_json=output.model_dump(mode="json"),
                status=status.value,
                failure_reason_json=None if reason is None else reason.model_dump(mode="json"),
            )
        )


class _ScriptedOrchestrator:
    """AgentLLMOrchestrator fake: no-call success, genuine call, or failure."""

    def __init__(self, modes: dict[LLMFeature, str]) -> None:
        self._modes = modes

    @property
    def model(self) -> str:
        return "test-model"

    async def prepare(
        self,
        spec: LLMFeatureSpec[str, Any],
        *,
        user_id: str,
        feature_input: str,
    ) -> LLMOutcome[Any] | ProviderCallPlan[str, Any]:
        mode = self._modes.get(spec.feature, "ok")
        if mode == "exception":
            raise RuntimeError("injected orchestrator failure")
        if mode == "call":
            # A provider call is initiated: the hash over the provider-bound
            # text lands on the span (hashes only — never content, Req 13.3).
            input_hash = hashlib.sha256(feature_input.encode("utf-8")).hexdigest()
            return ProviderCallPlan(
                spec=spec,
                envelope_cls=cast("type[LLMResultEnvelope[Any]]", LLMResultEnvelope),
                user_id=UUID(user_id),
                match=cast("MatchResult", object()),
                feature_input=feature_input,
                request=cast("LLMRequest", None),
                template_version=1,
                input_hash=input_hash,
                quota_remaining=1,
            )
        envelope: LLMResultEnvelope[Any] = LLMResultEnvelope(
            is_fallback=False,
            fallback_reason=None,
            result=spec.result_schema(),
        )
        return LLMOutcome(envelope=envelope, quota_remaining=10)

    async def execute(self, plan: ProviderCallPlan[str, Any]) -> LLMOutcome[Any]:
        envelope: LLMResultEnvelope[Any] = LLMResultEnvelope(
            is_fallback=False,
            fallback_reason=None,
            result=plan.spec.result_schema(),
        )
        return LLMOutcome(envelope=envelope, quota_remaining=9)


class _ScriptedScorer:
    """ScorerAdapter fake failing on command."""

    active_scorer_version = "2.0.0+active"
    semantic_active = True
    resume_len = 1_000
    jd_len = 500

    def __init__(self, mode: str = "ok") -> None:
        self._mode = mode

    async def score(self) -> ScoredMatch:
        if self._mode == "exception":
            raise RuntimeError("injected total scoring failure")
        return ScoredMatch(
            score=55.0,
            breakdown={"similarity": 0.55},
            scorer_version="2.0.0+active",
            semantic=True,
        )


# ---------------------------------------------------------------------------
# Generators: raw resume text with planted, filler-separated PII values
# (the test_provider_bound_redaction.py approach).
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
def _pii_resume_texts(draw: st.DrawFn) -> tuple[str, list[str]]:
    """Build ``(raw text, planted PII values)`` with filler separation."""
    emails = draw(st.lists(_email_value, min_size=1, max_size=3, unique=True))
    phones = draw(st.lists(_phone_value, min_size=1, max_size=3, unique=True))

    occurrences: list[str] = []
    for value in (*emails, *phones):
        occurrences.extend(value for _ in range(draw(st.integers(1, 2))))
    occurrences = list(draw(st.permutations(occurrences)))

    parts: list[str] = [draw(_filler_word)]
    for token in occurrences:
        parts.append(token)
        parts.append(draw(_filler_word))
    return " ".join(parts), [*emails, *phones]


_LLM_MODES = st.sampled_from(["ok", "call", "exception"])
_ATS_MODES = st.sampled_from(["ok", "exception"])


@st.composite
def _run_shapes(draw: st.DrawFn) -> dict[str, str]:
    return {
        "resume_analysis": draw(_LLM_MODES),
        "improvement": draw(_LLM_MODES),
        "ats": draw(_ATS_MODES),
    }


# ---------------------------------------------------------------------------
# Driving one traced graph execution.
# ---------------------------------------------------------------------------


async def _run_graph(
    state: AgentState,
    modes: dict[str, str],
    tracer_provider: TracerProvider,
) -> _RunRecorder:
    recorder = _RunRecorder()
    tracer = tracer_provider.get_tracer("matchlayer.agent_worker.test")
    deps = AgentDeps(
        node_timeout_s=30.0,
        persist_agent_run=recorder,
        tracer=tracer,
        clock=_FakeClock(),
    )
    orchestrator = _ScriptedOrchestrator(
        {
            LLMFeature.AGENT_RESUME_ANALYSIS: modes["resume_analysis"],
            LLMFeature.AGENT_IMPROVEMENT: modes["improvement"],
        }
    )
    scorer = _ScriptedScorer(modes["ats"])
    compiled = compile_graph(build_agents(deps, orchestrator, scorer))
    # Mirror the Agent_Worker's job-span framing (agent_worker.py).
    with tracer.start_as_current_span("agent.job") as span:
        span.set_attribute("matchlayer.job_id", state.job_id)
        result = await compiled.ainvoke(state)
    assert result["analysis_result"] is not None  # the run completed
    return recorder


def _run_sync(state: AgentState, modes: dict[str, str]) -> tuple[_RunRecorder, list[ReadableSpan]]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    try:
        with asyncio.Runner() as runner:
            recorder = runner.run(_run_graph(state, modes, provider))
        spans = list(exporter.get_finished_spans())
    finally:
        provider.shutdown()
    return recorder, spans


def _span_surface(span: ReadableSpan) -> str:
    """Serialize everything a span exposes: name, attributes, events."""
    parts: list[str] = [span.name]
    if span.attributes:
        for key, value in span.attributes.items():
            parts.append(f"{key}={value!r}")
    for event in span.events:
        parts.append(event.name)
        if event.attributes:
            for key, value in event.attributes.items():
                parts.append(f"{key}={value!r}")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# The property.
# ---------------------------------------------------------------------------


# Feature: phase-4-agentic, Property 1: No raw resume PII on any persistence
# or telemetry surface
@settings(max_examples=100, deadline=None)
@given(case=_pii_resume_texts(), modes=_run_shapes())
def test_no_raw_pii_on_any_persistence_or_telemetry_surface(
    case: tuple[str, list[str]], modes: dict[str, str]
) -> None:
    """After a full graph run over redacted resume text derived from raw
    text with planted PII, no serialized AgentState snapshot (Requirements
    1.3, 2.3), no persisted run input/output/failure document (Requirement
    12.3), and no span name, attribute, or event (Requirement 13.3)
    contains the raw text or any planted PII value."""
    raw_text, pii_values = case

    # The worker's construction step: the PII_Redactor runs BEFORE the
    # state is built (Requirement 1.3). Redaction removed every planted
    # value in favor of typed placeholders.
    redacted = redact(raw_text, kind="resume").text
    for value in pii_values:
        assert value not in redacted, f"raw PII value {value!r} survived redaction"
    assert "[EMAIL_" in redacted
    assert "[PHONE_" in redacted

    state = AgentState(
        job_id=str(uuid4()),
        match_id=str(uuid4()),
        user_id=str(uuid4()),
        redacted_resume_text=redacted,
        job_description_skills=["python", "kubernetes"],
        match_snapshot=MatchSnapshot(
            score=71.5,
            breakdown={"similarity": 0.6},
            scorer_version="1.0.0+stale",
            matched_skills=["python"],
            missing_skills=["kubernetes"],
            suggestions=["Add a Kubernetes project."],
        ),
    )
    recorder, spans = _run_sync(state, modes)

    # Every node persisted exactly one row; scan each persistence surface.
    assert len(recorder.rows) == 5
    forbidden = [raw_text, *pii_values]
    for row in recorder.rows:
        input_doc = json.dumps(row.input_state_json, ensure_ascii=False)
        output_doc = json.dumps(row.output_state_json, ensure_ascii=False)
        reason_doc = json.dumps(row.failure_reason_json, ensure_ascii=False)
        for value in forbidden:
            assert value not in input_doc, (
                f"raw PII {value!r} leaked into {row.agent_name} input_state_json (Req 12.3)"
            )
            assert value not in output_doc, (
                f"raw PII {value!r} leaked into {row.agent_name} output_state_json (Req 12.3)"
            )
            assert value not in reason_doc, (
                f"raw PII {value!r} leaked into {row.agent_name} failure reason (Req 12.3)"
            )

    # Every telemetry surface: span names, attributes, and events —
    # the job span and every agent child span alike (Requirement 13.3).
    assert spans, "the traced run must have captured spans"
    for span in spans:
        surface = _span_surface(span)
        for value in forbidden:
            assert value not in surface, (
                f"raw PII {value!r} leaked onto span {span.name!r} (Req 13.3)"
            )
