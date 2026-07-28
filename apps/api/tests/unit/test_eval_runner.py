"""Unit tests for the Eval_Runner (phase-2-nlp-embeddings, task 14.3).

Covers ``ml/evals/run_eyeball.py`` per the design's "Eval and pipeline
tooling" test strategy:

* **Report shape and side-by-side scores** (Req 11.2, 11.3): every
  ``PairResult`` carries both engines' scores, the band verdict, the
  matched/missing sets, and one met/violated entry per declared
  expectation; the rendered per-pair block shows the two scores side by
  side and names each violated expectation.
* **Malformed-file rejection** (Req 11.7): a schema-invalid file raises
  ``DatasetValidationError`` naming the file; ``main()`` exits non-zero
  (2) with the filename on stderr and emits **no pair results** — the
  whole dataset validates before any scoring happens.
* **Summary counts and exit codes on fixture datasets** (Req 11.8):
  ``main()`` returns 0 when every Phase 2 expectation passes and 1 when
  any is violated, with the summary counting pass/fail correctly. The
  engine builders are monkeypatched onto stub components so no model
  artifact is needed (per the design, the real model runs only in the
  task 14.4 evaluation gate).
* **Committed dataset content** (Req 11.1): ≥10 pairs, the required
  categories present, and the generic-term-leak pair's
  ``must_miss_skills`` covering "check" and "selection".

The runner lives in the repo-root ``ml/`` workspace (which the API package
must never import — the boundary tests pin that), so this test module loads
it by file path via ``importlib``; test code is outside that boundary.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import spacy

from _stub_encoder import Stub_Text_Encoder
from matchlayer_api.scoring.embedding import Embedding_Service
from matchlayer_api.scoring.lexicon import load_lexicon_v2
from matchlayer_api.scoring.scorer import Semantic_Match_Scorer
from matchlayer_api.scoring.semantic import Semantic_Scorer
from matchlayer_api.scoring.skills import Skill_Extractor

# ---------------------------------------------------------------------------
# Loading the runner module (file-path import; ml/ is not a package)
# ---------------------------------------------------------------------------


def _locate(relative: str) -> Path:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / relative
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"could not locate {relative} above {__file__}")


def _load_runner() -> ModuleType:
    path = _locate("ml/evals/run_eyeball.py")
    spec = importlib.util.spec_from_file_location("run_eyeball", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered so dataclass processing can resolve the module globals.
    sys.modules["run_eyeball"] = module
    spec.loader.exec_module(module)
    return module


_RUNNER: ModuleType = _load_runner()
_EYEBALL_DIR: Path = _locate("ml/evals/datasets/eyeball")


# ---------------------------------------------------------------------------
# Stub Phase 2 composition (no model artifact; design PBT/test strategy)
# ---------------------------------------------------------------------------

_LEXICON = load_lexicon_v2()
_STUB_ENCODER = Stub_Text_Encoder(dimension=32, max_tokens=64)
_EMBEDDING_SERVICE = Embedding_Service(_STUB_ENCODER)
_PHASE2 = Semantic_Match_Scorer(
    _LEXICON,
    Skill_Extractor(spacy.blank("en"), _LEXICON, max_keywords=50),
    Semantic_Scorer(),
    w_similarity=0.6,
    w_keyword=0.4,
    max_suggestions=10,
    scorer_version="2.0.0+lex.v2+emb.stub@rev+spacy.blank_en@0",
)
_PHASE1 = _RUNNER.build_phase1_scorer()

_RESUME = (
    "Backend engineer shipping python services with docker containers, "
    "postgresql schemas, and aws deployments guarded by pytest suites."
)
_JD = (
    "Hiring a backend engineer for python and docker work on aws with "
    "postgresql and disciplined pytest coverage."
)


def _phase2_band_for(resume_text: str, jd_text: str) -> str:
    """The band the stub composition actually produces for this pair."""
    result = _PHASE2.score(
        resume_text,
        jd_text,
        _EMBEDDING_SERVICE.embed(resume_text),
        _EMBEDDING_SERVICE.embed(jd_text),
    )
    return str(_RUNNER.band_of(result.score))


def _pair_doc(
    *,
    pair_id: str = "fixture-001",
    resume_text: str = _RESUME,
    jd_text: str = _JD,
    score_band: str | None = None,
    must_match: list[str] | None = None,
    must_miss: list[str] | None = None,
) -> dict[str, Any]:
    """A schema-valid pair document; band defaults to the actual stub band."""
    return {
        "id": pair_id,
        "label": f"fixture pair {pair_id}",
        "resume_text": resume_text,
        "jd_text": jd_text,
        "expected": {
            "score_band": score_band or _phase2_band_for(resume_text, jd_text),
            "must_match_skills": must_match if must_match is not None else ["python", "docker"],
            "must_miss_skills": must_miss if must_miss is not None else [],
        },
        "notes": "Synthetic fixture pair for the Eval_Runner unit tests.",
    }


def _write_dataset(directory: Path, docs: dict[str, dict[str, Any]]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    for filename, doc in docs.items():
        (directory / filename).write_text(json.dumps(doc), encoding="utf-8")
    return directory


def _stub_main_builders(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point ``main()`` at the stub composition instead of the real model."""
    monkeypatch.setattr(_RUNNER, "build_real_encoder", lambda model_path, model_name: _STUB_ENCODER)
    monkeypatch.setattr(_RUNNER, "build_phase2_scorer", lambda *args, **kwargs: _PHASE2)
    monkeypatch.setattr(_RUNNER, "build_phase1_scorer", lambda: _PHASE1)


