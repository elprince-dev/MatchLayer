"""Unit tests for the SkillGapAgent (phase-4-agentic task 5.3).

Covers the contract in ``ml/agents/skill_gap_agent.py`` with fake deps
(no models, no database, no LLM):

* Normal path — pure function of the Candidate_Profile, Match_Result
  skill snapshot, and JD skills; classification and prioritization via
  the ``gap_rules`` free functions (Requirement 5.1 as amended:
  profile-presence counts as coverage, so only ``missing`` entries are
  produced); flag COMPLETED.
* Full coverage — empty gap list is a valid, non-degraded result and
  never routes to the degraded path (Requirement 5.7).
* Degraded profile input — report derived from the Match_Result skill
  analysis alone (degraded profile's skills ignored, so only matched
  skills count as coverage), ``derived_from_degraded_input=True`` with
  ``degraded=False`` (Requirement 5.4).
* Missing profile/snapshot → the lifecycle degrades to the persisted
  missing skills, each ``missing``, ranked sequentially in persisted
  order without the prioritization rule, ``degraded=True``
  (Requirement 5.5); missing snapshot falls through to the minimal
  output (Requirement 8.6).
* Output lands on the ``skill_gap_report`` state field (Requirement 5.6).
* Hierarchy contract — extends ``DeterministicAgent``, does not override
  ``__call__``, no LLM imports (Requirement 1.6).
"""

from __future__ import annotations

from pathlib import Path

from opentelemetry.trace import NoOpTracer
from pydantic import BaseModel

from matchlayer_api.ml.agents.base import AgentDeps, BaseAgent
from matchlayer_api.ml.agents.deterministic_agent import DeterministicAgent
from matchlayer_api.ml.agents.skill_gap_agent import SkillGapAgent
from matchlayer_api.ml.agents.state import (
    AgentCompletion,
    AgentState,
    AgentStatusFlag,
    CandidateProfile,
    FailureDetail,
    MatchSnapshot,
    SkillGapReport,
)


class _FakeClock:
    def monotonic(self) -> float:
        return 0.0


async def _persist_noop(
    agent_name: str,
    state: AgentState,
    output: BaseModel,
    status: AgentCompletion,
    reason: FailureDetail | None,
    latency_ms: int,
) -> None:
    return None


def _deps() -> AgentDeps:
    return AgentDeps(
        node_timeout_s=5.0,
        persist_agent_run=_persist_noop,
        tracer=NoOpTracer(),
        clock=_FakeClock(),
    )


def _snapshot(
    *,
    matched_skills: list[str] | None = None,
    missing_skills: list[str] | None = None,
) -> MatchSnapshot:
    return MatchSnapshot(
        score=71.5,
        breakdown={"similarity": 0.6, "keyword": 0.8},
        scorer_version="2.0.0+lexv2+emb-minilm+spacy-3.8",
        matched_skills=matched_skills if matched_skills is not None else ["python"],
        missing_skills=missing_skills if missing_skills is not None else ["kubernetes", "go"],
        suggestions=["Add Kubernetes experience"],
    )


def _profile(*, skills: list[str] | None = None, degraded: bool = False) -> CandidateProfile:
    return CandidateProfile(
        skills=skills if skills is not None else ["python", "docker"],
        degraded=degraded,
    )


def _state(
    *,
    jd_skills: list[str] | None = None,
    profile: CandidateProfile | None = None,
    snapshot: MatchSnapshot | None = None,
    with_profile: bool = True,
    with_snapshot: bool = True,
) -> AgentState:
    return AgentState(
        job_id="job-1",
        match_id="match-1",
        user_id="user-1",
        job_description_skills=jd_skills if jd_skills is not None else [],
        candidate_profile=(profile or _profile()) if with_profile else None,
        match_snapshot=(snapshot or _snapshot()) if with_snapshot else None,
    )


def _report_of(update: dict[str, object]) -> SkillGapReport:
    report = update["skill_gap_report"]
    assert isinstance(report, SkillGapReport)
    return report


