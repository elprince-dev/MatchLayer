"""Unit tests for the BaseAgent invocation lifecycle (phase-4-agentic task 4.1).

Covers the template-method contract in ``ml/agents/base.py``: success path,
exception → classify → degraded, degraded-construction double failure,
per-node timeout, latency accounting on the injected clock, Agent_Run
persistence callback, and the partial-state return shape.
Requirements: 1.2, 8.1, 8.2, 8.3, 8.4, 8.6, 12.2, 13.1.

The hierarchy-contract tests (concrete agents, no-__call__-override) belong
to task 5.6; graph/timeout integration belongs to task 7.3.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest
from opentelemetry.trace import NoOpTracer
from pydantic import BaseModel, ValidationError

from matchlayer_api.ml.agents.base import (
    AgentDeps,
    BaseAgent,
    EmptyInputError,
    classify_failure,
)
from matchlayer_api.ml.agents.state import (
    AgentCompletion,
    AgentState,
    AgentStatusFlag,
    FailureDetail,
)


class _Out(BaseModel):
    value: str = "ok"
    degraded: bool = False


class _FakeClock:
    """Injected monotonic clock; tests advance it explicitly."""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now


class _PersistRecorder:
    """Records persist_agent_run calls (the agent layer never touches the DB)."""

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


class _StubAgent(BaseAgent[_Out]):
    """Configurable agent: behavior of run/build_degraded set per test."""

    name = "stub"
    output_field = "stub_output"

    def __init__(
        self,
        deps: AgentDeps,
        *,
        run_exc: BaseException | None = None,
        run_sleep_s: float = 0.0,
        degraded_raises: bool = False,
        clock: _FakeClock | None = None,
        run_advances_clock_by: float = 0.0,
    ) -> None:
        super().__init__(deps)
        self._run_exc = run_exc
        self._run_sleep_s = run_sleep_s
        self._degraded_raises = degraded_raises
        self._clock = clock
        self._run_advances_clock_by = run_advances_clock_by

    async def run(self, state: AgentState) -> _Out:
        if self._run_sleep_s:
            await asyncio.sleep(self._run_sleep_s)
        if self._clock is not None:
            self._clock.now += self._run_advances_clock_by
        if self._run_exc is not None:
            raise self._run_exc
        return _Out(value="normal")

    def build_degraded(self, state: AgentState) -> _Out:
        if self._degraded_raises:
            raise RuntimeError("degraded constructor blew up")
        return _Out(value="degraded", degraded=True)

    def build_minimal(self) -> _Out:
        return _Out(value="", degraded=True)


def _make_state() -> AgentState:
    return AgentState(job_id="job-1", match_id="match-1", user_id="user-1")


def _make_agent(**kwargs: Any) -> tuple[_StubAgent, _PersistRecorder, _FakeClock]:
    clock = _FakeClock()
    persist = _PersistRecorder()
    deps = AgentDeps(
        node_timeout_s=kwargs.pop("node_timeout_s", 5.0),
        persist_agent_run=persist,
        tracer=NoOpTracer(),
        clock=clock,
    )
    agent = _StubAgent(deps, clock=clock, **kwargs)
    return agent, persist, clock


class TestSuccessPath:
    async def test_returns_partial_state_with_output_and_status(self) -> None:
        agent, _, _ = _make_agent()
        update = await agent(_make_state())

        assert set(update) == {"stub_output", "agent_status"}
        output = update["stub_output"]
        assert isinstance(output, _Out)
        assert output.value == "normal"
        assert output.degraded is False

        status_map = update["agent_status"]
        assert isinstance(status_map, dict)
        flag = status_map["stub"]
        assert isinstance(flag, AgentStatusFlag)
        assert flag.status is AgentCompletion.COMPLETED
        assert flag.failure_reason is None

    async def test_persists_exactly_one_completed_run(self) -> None:
        agent, persist, _ = _make_agent()
        state = _make_state()
        await agent(state)

        assert len(persist.calls) == 1
        name, got_state, output, status, reason, latency_ms = persist.calls[0]
        assert name == "stub"
        assert got_state is state
        assert isinstance(output, _Out)
        assert status is AgentCompletion.COMPLETED
        assert reason is None
        assert isinstance(latency_ms, int)

    async def test_latency_measured_on_injected_clock(self) -> None:
        # run advances the fake clock by 0.25 s → latency_ms must be 250.
        agent, persist, _ = _make_agent(run_advances_clock_by=0.25)
        await agent(_make_state())

        latency_ms = persist.calls[0][5]
        assert latency_ms == 250


class TestDegradedPath:
    async def test_run_exception_degrades_with_classified_reason(self) -> None:
        agent, persist, _ = _make_agent(run_exc=ValueError("boom"))
        update = await agent(_make_state())

        output = update["stub_output"]
        assert isinstance(output, _Out)
        assert output.value == "degraded"
        assert output.degraded is True

        status_map = update["agent_status"]
        assert isinstance(status_map, dict)
        flag = status_map["stub"]
        assert isinstance(flag, AgentStatusFlag)
        assert flag.status is AgentCompletion.DEGRADED
        assert flag.failure_reason is not None
        assert flag.failure_reason.trigger == "error"

        _, _, _, status, reason, _ = persist.calls[0]
        assert status is AgentCompletion.DEGRADED
        assert reason is not None
        assert reason.trigger == "error"

    async def test_degraded_construction_failure_yields_minimal_output(self) -> None:
        # Requirement 8.6: build_degraded raising must not fail the node.
        agent, persist, _ = _make_agent(run_exc=ValueError("boom"), degraded_raises=True)
        update = await agent(_make_state())

        output = update["stub_output"]
        assert isinstance(output, _Out)
        assert output.value == ""  # the minimal schema-valid output
        assert output.degraded is True

        status_map = update["agent_status"]
        assert isinstance(status_map, dict)
        flag = status_map["stub"]
        assert isinstance(flag, AgentStatusFlag)
        assert flag.failure_reason is not None
        assert flag.failure_reason.trigger == "degraded_construction_error"
        # The original trigger is recorded in the operator-safe detail.
        assert flag.failure_reason.detail is not None
        assert "error" in flag.failure_reason.detail
        assert "RuntimeError" in flag.failure_reason.detail

        _, _, _, status, reason, _ = persist.calls[0]
        assert status is AgentCompletion.DEGRADED
        assert reason is not None
        assert reason.trigger == "degraded_construction_error"

    async def test_timeout_degrades_with_timeout_trigger(self) -> None:
        # Requirement 8.3: asyncio.wait_for bounds run; late work is abandoned.
        agent, persist, _ = _make_agent(node_timeout_s=0.01, run_sleep_s=5.0)
        update = await asyncio.wait_for(agent(_make_state()), timeout=2.0)

        output = update["stub_output"]
        assert isinstance(output, _Out)
        assert output.degraded is True

        status_map = update["agent_status"]
        assert isinstance(status_map, dict)
        flag = status_map["stub"]
        assert isinstance(flag, AgentStatusFlag)
        assert flag.status is AgentCompletion.DEGRADED
        assert flag.failure_reason is not None
        assert flag.failure_reason.trigger == "timeout"
        assert persist.calls[0][3] is AgentCompletion.DEGRADED


class TestClassifyFailure:
    def test_empty_input(self) -> None:
        detail = classify_failure(EmptyInputError())
        assert detail.trigger == "empty_input"

    def test_quota_exhausted(self) -> None:
        from matchlayer_api.services.llm.orchestrator import DailyQuotaExceededError

        exc = DailyQuotaExceededError(
            limit=10, remaining=0, resets_at=datetime(2026, 1, 1, tzinfo=UTC)
        )
        assert classify_failure(exc).trigger == "quota_exhausted"

    def test_breaker_open(self) -> None:
        from matchlayer_api.services.llm.orchestrator import SpendLimitExceededError

        assert classify_failure(SpendLimitExceededError()).trigger == "breaker_open"

    def test_timeout(self) -> None:
        assert classify_failure(TimeoutError()).trigger == "timeout"

    def test_schema_validation(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            _Out.model_validate({"value": 123, "degraded": "not-a-bool"})
        assert classify_failure(exc_info.value).trigger == "schema_validation"

    def test_generic_error_detail_is_class_name_only(self) -> None:
        # Operator-safe detail: class name only, never str(exc) which could
        # carry resume content or secrets (security.md).
        detail = classify_failure(ValueError("SSN 123-45-6789 leaked"))
        assert detail.trigger == "error"
        assert detail.detail == "ValueError"
        assert "123-45-6789" not in (detail.detail or "")


class TestSpanHooks:
    def test_default_on_span_end_sets_status_and_latency(self) -> None:
        agent, _, _ = _make_agent()

        recorded: dict[str, object] = {}

        class _FakeSpan:
            def set_attribute(self, key: str, value: object) -> None:
                recorded[key] = value

        agent.on_span_end(_FakeSpan(), AgentCompletion.COMPLETED, 42)  # type: ignore[arg-type]
        assert recorded == {"agent.status": "completed", "agent.latency_ms": 42}
