"""Agent_Run persistence — exactly one immutable row per node invocation.

This is the ONLY module in the API that writes ``agent_runs`` rows
(single-writer discipline, mirroring ``service.py`` for ``agent_jobs``
and the model docstring in ``db/models.py``). Two layers:

* :func:`persist_agent_run` — the staging primitive. Builds one
  :class:`~matchlayer_api.db.models.AgentRun` from the JSON-serialized
  Pydantic input/output state and stages it on the caller's session
  (flush, never commit — the caller owns the transaction). Used
  directly by the Agent_Worker for the Synthesizer ``failed`` row of
  Requirement 12.7, which the agent lifecycle callback below never
  produces (a Synthesizer failure propagates out of
  ``BaseAgent.__call__`` before its persistence step runs).
* :func:`build_persist_agent_run` — the production adapter behind the
  :data:`~matchlayer_api.ml.agents.base.PersistAgentRun` callback that
  ``BaseAgent.__call__`` invokes once per node invocation (Requirement
  12.2). Bound to one Agent_Job id and an ``async_sessionmaker``, it
  opens a **fresh short transaction per invocation and commits it**:
  run rows must be visible to ``GET /api/v1/jobs/{id}`` while the job
  is still ``running`` — the per-agent step statuses of Requirement
  10.2 are derived solely from committed ``agent_runs`` rows — so
  staging them on the worker's job-transition session (committed only
  at the terminal transition, Requirement 11.4) would hide every step
  until the job ended.

Invariants this writer enforces:

* **Exactly one row per invocation** (Requirement 12.2): the lifecycle
  calls the callback exactly once per node — normal completion,
  degraded path, and Agent_Cache hit alike — and this module exposes
  no update or delete: rows are immutable once written and retained
  for Phase 5 evaluation replay (Requirement 12.6).
* **``failure_reason_json`` is null iff status is ``completed``**
  (Requirement 12.1 / the task's "null iff completed"): a ``completed``
  row with a reason and a ``degraded``/``failed`` row without one are
  both rejected loudly — the schema cannot express this cross-column
  rule, so the single writer does.
* **Redacted/derived content by construction** (Requirement 12.3):
  ``input_state_json`` is the serialized
  :class:`~matchlayer_api.ml.agents.state.AgentState`, which carries
  identifiers plus PII_Redactor-transformed or derived content only —
  it has no field for raw ``extracted_text`` — and every agent output
  schema inherits that property. Nothing here inspects or filters
  content; the guarantee is structural.
* **Persistence is NOT best-effort.** Unlike checkpoints (Requirement
  2.4) there is no swallow-and-warn: Requirement 12.2's "debugging and
  replay never depend on ephemeral logs" makes the Agent_Run row a
  hard deliverable, so a database failure here propagates out of the
  node lifecycle rather than silently dropping the record.

Design reference: phase-4-agentic design §8 (``runs.py``) and §2 (the
``BaseAgent`` lifecycle). Requirements covered: 12.2, 12.3, 12.7.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Final, Literal
from uuid import UUID

import structlog
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from uuid_utils.compat import uuid7

from matchlayer_api.db.models import AgentRun
from matchlayer_api.ml.agents.base import PersistAgentRun
from matchlayer_api.ml.agents.state import AgentCompletion, AgentState, FailureDetail

__all__ = [
    "RunStatus",
    "build_persist_agent_run",
    "persist_agent_run",
]

_log = structlog.get_logger(__name__)

#: The closed ``agent_runs.status`` vocabulary (Requirement 12.1's CHECK
#: constraint). ``failed`` is reachable only through the Synthesizer
#: failure path of Requirement 12.7 — the worker persists it directly via
#: :func:`persist_agent_run`; the :data:`PersistAgentRun` callback maps
#: only from :class:`AgentCompletion` (``completed``/``degraded``).
RunStatus = Literal["completed", "degraded", "failed"]

# AgentCompletion → column value. A dict (rather than `.value`) keeps the
# mapping total and mypy-checkable against the RunStatus literal.
_COMPLETION_TO_STATUS: Final[dict[AgentCompletion, RunStatus]] = {
    AgentCompletion.COMPLETED: "completed",
    AgentCompletion.DEGRADED: "degraded",
}


def _now() -> datetime:
    """Return a timezone-aware "now" in UTC.

    Centralised (mirroring ``service.py``) so ``created_at`` is a UTC
    timestamptz per Requirement 12.1 without relying on the column's
    server default — which fakes in unit tests don't evaluate. Tests
    freeze time by monkey-patching ``services.agent_jobs.runs._now``.
    """
    return datetime.now(UTC)


async def persist_agent_run(
    session: AsyncSession,
    *,
    job_id: UUID,
    agent_name: str,
    input_state: AgentState,
    output: BaseModel,
    status: RunStatus,
    failure_reason: FailureDetail | None,
    latency_ms: int,
) -> AgentRun:
    """Stage exactly one immutable ``agent_runs`` row (Requirement 12.2).

    Input and output state are stored as their JSON-serialized Pydantic
    documents (``model_dump(mode="json")``) — redacted/derived content
    by construction (Requirement 12.3), and field-for-field sufficient
    for the deterministic re-execution and prompt-reconstruction checks
    of Requirement 12.5.

    Work is staged on the caller's session (flush, never commit),
    matching every service in this package tree — the adapter from
    :func:`build_persist_agent_run` supplies its own per-invocation
    transaction; the worker's Synthesizer-failure path (Requirement
    12.7) stages the ``failed`` row on its own session and commits it
    before recording the terminal Job_Status.

    Args:
        session: The caller's active session.
        job_id: The enclosing Agent_Job id (``agent_runs.job_id`` FK).
        agent_name: The node name (one of the five graph node names).
        input_state: The :class:`AgentState` the node was invoked with.
        output: The node's Pydantic output — normal or Degraded_Output
            (same schema either way, Requirement 8.2).
        status: ``completed`` | ``degraded`` | ``failed``.
        failure_reason: The structured PII-free reason — required for
            ``degraded``/``failed``, forbidden for ``completed``.
        latency_ms: Node-invocation-start → output-return milliseconds,
            measured by the lifecycle (Requirement 12.2).

    Returns:
        The staged (flushed) :class:`AgentRun`.

    Raises:
        ValueError: The "failure reason is null iff completed" invariant
            was violated — always a caller bug, never data-dependent.
    """
    if (failure_reason is None) != (status == "completed"):
        raise ValueError(
            f"agent_runs invariant violated: failure_reason must be null iff "
            f"status is 'completed' (got status={status!r}, "
            f"failure_reason={'None' if failure_reason is None else 'set'})."
        )
    run = AgentRun(
        id=uuid7(),
        job_id=job_id,
        agent_name=agent_name,
        input_state_json=input_state.model_dump(mode="json"),
        output_state_json=output.model_dump(mode="json"),
        latency_ms=latency_ms,
        status=status,
        failure_reason_json=(
            None if failure_reason is None else failure_reason.model_dump(mode="json")
        ),
        created_at=_now(),
    )
    session.add(run)
    await session.flush()
    return run


def build_persist_agent_run(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    job_id: UUID,
) -> PersistAgentRun:
    """Build the production ``PersistAgentRun`` callback for one Agent_Job.

    The returned coroutine function matches the positional callback
    signature ``BaseAgent.__call__`` invokes — ``(agent_name,
    input_state, output, status, failure_reason, latency_ms)`` — and is
    injected via ``AgentDeps`` at graph-composition time, so the agent
    layer never touches the database directly.

    Each invocation opens a fresh session from *session_factory* and
    commits its single row immediately: parallel branch nodes (Skill_Gap
    ∥ Improvement, Requirement 1.5) persist concurrently without sharing
    a session (``AsyncSession`` is not concurrency-safe), and each
    committed row becomes visible to job polling while the run is still
    in flight (Requirement 10.2's per-agent step statuses).

    Args:
        session_factory: The application ``async_sessionmaker``.
        job_id: The Agent_Job every persisted row belongs to.

    Returns:
        A :data:`PersistAgentRun` callback bound to *job_id*.
    """

    async def _persist(
        agent_name: str,
        input_state: AgentState,
        output: BaseModel,
        status: AgentCompletion,
        failure_reason: FailureDetail | None,
        latency_ms: int,
    ) -> None:
        async with session_factory() as session, session.begin():
            await persist_agent_run(
                session,
                job_id=job_id,
                agent_name=agent_name,
                input_state=input_state,
                output=output,
                status=_COMPLETION_TO_STATUS[status],
                failure_reason=failure_reason,
                latency_ms=latency_ms,
            )
        _log.info(
            "agent_run_persisted",
            job_id=str(job_id),
            agent_name=agent_name,
            status=status.value,
            latency_ms=latency_ms,
        )

    return _persist
