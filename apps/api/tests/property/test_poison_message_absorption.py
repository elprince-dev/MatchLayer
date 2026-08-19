"""Feature: phase-4-agentic — Property 17.

Property 17: Poison messages are absorbed.

    *For any* malformed message body — arbitrary non-message text, JSON of
    the wrong shape, a valid payload with an extra field, or a payload with
    a non-UUID identifier — or a well-formed message referencing an
    Agent_Job that does not exist, the worker never raises, emits a
    structured PII-free warning, deletes the message, executes nothing,
    and mutates no job state; subsequent messages keep processing.

**Validates: Requirements 11.7**

Driven over the established fake queue/store/executor pattern
(``tests/unit/test_agent_worker.py``): the store records every mutation
attempt and the executor records every invocation, so "executes nothing,
mutates nothing" is asserted directly. ``structlog``'s ``capture_logs``
captures the poison warning, asserting both that exactly the structured
``agent_worker_poison_message`` event fires and that no fragment of the
(potentially PII-bearing) message body leaks into any log event.

"Continues processing subsequent messages" is asserted structurally: each
example feeds a *batch* of poison messages followed by one valid,
executable message through the same worker instance, and the trailing
message must still execute normally.

Async note: each example drives its coroutines inside an
:class:`asyncio.Runner` (the suite's established pattern) so the event
loop closes deterministically under ``filterwarnings = ["error"]``.
"""

# Feature: phase-4-agentic, Property 17: Poison messages are absorbed

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID, uuid4

import structlog.testing
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from matchlayer_api.services.agent_jobs.queue import JobMessage, ReceivedMessage
from matchlayer_api.workers.agent_worker import (
    POISON_MESSAGE_EVENT,
    AgentWorker,
    JobSnapshot,
    parse_job_message,
)

_MAX_ATTEMPTS = 2


# ---------------------------------------------------------------------------
# Poison-body generators: every branch is structurally malformed.
# ---------------------------------------------------------------------------


def _valid_payload() -> dict[str, str]:
    return {"job_id": str(uuid4()), "match_id": str(uuid4()), "user_id": str(uuid4())}


@st.composite
def _extra_field_bodies(draw: st.DrawFn) -> str:
    """A valid payload plus an extra field (``extra="forbid"`` rejects it)."""
    payload: dict[str, Any] = dict(_valid_payload())
    key = draw(st.text(min_size=1, max_size=20).filter(lambda k: k not in payload))
    payload[key] = draw(st.text(max_size=100))
    return json.dumps(payload)


@st.composite
def _bad_uuid_bodies(draw: st.DrawFn) -> str:
    """A valid payload with one identifier replaced by a non-UUID string."""
    payload = _valid_payload()
    field = draw(st.sampled_from(["job_id", "match_id", "user_id"]))
    bad = draw(st.text(max_size=40))
    assume(not _is_uuid(bad))
    payload[field] = bad
    return json.dumps(payload)


@st.composite
def _missing_field_bodies(draw: st.DrawFn) -> str:
    """A payload missing one required identifier."""
    payload = _valid_payload()
    del payload[draw(st.sampled_from(["job_id", "match_id", "user_id"]))]
    return json.dumps(payload)


@st.composite
def _wrong_shape_json_bodies(draw: st.DrawFn) -> str:
    """Arbitrary JSON documents of the wrong shape (lists, scalars, dicts)."""
    doc = draw(
        st.recursive(
            st.none() | st.booleans() | st.integers() | st.text(max_size=20),
            lambda inner: (
                st.lists(inner, max_size=3)
                | st.dictionaries(st.text(max_size=10), inner, max_size=3)
            ),
            max_leaves=8,
        )
    )
    body = json.dumps(doc)
    assume(parse_job_message(body) is None)  # exclude the astronomically unlikely valid hit
    return body


@st.composite
def _arbitrary_text_bodies(draw: st.DrawFn) -> str:
    """Arbitrary text that is not a valid JobMessage document."""
    body = draw(st.text(max_size=200))
    assume(parse_job_message(body) is None)
    return body


def _is_uuid(value: str) -> bool:
    try:
        UUID(value)
    except ValueError:
        return False
    return True


_poison_bodies = st.one_of(
    _arbitrary_text_bodies(),
    _wrong_shape_json_bodies(),
    _extra_field_bodies(),
    _bad_uuid_bodies(),
    _missing_field_bodies(),
)


# ---------------------------------------------------------------------------
# Fakes recording every interaction (the established pattern).
# ---------------------------------------------------------------------------


class _FakeQueue:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    async def delete(self, receipt_handle: str) -> None:
        self.deleted.append(receipt_handle)

    async def receive(self) -> list[ReceivedMessage]:  # pragma: no cover — loop-only
        return []


