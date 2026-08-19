"""Feature: phase-4-agentic — Property 5.

Property 5: Invalid invocation fails before any node executes.

    *For any* invalid job context — a message referencing a nonexistent
    Match_Result, a Match_Result whose Resume no longer exists, or loaded
    data whose projection fails ``AgentState``/``MatchSnapshot`` Pydantic
    validation — the worker transitions the Agent_Job to ``failed`` with a
    structured PII-free error, ZERO agent nodes execute (the graph is never
    invoked and ``persist_agent_run`` records nothing), and no agent output
    is produced.

**Validates: Requirements 1.8**

The property drives the production seam end to end with fakes: a real
:class:`GraphJobExecutor` over a scripted fake session factory (no
database), composed into a real :class:`AgentWorker` over the established
fake queue/store pattern (``tests/unit/test_agent_worker.py``). Sentinels
replace ``GraphJobExecutor._invoke_graph`` (any touch = a node was about
to execute) and the worker module's ``persist_agent_run`` /
``build_persist_agent_run`` bindings (any touch = an ``agent_runs`` row
was about to be written), so "no node executes" is asserted directly
rather than inferred.

PII discipline (the second half of Requirement 1.8): every generated
resume text and job-description text embeds a unique high-entropy marker,
and the property asserts the marker appears nowhere in the structured
error recorded on the failed job.

Async note: each example drives its coroutine inside an
:class:`asyncio.Runner` (the suite's established pattern) so the event
loop closes deterministically under ``filterwarnings = ["error"]``.
"""

# Feature: phase-4-agentic, Property 5: Invalid invocation fails before any node executes

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from contextlib import ExitStack
from types import SimpleNamespace
from typing import Any, cast, get_args
from unittest import mock
from uuid import UUID, uuid4

from hypothesis import given, settings
from hypothesis import strategies as st

from matchlayer_api.config import Settings
from matchlayer_api.ml.agents.state import FailureTrigger
from matchlayer_api.services.agent_jobs.queue import JobMessage, ReceivedMessage
from matchlayer_api.workers import agent_worker
from matchlayer_api.workers.agent_worker import (
    AgentWorker,
    GraphJobExecutor,
    JobSnapshot,
)

# The closed FailureTrigger vocabulary the structured error must draw from.
_TRIGGERS: frozenset[str] = frozenset(get_args(FailureTrigger))

_MAX_ATTEMPTS = 2


# ---------------------------------------------------------------------------
# Fake database session: scripted rows, no I/O.
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, row: Any) -> None:
        self._row = row

    def scalar_one_or_none(self) -> Any:
        return self._row


class _FakeSession:
    """Serves the executor's two scripted SELECTs (match row, resume row)."""

    def __init__(self, rows: list[Any]) -> None:
        self._rows = list(rows)

    async def execute(self, statement: Any) -> _FakeResult:
        return _FakeResult(self._rows.pop(0) if self._rows else None)

    async def commit(self) -> None:  # pragma: no cover — not reached pre-invocation
        pass

    async def rollback(self) -> None:  # pragma: no cover — not reached pre-invocation
        pass

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


def _session_factory(rows: list[Any]) -> Any:
    def _factory() -> _FakeSession:
        return _FakeSession(rows)

    return _factory


# ---------------------------------------------------------------------------
# Fake queue/store (the established test_agent_worker.py pattern).
# ---------------------------------------------------------------------------


class _FakeQueue:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    async def delete(self, receipt_handle: str) -> None:
        self.deleted.append(receipt_handle)

    async def receive(self) -> list[ReceivedMessage]:  # pragma: no cover — loop-only
        return []


class _FakeStore:
    def __init__(self, job: JobSnapshot) -> None:
        self._job = job
        self.failed_errors: list[dict[str, Any]] = []
        self.completed_results: list[dict[str, Any]] = []

    async def load(self, job_id: UUID) -> JobSnapshot | None:
        return self._job

    async def mark_running(self, job_id: UUID) -> None:
        pass

    async def mark_completed(self, job_id: UUID, result: dict[str, Any]) -> None:
        self.completed_results.append(result)

    async def mark_failed(self, job_id: UUID, error: dict[str, Any]) -> None:
        self.failed_errors.append(error)


# ---------------------------------------------------------------------------
# Invalid-context scenarios and their scripted rows.
# ---------------------------------------------------------------------------

# Every scenario yields a context build that must fail BEFORE graph
# invocation: missing match, missing resume, or a Pydantic/projection
# failure on loaded data (Requirement 1.8's two named triggers plus the
# validation gate the AgentState/MatchSnapshot construction is).
_SCENARIOS = (
    "match_not_found",
    "resume_not_found",
    "snapshot_validation_failure",  # scorer_version None → ValidationError
    "score_not_numeric",  # score None → TypeError at float()
)


def _match_row(pii_marker: str, jd_text: str, *, scenario: str) -> SimpleNamespace:
    """A fake ``match_results`` row; invalid per the scenario."""
    return SimpleNamespace(
        resume_id=uuid4(),
        job_description_text=f"{pii_marker} {jd_text}",
        score=None if scenario == "score_not_numeric" else 55.0,
        score_breakdown={"similarity_component": 0.5},
        scorer_version=None if scenario == "snapshot_validation_failure" else "2.0.0+test",
        matched_keywords=[],
        missing_keywords=[],
        suggestions=[],
    )


