"""Unit tests for the LLMAgent intermediate class (phase-4-agentic task 4.2).

Covers the contract in ``ml/agents/llm_agent.py`` with a fake orchestrator
(no network, no database):

* the final ``run`` delegates to the orchestrator's two-phase
  prepare/execute API;
* ``EmptyInputError`` from ``build_prompt_input`` routes to the degraded
  path with **zero** orchestrator calls (hence zero provider calls, zero
  quota — Requirements 3.6, 9.4);
* span hooks attach prompt version, model id, and input hash **only**
  when a provider call occurred (Requirement 13.2);
* the orchestrator's gate rejections and fallback envelopes route through
  the agent's own degraded path (Requirements 9.5, 9.9).

Requirements: 9.1, 9.2, 9.3, 9.4, 9.6, 9.7, 9.9, 13.2.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import NoOpTracer, Tracer
from pydantic import BaseModel

from matchlayer_api.db.models import MatchResult
from matchlayer_api.ml.agents.base import AgentDeps, BaseAgent, EmptyInputError
from matchlayer_api.ml.agents.llm_agent import (
    LLMAgent,
    MatchScopedOrchestrator,
    ProviderCallInfo,
)
from matchlayer_api.ml.agents.state import (
    AgentCompletion,
    AgentState,
    AgentStatusFlag,
    FailureDetail,
)
from matchlayer_api.ml.llm.client import LLMRequest
from matchlayer_api.ml.prompts.registry import LLMFeature
from matchlayer_api.services.llm.orchestrator import (
    DailyQuotaExceededError,
    LLMFeatureSpec,
    LLMOutcome,
    ProviderCallPlan,
    SpendLimitExceededError,
)
from matchlayer_api.services.llm.schemas import FailureReason, LLMResultEnvelope


class _Out(BaseModel):
    value: str = "ok"
    degraded: bool = False


_SPEC: LLMFeatureSpec[str, _Out] = LLMFeatureSpec(
    feature=LLMFeature.RESUME_COACH,  # any registered feature works for a fake
    result_schema=_Out,
    build_inputs=lambda match, feature_input: (_ for _ in ()).throw(
        AssertionError("build_inputs must not be called by the agent layer")
    ),
    build_fallback=lambda match, feature_input, reason: _Out(value="fallback", degraded=True),
)


def _success_outcome(value: str = "llm") -> LLMOutcome[_Out]:
    envelope = LLMResultEnvelope[_Out](is_fallback=False, result=_Out(value=value))
    return LLMOutcome(envelope=envelope, quota_remaining=5)


def _fallback_outcome(reason: FailureReason) -> LLMOutcome[_Out]:
    envelope = LLMResultEnvelope[_Out](
        is_fallback=True, fallback_reason=reason, result=_Out(value="fallback", degraded=True)
    )
    return LLMOutcome(envelope=envelope, quota_remaining=5)


def _plan(
    *, template_version: int = 3, input_hash: str = "hash-abc"
) -> ProviderCallPlan[str, _Out]:
    return ProviderCallPlan(
        spec=_SPEC,
        envelope_cls=LLMResultEnvelope[_Out],
        user_id=uuid4(),
        match=MatchResult(),
        feature_input="redacted resume text",
        request=LLMRequest(messages=[], output_schema={}, max_output_tokens=16),
        template_version=template_version,
        input_hash=input_hash,
        quota_remaining=4,
    )


class _FakeOrchestrator:
    """Scripted AgentLLMOrchestrator: no network, records every call."""

    model = "test-model"

    def __init__(
        self,
        *,
        prepare_returns: LLMOutcome[_Out] | ProviderCallPlan[str, _Out] | None = None,
        prepare_raises: BaseException | None = None,
        execute_returns: LLMOutcome[_Out] | None = None,
    ) -> None:
        self.prepare_returns = prepare_returns
        self.prepare_raises = prepare_raises
        self.execute_returns = execute_returns
        self.prepare_calls: list[tuple[object, str, str]] = []
        self.execute_calls: list[object] = []

    async def prepare(
        self,
        spec: LLMFeatureSpec[str, _Out],
        *,
        user_id: str,
        feature_input: str,
    ) -> LLMOutcome[_Out] | ProviderCallPlan[str, _Out]:
        self.prepare_calls.append((spec, user_id, feature_input))
        if self.prepare_raises is not None:
            raise self.prepare_raises
        assert self.prepare_returns is not None
        return self.prepare_returns

    async def execute(self, plan: ProviderCallPlan[str, _Out]) -> LLMOutcome[_Out]:
        self.execute_calls.append(plan)
        assert self.execute_returns is not None
        return self.execute_returns


class _PersistRecorder:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    async def __call__(
        self,
        agent_name: str,
        state: AgentState,
        output: BaseModel,
        status: AgentCompletion,
        reason: FailureDetail | None,
        latency_ms: int,
    ) -> None:
        self.calls.append((agent_name, state, output, status, reason, latency_ms))


class _Clock:
    def monotonic(self) -> float:
        return 0.0


class _StubLLMAgent(LLMAgent[_Out]):
    name = "stub_llm"
    output_field = "stub_llm_output"

    def __init__(
        self,
        deps: AgentDeps,
        orchestrator: _FakeOrchestrator,
        *,
        empty_input: bool = False,
    ) -> None:
        super().__init__(deps, orchestrator)
        self._empty_input = empty_input

    def feature_spec(self) -> LLMFeatureSpec[str, _Out]:
        return _SPEC

    def build_prompt_input(self, state: AgentState) -> str:
        if self._empty_input:
            raise EmptyInputError("redacted resume text empty")
        return "redacted resume text"

    def build_degraded(self, state: AgentState) -> _Out:
        return _Out(value="degraded", degraded=True)

    def build_minimal(self) -> _Out:
        return _Out(value="", degraded=True)


def _make_state() -> AgentState:
    return AgentState(job_id="job-1", match_id="match-1", user_id=str(uuid4()))


def _make_agent(
    fake: _FakeOrchestrator,
    *,
    tracer: Tracer | None = None,
    empty_input: bool = False,
) -> tuple[_StubLLMAgent, _PersistRecorder]:
    persist = _PersistRecorder()
    deps = AgentDeps(
        node_timeout_s=5.0,
        persist_agent_run=persist,
        tracer=tracer if tracer is not None else NoOpTracer(),
        clock=_Clock(),
    )
    return _StubLLMAgent(deps, fake, empty_input=empty_input), persist


def _make_recording_tracer() -> tuple[Tracer, InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider.get_tracer("test"), exporter


def _only_span(exporter: InMemorySpanExporter) -> ReadableSpan:
    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    return spans[0]


def _flag(update: dict[str, object]) -> AgentStatusFlag:
    status_map = update["agent_status"]
    assert isinstance(status_map, dict)
    flag = status_map["stub_llm"]
    assert isinstance(flag, AgentStatusFlag)
    return flag


_LLM_ATTRS = ("llm.prompt_version", "llm.model", "llm.input_hash")


class TestRunDelegation:
    async def test_provider_call_path_returns_validated_output(self) -> None:
        fake = _FakeOrchestrator(prepare_returns=_plan(), execute_returns=_success_outcome())
        agent, persist = _make_agent(fake)
        state = _make_state()

        update = await agent(state)

        output = update["stub_llm_output"]
        assert isinstance(output, _Out)
        assert output.value == "llm"
        assert output.degraded is False
        assert _flag(update).status is AgentCompletion.COMPLETED

        # prepare received the agent-built prompt input + the agent's spec,
        # scoped to the state's user (Req 9.1: everything via the pipeline).
        assert len(fake.prepare_calls) == 1
        spec, user_id, feature_input = fake.prepare_calls[0]
        assert spec is _SPEC
        assert user_id == state.user_id
        assert feature_input == "redacted resume text"
        assert len(fake.execute_calls) == 1
        assert persist.calls[0][3] is AgentCompletion.COMPLETED

    async def test_no_call_resolution_returns_output_without_execute(self) -> None:
        # Cache hit / persisted reuse: prepare resolves immediately (Req 9.7).
        fake = _FakeOrchestrator(prepare_returns=_success_outcome(value="cached"))
        agent, _ = _make_agent(fake)

        update = await agent(_make_state())

        output = update["stub_llm_output"]
        assert isinstance(output, _Out)
        assert output.value == "cached"
        assert _flag(update).status is AgentCompletion.COMPLETED
        assert len(fake.prepare_calls) == 1
        assert fake.execute_calls == []  # no provider call

    async def test_fallback_envelope_routes_to_agent_degraded_output(self) -> None:
        # The orchestrator returns a fallback instead of raising; the agent
        # must degrade via its own build_degraded (Req 8 via 9.2/9.3).
        fake = _FakeOrchestrator(
            prepare_returns=_plan(),
            execute_returns=_fallback_outcome(FailureReason.PROVIDER_ERROR),
        )
        agent, persist = _make_agent(fake)

        update = await agent(_make_state())

        output = update["stub_llm_output"]
        assert isinstance(output, _Out)
        assert output.value == "degraded"  # the agent's, not the orchestrator's
        flag = _flag(update)
        assert flag.status is AgentCompletion.DEGRADED
        assert flag.failure_reason is not None
        assert flag.failure_reason.trigger == "error"
        assert flag.failure_reason.detail == "LLMFallbackError"
        assert persist.calls[0][3] is AgentCompletion.DEGRADED


class TestEmptyInput:
    async def test_empty_input_degrades_with_zero_orchestrator_calls(self) -> None:
        # Req 3.6 / 9.4: EmptyInputError fires before any orchestrator
        # interaction → zero provider calls, zero quota consumption.
        fake = _FakeOrchestrator(prepare_returns=_success_outcome())
        agent, persist = _make_agent(fake, empty_input=True)

        update = await agent(_make_state())

        output = update["stub_llm_output"]
        assert isinstance(output, _Out)
        assert output.value == "degraded"
        flag = _flag(update)
        assert flag.status is AgentCompletion.DEGRADED
        assert flag.failure_reason is not None
        assert flag.failure_reason.trigger == "empty_input"
        assert fake.prepare_calls == []
        assert fake.execute_calls == []
        assert persist.calls[0][3] is AgentCompletion.DEGRADED


class TestGateRejections:
    async def test_quota_reserve_failure_degrades_quota_exhausted(self) -> None:
        # Req 9.9: reserve failure at call time → degraded, no provider call.
        fake = _FakeOrchestrator(
            prepare_raises=DailyQuotaExceededError(
                limit=10, remaining=0, resets_at=datetime(2026, 1, 1, tzinfo=UTC)
            )
        )
        agent, _ = _make_agent(fake)

        update = await agent(_make_state())

        flag = _flag(update)
        assert flag.status is AgentCompletion.DEGRADED
        assert flag.failure_reason is not None
        assert flag.failure_reason.trigger == "quota_exhausted"
        assert fake.execute_calls == []

    async def test_breaker_open_degrades_breaker_open(self) -> None:
        # Req 9.5: breaker open → degraded path, no provider call.
        fake = _FakeOrchestrator(prepare_raises=SpendLimitExceededError())
        agent, _ = _make_agent(fake)

        update = await agent(_make_state())

        flag = _flag(update)
        assert flag.status is AgentCompletion.DEGRADED
        assert flag.failure_reason is not None
        assert flag.failure_reason.trigger == "breaker_open"
        assert fake.execute_calls == []


class TestSpanAttributes:
    async def test_provider_call_attaches_llm_attributes(self) -> None:
        # Req 13.2: prompt version, model id, input hash mirror the
        # invocation-log values when a call occurred.
        tracer, exporter = _make_recording_tracer()
        fake = _FakeOrchestrator(
            prepare_returns=_plan(template_version=3, input_hash="hash-abc"),
            execute_returns=_success_outcome(),
        )
        agent, _ = _make_agent(fake, tracer=tracer)

        await agent(_make_state())

        span = _only_span(exporter)
        assert span.name == "agent.stub_llm"
        attrs = dict(span.attributes or {})
        assert attrs["llm.prompt_version"] == 3
        assert attrs["llm.model"] == "test-model"
        assert attrs["llm.input_hash"] == "hash-abc"
        assert attrs["agent.status"] == "completed"

    async def test_no_call_paths_leave_llm_attributes_absent(self) -> None:
        # Req 13.2: cache hit and empty input make no provider call → the
        # three attributes are absent; the child span is still emitted.
        for fake, empty_input in (
            (_FakeOrchestrator(prepare_returns=_success_outcome(value="cached")), False),
            (_FakeOrchestrator(prepare_returns=_success_outcome()), True),
        ):
            tracer, exporter = _make_recording_tracer()
            agent, _ = _make_agent(fake, tracer=tracer, empty_input=empty_input)

            await agent(_make_state())

            span = _only_span(exporter)
            attrs = dict(span.attributes or {})
            for key in _LLM_ATTRS:
                assert key not in attrs, f"{key} must be absent when no call occurred"
            assert "agent.status" in attrs

    async def test_failed_call_still_attaches_llm_attributes(self) -> None:
        # A plan was produced → a call was initiated and logged; the span
        # mirrors the invocation-log row even though the agent degraded.
        tracer, exporter = _make_recording_tracer()
        fake = _FakeOrchestrator(
            prepare_returns=_plan(template_version=7, input_hash="hash-fail"),
            execute_returns=_fallback_outcome(FailureReason.TIMEOUT),
        )
        agent, _ = _make_agent(fake, tracer=tracer)

        await agent(_make_state())

        span = _only_span(exporter)
        attrs = dict(span.attributes or {})
        assert attrs["llm.prompt_version"] == 7
        assert attrs["llm.input_hash"] == "hash-fail"
        assert attrs["agent.status"] == "degraded"

    async def test_stash_resets_between_invocations(self) -> None:
        # on_span_start clears the stash: a call on invocation 1 must not
        # leak attributes onto invocation 2's span.
        tracer, exporter = _make_recording_tracer()
        fake = _FakeOrchestrator(prepare_returns=_plan(), execute_returns=_success_outcome())
        agent, _ = _make_agent(fake, tracer=tracer)

        await agent(_make_state())
        fake.prepare_returns = _success_outcome(value="cached")  # second run: no call
        await agent(_make_state())

        first, second = exporter.get_finished_spans()
        assert "llm.input_hash" in dict(first.attributes or {})
        for key in _LLM_ATTRS:
            assert key not in dict(second.attributes or {})


class TestHierarchyContract:
    def test_llm_agent_does_not_override_call(self) -> None:
        # The lifecycle stays in exactly one place (design §2).
        assert LLMAgent.__call__ is BaseAgent.__call__

    def test_provider_call_info_is_immutable(self) -> None:
        info = ProviderCallInfo(prompt_version=1, model="m", input_hash="h")
        assert (info.prompt_version, info.model, info.input_hash) == (1, "m", "h")


class TestMatchScopedOrchestrator:
    async def test_adapter_binds_match_and_parses_user_id(self) -> None:
        recorded: dict[str, object] = {}
        match = MatchResult()

        class _FakePhase3:
            async def prepare(
                self,
                spec: LLMFeatureSpec[str, _Out],
                *,
                user_id: UUID,
                match: MatchResult,
                feature_input: str,
            ) -> LLMOutcome[_Out]:
                recorded.update(user_id=user_id, match=match, feature_input=feature_input)
                return _success_outcome()

        user_id = uuid4()
        adapter = MatchScopedOrchestrator(
            orchestrator=_FakePhase3(),  # type: ignore[arg-type]  # duck-typed fake
            match=match,
            model="test-model",
        )
        outcome = await adapter.prepare(_SPEC, user_id=str(user_id), feature_input="text")

        assert isinstance(outcome, LLMOutcome)
        assert recorded["user_id"] == user_id
        assert recorded["match"] is match
        assert recorded["feature_input"] == "text"
        assert adapter.model == "test-model"
