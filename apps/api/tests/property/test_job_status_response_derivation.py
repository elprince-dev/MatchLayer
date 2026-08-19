"""Feature: phase-4-agentic — Property 13.

# Feature: phase-4-agentic, Property 13: Job status response derivation

Property 13: Job status response derivation.

    *For any* Agent_Job state (one of the four Job_Status values) and any
    subset of persisted Agent_Run rows, ``GET /api/v1/jobs/{id}`` reports
    that status; a per-agent step status for each of the five agents
    equal to its run row's status or ``pending`` when no row exists; ISO
    8601 UTC ``Z`` timestamps with ``started_at``/``completed_at`` null
    exactly when unset; the ``Analysis_Result`` present iff ``completed``;
    and structured PII-free error details present iff ``failed``.

**Validates: Requirements 10.2, 10.3**

What is driven, and how
-----------------------
The unit under test is the jobs router's **projection function**
``_job_response`` — the single place the ``GET /api/v1/jobs/{id}``
response body is derived from an owned ``agent_jobs`` row plus its
``agent_runs`` rows (the ownership/lookup half of the endpoint is
Property 14's concern). Per the task, the projection is driven directly
with generated ORM rows — no database: :class:`AgentJob` /
:class:`AgentRun` instances are plain in-memory objects here, exactly
the shapes ``get_job_with_runs`` hands the router.

Hypothesis generates:

* the **Job_Status** (all four values), with column content decoupled
  from status where the "iff" matters: a non-completed job may carry a
  stale ``result_json`` (the projection must still omit ``result``,
  because the guard is the status, not column nullness), and a failed
  job may carry a sparse / unknown-keyed / absent ``error_json`` (the
  tolerant :class:`JobErrorOut` defaults must still yield a display-safe
  envelope);
* aware-UTC **timestamps**, each of ``started_at``/``completed_at``
  independently set or ``None``;
* any **multiset of Agent_Run rows** — zero or many rows per agent,
  including rows for unknown agent names (ignored rather than failing
  the read), in ascending ``created_at`` order exactly as the service
  returns them (ties broken by time-ordered UUIDv7 id) — so the
  redelivered-job case where one agent has several rows exercises the
  latest-row-wins rule.

Assertions per example: the echoed id/status; exactly five steps in
graph order with each step equal to the agent's **latest** run row's
status (or ``pending`` with no row); ``result`` present iff
``completed`` (revalidating as :class:`AnalysisResult`); ``error``
present iff ``failed`` (with safe defaults for sparse documents); and —
on the ``mode="json"`` serialization the response model produces — every
set timestamp rendered as ISO 8601 UTC with the ``Z`` suffix, and null
exactly when the column was unset.
"""

# Feature: phase-4-agentic, Property 13: Job status response derivation

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st
from uuid_utils.compat import uuid7

from matchlayer_api.api.jobs.router import _job_response
from matchlayer_api.api.jobs.schemas import AGENT_STEP_ORDER
from matchlayer_api.db.models import AgentJob, AgentRun
from matchlayer_api.ml.agents.state import AnalysisResult
from matchlayer_api.services.agent_jobs.service import JobWithRuns

# ---------------------------------------------------------------------------
# Row generators.
# ---------------------------------------------------------------------------

_JOB_STATUSES = ("queued", "running", "completed", "failed")
_RUN_STATUSES = ("completed", "degraded", "failed")

# Unknown node names must be ignored by the projection, not fail the read.
_UNKNOWN_AGENT_NAMES = ("legacy_agent", "renamed_node")

_BASE_INSTANT = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)

# Aware-UTC timestamps around the base instant (whole seconds keep the
# ISO expectations exact without microsecond-formatting concerns).
_aware_utc = st.integers(min_value=-86_400, max_value=86_400).map(
    lambda offset: _BASE_INSTANT + timedelta(seconds=offset)
)


def _minimal_analysis_result_json() -> dict[str, Any]:
    """A schema-valid AnalysisResult document, as the worker persists it."""
    return AnalysisResult.model_validate(
        {
            "ats": {
                "score": 72.0,
                "breakdown": {"similarity": 0.7},
                "confidence": "high",
                "scorer_version": "2.0.0+test",
            },
            "skill_gaps": {
                "gaps": [{"skill": "kubernetes", "classification": "missing", "rank": 1}]
            },
            "improvements": {"actions": [{"rank": 1, "text": "Add a Kubernetes project."}]},
            "profile": {"skills": ["python"]},
            "agent_traces": [
                {"agent_name": "resume_analysis", "status": "completed", "latency_ms": 10}
            ],
        }
    ).model_dump(mode="json")


# error_json variants a failed job may carry: the canonical shape, a
# sparse document, unknown keys, and an entirely absent column — the
# tolerant JobErrorOut must render a display-safe envelope for each.
_error_json = st.one_of(
    st.none(),
    st.just({}),
    st.just({"type": "enqueue_failed", "detail": "The analysis could not be queued."}),
    st.just({"type": "max_attempts_exhausted"}),
    st.just({"detail": "The analysis failed.", "unknown_key": ["ignored"]}),
)

# A stale result document that may sit on a NON-completed row (e.g. a
# hypothetical dirty write): the "present iff completed" guard must be
# the status, never mere column nullness.
_stale_result_json = st.one_of(st.none(), st.builds(_minimal_analysis_result_json))


