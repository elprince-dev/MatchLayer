"""Pydantic v2 response models for the job-polling surface.

These models are the source of truth for the ``GET /api/v1/jobs/{id}``
section of the OpenAPI schema; the existing OpenAPI→TS/Zod codegen
(``pnpm codegen``, regenerated in task 13.1) exposes them to the
frontend Progress_UI (phase-4-agentic Requirement 10.8). Embedding
:class:`~matchlayer_api.ml.agents.state.AnalysisResult` directly means
the generated types carry the full typed result the frontend renders —
no hand-written frontend types (``conventions.md`` "Shared schemas").

Model coverage (design §7 "GET /api/v1/jobs/{id}" response sketch):

* :class:`JobStepOut` — one per-agent step ``{agent_name, status}``.
  ``status`` is derived solely from ``agent_runs`` rows: ``pending``
  for an agent with no row yet, else that row's status
  (``completed`` / ``degraded`` / ``failed``) — Requirement 10.2.
* :class:`JobErrorOut` — the structured, PII-free, display-safe error
  present iff Job_Status is ``failed`` (Requirements 10.3, 12.1). The
  fields carry safe defaults and unknown keys are ignored, so any
  historically persisted ``error_json`` shape still renders a safe
  envelope rather than a 500.
* :class:`JobResponse` — the full body: Job_Status, ISO 8601 UTC
  timestamps (Pydantic v2 serializes UTC-aware datetimes with the
  ``Z`` suffix per ``conventions.md``), the five steps, and the
  ``result`` / ``error`` fields (each present iff the corresponding
  terminal status, Requirement 10.3).

PRIVACY: every field is Internal-classified by construction — the
``AnalysisResult`` carries redacted/derived content only (Requirement
1.3) and ``JobErrorOut`` is built from the closed failure vocabulary.
No raw resume or job-description text can appear here.

Requirements covered: 10.2, 10.3, 10.8, 12.4.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from matchlayer_api.ml.agents.state import AnalysisResult

# The five Agent_Graph node names in graph (and display) order —
# START→{resume_analysis, ats}, resume_analysis→{skill_gap, improvement},
# join→synthesizer. Mirrors the agents' ``name`` ClassVars registered in
# ``ml/agents/graph.py``; the jobs router derives exactly one step per
# name (Requirement 10.2, design §7).
AGENT_STEP_ORDER: tuple[str, ...] = (
    "resume_analysis",
    "ats",
    "skill_gap",
    "improvement",
    "synthesizer",
)


class JobStepOut(BaseModel):
    """One per-agent step status (Requirement 10.2)."""

    model_config = ConfigDict(extra="forbid")

    agent_name: Literal[
        "resume_analysis",
        "ats",
        "skill_gap",
        "improvement",
        "synthesizer",
    ] = Field(
        description="The Agent_Graph node this step reports on.",
    )
    status: Literal["pending", "completed", "degraded", "failed"] = Field(
        description="Derived solely from agent_runs rows: 'pending' when "
        "no row exists yet for this agent, else that row's status.",
    )


class JobErrorOut(BaseModel):
    """Structured, display-safe job error — present iff status is 'failed'.

    Built from the ``error_json`` column, which the analyze endpoint's
    enqueue-failure compensation and the Agent_Worker populate from the
    closed, PII-free failure vocabulary (Requirements 10.3, 12.1).
    Defaults + ignored unknown keys make rendering tolerant to any
    historically persisted shape: a job read can never 500 because an
    older error document spelled its keys differently.
    """

    type: str = Field(
        default="job_failed",
        description="Machine-readable failure category (e.g. "
        "'enqueue_failed', 'max_attempts_exhausted').",
    )
    detail: str = Field(
        default="The analysis failed.",
        description="Display-safe human-readable summary. Never contains "
        "resume text, job-description text, or provider payloads.",
    )


class JobResponse(BaseModel):
    """Body of ``GET /api/v1/jobs/{id}`` (Requirements 10.2, 10.3).

    Timestamps serialize as ISO 8601 UTC with the ``Z`` suffix
    (``conventions.md``); ``started_at`` / ``completed_at`` are null
    until the corresponding Job_Status transition records them.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(
        description="UUIDv7 of the Agent_Job, encoded as a string.",
    )
    status: Literal["queued", "running", "completed", "failed"] = Field(
        description="The job's current Job_Status.",
    )
    created_at: datetime = Field(
        description="Job creation instant (ISO 8601 UTC, Z suffix).",
    )
    started_at: datetime | None = Field(
        default=None,
        description="First 'running' transition instant; null until the worker picks the job up.",
    )
    completed_at: datetime | None = Field(
        default=None,
        description="Terminal transition instant ('completed' or 'failed'); "
        "null while the job is non-terminal.",
    )
    steps: list[JobStepOut] = Field(
        description="Exactly five per-agent steps in graph order, statuses "
        "derived solely from agent_runs rows.",
    )
    result: AnalysisResult | None = Field(
        default=None,
        description="The AnalysisResult; present iff status is 'completed'.",
    )
    error: JobErrorOut | None = Field(
        default=None,
        description="Structured display-safe error; present iff status is 'failed'.",
    )


__all__ = [
    "AGENT_STEP_ORDER",
    "JobErrorOut",
    "JobResponse",
    "JobStepOut",
]
