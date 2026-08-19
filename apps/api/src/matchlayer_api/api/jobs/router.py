"""``Jobs_Router``: ``GET /api/v1/jobs/{id}`` (phase-4-agentic).

Pure HTTP-shape concerns only, mirroring ``api/matches/router.py``:
the single query delegates to
:func:`~matchlayer_api.services.agent_jobs.service.get_job_with_runs`,
the only module permitted to read ``agent_jobs`` + ``agent_runs`` on
behalf of the API.

Behaviour (design §7; Requirements 10.2, 10.3, 10.4, 10.6, 10.7, 12.4):

* **Owner-scoped read** — the lookup is scoped by the authenticated
  user id; a missing job, another account's job, and a malformed id
  all collapse to the same 404 ``not_found`` RFC 7807 envelope, so the
  existence of another user's job is never disclosed (Requirements
  10.4, 12.4).
* **Step derivation** — per-agent step statuses come solely from
  ``agent_runs`` rows: ``pending`` for an agent with no row yet, else
  that row's status (``completed`` / ``degraded`` / ``failed``). Runs
  arrive ordered ``created_at`` ascending, so on a redelivered job the
  latest invocation's row wins per agent (Requirement 10.2).
* **Terminal payloads** — ``result`` (the AnalysisResult) is present
  iff ``completed``; ``error`` (structured, PII-free, display-safe) is
  present iff ``failed`` (Requirement 10.3).
* **Timestamps** — ISO 8601 UTC with the ``Z`` suffix; ``started_at``
  and ``completed_at`` are null until their transitions record them
  (Requirement 10.2, ``conventions.md``).
* **Rate limit** — ``MATCHLAYER_AGENT_JOB_POLL_RATE_LIMIT_PER_MINUTE``
  (default 120/min) per user via the shared ``user_rate_limit``
  dependency: 429 ``rate_limited`` on breach, fail-closed 503 when
  Redis is unreachable (Requirement 10.7).
* **Headers** — ``X-Robots-Tag: noindex, nofollow`` on every response
  via the ``ApiNoIndexMiddleware`` covering ``/api/v1/*``
  (Requirement 10.6).

Requirements covered: 10.2, 10.3, 10.4, 10.6, 10.7, 12.4.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from matchlayer_api.api.jobs.schemas import (
    AGENT_STEP_ORDER,
    JobErrorOut,
    JobResponse,
    JobStepOut,
)
from matchlayer_api.core.db import get_session
from matchlayer_api.core.dependencies import get_current_user, user_rate_limit
from matchlayer_api.core.errors import NotFoundError
from matchlayer_api.db.models import User
from matchlayer_api.ml.agents.state import AnalysisResult
from matchlayer_api.services.agent_jobs.service import JobWithRuns, get_job_with_runs

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])

# Module-scope dependency aliases (ruff B008 pattern shared with the
# other routers).
_SessionDep = Annotated[AsyncSession, Depends(get_session)]
_CurrentUser = Annotated[User, Depends(get_current_user)]

# Per-user poll budget: MATCHLAYER_AGENT_JOB_POLL_RATE_LIMIT_PER_MINUTE,
# default 120/min (Requirement 10.7) — sized for the frontend's 1-5 s
# polling interval with headroom.
_JobPollRateLimit = Depends(user_rate_limit("job_poll"))


def _job_response(job_with_runs: JobWithRuns) -> JobResponse:
    """Project one owner-scoped job + its runs onto :class:`JobResponse`.

    Steps are derived solely from the ``agent_runs`` rows (Requirement
    10.2): the run list arrives ordered ``created_at`` ascending (ties
    by time-ordered UUIDv7 id), so overwriting per agent name leaves the
    *latest* invocation's status — the correct view for a job that was
    redelivered and re-executed (Requirement 11.5b). An agent with no
    row yet reports ``pending``. Rows whose ``agent_name`` is outside
    the five known nodes are ignored rather than failing the read.

    ``result`` is revalidated through :class:`AnalysisResult` — the
    exact model the worker serialized into ``result_json`` — so the
    response body always matches the OpenAPI schema the codegen
    consumes. ``error`` validates tolerantly into :class:`JobErrorOut`
    (defaults + ignored unknowns), guaranteeing a display-safe envelope
    even for a failed job with a sparse error document.
    """
    job = job_with_runs.job

    latest_status_by_agent: dict[str, str] = {}
    for run in job_with_runs.runs:
        latest_status_by_agent[run.agent_name] = run.status

    steps = [
        JobStepOut.model_validate(
            {
                "agent_name": agent_name,
                "status": latest_status_by_agent.get(agent_name, "pending"),
            }
        )
        for agent_name in AGENT_STEP_ORDER
    ]

    # ``result`` present iff completed; ``error`` present iff failed
    # (Requirement 10.3). The status guard — not mere column nullness —
    # is what enforces the "iff".
    result: AnalysisResult | None = None
    if job.status == "completed" and job.result_json is not None:
        result = AnalysisResult.model_validate(job.result_json)

    error: JobErrorOut | None = None
    if job.status == "failed":
        error = JobErrorOut.model_validate(job.error_json or {})

    return JobResponse.model_validate(
        {
            "id": str(job.id),
            "status": job.status,
            "created_at": job.created_at,
            "started_at": job.started_at,
            "completed_at": job.completed_at,
            "steps": steps,
            "result": result,
            "error": error,
        }
    )


@router.get(
    "/{job_id}",
    response_model=JobResponse,
    dependencies=[_JobPollRateLimit],
)
async def get_job(
    job_id: str,
    user: _CurrentUser,
    session: _SessionDep,
) -> JobResponse:
    """Return one owned Agent_Job with per-agent step statuses.

    A missing job, a job owned by another User_Account, and a
    syntactically invalid id all yield the identical ``not_found``
    envelope (Requirements 10.4, 12.4 — ownership indistinguishability;
    the same malformed-id-as-404 mapping the matches router applies).
    """
    try:
        parsed = UUID(job_id)
    except ValueError as exc:
        raise NotFoundError("Job not found.") from exc

    job_with_runs = await get_job_with_runs(session, user_id=user.id, job_id=parsed)
    return _job_response(job_with_runs)


__all__ = ["router"]
