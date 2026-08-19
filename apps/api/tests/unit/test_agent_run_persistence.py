"""Logic-level tests for ``services/agent_jobs/runs.py`` (task 8.2).

Validates Requirements 12.2, 12.3, and 12.7 of ``phase-4-agentic`` at
the service-logic level using scripted fake sessions — no Postgres
(mirroring ``test_agent_job_service.py`` for task 8.1).

Covered here:

* :func:`persist_agent_run` stages exactly one ``AgentRun`` per call
  with the JSON-serialized Pydantic input/output state, latency,
  status, and UTC ``created_at`` (Requirement 12.2).
* the "failure reason is null iff completed" invariant is enforced in
  both directions, for every status (Requirements 12.1, 12.7).
* the serialized input state is the redacted/derived AgentState — it
  structurally carries no raw ``extracted_text`` (Requirement 12.3).
* the ``failed`` status of the Synthesizer path is persistable through
  the staging primitive (Requirement 12.7).
* :func:`build_persist_agent_run` produces a callback matching the
  positional ``PersistAgentRun`` signature ``BaseAgent.__call__``
  invokes, mapping ``AgentCompletion`` onto the column vocabulary and
  opening + committing one fresh session per invocation.
* immutability: the module exposes no update or delete code path
  (Requirement 12.6).
"""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC
from typing import Any
from uuid import UUID

import pytest
from uuid_utils.compat import uuid7

from matchlayer_api.db.models import AgentRun
from matchlayer_api.ml.agents.state import (
    AgentCompletion,
    AgentState,
    ATSOutput,
    FailureDetail,
    MatchSnapshot,
)
from matchlayer_api.services.agent_jobs import runs
from matchlayer_api.services.agent_jobs.runs import build_persist_agent_run, persist_agent_run

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeSession:
    """Scripted stand-in for ``AsyncSession``.

    Records added objects and whether the ``begin()`` transaction block
    was committed (exited without error).
    """

    def __init__(self) -> None:
        self.added: list[Any] = []
        self.flush_count = 0
        self.begin_committed = False
        self.closed = False

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        self.flush_count += 1

    def begin(self) -> Any:
        @asynccontextmanager
        async def _txn() -> AsyncIterator[None]:
            yield
            self.begin_committed = True

        return _txn()

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        self.closed = True


class FakeSessionFactory:
    """Callable stand-in for ``async_sessionmaker``: one fresh session per call."""

    def __init__(self) -> None:
        self.sessions: list[FakeSession] = []

    def __call__(self) -> FakeSession:
        session = FakeSession()
        self.sessions.append(session)
        return session


# ---------------------------------------------------------------------------
# Fixtures / builders
# ---------------------------------------------------------------------------


def _state(job_id: UUID) -> AgentState:
    return AgentState(
        job_id=str(job_id),
        match_id=str(uuid7()),
        user_id=str(uuid7()),
        redacted_resume_text="Experienced [NAME_1] engineer, contact [EMAIL_1].",
        job_description_skills=["python", "sql"],
        match_snapshot=MatchSnapshot(
            score=0.72,
            scorer_version="2.0.0+lex2+embMiniLM+spacy3.8",
            matched_skills=["python"],
            missing_skills=["sql"],
        ),
    )


def _output(*, degraded: bool = False) -> ATSOutput:
    return ATSOutput(
        score=0.72,
        breakdown={"semantic": 0.8, "keyword": 0.6},
        confidence="medium",
        scorer_version="2.0.0+lex2+embMiniLM+spacy3.8",
        degraded=degraded,
    )


# ---------------------------------------------------------------------------
# persist_agent_run — the staging primitive (Requirements 12.2, 12.3)
# ---------------------------------------------------------------------------


async def test_persist_stages_exactly_one_row_with_serialized_state() -> None:
    session = FakeSession()
    job_id = uuid7()
    state = _state(job_id)
    output = _output()

    run = await persist_agent_run(
        session,
        job_id=job_id,
        agent_name="ats",
        input_state=state,
        output=output,
        status="completed",
        failure_reason=None,
        latency_ms=142,
    )

    assert session.added == [run]  # exactly one row (Requirement 12.2)
    assert session.flush_count == 1
    assert isinstance(run, AgentRun)
    assert run.job_id == job_id
    assert run.agent_name == "ats"
    assert run.status == "completed"
    assert run.latency_ms == 142
    assert run.failure_reason_json is None
    # JSON-mode serialization: plain dicts, field-for-field (Req 12.5 input).
    assert run.input_state_json == state.model_dump(mode="json")
    assert run.output_state_json == output.model_dump(mode="json")
    assert isinstance(run.input_state_json, dict)
    assert isinstance(run.output_state_json, dict)
    # UUIDv7 id assigned; created_at is a UTC timestamptz (Requirement 12.1).
    assert run.id is not None
    assert run.created_at.tzinfo is UTC


async def test_persisted_input_state_carries_no_raw_extracted_text() -> None:
    """Requirement 12.3: redacted/derived by construction — AgentState has
    no field that could hold raw resume ``extracted_text``."""
    session = FakeSession()
    job_id = uuid7()

    run = await persist_agent_run(
        session,
        job_id=job_id,
        agent_name="ats",
        input_state=_state(job_id),
        output=_output(),
        status="completed",
        failure_reason=None,
        latency_ms=1,
    )

    assert "extracted_text" not in run.input_state_json
    assert set(run.input_state_json) == set(AgentState.model_fields)