# ---------------------------------------------------------------------------
# Committed dataset content (Requirement 11.1)
# ---------------------------------------------------------------------------


def test_committed_dataset_has_ten_plus_pairs_and_required_categories() -> None:
    """≥10 valid pairs covering every category the task names (Req 11.1)."""
    specs = _RUNNER.load_dataset(_EYEBALL_DIR)
    assert len(specs) >= 10

    filenames = {spec.filename for spec in specs}
    for required in (
        "strong_match.json",
        "clear_mismatch.json",
        "partial_match.json",
        "keyword_stuffed.json",
        "semantic_paraphrase.json",
        "generic_term_leak.json",
    ):
        assert required in filenames, f"required category file {required} missing"

    leak = next(spec for spec in specs if spec.filename == "generic_term_leak.json")
    assert "check" in leak.must_miss_skills
    assert "selection" in leak.must_miss_skills


# ---------------------------------------------------------------------------
# Malformed-file rejection (Requirement 11.7)
# ---------------------------------------------------------------------------


def test_malformed_file_raises_naming_the_file(tmp_path: Path) -> None:
    """A pair missing a required key is rejected with its filename."""
    doc = _pair_doc(score_band="high")
    del doc["expected"]["score_band"]
    dataset = _write_dataset(tmp_path / "eyeball", {"broken_pair.json": doc})

    with pytest.raises(_RUNNER.DatasetValidationError) as excinfo:
        _RUNNER.load_dataset(dataset)

    assert excinfo.value.filename == "broken_pair.json"
    assert "broken_pair.json" in str(excinfo.value)


def test_invalid_json_raises_naming_the_file(tmp_path: Path) -> None:
    """Unparseable JSON is rejected with its filename."""
    dataset = tmp_path / "eyeball"
    dataset.mkdir()
    (dataset / "garbage.json").write_text("{not json", encoding="utf-8")

    with pytest.raises(_RUNNER.DatasetValidationError) as excinfo:
        _RUNNER.load_dataset(dataset)

    assert excinfo.value.filename == "garbage.json"