class _FakeStore:
    """Knows exactly one job; every mutation attempt is recorded."""

    def __init__(self, known_job: JobSnapshot) -> None:
        self._known_job = known_job
        self.mutations: list[str] = []
        self.completed_results: list[dict[str, Any]] = []

    async def load(self, job_id: UUID) -> JobSnapshot | None:
        return self._known_job if job_id == self._known_job.id else None

    async def mark_running(self, job_id: UUID) -> None:
        self.mutations.append("mark_running")

    async def mark_completed(self, job_id: UUID, result: dict[str, Any]) -> None:
        self.mutations.append("mark_completed")
        self.completed_results.append(result)

    async def mark_failed(self, job_id: UUID, error: dict[str, Any]) -> None:
        self.mutations.append("mark_failed")


class _FakeExecutor:
    def __init__(self) -> None:
        self.calls: list[JobMessage] = []

    async def __call__(self, message: JobMessage) -> dict[str, Any]:
        self.calls.append(message)
        return {"analysis": "ok"}


def _run_sync(coro_factory: Callable[[], Awaitable[None]]) -> None:
    with asyncio.Runner() as runner:
        runner.run(coro_factory())


# ---------------------------------------------------------------------------
# The property.
# ---------------------------------------------------------------------------


# Feature: phase-4-agentic, Property 17: Poison messages are absorbed
@settings(max_examples=150, deadline=None)
@given(
    poison_batch=st.lists(
        st.one_of(
            # Malformed body variants...
            _poison_bodies.map(lambda body: ("malformed", body)),
            # ...or a well-formed message referencing a nonexistent job.
            st.just(("job_not_found", None)),
        ),
        min_size=1,
        max_size=5,
    ),
)
def test_poison_messages_are_absorbed(poison_batch: list[tuple[str, str | None]]) -> None:
    """Every poison message is warned about, deleted, and side-effect free.

    Requirement 11.7: a malformed message or one referencing an Agent_Job
    that does not exist yields one structured PII-free warning and an
    acknowledge (delete) — never an exception, never an execution, never a
    job-state mutation — so a poison message can never crash-loop the
    worker; a subsequent valid message still processes normally.
    """
    known_job_id, match_id, user_id = uuid4(), uuid4(), uuid4()
    queue = _FakeQueue()
    store = _FakeStore(
        JobSnapshot(
            id=known_job_id, status="queued", attempts=0, match_id=match_id, user_id=user_id
        )
    )
    executor = _FakeExecutor()
    worker = AgentWorker(queue=queue, store=store, execute_job=executor, max_attempts=_MAX_ATTEMPTS)

    # Materialize the batch: bodies plus per-message receipt handles.
    messages: list[ReceivedMessage] = []
    poison_fragments: list[str] = []
    for index, (kind, body) in enumerate(poison_batch):
        if kind == "malformed":
            assert body is not None
            poison_fragments.append(body)
            messages.append(ReceivedMessage(body=body, receipt_handle=f"rh-{index}"))
        else:
            ghost = JobMessage(job_id=str(uuid4()), match_id=str(match_id), user_id=str(user_id))
            messages.append(
                ReceivedMessage(body=ghost.model_dump_json(), receipt_handle=f"rh-{index}")
            )

    async def _drive() -> None:
        for received in messages:
            # The absorption contract: process_message never raises.
            await worker.process_message(received)

    with structlog.testing.capture_logs() as captured:
        _run_sync(_drive)

    # Every poison message was acknowledged (deleted), in order.
    assert queue.deleted == [message.receipt_handle for message in messages]

    # Nothing executed, no job state mutated.
    assert executor.calls == []
    assert store.mutations == []

    # One structured poison warning per message, and no fragment of any
    # malformed body (potential PII) leaks into any captured log event.
    poison_events = [event for event in captured if event["event"] == POISON_MESSAGE_EVENT]
    assert len(poison_events) == len(messages)
    serialized_logs = json.dumps(captured, default=str)
    for body in poison_fragments:
        if len(body) >= 8:  # short fragments collide with legitimate log text
            assert body not in serialized_logs

    # The worker keeps consuming: a subsequent valid message executes.
    valid = JobMessage(job_id=str(known_job_id), match_id=str(match_id), user_id=str(user_id))
    _run_sync(
        lambda: worker.process_message(
            ReceivedMessage(body=valid.model_dump_json(), receipt_handle="rh-valid")
        )
    )
    assert len(executor.calls) == 1
    assert store.completed_results == [{"analysis": "ok"}]
    assert queue.deleted[-1] == "rh-valid"
