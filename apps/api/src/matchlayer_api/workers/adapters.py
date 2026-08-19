"""Production adapters binding one Agent_Job's texts to Phase 2 machinery.

The agent layer deliberately never carries raw resume or job-description
text — :class:`~matchlayer_api.ml.agents.state.AgentState` has no field
for it (Requirement 1.3) — so the ATS_Agent reaches scoring through a
job-scoped handle that holds the texts privately. This module supplies
the two production pieces the Agent_Worker composes per job:

* :class:`WorkerScorerAdapter` — the production implementation of the
  :class:`~matchlayer_api.ml.agents.ats_agent.ScorerAdapter` protocol.
  Binds the resume text and job-description text loaded by the worker
  and delegates every scoring computation to the Phase 2
  ``Semantic_Match_Scorer`` with its Degraded_Mode ladder: any semantic
  rung failure (Degraded_Mode at startup, embed timeout, embed error,
  scoring error) completes through the untouched Phase 1 engine with
  ``semantic=False`` rather than raising (Requirement 4.1). No scoring
  arithmetic is reimplemented here.
* :func:`extract_job_description_skills` — runs the Phase 2
  ``Skill_Extractor`` over the Job_Description to populate the initial
  ``AgentState.job_description_skills`` (design §6 step 4). When the
  semantic pipeline is unavailable it falls back to the Phase 1
  ``Keyword_Analyzer``'s lexicon-derived analyzed set, so the
  Skill_Gap_Agent always receives canonical lexicon terms.

PII discipline: both pieces hold Restricted text in memory only for the
duration of one job and never log it — warnings carry exception class
names and identifiers only (``security.md``).

Design reference: phase-4-agentic design §3 (ATSAgent) and §6 (worker
step 4). Requirements covered: 4.1, 11.8 (worker-side text processing).
"""

from __future__ import annotations

import structlog

from matchlayer_api.config import Settings, get_settings
from matchlayer_api.ml import scorer_adapter
from matchlayer_api.ml.agents.ats_agent import ScoredMatch
from matchlayer_api.ml.semantic_adapter import embed_with_timeout, get_semantic_pipeline
from matchlayer_api.scoring.keyword_analyzer import Keyword_Analyzer
from matchlayer_api.scoring.lexicon import load_lexicon
from matchlayer_api.scoring.scorer import ScoreResult

__all__ = ["WorkerScorerAdapter", "extract_job_description_skills"]

_log = structlog.get_logger(__name__)

# The structured event emitted once per semantic-rung failure before the
# adapter completes through the Phase 1 engine (mirrors the Scoring_Service
# fallback-ladder events; carries an exception class name only, never text).
SCORING_FALLBACK_EVENT = "agent_semantic_scoring_fallback"


def _breakdown_dict(result: ScoreResult) -> dict[str, float]:
    """Project a ``ScoreResult`` breakdown onto the numeric-only dict shape.

    ``ATSOutput.breakdown`` / ``MatchSnapshot.breakdown`` are typed
    ``dict[str, float]``, so the non-numeric ``similarity_method`` marker
    the Phase 2 breakdown carries is deliberately excluded — the method is
    already encoded in the ``scorer_version`` stamp.
    """
    breakdown = result.breakdown
    return {
        "similarity_component": float(breakdown.similarity_component),
        "keyword_coverage_component": float(breakdown.keyword_coverage_component),
        "weight_similarity": float(breakdown.weight_similarity),
        "weight_keyword": float(breakdown.weight_keyword),
        "final_score": float(breakdown.final_score),
    }