def test_main_exits_2_on_malformed_dataset_with_no_pair_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Validation failure: exit 2, filename on stderr, zero pair output.

    One valid pair sits alongside the malformed one — the whole dataset is
    validated before any scoring, so even the valid pair produces no
    result (Req 11.7). The builders are stubbed defensively; a correct
    runner never reaches them on this path.
    """
    _stub_main_builders(monkeypatch)
    good = _pair_doc(pair_id="fixture-good")
    bad = _pair_doc(pair_id="fixture-bad")
    bad["expected"]["score_band"] = "colossal"  # not a valid band
    dataset = _write_dataset(tmp_path / "eyeball", {"aaa_good.json": good, "zzz_bad.json": bad})

    exit_code = _RUNNER.main(["--dataset-dir", str(dataset)])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "zzz_bad.json" in captured.err
    assert captured.out.strip() == ""  # no pair results before the abort


# ---------------------------------------------------------------------------
# Report shape and side-by-side scores (Requirements 11.2, 11.3)
# ---------------------------------------------------------------------------


def test_pair_result_shape_and_side_by_side_scores() -> None:
    """One evaluated pair carries both scores, band verdict, sets, checks."""
    spec = _RUNNER.validate_pair("fixture.json", _pair_doc(must_match=["python", "docker"]))

    result = _RUNNER.evaluate_pair(
        spec, phase1=_PHASE1, phase2=_PHASE2, embedding_service=_EMBEDDING_SERVICE
    )

    # Side-by-side scores from BOTH engines (Req 11.2).
    assert isinstance(result.phase1_score, int) and 0 <= result.phase1_score <= 100
    assert isinstance(result.phase2_score, int) and 0 <= result.phase2_score <= 100
    # Band verdict against the Phase 2 score.
    assert result.phase2_band == _RUNNER.band_of(result.phase2_score)
    assert result.band_met is True  # fixture band derived from the actual score
    # Matched/missing sets are skill terms.
    assert "python" in result.matched
    assert "docker" in result.matched
    # One met/violated entry per declared expectation (Req 11.3).
    assert {(e.kind, e.skill) for e in result.expectations} == {
        ("must_match", "python"),
        ("must_match", "docker"),
    }
    assert all(e.met for e in result.expectations)
    assert result.passed is True

    rendered = _RUNNER.render_pair(result)
    assert f"phase1={result.phase1_score}" in rendered
    assert f"phase2={result.phase2_score}" in rendered
    assert "band:" in rendered and "(met)" in rendered


def test_violations_are_reported_per_expectation() -> None:
    """A must_miss leak and a band miss each show as violations (Req 11.3)."""
    spec = _RUNNER.validate_pair(
        "fixture.json",
        _pair_doc(
            # Deliberately wrong band: pick a band the stub score is not in.
            score_band="low" if _phase2_band_for(_RESUME, _JD) != "low" else "high",
            must_match=["python"],
            must_miss=["docker"],  # docker IS matched → violated
        ),
    )

    result = _RUNNER.evaluate_pair(
        spec, phase1=_PHASE1, phase2=_PHASE2, embedding_service=_EMBEDDING_SERVICE
    )

    assert result.band_met is False
    by_key = {(e.kind, e.skill): e.met for e in result.expectations}
    assert by_key[("must_match", "python")] is True
    assert by_key[("must_miss", "docker")] is False
    assert result.passed is False

    rendered = _RUNNER.render_pair(result)
    assert "MISSED" in rendered  # the band verdict
    assert "must_miss: 'docker' (VIOLATED)" in rendered


def test_band_boundaries() -> None:
    """low 0-39 / medium 40-69 / high 70-100, inclusive at the edges."""
    assert _RUNNER.band_of(0) == "low"
    assert _RUNNER.band_of(39) == "low"
    assert _RUNNER.band_of(40) == "medium"
    assert _RUNNER.band_of(69) == "medium"
    assert _RUNNER.band_of(70) == "high"
    assert _RUNNER.band_of(100) == "high"
    with pytest.raises(ValueError, match="outside"):
        _RUNNER.band_of(101)


# ---------------------------------------------------------------------------
# Summary counts and exit codes on fixture datasets (Requirement 11.8)
# ---------------------------------------------------------------------------


def test_main_exits_0_when_all_expectations_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """All-pass fixture dataset → exit 0 with a full report + summary."""
    _stub_main_builders(monkeypatch)
    dataset = _write_dataset(
        tmp_path / "eyeball",
        {
            "pair_one.json": _pair_doc(pair_id="fixture-001"),
            "pair_two.json": _pair_doc(pair_id="fixture-002", must_match=["postgresql"]),
        },
    )

    exit_code = _RUNNER.main(["--dataset-dir", str(dataset)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "summary: 2 pairs, 2 passed, 0 failed" in captured.out
    assert captured.out.count("[PASS]") == 2


def test_main_exits_1_on_any_expectation_violation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """One violated pair among passing ones → full report, then exit 1."""
    _stub_main_builders(monkeypatch)
    violated = _pair_doc(pair_id="fixture-bad", must_miss=["python"])  # python IS matched
    dataset = _write_dataset(
        tmp_path / "eyeball",
        {
            "pair_good.json": _pair_doc(pair_id="fixture-good"),
            "pair_violated.json": violated,
        },
    )

    exit_code = _RUNNER.main(["--dataset-dir", str(dataset)])

    captured = capsys.readouterr()
    assert exit_code == 1
    # The full report still emits before the non-zero exit (Req 11.8).
    assert captured.out.count("[PASS]") == 1
    assert captured.out.count("[FAIL]") == 1
    assert "summary: 2 pairs, 1 passed, 1 failed" in captured.out


def test_run_report_summary_counts() -> None:
    """RunReport aggregates pass/fail counts from the pair results."""
    passing = _RUNNER.validate_pair("a.json", _pair_doc(pair_id="fixture-a"))
    failing = _RUNNER.validate_pair("b.json", _pair_doc(pair_id="fixture-b", must_miss=["python"]))

    report = _RUNNER.run_dataset(
        [passing, failing],
        phase1=_PHASE1,
        phase2=_PHASE2,
        embedding_service=_EMBEDDING_SERVICE,
    )

    assert len(report.results) == 2
    assert report.passed == 1
    assert report.failed == 1
    assert _RUNNER.render_summary(report) == "summary: 2 pairs, 1 passed, 1 failed"
