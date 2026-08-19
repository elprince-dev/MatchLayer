"""Feature: phase-4-agentic — Property 16.

Property 16: Redelivery dispatch decision.

    *For any* combination of Job_Status, persisted attempt count, and
    configured ``MATCHLAYER_AGENT_MAX_ATTEMPTS``, the worker's redelivery
    decision matches the specified rule exactly: terminal → acknowledge
    without re-execution; ``queued``, or ``running`` with attempts below
    the maximum → re-execute; ``running`` with attempts at (or beyond) the
    maximum → transition to ``failed`` and acknowledge.

**Validates: Requirements 11.5**

Two layers, both over generated inputs:

1. :func:`decide_redelivery` — the pure decision — is total over the
   whole ``(status, attempts, max_attempts)`` space (never raises, always
   yields exactly one :class:`RedeliveryDecision` member) and maps every
   point to the Requirement 11.5 rule.
2. :meth:`AgentWorker.process_message` HONORS the decision: the executor
   is invoked *iff* the decision is ``EXECUTE``; ``ACK_TERMINAL`` deletes
   without any store mutation; ``FAIL_MAX_ATTEMPTS`` records the
   structured max-attempts error and then deletes — driven over the
   established fake queue/store/executor pattern
   (``tests/unit/test_agent_worker.py``), no database or SQS.

Async note: each example drives its coroutine inside an
:class:`asyncio.Runner` (the suite's established pattern) so the event
loop closes deterministically under ``filterwarnings = ["error"]``.
"""

# Feature: phase-4-agentic, Property 16: Redelivery dispatch decision

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID, uuid4

from hypothesis import given, settings
from hypothesis import strategies as st

from matchlayer_api.services.agent_jobs.queue import JobMessage, ReceivedMessage
from matchlayer_api.workers.agent_worker import (
    AgentWorker,
    JobSnapshot,
    RedeliveryDecision,
    decide_redelivery,
)

# The persisted Job_Status vocabulary (agent_jobs CHECK constraint).
_STATUSES = ("queued", "running", "completed", "failed")
_TERMINAL = frozenset({"completed", "failed"})

_statuses = st.sampled_from(_STATUSES)
_attempts = st.integers(min_value=0, max_value=10)
_max_attempts = st.integers(min_value=1, max_value=5)


def _expected(status: str, attempts: int, max_attempts: int) -> RedeliveryDecision:
    """The Requirement 11.5 rule, stated declaratively case by case."""
    if status in _TERMINAL:  # 11.5a
        return RedeliveryDecision.ACK_TERMINAL
    if status == "queued":  # first delivery always executes
        return RedeliveryDecision.EXECUTE
    if attempts < max_attempts:  # 11.5b
        return RedeliveryDecision.EXECUTE
    return RedeliveryDecision.FAIL_MAX_ATTEMPTS  # 11.5c


# ---------------------------------------------------------------------------
# Layer 1: the pure decision is total and matches the rule exactly.
# ---------------------------------------------------------------------------


# Feature: phase-4-agentic, Property 16: Redelivery dispatch decision
@settings(max_examples=200, deadline=None)
@given(status=_statuses, attempts=_attempts, max_attempts=_max_attempts)
def test_decide_redelivery_is_total_and_matches_the_rule(
    status: str, attempts: int, max_attempts: int
) -> None:
    """Every (status, attempts, max_attempts) maps to exactly the 11.5 case."""
    decision = decide_redelivery(status, attempts, max_attempts)
    assert isinstance(decision, RedeliveryDecision)  # total: one member, no raise
    assert decision is _expected(status, attempts, max_attempts)


# ---------------------------------------------------------------------------
# Layer 2: the worker honors the decision (executor invoked iff EXECUTE).
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
        self.running_marks = 0
        self.failed_errors: list[dict[str, Any]] = []
        self.completed_results: list[dict[str, Any]] = []

    async def load(self, job_id: UUID) -> JobSnapshot | None:
        return self._job

    async def mark_running(self, job_id: UUID) -> None:
        self.running_marks += 1

    async def mark_completed(self, job_id: UUID, result: dict[str, Any]) -> None:
        self.completed_results.append(result)

    async def mark_failed(self, job_id: UUID, error: dict[str, Any]) -> None:
        self.failed_errors.append(error)


class _FakeExecutor:
    def __init__(self) -> None:
        self.calls: list[JobMessage] = []

    async def __call__(self, message: JobMessage) -> dict[str, Any]:
        self.calls.append(message)
        return {"analysis": "ok"}


def _run_sync(coro_factory: Callable[[], Awaitable[None]]) -> None:
    with asyncio.Runner() as runner:
        runner.run(coro_factory())


# Feature: phase-4-agentic, Property 16: Redelivery dispatch decision
@settings(max_examples=150, deadline=None)
@given(status=_statuses, attempts=_attempts, max_attempts=_max_attempts)
def test_process_message_honors_the_redelivery_decision(
    status: str, attempts: int, max_attempts: int
) -> None:
    """The consume flow dispatches exactly per the decision for every input.

    Requirement 11.5: (a) terminal jobs are acknowledged without
    re-execution and without any store mutation; (b) ``queued`` /
    ``running`` below the cap re-execute (here: through to ``completed``);
    (c) at the cap the job is failed with a structured error and
    acknowledged — the executor never runs.
    """
    job_id, match_id, user_id = uuid4(), uuid4(), uuid4()
    body = JobMessage(
        job_id=str(job_id), match_id=str(match_id), user_id=str(user_id)
    ).model_dump_json()

    queue = _FakeQueue()
    store = _FakeStore(
        JobSnapshot(id=job_id, status=status, attempts=attempts, match_id=match_id, user_id=user_id)
    )
    executor = _FakeExecutor()
    worker = AgentWorker(queue=queue, store=store, execute_job=executor, max_attempts=max_attempts)

    _run_sync(lambda: worker.process_message(ReceivedMessage(body=body, receipt_handle="rh-1")))

    decision = _expected(status, attempts, max_attempts)

    # The executor runs iff the decision is EXECUTE.
    assert (len(executor.calls) == 1) == (decision is RedeliveryDecision.EXECUTE)

    # Every decision ends in an acknowledge (all store fakes commit cleanly).
    assert queue.deleted == ["rh-1"]

    if decision is RedeliveryDecision.ACK_TERMINAL:
        # (a) no re-execution, no mutation of any kind.
        assert store.running_marks == 0
        assert store.failed_errors == []
        assert store.completed_results == []
    elif decision is RedeliveryDecision.EXECUTE:
        # (b) transition to running, execute, record the terminal result.
        assert store.running_marks == 1
        assert store.completed_results == [{"analysis": "ok"}]
        assert store.failed_errors == []
    else:
        # (c) failed with the structured max-attempts error, never executed.
        assert store.running_marks == 0
        assert store.completed_results == []
        (error,) = store.failed_errors
        assert error["trigger"] == "error"
        assert str(max_attempts) in json.dumps(error)
