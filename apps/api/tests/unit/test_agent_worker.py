"""Unit tests for the Agent_Worker message-handling decision logic.

Covers phase-4-agentic task 10.1 (Requirements 11.4, 11.5, 11.7): the
pure redelivery policy (:func:`decide_redelivery`), poison-message
parsing (:func:`parse_job_message`), and the :class:`AgentWorker`
consume flow — terminal-ack, max-attempts failure, the
execute → terminal-transition → delete ordering, and the
never-delete-before-the-terminal-commit guarantee.

Everything runs against fakes: a recording queue, a scripted
:class:`JobStore`, and a scripted executor — no database, no SQS, no
graph. The deep end-to-end paths (state building, graph invocation,
LocalStack) are integration-test scope (task 17.2); the property tests
over these decisions are tasks 10.3-10.5.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import pytest

from matchlayer_api.services.agent_jobs.queue import JobMessage, ReceivedMessage
from matchlayer_api.services.agent_jobs.service import InvalidJobTransitionError
from matchlayer_api.workers.agent_worker import (
    AgentWorker,
    JobExecutionError,
    JobSnapshot,
    RedeliveryDecision,
    decide_redelivery,
    parse_job_message,
)

_JOB_ID = UUID("0192aaaa-0000-7000-8000-000000000001")
_MATCH_ID = UUID("0192aaaa-0000-7000-8000-000000000002")
_USER_ID = UUID("0192aaaa-0000-7000-8000-000000000003")
_MAX_ATTEMPTS = 2


def _body(**overrides: str) -> str:
    payload = {
        "job_id": str(_JOB_ID),
        "match_id": str(_MATCH_ID),
        "user_id": str(_USER_ID),
    }
    payload.update(overrides)
    return json.dumps(payload)


def _received(body: str | None = None) -> ReceivedMessage:
    return ReceivedMessage(body=body if body is not None else _body(), receipt_handle="rh-1")


def _snapshot(status: str = "queued", attempts: int = 0) -> JobSnapshot:
    return JobSnapshot(
        id=_JOB_ID, status=status, attempts=attempts, match_id=_MATCH_ID, user_id=_USER_ID
    )


# ---------------------------------------------------------------------------
# Fakes — each appends to a shared event log so ordering is assertable.
# ---------------------------------------------------------------------------


class FakeQueue:
    """Records deletes into the shared event log."""

    def __init__(self, events: list[str]) -> None:
        self._events = events
        self.deleted: list[str] = []

    async def delete(self, receipt_handle: str) -> None:
        self._events.append("delete")
        self.deleted.append(receipt_handle)

    async def receive(self) -> list[ReceivedMessage]:  # pragma: no cover — loop-only
        return []


class FakeStore:
    """Scripted JobStore recording every call into the shared event log."""

    def __init__(
        self,
        events: list[str],
        *,
        job: JobSnapshot | None,
        running_exc: Exception | None = None,
        failed_exc: Exception | None = None,
        completed_exc: Exception | None = None,
    ) -> None:
        self._events = events
        self._job = job
        self._running_exc = running_exc
        self._failed_exc = failed_exc
        self._completed_exc = completed_exc
        self.failed_errors: list[dict[str, Any]] = []
        self.completed_results: list[dict[str, Any]] = []

    async def load(self, job_id: UUID) -> JobSnapshot | None:
        self._events.append("load")
        return self._job

    async def mark_running(self, job_id: UUID) -> None:
        self._events.append("mark_running")
        if self._running_exc is not None:
            raise self._running_exc

    async def mark_completed(self, job_id: UUID, result: dict[str, Any]) -> None:
        self._events.append("mark_completed")
        if self._completed_exc is not None:
            raise self._completed_exc
        self.completed_results.append(result)

    async def mark_failed(self, job_id: UUID, error: dict[str, Any]) -> None:
        self._events.append("mark_failed")
        if self._failed_exc is not None:
            raise self._failed_exc
        self.failed_errors.append(error)


class FakeExecutor:
    """Scripted executor: returns a result or raises the configured error."""

    def __init__(
        self,
        events: list[str],
        *,
        result: dict[str, Any] | None = None,
        exc: Exception | None = None,
    ) -> None:
        self._events = events
        self._result = result if result is not None else {"analysis": "ok"}
        self._exc = exc
        self.calls: list[JobMessage] = []

    async def __call__(self, message: JobMessage) -> dict[str, Any]:
        self._events.append("execute")
        self.calls.append(message)
        if self._exc is not None:
            raise self._exc
        return self._result


def _worker(
    events: list[str],
    *,
    job: JobSnapshot | None,
    executor: FakeExecutor | None = None,
    **store_kwargs: Any,
) -> tuple[AgentWorker, FakeQueue, FakeStore, FakeExecutor]:
    queue = FakeQueue(events)
    store = FakeStore(events, job=job, **store_kwargs)
    execute = executor if executor is not None else FakeExecutor(events)
    worker = AgentWorker(
        queue=queue,
        store=store,
        execute_job=execute,
        max_attempts=_MAX_ATTEMPTS,
    )
    return worker, queue, store, execute


# ---------------------------------------------------------------------------
# decide_redelivery — the pure Requirement 11.5 policy.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "attempts", "expected"),
    [
        # 11.5a: terminal → acknowledge without re-execution.
        ("completed", 0, RedeliveryDecision.ACK_TERMINAL),
        ("completed", 5, RedeliveryDecision.ACK_TERMINAL),
        ("failed", 1, RedeliveryDecision.ACK_TERMINAL),
        # First delivery: queued always executes.
        ("queued", 0, RedeliveryDecision.EXECUTE),
        # 11.5b: running below the cap re-executes.
        ("running", 0, RedeliveryDecision.EXECUTE),
        ("running", 1, RedeliveryDecision.EXECUTE),
        # 11.5c: running at (or beyond) the cap fails + acks.
        ("running", 2, RedeliveryDecision.FAIL_MAX_ATTEMPTS),
        ("running", 3, RedeliveryDecision.FAIL_MAX_ATTEMPTS),
    ],
)
def test_decide_redelivery(status: str, attempts: int, expected: RedeliveryDecision) -> None:
    """The persisted attempt counter drives the redelivery decision."""
    assert decide_redelivery(status, attempts, _MAX_ATTEMPTS) is expected


# ---------------------------------------------------------------------------
# parse_job_message — poison-message parsing (Requirement 11.7).
# ---------------------------------------------------------------------------


def test_parse_job_message_valid() -> None:
    message = parse_job_message(_body())
    assert message is not None
    assert message.job_id == str(_JOB_ID)


@pytest.mark.parametrize(
    "body",
    [
        "not json at all",
        "{}",
        json.dumps({"job_id": str(_JOB_ID)}),  # missing fields
        json.dumps(  # extra field is structurally rejected
            {
                "job_id": str(_JOB_ID),
                "match_id": str(_MATCH_ID),
                "user_id": str(_USER_ID),
                "resume_text": "PII must never fit here",
            }
        ),
        _body(job_id="not-a-uuid"),
        _body(match_id="also-not-a-uuid"),
        _body(user_id=""),
    ],
)
def test_parse_job_message_malformed(body: str) -> None:
    """Any malformed shape parses to None (poison) rather than raising."""
    assert parse_job_message(body) is None


# ---------------------------------------------------------------------------
# process_message — poison messages are warned + deleted (Requirement 11.7).
# ---------------------------------------------------------------------------


async def test_malformed_body_is_deleted_without_store_access() -> None:
    events: list[str] = []
    worker, queue, _store, execute = _worker(events, job=_snapshot())

    await worker.process_message(_received("definitely not a JobMessage"))

    assert events == ["delete"]
    assert queue.deleted == ["rh-1"]
    assert execute.calls == []


async def test_job_not_found_is_deleted_without_execution() -> None:
    events: list[str] = []
    worker, queue, _store, execute = _worker(events, job=None)

    await worker.process_message(_received())

    assert events == ["load", "delete"]
    assert queue.deleted == ["rh-1"]
    assert execute.calls == []


# ---------------------------------------------------------------------------
# Terminal-ack (Requirement 11.5a).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["completed", "failed"])
async def test_terminal_job_is_acked_without_reexecution(status: str) -> None:
    events: list[str] = []
    worker, _queue, _store, execute = _worker(events, job=_snapshot(status=status))

    await worker.process_message(_received())

    assert events == ["load", "delete"]
    assert execute.calls == []


# ---------------------------------------------------------------------------
# Max-attempts (Requirement 11.5c): failed + acked, executor never runs.
# ---------------------------------------------------------------------------


async def test_max_attempts_fails_job_and_acks() -> None:
    events: list[str] = []
    worker, _queue, store, execute = _worker(
        events, job=_snapshot(status="running", attempts=_MAX_ATTEMPTS)
    )

    await worker.process_message(_received())

    assert events == ["load", "mark_failed", "delete"]
    assert execute.calls == []
    (error,) = store.failed_errors
    assert error["trigger"] == "error"
    assert "attempts" in error["detail"]


# ---------------------------------------------------------------------------
# The happy path: running → execute → completed → delete, in that order
# (Requirement 11.4 — delete only after the terminal transition commits).
# ---------------------------------------------------------------------------


async def test_success_path_ordering_and_result() -> None:
    events: list[str] = []
    result = {"ats": {"score": 88.0}}
    executor = FakeExecutor(events, result=result)
    worker, queue, store, _ = _worker(events, job=_snapshot(), executor=executor)

    await worker.process_message(_received())

    assert events == ["load", "mark_running", "execute", "mark_completed", "delete"]
    assert store.completed_results == [result]
    assert queue.deleted == ["rh-1"]


async def test_running_redelivery_below_cap_reexecutes() -> None:
    """11.5b: an in-flight job below the attempt cap is re-executed."""
    events: list[str] = []
    worker, _queue, _store, execute = _worker(events, job=_snapshot(status="running", attempts=1))

    await worker.process_message(_received())

    assert "execute" in events
    assert len(execute.calls) == 1


# ---------------------------------------------------------------------------
# Execution failure: mark_failed with the structured error, then delete.
# ---------------------------------------------------------------------------


async def test_execution_error_fails_job_with_structured_error_then_acks() -> None:
    events: list[str] = []
    error = {
        "trigger": "error",
        "detail": "pre-invocation validation failed: match_result_not_found",
    }
    executor = FakeExecutor(events, exc=JobExecutionError(error))
    worker, _queue, store, _ = _worker(events, job=_snapshot(), executor=executor)

    await worker.process_message(_received())

    assert events == ["load", "mark_running", "execute", "mark_failed", "delete"]
    assert store.failed_errors == [error]


async def test_unexpected_executor_crash_records_class_name_only() -> None:
    """An unexpected exception yields a PII-free error: class name, not str(exc)."""
    events: list[str] = []
    executor = FakeExecutor(events, exc=RuntimeError("resume text could leak through str()"))
    worker, _queue, store, _ = _worker(events, job=_snapshot(), executor=executor)

    await worker.process_message(_received())

    (error,) = store.failed_errors
    assert error == {"trigger": "error", "detail": "RuntimeError"}
    assert "resume text" not in json.dumps(error)


# ---------------------------------------------------------------------------
# Terminal-transition persistence failure: the message is NOT deleted
# (Requirement 11.4 — delete only after the terminal transition commits).
# ---------------------------------------------------------------------------


async def test_failed_transition_persist_failure_keeps_message() -> None:
    events: list[str] = []
    executor = FakeExecutor(events, exc=JobExecutionError({"trigger": "error", "detail": "x"}))
    worker, queue, _store, _ = _worker(
        events, job=_snapshot(), executor=executor, failed_exc=ConnectionError("db down")
    )

    await worker.process_message(_received())

    assert queue.deleted == []
    assert events == ["load", "mark_running", "execute", "mark_failed"]


async def test_completed_transition_persist_failure_keeps_message() -> None:
    events: list[str] = []
    worker, queue, _store, _ = _worker(
        events, job=_snapshot(), completed_exc=ConnectionError("db down")
    )

    await worker.process_message(_received())

    assert queue.deleted == []
    assert events == ["load", "mark_running", "execute", "mark_completed"]


# ---------------------------------------------------------------------------
# Transition races: a lost guarded transition acks safely.
# ---------------------------------------------------------------------------


async def test_lost_running_transition_acks_without_execution() -> None:
    """A concurrent worker won the transition — ack, never re-execute (11.5a)."""
    events: list[str] = []
    worker, _queue, _store, execute = _worker(
        events,
        job=_snapshot(),
        running_exc=InvalidJobTransitionError(job_id=_JOB_ID, target="running"),
    )

    await worker.process_message(_received())

    assert events == ["load", "mark_running", "delete"]
    assert execute.calls == []


async def test_lost_terminal_transition_still_acks() -> None:
    """The job is terminal either way — acknowledging is safe."""
    events: list[str] = []
    worker, queue, _store, _ = _worker(
        events,
        job=_snapshot(),
        completed_exc=InvalidJobTransitionError(job_id=_JOB_ID, target="completed"),
    )

    await worker.process_message(_received())

    assert events == ["load", "mark_running", "execute", "mark_completed", "delete"]
    assert queue.deleted == ["rh-1"]


# ---------------------------------------------------------------------------
# Max-attempts persistence failure keeps the message too.
# ---------------------------------------------------------------------------


async def test_max_attempts_persist_failure_keeps_message() -> None:
    events: list[str] = []
    worker, queue, _store, execute = _worker(
        events,
        job=_snapshot(status="running", attempts=_MAX_ATTEMPTS),
        failed_exc=ConnectionError("db down"),
    )

    await worker.process_message(_received())

    assert queue.deleted == []
    assert execute.calls == []