@st.composite
def _jobs(draw: st.DrawFn) -> AgentJob:
    status = draw(st.sampled_from(_JOB_STATUSES))
    result_json: dict[str, Any] | None
    if status == "completed":
        # The worker always persists the AnalysisResult with the
        # completed transition (mark_completed sets both atomically).
        result_json = _minimal_analysis_result_json()
    else:
        result_json = draw(_stale_result_json)
    error_json = draw(_error_json) if status == "failed" else None
    return AgentJob(
        id=uuid7(),
        user_id=uuid7(),
        match_id=uuid7(),
        status=status,
        attempts=draw(st.integers(min_value=0, max_value=3)),
        created_at=draw(_aware_utc),
        started_at=draw(st.none() | _aware_utc),
        completed_at=draw(st.none() | _aware_utc),
        result_json=result_json,
        error_json=error_json,
    )


@st.composite
def _run_rows(draw: st.DrawFn, job_id: Any) -> list[AgentRun]:
    """Agent_Run rows in ascending created_at order (ties by UUIDv7 id).

    Zero or many rows per agent name — several rows for one agent model
    the redelivered-and-re-executed job of Requirement 11.5, where the
    latest invocation's row must win — plus optional rows for unknown
    agent names, which the projection ignores.
    """
    names = draw(
        st.lists(
            st.sampled_from(AGENT_STEP_ORDER + _UNKNOWN_AGENT_NAMES),
            min_size=0,
            max_size=12,
        )
    )
    rows: list[AgentRun] = []
    for index, name in enumerate(names):
        rows.append(
            AgentRun(
                id=uuid7(),
                job_id=job_id,
                agent_name=name,
                input_state_json={"job_id": str(job_id)},
                output_state_json={"degraded": False},
                latency_ms=draw(st.integers(min_value=0, max_value=60_000)),
                status=draw(st.sampled_from(_RUN_STATUSES)),
                failure_reason_json=None,
                created_at=_BASE_INSTANT + timedelta(seconds=index),
            )
        )
    return rows


@st.composite
def _job_with_runs(draw: st.DrawFn) -> JobWithRuns:
    job = draw(_jobs())
    runs = draw(_run_rows(job.id))
    return JobWithRuns(job=job, runs=runs)


def _iso_utc_z(value: datetime) -> str:
    """The ISO 8601 UTC ``Z`` rendering the response must carry."""
    return value.isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# The property.
# ---------------------------------------------------------------------------


# Feature: phase-4-agentic, Property 13: Job status response derivation
@settings(max_examples=200, deadline=None)
@given(job_with_runs=_job_with_runs())
def test_job_response_derivation(job_with_runs: JobWithRuns) -> None:
    """Steps solely from runs, terminal payload iff-rules, Z timestamps.

    Property 13 (Requirements 10.2, 10.3): for any Agent_Job row and any
    set of Agent_Run rows, the projection reports the job's status;
    exactly five per-agent steps in graph order, each ``pending`` when
    the agent has no run row and otherwise equal to the **latest** run
    row's status (unknown agent names ignored); ``result`` present iff
    ``completed``; ``error`` present iff ``failed`` (display-safe even
    for sparse documents); and every timestamp serialized ISO 8601 UTC
    with the ``Z`` suffix, null exactly when unset.
    """
    job = job_with_runs.job
    response = _job_response(job_with_runs)
    body = response.model_dump(mode="json")

    # --- id and status echo the row (Requirement 10.2) --------------------
    assert body["id"] == str(job.id)
    assert body["status"] == job.status

    # --- steps derived SOLELY from run rows: latest row wins, pending
    # when absent, unknown names ignored (Requirement 10.2) ----------------
    latest: dict[str, str] = {}
    for run in job_with_runs.runs:  # already ascending created_at, id
        latest[run.agent_name] = run.status
    expected_steps = [
        {"agent_name": name, "status": latest.get(name, "pending")} for name in AGENT_STEP_ORDER
    ]
    assert body["steps"] == expected_steps

    # --- result present iff completed (Requirement 10.3) -------------------
    if job.status == "completed":
        assert body["result"] is not None
        # The payload revalidates as the exact worker-serialized model.
        assert AnalysisResult.model_validate(body["result"]).model_dump(mode="json") == (
            job.result_json
        )
    else:
        # Even a stale result_json on a non-completed row must not leak:
        # the guard is the status, not column nullness.
        assert body["result"] is None

    # --- error present iff failed, display-safe defaults (Req 10.3) --------
    if job.status == "failed":
        error = body["error"]
        assert error is not None
        assert isinstance(error["type"], str) and error["type"]
        assert isinstance(error["detail"], str) and error["detail"]
        stored = job.error_json or {}
        assert error["type"] == stored.get("type", "job_failed")
        assert error["detail"] == stored.get("detail", "The analysis failed.")
    else:
        assert body["error"] is None

    # --- ISO 8601 UTC Z timestamps; null exactly when unset (Req 10.2) -----
    assert body["created_at"] == _iso_utc_z(job.created_at)
    assert body["created_at"].endswith("Z")
    for column, key in (("started_at", "started_at"), ("completed_at", "completed_at")):
        stored_value = getattr(job, column)
        if stored_value is None:
            assert body[key] is None
        else:
            assert body[key] == _iso_utc_z(stored_value)
            assert body[key].endswith("Z")
