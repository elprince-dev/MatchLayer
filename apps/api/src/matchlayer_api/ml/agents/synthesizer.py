"""The synthesizer — deterministic terminal assembly (phase-4-agentic).

:class:`SynthesizerAgent` is the sole join point and terminal node of the
Agent_Graph (design section "3. The five concrete agents"). It extends
:class:`~matchlayer_api.ml.agents.deterministic_agent.DeterministicAgent`
— no LLM dependency by construction (Requirement 1.6) — and assembles the
four upstream outputs already present in Agent_State into the single
:class:`~matchlayer_api.ml.agents.state.AnalysisResult` (Requirement 7.1).

Pure function (Requirement 7.3)
-------------------------------

``run`` is a pure function of exactly the state fields the four upstream
nodes wrote: ``(candidate_profile, ats_output, skill_gap_report,
improvement_report, agent_status)``. No I/O, no clock, no randomness —
field-for-field identical upstream outputs, status flags, and trace
metadata yield a field-for-field identical AnalysisResult across repeated
invocations. Latencies come from the lifecycle-recorded values the base
``__call__`` stamped onto each upstream
:class:`~matchlayer_api.ml.agents.state.AgentStatusFlag` — state metadata,
never a database read (Requirement 7.5).

Trace summaries (Requirements 7.2, 7.5): one
:class:`~matchlayer_api.ml.agents.state.AgentTraceSummary` per contributing
agent — name, completion status (normal or degraded) from the per-agent
state flags, invocation latency in milliseconds, and the structured
failure reason exactly when the agent degraded — so the Web_App can
visibly mark degraded sections. Never Restricted PII: state carries
redacted/derived content only (Requirement 1.3), so the assembled result
inherits that by construction.

The exception to degradation (Requirement 7.6)
----------------------------------------------

No final result can exist without synthesis, so this is the **only** node
whose failure fails the Agent_Job: :meth:`SynthesizerAgent._build_degraded_safely`
re-raises the original exception with a bare ``raise`` (sanctioned by its
base docstring — it is always called from within the ``except`` block of
``BaseAgent.__call__``), letting the failure propagate out of the graph so
the Agent_Worker transitions the job to ``failed`` after persisting the
Synthesizer's failed Agent_Run row (Requirement 12.7). ``build_degraded``
and ``build_minimal`` accordingly raise ``NotImplementedError`` and are
never reached.

Import discipline: like its base module, this module imports nothing from
``ml/llm/`` or ``services/llm/`` — the structural half of Requirement 1.6.
"""

from __future__ import annotations

from typing import Final, NoReturn

from matchlayer_api.ml.agents.deterministic_agent import DeterministicAgent
from matchlayer_api.ml.agents.state import (
    AgentState,
    AgentTraceSummary,
    AnalysisResult,
    FailureDetail,
)

__all__ = ["SynthesizerAgent"]

_UPSTREAM_AGENTS: Final[tuple[str, ...]] = ("resume_analysis", "ats", "skill_gap", "improvement")
"""The four contributing agents, in graph-topology order (Requirement 7.5:
one trace summary per contributing agent). The Synthesizer itself carries
no summary — its own status flag does not exist until after ``run``
returns, and a failure here fails the job instead (Requirement 7.6)."""


class SynthesizerAgent(DeterministicAgent[AnalysisResult]):
    """Deterministic terminal node (design "3. The five concrete agents").

    ``run`` is a pure assembly over Agent_State (Requirements 7.1, 7.3);
    the degradation lifecycle is deliberately disabled — failure here is
    the only single-node failure that fails the job (Requirement 7.6).
    Timeout, Agent_Run persistence, and span emission are inherited
    unchanged from ``BaseAgent.__call__``.
    """

    name = "synthesizer"
    output_field = "analysis_result"

    # ---- pure agent logic --------------------------------------------------

    async def run(self, state: AgentState) -> AnalysisResult:
        """Assemble the four upstream outputs plus per-agent trace summaries.

        Raises on any missing upstream output or status flag: every
        upstream node writes its output field and status flag on both its
        normal and degraded paths (Requirement 8.1), so an absence means a
        broken invocation — and with no inputs to assemble, the raise
        correctly propagates and fails the job (Requirement 7.6).
        """
        profile = state.candidate_profile
        ats = state.ats_output
        skill_gaps = state.skill_gap_report
        improvements = state.improvement_report
        if profile is None or ats is None or skill_gaps is None or improvements is None:
            missing = [
                field
                for field, value in (
                    ("candidate_profile", profile),
                    ("ats_output", ats),
                    ("skill_gap_report", skill_gaps),
                    ("improvement_report", improvements),
                )
                if value is None
            ]
            msg = f"AgentState is missing upstream outputs: {', '.join(missing)}"
            raise ValueError(msg)
        return AnalysisResult(
            ats=ats,
            skill_gaps=skill_gaps,
            improvements=improvements,
            profile=profile,
            agent_traces=[_trace_summary(state, agent_name) for agent_name in _UPSTREAM_AGENTS],
        )

    # ---- the exception to degradation (Requirement 7.6) --------------------

    def _build_degraded_safely(
        self, state: AgentState, reason: FailureDetail
    ) -> tuple[AnalysisResult, FailureDetail]:
        """Re-raise: a Synthesizer failure propagates and fails the job.

        Always invoked from within the ``except`` block of
        ``BaseAgent.__call__`` (see the base docstring, which sanctions
        exactly this override), so the bare ``raise`` re-raises the
        original exception — error, timeout, or output validation failure
        alike (Requirement 7.6).
        """
        del state, reason  # nothing to build — the failure is the outcome
        raise

    def build_degraded(self, state: AgentState) -> NoReturn:
        """Never reached: ``_build_degraded_safely`` re-raises first (Req 7.6)."""
        raise NotImplementedError("the Synthesizer has no degraded path (Requirement 7.6)")

    def build_minimal(self) -> NoReturn:
        """Never reached: ``_build_degraded_safely`` re-raises first (Req 7.6)."""
        raise NotImplementedError("the Synthesizer has no degraded path (Requirement 7.6)")


def _trace_summary(state: AgentState, agent_name: str) -> AgentTraceSummary:
    """One contributing agent's trace summary from its state flag (Req 7.5).

    Status and structured failure reason come straight from the per-agent
    flag the lifecycle wrote; latency is the lifecycle-recorded value the
    flag carries as state metadata (the same measurement persisted on the
    Agent_Run row). A missing flag raises — every upstream node writes its
    flag on both paths, so absence is a broken invocation and the raise
    fails the job (Requirement 7.6).
    """
    flag = state.agent_status.get(agent_name)
    if flag is None:
        msg = f"AgentState carries no status flag for upstream agent {agent_name!r}"
        raise ValueError(msg)
    return AgentTraceSummary(
        agent_name=agent_name,
        status=flag.status,
        latency_ms=flag.latency_ms,
        failure_reason=flag.failure_reason,
    )