def _flag_of(update: dict[str, object]) -> AgentStatusFlag:
    status_map = update["agent_status"]
    assert isinstance(status_map, dict)
    flag = status_map["skill_gap"]
    assert isinstance(flag, AgentStatusFlag)
    return flag


class TestNormalPath:
    async def test_classifies_and_prioritizes_jd_skills(self) -> None:
        # Requirement 5.1/5.2 (amended rule): docker is in the profile →
        # covered (no entry); python is matched → covered (no entry);
        # kubernetes is in neither → missing. Ranks 1..n.
        agent = SkillGapAgent(_deps())
        update = await agent(
            _state(
                jd_skills=["python", "docker", "kubernetes"],
                profile=_profile(skills=["python", "docker"]),
                snapshot=_snapshot(matched_skills=["python"]),
            )
        )

        report = _report_of(update)
        assert [(g.skill, g.classification, g.rank) for g in report.gaps] == [
            ("kubernetes", "missing", 1),
        ]
        assert report.degraded is False
        assert report.derived_from_degraded_input is False
        assert _flag_of(update).status is AgentCompletion.COMPLETED

    async def test_jd_occurrence_count_drives_priority(self) -> None:
        # Requirement 5.2: descending JD occurrence count within a class.
        agent = SkillGapAgent(_deps())
        update = await agent(
            _state(
                jd_skills=["go", "rust", "go"],
                profile=_profile(skills=[]),
                snapshot=_snapshot(matched_skills=[]),
            )
        )
        report = _report_of(update)
        assert [(g.skill, g.rank) for g in report.gaps] == [("go", 1), ("rust", 2)]


class TestFullCoverage:
    async def test_empty_gap_list_is_valid_and_not_degraded(self) -> None:
        # Requirement 5.7: every JD skill covered → empty gaps, COMPLETED,
        # never the degraded path.
        agent = SkillGapAgent(_deps())
        update = await agent(
            _state(
                jd_skills=["python", "docker"],
                profile=_profile(skills=["docker"]),
                snapshot=_snapshot(matched_skills=["python"]),
            )
        )

        report = _report_of(update)
        assert report.gaps == []
        assert report.degraded is False
        flag = _flag_of(update)
        assert flag.status is AgentCompletion.COMPLETED
        assert flag.failure_reason is None

    async def test_empty_jd_skill_list_is_valid(self) -> None:
        # Degenerate full coverage: nothing extracted from the JD.
        agent = SkillGapAgent(_deps())
        update = await agent(_state(jd_skills=[]))
        report = _report_of(update)
        assert report.gaps == []
        assert report.degraded is False
        assert _flag_of(update).status is AgentCompletion.COMPLETED


class TestDegradedProfileInput:
    async def test_derives_from_match_result_alone(self) -> None:
        # Requirement 5.4: degraded profile → its skill list is ignored, so
        # `docker` (in the degraded profile, unmatched) is not covered and
        # classifies missing; matched skills still cover.
        agent = SkillGapAgent(_deps())
        update = await agent(
            _state(
                jd_skills=["python", "docker"],
                profile=_profile(skills=["python", "docker"], degraded=True),
                snapshot=_snapshot(matched_skills=["python"]),
            )
        )

        report = _report_of(update)
        assert [(g.skill, g.classification, g.rank) for g in report.gaps] == [
            ("docker", "missing", 1),
        ]
        assert report.derived_from_degraded_input is True
        # Degraded input is not degraded output (Requirements 5.4, 8.2).
        assert report.degraded is False
        assert _flag_of(update).status is AgentCompletion.COMPLETED


