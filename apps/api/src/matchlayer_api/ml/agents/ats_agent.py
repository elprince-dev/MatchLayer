"""The ATS agent — deterministic score + Confidence_Level (phase-4-agentic).

:class:`ATSAgent` is the deterministic node that carries the Phase 2 ATS
score into the agent analysis (design section "3. The five concrete
agents"). It extends
:class:`~matchlayer_api.ml.agents.deterministic_agent.DeterministicAgent`
— no LLM dependency by construction (Requirement 1.6) — and never
reimplements scoring arithmetic: the composite score comes either from the
persisted Match_Result or from the Phase 2 ``Semantic_Match_Scorer``
(including its Degraded_Mode ladder), reached through the injected
:class:`ScorerAdapter` (Requirement 4.1).

The reuse-versus-rescore decision (Requirements 4.1, 4.3)
---------------------------------------------------------

``run`` compares ``match_snapshot.scorer_version`` against the adapter's
active Scorer_Version:

* **Equal** — the persisted score was produced by the currently active
  scorer: reuse the persisted score and breakdown with **no** scorer
  invocation (Requirement 4.3).
* **Different** — obtain a fresh score by invoking the Phase 2
  ``Semantic_Match_Scorer`` via the adapter. The adapter runs the full
  Phase 2 fallback ladder, so a rung failure yields a Phase 1-scored
  result (``semantic=False``) rather than an exception; only total
  scoring failure raises (Requirement 4.1).

Either way the output is tagged with a Confidence_Level derived by the
documented pure rule in :mod:`matchlayer_api.ml.agents.confidence`
(Requirement 4.2) over ``(semantic, resume_len, jd_len)``. The text
lengths come from the adapter — :class:`~matchlayer_api.ml.agents.state.AgentState`
deliberately carries no raw resume or job-description text (Requirement
1.3), so the observables live behind the same job-scoped handle that holds
the texts for scoring. A Degraded_Mode-produced score (``semantic=False``)
can never be tagged ``high`` (Requirement 4.5, enforced by the rule).

Degradation (Requirement 4.4): if scoring fails entirely — the adapter
raises after exhausting the ladder, or the node exceeds the per-node
timeout — ``BaseAgent.__call__`` routes to :meth:`ATSAgent.build_degraded`,
which carries the persisted Match_Result score fields with
``confidence="low"`` and ``degraded=True``, and the Agent_Graph continues.

Output (Requirement 4.6): every completion — normal or degraded — writes
an :class:`~matchlayer_api.ml.agents.state.ATSOutput` carrying the
composite score, the score breakdown, the Confidence_Level, and the
Scorer_Version that produced the score to the ``ats_output`` state field,
where the Synthesizer consumes it.

Import discipline: like its base module, this module imports nothing from
``ml/llm/`` or ``services/llm/`` — the structural half of Requirement 1.6.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from matchlayer_api.ml.agents.base import AgentDeps
from matchlayer_api.ml.agents.confidence import confidence_level
from matchlayer_api.ml.agents.deterministic_agent import DeterministicAgent
from matchlayer_api.ml.agents.state import AgentState, ATSOutput, MatchSnapshot

__all__ = ["ATSAgent", "ScoredMatch", "ScorerAdapter"]


@dataclass(frozen=True, slots=True)
class ScoredMatch:
    """One fresh scoring result from the adapter (Requirement 4.1).

    ``semantic`` records whether semantic scoring produced the score or a
    Degraded_Mode fallback rung did — the first observable input of the
    Confidence_Level rule (Requirement 4.2). ``scorer_version`` is the
    stamp of the engine that actually produced the score (the Phase 1
    version on a fallback rung), so the output's version always names its
    true producer (Requirement 4.6).
    """

    score: float
    breakdown: dict[str, float] = field(default_factory=dict)
    scorer_version: str = ""
    semantic: bool = False


class ScorerAdapter(Protocol):
    """Job-scoped handle onto the Phase 2 scoring machinery.

    The production implementation (constructed per job by the graph
    composition layer, ``ml/agents/graph.py``) binds the resume text and
    job-description text the Agent_Worker loaded before graph invocation —
    :class:`~matchlayer_api.ml.agents.state.AgentState` never carries them
    (Requirement 1.3) — and delegates scoring to the Phase 2
    ``Semantic_Match_Scorer`` with its full Degraded_Mode ladder, never
    reimplementing any scoring logic (Requirement 4.1). Tests inject fakes
    satisfying this protocol.
    """

    @property
    def active_scorer_version(self) -> str:
        """The currently active Scorer_Version (drives reuse, Req 4.3)."""
        ...

    @property
    def semantic_active(self) -> bool:
        """Whether the active scorer is the semantic one.

        Feeds the Confidence_Level rule's ``semantic`` input on the reuse
        path: a persisted score is reused only when its version equals the
        active version, so this flag states whether that reused score was
        produced by semantic scoring or by the Phase 1 engine.
        """
        ...

    @property
    def resume_len(self) -> int:
        """Resume text length in characters (Confidence_Level input)."""
        ...

    @property
    def jd_len(self) -> int:
        """Job-description text length in characters (Confidence_Level input)."""
        ...

    async def score(self) -> ScoredMatch:
        """Score the bound resume + job-description pair.

        Runs the Phase 2 ``Semantic_Match_Scorer`` with its Degraded_Mode
        ladder: a rung failure returns a Phase 1-scored :class:`ScoredMatch`
        with ``semantic=False`` rather than raising. Raises only on total
        scoring failure (Requirement 4.4).
        """
        ...


class ATSAgent(DeterministicAgent[ATSOutput]):
    """Deterministic ATS-score node (design "3. The five concrete agents").

    ``run`` reuses the persisted score when the active Scorer_Version
    already produced it, otherwise scores freshly via the injected
    :class:`ScorerAdapter`; either way the output carries the composite
    score, breakdown, Confidence_Level, and Scorer_Version (Requirements
    4.1, 4.3, 4.6). Timeout, degradation, Agent_Run persistence, and span
    emission are inherited unchanged from ``BaseAgent.__call__``.
    """

    name = "ats"
    output_field = "ats_output"

    def __init__(self, deps: AgentDeps, scorer: ScorerAdapter) -> None:
        super().__init__(deps)
        self._scorer = scorer

    # ---- pure agent logic --------------------------------------------------

    async def run(self, state: AgentState) -> ATSOutput:
        """Reuse-or-rescore, then attach the Confidence_Level.

        Raises on any failure (missing snapshot, total scoring failure) so
        ``BaseAgent.__call__`` classifies and routes to
        :meth:`build_degraded` (Requirement 4.4).
        """
        snapshot = _require_snapshot(state)
        if snapshot.scorer_version == self._scorer.active_scorer_version:
            # Reuse: the persisted score was produced by the active scorer —
            # no scorer invocation (Requirement 4.3).
            semantic = self._scorer.semantic_active
            score = snapshot.score
            breakdown = dict(snapshot.breakdown)
            scorer_version = snapshot.scorer_version
        else:
            scored = await self._scorer.score()
            semantic = scored.semantic
            score = scored.score
            breakdown = dict(scored.breakdown)
            scorer_version = scored.scorer_version
        return ATSOutput(
            score=score,
            breakdown=breakdown,
            confidence=confidence_level(semantic, self._scorer.resume_len, self._scorer.jd_len),
            scorer_version=scorer_version,
        )

    def build_degraded(self, state: AgentState) -> ATSOutput:
        """Persisted Match_Result score fields, ``confidence="low"`` (Req 4.4)."""
        snapshot = _require_snapshot(state)
        return ATSOutput(
            score=snapshot.score,
            breakdown=dict(snapshot.breakdown),
            confidence="low",
            scorer_version=snapshot.scorer_version,
            degraded=True,
        )

    def build_minimal(self) -> ATSOutput:
        """Last-resort schema-valid output (Requirement 8.6): no persisted data."""
        return ATSOutput(
            score=0.0,
            breakdown={},
            confidence="low",
            scorer_version="unknown",
            degraded=True,
        )


def _require_snapshot(state: AgentState) -> MatchSnapshot:
    """The state's MatchSnapshot, or raise.

    The Agent_Worker loads the persisted Match_Result projection before
    graph invocation, so a missing snapshot is a pre-validation gap — the
    raise routes through the standard degradation lifecycle rather than
    producing an output from nothing.
    """
    snapshot = state.match_snapshot
    if snapshot is None:
        msg = "AgentState carries no MatchSnapshot; the worker must load it before invocation"
        raise ValueError(msg)
    return snapshot