async def test_degraded_row_carries_structured_failure_reason() -> None:
    session = FakeSession()
    job_id = uuid7()
    reason = FailureDetail(trigger="timeout", detail="node exceeded the per-node timeout")

    run = await persist_agent_run(
        session,
        job_id=job_id,
        agent_name="ats",
        input_state=_state(job_id),
        output=_output(degraded=True),
        status="degraded",
        failure_reason=reason,
        latency_ms=20_000,
    )

    assert run.status == "degraded"
    assert run.failure_reason_json == {
        "trigger": "timeout",
        "detail": "node exceeded the per-node timeout",
    }


async def test_failed_status_persists_for_the_synthesizer_path() -> None:
    """Requirement 12.7: the worker persists the Synthesizer's ``failed``
    Agent_Run row through this primitive before the terminal Job_Status."""
    session = FakeSession()
    job_id = uuid7()

    run = await persist_agent_run(
        session,
        job_id=job_id,
        agent_name="synthesizer",
        input_state=_state(job_id),
        output=_output(degraded=True),
        status="failed",
        failure_reason=FailureDetail(trigger="error", detail="RuntimeError"),
        latency_ms=5,
    )

    assert run.status == "failed"
    assert run.failure_reason_json is not None


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        ("completed", FailureDetail(trigger="error")),  # completed must be reason-less
        ("degraded", None),  # degraded must carry a reason
        ("failed", None),  # failed must carry a reason
    ],
)
async def test_failure_reason_null_iff_completed_is_enforced(
    status: str, reason: FailureDetail | None
) -> None:
    session = FakeSession()
    job_id = uuid7()

    with pytest.raises(ValueError, match="null iff"):
        await persist_agent_run(
            session,
            job_id=job_id,
            agent_name="ats",
            input_state=_state(job_id),
            output=_output(),
            status=status,  # type: ignore[arg-type]  # parametrized literal
            failure_reason=reason,
            latency_ms=1,
        )

    assert session.added == []  # nothing staged on an invariant violation


# ---------------------------------------------------------------------------
# build_persist_agent_run — the production PersistAgentRun adapter
# ---------------------------------------------------------------------------


async def test_adapter_matches_the_positional_lifecycle_callback_signature() -> None:
    """``BaseAgent.__call__`` invokes the callback positionally as
    ``(agent_name, input_state, output, status, failure_reason, latency_ms)``."""
    factory = FakeSessionFactory()
    job_id = uuid7()
    callback = build_persist_agent_run(factory, job_id=job_id)  # type: ignore[arg-type]

    # Positional call, exactly as the lifecycle does it.
    await callback(
        "resume_analysis", _state(job_id), _output(), AgentCompletion.COMPLETED, None, 987
    )

    assert len(factory.sessions) == 1
    (run,) = factory.sessions[0].added
    assert run.job_id == job_id
    assert run.agent_name == "resume_analysis"
    assert run.status == "completed"
    assert run.latency_ms == 987


@pytest.mark.parametrize(
    ("completion", "reason", "expected_status"),
    [
        (AgentCompletion.COMPLETED, None, "completed"),
        (AgentCompletion.DEGRADED, FailureDetail(trigger="quota_exhausted"), "degraded"),
    ],
)
async def test_adapter_maps_completion_onto_the_column_vocabulary(
    completion: AgentCompletion,
    reason: FailureDetail | None,
    expected_status: str,
) -> None:
    factory = FakeSessionFactory()
    job_id = uuid7()
    callback = build_persist_agent_run(factory, job_id=job_id)  # type: ignore[arg-type]

    await callback("improvement", _state(job_id), _output(), completion, reason, 10)

    (run,) = factory.sessions[0].added
    assert run.status == expected_status


async def test_adapter_opens_and_commits_one_fresh_session_per_invocation() -> None:
    """Each invocation gets its own committed short transaction, so run rows
    are visible to job polling mid-run (Requirement 10.2) and parallel
    branch nodes never share a session."""
    factory = FakeSessionFactory()
    job_id = uuid7()
    callback = build_persist_agent_run(factory, job_id=job_id)  # type: ignore[arg-type]

    await callback("skill_gap", _state(job_id), _output(), AgentCompletion.COMPLETED, None, 1)
    await callback("improvement", _state(job_id), _output(), AgentCompletion.COMPLETED, None, 2)

    assert len(factory.sessions) == 2
    for session in factory.sessions:
        assert len(session.added) == 1  # exactly one row per invocation
        assert session.begin_committed is True
        assert session.closed is True


# ---------------------------------------------------------------------------
# Immutability (Requirement 12.6)
# ---------------------------------------------------------------------------


def test_module_exposes_no_update_or_delete_path() -> None:
    """Agent_Run rows are immutable once written: the single writer contains
    no update or delete code path (Requirement 12.6)."""
    src = inspect.getsource(runs)
    assert "delete(" not in src
    assert ".delete" not in src
    assert "update(" not in src