class WorkerScorerAdapter:
    """Job-scoped :class:`ScorerAdapter` over the Phase 2 scoring machinery.

    Constructed once per Agent_Job by the worker's graph composition with
    the resume text and job-description text it loaded before invocation.
    The ATS_Agent consumes it through the protocol only; the texts never
    enter ``AgentState`` (Requirement 1.3).
    """

    def __init__(
        self,
        *,
        resume_text: str,
        job_description: str,
        settings: Settings | None = None,
    ) -> None:
        """Bind one job's texts; read the embed timeout from ``Settings``."""
        self._resume_text = resume_text
        self._job_description = job_description
        self._settings = settings if settings is not None else get_settings()

    @property
    def active_scorer_version(self) -> str:
        """The Scorer_Version of the engine a fresh score would come from.

        The semantic pipeline's composed v2 version when loaded, else the
        Phase 1 lexicon-derived version — exactly the stamp
        :meth:`score` would put on a fresh result, which is what drives
        the ATS_Agent's reuse-versus-rescore decision (Requirement 4.3).
        """
        pipeline = get_semantic_pipeline()
        if pipeline is not None:
            return pipeline.scorer.scorer_version
        return scorer_adapter.get_scorer().scorer_version

    @property
    def semantic_active(self) -> bool:
        """Whether the active scorer is the Phase 2 semantic one."""
        return get_semantic_pipeline() is not None

    @property
    def resume_len(self) -> int:
        """Resume text length in characters (Confidence_Level input)."""
        return len(self._resume_text)

    @property
    def jd_len(self) -> int:
        """Job-description length in characters (Confidence_Level input)."""
        return len(self._job_description)

    async def score(self) -> ScoredMatch:
        """Score the bound pair via Phase 2, degrading to Phase 1 on failure.

        The Degraded_Mode ladder (Requirement 4.1, mirroring the
        Scoring_Service's fallback decision tree): no loaded pipeline,
        an embed timeout, an embed error, or a semantic scoring error
        each complete through the untouched Phase 1 engine with
        ``semantic=False`` and the Phase 1 ``scorer_version`` stamp —
        one structured warning per fallback, exception class name only.
        Only a Phase 1 failure (total scoring failure) raises, routing
        the ATS_Agent to its degraded output (Requirement 4.4).
        """
        pipeline = get_semantic_pipeline()
        if pipeline is None:
            # Degraded_Mode: the once-at-startup ``model_load_failure``
            # event already covers it — no per-job warning noise.
            return self._phase1_scored()

        timeout_seconds = float(self._settings.embedding_timeout_seconds)
        try:
            resume_vector = await embed_with_timeout(self._resume_text, timeout_seconds)
            jd_vector = await embed_with_timeout(self._job_description, timeout_seconds)
            result = pipeline.scorer.score(
                self._resume_text, self._job_description, resume_vector, jd_vector
            )
        except Exception as exc:  # any semantic rung failure → Phase 1 rung
            _log.warning(SCORING_FALLBACK_EVENT, error_type=type(exc).__name__)
            return self._phase1_scored()

        return ScoredMatch(
            score=float(result.score),
            breakdown=_breakdown_dict(result),
            scorer_version=result.scorer_version,
            semantic=True,
        )

    def _phase1_scored(self) -> ScoredMatch:
        """Score via the untouched Phase 1 engine (``semantic=False``)."""
        result = scorer_adapter.score(self._resume_text, self._job_description)
        return ScoredMatch(
            score=float(result.score),
            breakdown=_breakdown_dict(result),
            scorer_version=result.scorer_version,
            semantic=False,
        )


def extract_job_description_skills(
    job_description: str,
    settings: Settings | None = None,
) -> list[str]:
    """The canonical skill terms found in ``job_description``.

    Runs the Phase 2 ``Skill_Extractor`` (through the loaded semantic
    pipeline) when available — the same extractor instance the scorer
    analyzes with — else the Phase 1 ``Keyword_Analyzer``'s analyzed set
    over the v1 lexicon. Either way the result is a list of canonical
    lexicon terms suitable for ``AgentState.job_description_skills``
    (each extractor dedupes, so occurrence counts are uniform — the
    gap-rule prioritization then resolves ties alphabetically).
    """
    pipeline = get_semantic_pipeline()
    if pipeline is not None:
        extractor = pipeline.scorer.skill_extractor
        return [keyword.term for keyword in extractor.extract(job_description)]
    cfg = settings if settings is not None else get_settings()
    analyzer = Keyword_Analyzer(load_lexicon(), max_keywords=cfg.match_max_keywords)
    return [keyword.term for keyword in analyzer.analyze("", job_description).analyzed]
