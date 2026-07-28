#!/usr/bin/env python3
"""Eval_Runner: score the eyeball dataset with both engines (task 14.2).

Anchors:
    * ``.kiro/specs/phase-2-nlp-embeddings/requirements.md`` → Requirement 11
      (11.2 both engines side by side, 11.3 per-pair expectations, 11.4
      imports only ``matchlayer_api.scoring``, 11.7 malformed-file rejection,
      11.8 non-zero exit on any Phase 2 expectation violation).
    * ``.kiro/specs/phase-2-nlp-embeddings/design.md`` → §13 "Eval_Runner"
      and decision D11 (the runner imports the scoring core; never the
      reverse).
    * ``ml/evals/datasets/README.md`` → the dataset pair schema this runner
      validates before scoring anything.

What this script does
---------------------
1. Loads every ``eyeball/*.json`` pair and validates it against the dataset
   README schema **first**. A malformed file aborts with an error naming the
   file and a non-zero exit before any pair result is emitted (Req 11.7).
2. Scores each pair with both engines (Req 11.2):
   * **Phase 2** — the ``Semantic_Match_Scorer`` over the real
     SentenceTransformer Embedding_Model and the real spaCy pipeline
     (``en_core_web_sm``), composed exactly as production composes them;
   * **Phase 1** — the untouched ``Match_Scorer`` (TF-IDF + keyword), the
     fallback engine.
3. Emits a per-pair report: both scores side by side, the Phase 2 band
   met/missed (low 0-39 / medium 40-69 / high 70-100), the matched/missing
   skill sets, and each ``must_match_skills`` / ``must_miss_skills``
   expectation met or violated (Req 11.2, 11.3).
4. Emits a summary with pass/fail counts and exits non-zero when any
   Phase 2 expectation is violated (Req 11.8) — CI-friendly.

Import boundary (Req 11.4, design D11): the only first-party imports are
``matchlayer_api.scoring.*``. The runner reads no ``matchlayer_api.config``
and touches no service/web/storage module; artifact identity comes from CLI
flags (or their environment-variable defaults). Heavy artifact imports
(sentence-transformers, spaCy) happen lazily inside the builder functions so
the module stays importable — and unit-testable with injected fixture
components — without the artifacts installed.

Usage
-----
Run from the API project so ``matchlayer_api`` is importable::

    cd apps/api
    uv run --no-sync python ../../ml/evals/run_eyeball.py \
        --model-path "$MATCHLAYER_EMBEDDING_MODEL_PATH"

Exit codes: ``0`` all Phase 2 expectations pass; ``1`` at least one
expectation violated; ``2`` dataset validation failed (malformed file).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from matchlayer_api.scoring.embedding import Embedding_Service, Text_Encoder
from matchlayer_api.scoring.lexicon import Skill_Lexicon, load_lexicon, load_lexicon_v2
from matchlayer_api.scoring.scorer import Match_Scorer, Semantic_Match_Scorer
from matchlayer_api.scoring.semantic import Semantic_Scorer
from matchlayer_api.scoring.skills import Skill_Extractor
from matchlayer_api.scoring.versioning import semantic_scorer_version

# ---------------------------------------------------------------------------
# Score bands (Requirement 11.2) and production-default composition knobs.
# ---------------------------------------------------------------------------

SCORE_BANDS: Final[dict[str, tuple[int, int]]] = {
    "low": (0, 39),
    "medium": (40, 69),
    "high": (70, 100),
}

# Mirror the production defaults (apps/api config): the eval must exercise
# the same composition users get, not a bespoke tuning.
DEFAULT_W_SIMILARITY: Final[float] = 0.6
DEFAULT_W_KEYWORD: Final[float] = 0.4
DEFAULT_MAX_KEYWORDS: Final[int] = 50
DEFAULT_MAX_SUGGESTIONS: Final[int] = 10
DEFAULT_MODEL_NAME: Final[str] = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_SPACY_PIPELINE: Final[str] = "en_core_web_sm"

# The dataset README schema: exactly these keys, with these shapes.
_REQUIRED_TOP_LEVEL: Final[frozenset[str]] = frozenset(
    {"id", "label", "resume_text", "jd_text", "expected", "notes"}
)
_REQUIRED_EXPECTED: Final[frozenset[str]] = frozenset(
    {"score_band", "must_match_skills", "must_miss_skills"}
)


class DatasetValidationError(Exception):
    """A dataset file does not conform to the README schema (Req 11.7)."""

    def __init__(self, filename: str, reason: str) -> None:
        self.filename = filename
        self.reason = reason
        super().__init__(f"{filename}: {reason}")


# ---------------------------------------------------------------------------
# Dataset loading + validation (Requirement 11.7)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PairSpec:
    """One validated dataset pair."""

    filename: str
    pair_id: str
    label: str
    resume_text: str
    jd_text: str
    score_band: str
    must_match_skills: tuple[str, ...]
    must_miss_skills: tuple[str, ...]


def _require_str(doc: dict[str, Any], key: str, filename: str) -> str:
    value = doc.get(key)
    if not isinstance(value, str) or not value.strip():
        raise DatasetValidationError(
            filename, f"field {key!r} must be a non-empty string"
        )
    return value


def _require_str_list(doc: dict[str, Any], key: str, filename: str) -> tuple[str, ...]:
    value = doc.get(key)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise DatasetValidationError(
            filename, f"field {key!r} must be a list of strings"
        )
    return tuple(value)


def validate_pair(filename: str, doc: object) -> PairSpec:
    """Validate one parsed dataset document against the README schema.

    Raises :class:`DatasetValidationError` naming the file on the first
    violation found (Req 11.7). Unknown top-level or ``expected`` keys are
    rejected too — a typo'd expectation key silently asserting nothing is
    exactly the failure mode strictness prevents.
    """
    if not isinstance(doc, dict):
        raise DatasetValidationError(filename, "top level must be a JSON object")
    doc_keys = set(doc)
    if doc_keys != _REQUIRED_TOP_LEVEL:
        missing = sorted(_REQUIRED_TOP_LEVEL - doc_keys)
        extra = sorted(doc_keys - _REQUIRED_TOP_LEVEL)
        raise DatasetValidationError(
            filename,
            f"top-level keys mismatch (missing: {missing}, unexpected: {extra})",
        )

    pair_id = _require_str(doc, "id", filename)
    label = _require_str(doc, "label", filename)
    resume_text = _require_str(doc, "resume_text", filename)
    jd_text = _require_str(doc, "jd_text", filename)
    _require_str(doc, "notes", filename)

    expected = doc["expected"]
    if not isinstance(expected, dict):
        raise DatasetValidationError(filename, "field 'expected' must be a JSON object")
    expected_keys = set(expected)
    if expected_keys != _REQUIRED_EXPECTED:
        missing = sorted(_REQUIRED_EXPECTED - expected_keys)
        extra = sorted(expected_keys - _REQUIRED_EXPECTED)
        raise DatasetValidationError(
            filename,
            f"'expected' keys mismatch (missing: {missing}, unexpected: {extra})",
        )
    score_band = expected.get("score_band")
    if score_band not in SCORE_BANDS:
        raise DatasetValidationError(
            filename, f"'expected.score_band' must be one of {sorted(SCORE_BANDS)}"
        )
    must_match = _require_str_list(expected, "must_match_skills", filename)
    must_miss = _require_str_list(expected, "must_miss_skills", filename)

    return PairSpec(
        filename=filename,
        pair_id=pair_id,
        label=label,
        resume_text=resume_text,
        jd_text=jd_text,
        score_band=str(score_band),
        must_match_skills=must_match,
        must_miss_skills=must_miss,
    )


def load_dataset(directory: Path) -> list[PairSpec]:
    """Load and validate EVERY pair file before returning any (Req 11.7).

    Validation of the whole directory happens up front, so a malformed
    file aborts the run before a single pair result exists.
    """
    files = sorted(directory.glob("*.json"))
    if not files:
        raise DatasetValidationError(str(directory), "no *.json pair files found")
    specs: list[PairSpec] = []
    for path in files:
        try:
            doc: object = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DatasetValidationError(
                path.name, f"unreadable or invalid JSON: {exc}"
            ) from exc
        specs.append(validate_pair(path.name, doc))
    return specs


# ---------------------------------------------------------------------------
# Evaluation (Requirements 11.2, 11.3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExpectationResult:
    """One ``must_match_skills`` / ``must_miss_skills`` check outcome."""

    kind: str  # "must_match" | "must_miss"
    skill: str
    met: bool


@dataclass(frozen=True)
class PairResult:
    """The full per-pair report row (Req 11.2, 11.3)."""

    spec: PairSpec
    phase1_score: int
    phase2_score: int
    phase2_band: str
    band_met: bool
    matched: tuple[str, ...]
    missing: tuple[str, ...]
    expectations: tuple[ExpectationResult, ...]
    error: str | None = None

    @property
    def passed(self) -> bool:
        return (
            self.error is None
            and self.band_met
            and all(e.met for e in self.expectations)
        )


@dataclass(frozen=True)
class RunReport:
    """All pair results plus the summary counts (Req 11.8)."""

    results: tuple[PairResult, ...]
    passed: int = field(init=False)
    failed: int = field(init=False)

    def __post_init__(self) -> None:
        passed = sum(1 for result in self.results if result.passed)
        object.__setattr__(self, "passed", passed)
        object.__setattr__(self, "failed", len(self.results) - passed)


def band_of(score: int) -> str:
    """Map a score onto its band (low 0-39 / medium 40-69 / high 70-100)."""
    for name, (low, high) in SCORE_BANDS.items():
        if low <= score <= high:
            return name
    raise ValueError(f"score {score} outside [0, 100]")


def evaluate_pair(
    spec: PairSpec,
    *,
    phase1: Match_Scorer,
    phase2: Semantic_Match_Scorer,
    embedding_service: Embedding_Service,
) -> PairResult:
    """Score one pair with both engines and check every expectation.

    ``must_match_skills`` are met when the skill is in the Phase 2 matched
    set; ``must_miss_skills`` are met when the skill appears in NO Phase 2
    skill set at all (neither matched nor missing — the generic-term-leak
    guard, Req 11.5). A Phase 2 scoring error is itself a violation: the
    pair fails with the error recorded, and the Phase 1 score is still
    reported for the side-by-side view.
    """
    phase1_score = phase1.score(spec.resume_text, spec.jd_text).score

    try:
        result = phase2.score(
            spec.resume_text,
            spec.jd_text,
            embedding_service.embed(spec.resume_text),
            embedding_service.embed(spec.jd_text),
        )
    except Exception as exc:
        return PairResult(
            spec=spec,
            phase1_score=phase1_score,
            phase2_score=-1,
            phase2_band="error",
            band_met=False,
            matched=(),
            missing=(),
            expectations=(),
            error=f"{type(exc).__name__}: {exc}",
        )

    matched = tuple(kw.term for kw in result.matched_keywords)
    missing = tuple(kw.term for kw in result.missing_keywords)
    all_skills = set(matched) | set(missing)

    expectations = tuple(
        [
            ExpectationResult("must_match", skill, met=skill in set(matched))
            for skill in spec.must_match_skills
        ]
        + [
            ExpectationResult("must_miss", skill, met=skill not in all_skills)
            for skill in spec.must_miss_skills
        ]
    )
    phase2_band = band_of(result.score)

    return PairResult(
        spec=spec,
        phase1_score=phase1_score,
        phase2_score=result.score,
        phase2_band=phase2_band,
        band_met=phase2_band == spec.score_band,
        matched=matched,
        missing=missing,
        expectations=expectations,
    )


def run_dataset(
    specs: list[PairSpec],
    *,
    phase1: Match_Scorer,
    phase2: Semantic_Match_Scorer,
    embedding_service: Embedding_Service,
) -> RunReport:
    """Evaluate every validated pair and aggregate the summary."""
    return RunReport(
        results=tuple(
            evaluate_pair(
                spec, phase1=phase1, phase2=phase2, embedding_service=embedding_service
            )
            for spec in specs
        )
    )


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------


def render_pair(result: PairResult) -> str:
    """One human-readable per-pair block (Req 11.2, 11.3)."""
    spec = result.spec
    lines = [
        f"[{'PASS' if result.passed else 'FAIL'}] {spec.pair_id} — {spec.label}",
        f"  file:     {spec.filename}",
        f"  scores:   phase1={result.phase1_score}  phase2={result.phase2_score}",
    ]
    if result.error is not None:
        lines.append(f"  error:    phase 2 scoring failed: {result.error}")
        return "\n".join(lines)
    band_status = "met" if result.band_met else "MISSED"
    lines.append(
        f"  band:     expected={spec.score_band}  actual={result.phase2_band}  ({band_status})"
    )
    lines.append(
        f"  matched:  {', '.join(result.matched) if result.matched else '(none)'}"
    )
    lines.append(
        f"  missing:  {', '.join(result.missing) if result.missing else '(none)'}"
    )
    for expectation in result.expectations:
        status = "met" if expectation.met else "VIOLATED"
        lines.append(f"  {expectation.kind}: {expectation.skill!r} ({status})")
    return "\n".join(lines)


def render_summary(report: RunReport) -> str:
    """The pass/fail summary line (Req 11.8)."""
    return f"summary: {len(report.results)} pairs, {report.passed} passed, {report.failed} failed"


# ---------------------------------------------------------------------------
# Real-artifact composition (lazy imports; Req 11.2 real model + spaCy)
# ---------------------------------------------------------------------------


class _SentenceTransformerEncoder:
    """The runner's ``Text_Encoder`` over a loaded SentenceTransformer.

    Local (deliberately duplicated) counterpart of the production wrapper:
    the runner may import only ``matchlayer_api.scoring`` (Req 11.4), so it
    cannot reuse ``matchlayer_api.ml.semantic_adapter``.
    """

    def __init__(self, model: Any) -> None:
        self._model = model
        self._tokenizer = model.tokenizer
        self._dimension = int(model.get_sentence_embedding_dimension())
        specials = int(self._tokenizer.num_special_tokens_to_add(pair=False))
        self._max_tokens = max(1, int(model.max_seq_length) - specials)

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def max_tokens(self) -> int:
        return self._max_tokens

    def count_tokens(self, text: str) -> int:
        return len(self._tokenizer.encode(text, add_special_tokens=False))

    def split_tokens(self, text: str, max_tokens: int) -> list[str]:
        ids = self._tokenizer.encode(text, add_special_tokens=False)
        step = max(1, max_tokens)
        return [
            str(
                self._tokenizer.decode(
                    ids[start : start + step], skip_special_tokens=True
                )
            )
            for start in range(0, len(ids), step)
        ]

    def encode(self, texts: list[str]) -> list[list[float]]:
        rows = self._model.encode(
            texts,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return [[float(component) for component in row] for row in rows]


def build_real_encoder(model_path: str | None, model_name: str) -> Text_Encoder:
    """Load the SentenceTransformer (local path preferred) as a Text_Encoder."""
    from sentence_transformers import SentenceTransformer

    source = model_path if model_path else model_name
    model = SentenceTransformer(source, device="cpu", local_files_only=bool(model_path))
    return _SentenceTransformerEncoder(model)


def build_phase2_scorer(
    lexicon: Skill_Lexicon,
    *,
    spacy_pipeline: str,
    model_name: str,
    model_revision: str,
) -> Semantic_Match_Scorer:
    """Compose the Phase 2 scorer exactly as production composes it."""
    from importlib import metadata

    import spacy

    nlp = spacy.load(spacy_pipeline)
    spacy_version = metadata.version(spacy_pipeline)
    return Semantic_Match_Scorer(
        lexicon,
        Skill_Extractor(nlp, lexicon, max_keywords=DEFAULT_MAX_KEYWORDS),
        Semantic_Scorer(),
        w_similarity=DEFAULT_W_SIMILARITY,
        w_keyword=DEFAULT_W_KEYWORD,
        max_suggestions=DEFAULT_MAX_SUGGESTIONS,
        scorer_version=semantic_scorer_version(
            lexicon.lexicon_version,
            model_name,
            model_revision,
            spacy_pipeline,
            spacy_version,
        ),
    )


def build_phase1_scorer() -> Match_Scorer:
    """The untouched Phase 1 engine with production-default knobs."""
    return Match_Scorer(
        load_lexicon(),
        w_similarity=DEFAULT_W_SIMILARITY,
        w_keyword=DEFAULT_W_KEYWORD,
        max_keywords=DEFAULT_MAX_KEYWORDS,
        max_suggestions=DEFAULT_MAX_SUGGESTIONS,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _default_dataset_dir() -> Path:
    return Path(__file__).resolve().parent / "datasets" / "eyeball"


def main(argv: list[str] | None = None) -> int:
    """Run the evaluation gate. Returns the process exit code."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dataset-dir",
        type=Path,
        default=_default_dataset_dir(),
        help="Directory of eyeball pair JSON files (default: ml/evals/datasets/eyeball/)",
    )
    parser.add_argument(
        "--model-path",
        default=os.environ.get("MATCHLAYER_EMBEDDING_MODEL_PATH") or None,
        help="Local SentenceTransformer directory (default: "
        "$MATCHLAYER_EMBEDDING_MODEL_PATH; falls back to --model-name)",
    )
    parser.add_argument(
        "--model-name",
        default=DEFAULT_MODEL_NAME,
        help="Model identifier used when --model-path is absent and for the Scorer_Version stamp",
    )
    parser.add_argument(
        "--model-revision",
        default=os.environ.get("MATCHLAYER_EMBEDDING_MODEL_REVISION", "local"),
        help="Model revision recorded in the Scorer_Version stamp",
    )
    parser.add_argument(
        "--spacy-pipeline",
        default=DEFAULT_SPACY_PIPELINE,
        help=f"spaCy pipeline for the Skill_Extractor (default: {DEFAULT_SPACY_PIPELINE})",
    )
    args = parser.parse_args(argv)

    # 1. Validate the WHOLE dataset before any scoring (Req 11.7).
    try:
        specs = load_dataset(args.dataset_dir)
    except DatasetValidationError as exc:
        print(f"error: dataset validation failed: {exc}", file=sys.stderr)
        return 2

    # 2. Compose both engines (real artifacts for Phase 2 — Req 11.2).
    lexicon_v2 = load_lexicon_v2()
    encoder = build_real_encoder(args.model_path, args.model_name)
    phase2 = build_phase2_scorer(
        lexicon_v2,
        spacy_pipeline=args.spacy_pipeline,
        model_name=args.model_name,
        model_revision=args.model_revision,
    )
    report = run_dataset(
        specs,
        phase1=build_phase1_scorer(),
        phase2=phase2,
        embedding_service=Embedding_Service(encoder),
    )

    # 3. Full report, then the summary, then the CI-friendly exit (Req 11.8).
    for result in report.results:
        print(render_pair(result))
        print()
    print(render_summary(report))
    return 0 if report.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