def _rows(scenario: str, pii_marker: str, resume_text: str, jd_text: str) -> list[Any]:
    if scenario == "match_not_found":
        return [None]
    match = _match_row(pii_marker, jd_text, scenario=scenario)
    if scenario == "resume_not_found":
        return [match, None]
    resume = SimpleNamespace(extracted_text=f"{pii_marker} {resume_text}")
    return [match, resume]


# ---------------------------------------------------------------------------
# Sentinels: any touch means a node was about to execute.
# ---------------------------------------------------------------------------


class _NodeTouchedError(AssertionError):
    """Graph invocation or run persistence was reached (Property 5 violation)."""


def _async_sentinel(label: str, calls: list[str]) -> Callable[..., Awaitable[Any]]:
    async def _record(*args: Any, **kwargs: Any) -> Any:
        calls.append(label)
        raise _NodeTouchedError(f"{label} reached on an invalid invocation (Property 5)")

    return _record


def _sync_sentinel(label: str, calls: list[str]) -> Callable[..., Any]:
    def _record(*args: Any, **kwargs: Any) -> Any:
        calls.append(label)
        raise _NodeTouchedError(f"{label} reached on an invalid invocation (Property 5)")

    return _record


# ---------------------------------------------------------------------------
# Lightweight stubs for the worker-side text processing (patched so the
# property is hermetic — redaction/extraction correctness has its own
# properties; here they only need to pass text through the seam).
# ---------------------------------------------------------------------------


def _fake_redact(text: str, *, kind: str) -> SimpleNamespace:
    return SimpleNamespace(text="[REDACTED]")


def _fake_extract(job_description: str, settings: Any = None) -> list[str]:
    return ["python"]


def _run_sync(coro_factory: Callable[[], Awaitable[None]]) -> None:
    with asyncio.Runner() as runner:
        runner.run(coro_factory())


# ---------------------------------------------------------------------------
# The property.
# ---------------------------------------------------------------------------

_texts = st.text(min_size=0, max_size=200)


# Feature: phase-4-agentic, Property 5: Invalid invocation fails before any node executes
@settings(max_examples=120, deadline=None)
@given(
    scenario=st.sampled_from(_SCENARIOS),
    resume_text=_texts,
    jd_text=_texts,
)
def test_invalid_invocation_fails_before_any_node_executes(
    scenario: str, resume_text: str, jd_text: str
) -> None:
    """Invalid context → job failed with a PII-free structured error, zero nodes run.

    Requirement 1.8: an Agent_State that fails Pydantic validation or
    references a nonexistent Match_Result fails the graph before any
    Agent node executes, and the Agent_Worker records a structured error
    containing no Restricted PII on the ``failed`` Agent_Job.
    """
    pii_marker = f"RESTRICTED-PII-{uuid4().hex}"
    job_id, match_id, user_id = uuid4(), uuid4(), uuid4()
    message_body = JobMessage(
        job_id=str(job_id), match_id=str(match_id), user_id=str(user_id)
    ).model_dump_json()

    executor = GraphJobExecutor(
        session_factory=_session_factory(_rows(scenario, pii_marker, resume_text, jd_text)),
        settings=cast(Settings, SimpleNamespace()),
    )
    queue = _FakeQueue()
    store = _FakeStore(
        JobSnapshot(id=job_id, status="queued", attempts=0, match_id=match_id, user_id=user_id)
    )
    worker = AgentWorker(queue=queue, store=store, execute_job=executor, max_attempts=_MAX_ATTEMPTS)

    node_touches: list[str] = []
    with ExitStack() as stack:
        # Any graph invocation or agent-run persistence is a violation.
        stack.enter_context(
            mock.patch.object(
                GraphJobExecutor, "_invoke_graph", _async_sentinel("_invoke_graph", node_touches)
            )
        )
        stack.enter_context(
            mock.patch.object(
                agent_worker,
                "persist_agent_run",
                _async_sentinel("persist_agent_run", node_touches),
            )
        )
        stack.enter_context(
            mock.patch.object(
                agent_worker,
                "build_persist_agent_run",
                _sync_sentinel("build_persist_agent_run", node_touches),
            )
        )
        # Hermetic pass-throughs for the worker-side text processing.
        stack.enter_context(mock.patch.object(agent_worker, "redact", _fake_redact))
        stack.enter_context(
            mock.patch.object(agent_worker, "extract_job_description_skills", _fake_extract)
        )

        _run_sync(
            lambda: worker.process_message(
                ReceivedMessage(body=message_body, receipt_handle="rh-1")
            )
        )

    # Zero nodes executed, zero agent_runs writes, zero agent output.
    assert node_touches == [], f"agent machinery reached on invalid invocation: {node_touches}"
    assert store.completed_results == []

    # The job transitioned to failed with exactly one structured error.
    (error,) = store.failed_errors
    assert error["trigger"] in _TRIGGERS

    # The structured error carries no Restricted PII (nothing generated
    # from the resume or job-description text leaks into it).
    assert pii_marker not in json.dumps(error)

    # Terminal transition committed before the acknowledge (Req 11.4).
    assert queue.deleted == ["rh-1"]