class TestDegradedPath:
    async def test_missing_profile_degrades_to_persisted_missing_skills(self) -> None:
        # Requirement 5.5: run raises (no profile in state) → persisted
        # missing skills, each `missing`, sequential ranks in persisted
        # order, no prioritization rule, degraded=True.
        agent = SkillGapAgent(_deps())
        update = await agent(
            _state(
                jd_skills=["python"],
                with_profile=False,
                snapshot=_snapshot(missing_skills=["kubernetes", "go"]),
            )
        )

        report = _report_of(update)
        assert [(g.skill, g.classification, g.rank) for g in report.gaps] == [
            ("kubernetes", "missing", 1),
            ("go", "missing", 2),
        ]
        assert report.degraded is True

        flag = _flag_of(update)
        assert flag.status is AgentCompletion.DEGRADED
        assert flag.failure_reason is not None
        assert flag.failure_reason.trigger == "error"

    async def test_missing_snapshot_yields_minimal_output(self) -> None:
        # Requirement 8.6: run and build_degraded both need the snapshot,
        # so a missing snapshot falls through to the minimal output.
        agent = SkillGapAgent(_deps())
        update = await agent(_state(with_profile=False, with_snapshot=False))

        report = _report_of(update)
        assert report.gaps == []
        assert report.degraded is True

        flag = _flag_of(update)
        assert flag.status is AgentCompletion.DEGRADED
        assert flag.failure_reason is not None
        assert flag.failure_reason.trigger == "degraded_construction_error"

    def test_build_degraded_preserves_persisted_order(self) -> None:
        # Requirement 5.5: persisted order, NOT the prioritization rule —
        # an alphabetically-later skill stays first.
        agent = SkillGapAgent(_deps())
        output = agent.build_degraded(
            _state(snapshot=_snapshot(missing_skills=["zsh", "aws", "make"]))
        )
        assert [(g.skill, g.classification, g.rank) for g in output.gaps] == [
            ("zsh", "missing", 1),
            ("aws", "missing", 2),
            ("make", "missing", 3),
        ]
        assert output.degraded is True

    def test_build_minimal_is_schema_valid_without_state(self) -> None:
        output = SkillGapAgent(_deps()).build_minimal()
        assert output.gaps == []
        assert output.degraded is True


class TestHierarchyContract:
    def test_extends_deterministic_agent(self) -> None:
        # Requirement 1.6: no LLM dependency by construction.
        assert issubclass(SkillGapAgent, DeterministicAgent)

    def test_does_not_override_call(self) -> None:
        assert SkillGapAgent.__call__ is BaseAgent.__call__

    def test_identity_classvars(self) -> None:
        assert SkillGapAgent.name == "skill_gap"
        assert SkillGapAgent.output_field == "skill_gap_report"

    async def test_output_lands_on_the_skill_gap_report_state_field(self) -> None:
        # Requirement 5.6: the Synthesizer consumes skill_gap_report from state.
        agent = SkillGapAgent(_deps())
        update = await agent(_state())
        assert set(update) == {"skill_gap_report", "agent_status"}
        parsed = AgentState.model_validate(
            {"job_id": "j", "match_id": "m", "user_id": "u", **update}
        )
        assert parsed.skill_gap_report is not None


class TestModuleImportBoundary:
    def test_module_source_has_no_llm_imports(self) -> None:
        """No import statement in skill_gap_agent.py reaches an LLM package.

        AST walk (not text grep) mirroring the DeterministicAgent module
        test, so docstring prose naming the banned packages cannot
        false-positive. Structural half of Requirement 1.6; the dynamic
        no-LLM-call property is task 5.7.
        """
        import ast

        import matchlayer_api.ml.agents.skill_gap_agent as module

        assert module.__file__ is not None
        path = Path(module.__file__)
        forbidden = ("matchlayer_api.ml.llm", "matchlayer_api.services.llm")
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        offenders: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                offenders.extend(
                    alias.name
                    for alias in node.names
                    if any(alias.name == p or alias.name.startswith(f"{p}.") for p in forbidden)
                )
            elif (
                isinstance(node, ast.ImportFrom)
                and node.level == 0
                and node.module is not None
                and any(node.module == p or node.module.startswith(f"{p}.") for p in forbidden)
            ):
                offenders.append(node.module)
        assert not offenders, (
            f"skill_gap_agent.py must import nothing from ml/llm/ or services/llm/ "
            f"(Requirement 1.6); found: {offenders}"
        )
