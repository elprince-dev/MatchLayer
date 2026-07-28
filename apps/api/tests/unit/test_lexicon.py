"""Unit tests for the Skill_Lexicon loader and Scorer_Version (task 3.3).

Phase 2 (phase-2-nlp-embeddings, task 5.4) extends this module with loader
coverage for the expanded v2 artifact — it loads through the unchanged
Scoring_Core loader with ``lexicon_version "v2"`` and strictly more canonical
terms than v1 (Requirements 5.2, 5.4) — and for schema-version rejection: an
artifact with an unsupported ``schema_version`` fails with an error naming
the version, before any skill is parsed (no partial load, Requirement 5.8).

Concrete-example coverage for
:mod:`matchlayer_api.scoring.lexicon` — the committed-artifact loader behind
Requirements 10.3 and 10.4. Two contracts are pinned here:

* **Alias normalization (Requirement 6.2's foundation, surfaced by 10.3).**
  :meth:`Skill_Lexicon.normalize` must (a) resolve a known alias to its
  canonical term, (b) map a canonical term to itself, and (c) pass an unknown
  term through in normalized form — case-folded and whitespace-collapsed —
  unchanged. The Keyword_Analyzer feeds both lexicon terms and free-text
  TF-IDF terms through ``normalize``, so the pass-through behavior is a hard
  requirement, not an incidental detail.

* **Scorer_Version composition (Requirement 10.4).** ``scorer_version`` must
  equal ``f"{ALGORITHM_VERSION}+lex.{lexicon_version}"`` and — crucially —
  constructing a :class:`Skill_Lexicon` from an artifact whose
  ``lexicon_version`` differs must yield a *different* ``scorer_version``, so a
  lexicon change is reflected in the version persisted on every Match_Result
  and a stored score stays reproducible and auditable.

The version cases drive the loader from small in-memory artifact dicts
(``schema_version=1`` plus a couple of skills) so the two-lexicon comparison is
hermetic and never depends on the shipped file. A single ``load_lexicon()``
smoke test then exercises the real committed artifact end-to-end.

References:
* Requirements 10.3 (source-of-truth vs runtime artifact), 10.4 (Scorer_Version).
* Design §"Skill_Lexicon".
"""

from __future__ import annotations

import json
from importlib import resources
from typing import Any

import pytest

from matchlayer_api.scoring.lexicon import (
    ALGORITHM_VERSION,
    SUPPORTED_SCHEMA_VERSION,
    LexiconError,
    Skill_Lexicon,
    load_lexicon,
    scorer_version,
)

# ---------------------------------------------------------------------------
# In-memory artifact builders
# ---------------------------------------------------------------------------

# A tiny but representative skill set: a single-word canonical with simple
# aliases, and a multi-word canonical with a short alias. The multi-word entry
# exercises the whitespace-collapsing half of normalization.
_SKILLS: list[dict[str, Any]] = [
    {
        "canonical": "python",
        "display": "Python",
        "category": "language",
        "weight": 1.0,
        "aliases": ["py", "python3", "cpython"],
    },
    {
        "canonical": "machine learning",
        "display": "Machine Learning",
        "category": "data",
        "weight": 0.85,
        "aliases": ["ml"],
    },
]


def _artifact(*, lexicon_version: str = "1.0.0") -> dict[str, Any]:
    """Build a minimal, valid parsed-artifact document for the loader.

    ``schema_version`` is pinned to the loader's
    :data:`SUPPORTED_SCHEMA_VERSION` (1); only ``lexicon_version`` varies across
    cases so the version assertions isolate exactly that axis.
    """
    return {
        "schema_version": 1,
        "lexicon_version": lexicon_version,
        "skills": _SKILLS,
    }


@pytest.fixture
def lexicon() -> Skill_Lexicon:
    """A :class:`Skill_Lexicon` built from the in-memory artifact above."""
    return Skill_Lexicon(_artifact())


# ---------------------------------------------------------------------------
# Alias normalization (Requirement 10.3)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("surface_form", "expected_canonical"),
    [
        ("py", "python"),
        ("python3", "python"),
        ("cpython", "python"),
        ("ml", "machine learning"),
    ],
)
def test_known_alias_resolves_to_canonical(
    lexicon: Skill_Lexicon, surface_form: str, expected_canonical: str
) -> None:
    """A known alias normalizes to its canonical term."""
    assert lexicon.normalize(surface_form) == expected_canonical


def test_alias_normalization_is_case_insensitive(lexicon: Skill_Lexicon) -> None:
    """Aliases resolve regardless of the input casing.

    ``normalize`` case-folds before the alias lookup, so a shouted ``"PY"`` and
    a mixed-case ``"Ml"`` resolve exactly as their lower-case forms do.
    """
    assert lexicon.normalize("PY") == "python"
    assert lexicon.normalize("Ml") == "machine learning"


@pytest.mark.parametrize("canonical", ["python", "machine learning"])
def test_canonical_term_maps_to_itself(lexicon: Skill_Lexicon, canonical: str) -> None:
    """A canonical term normalizes to itself (idempotent on canonicals)."""
    assert lexicon.normalize(canonical) == canonical


def test_canonical_term_normalizes_case_and_whitespace(lexicon: Skill_Lexicon) -> None:
    """A canonical supplied with odd casing/spacing still resolves to itself.

    ``"Machine   Learning"`` (title-case, doubled interior space) must collapse
    to the canonical ``"machine learning"`` — this is the multi-word-canonical
    half of the normalization contract.
    """
    assert lexicon.normalize("Machine   Learning") == "machine learning"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Rust", "rust"),  # case-folded
        ("  Kotlin  ", "kotlin"),  # outer whitespace stripped
        ("Spring   Boot", "spring boot"),  # interior whitespace collapsed
        ("GraphQL", "graphql"),  # case-folded, no alias entry
    ],
)
def test_unknown_term_passes_through_normalized(
    lexicon: Skill_Lexicon, raw: str, expected: str
) -> None:
    """A term that is neither alias nor canonical returns normalized, unchanged.

    Case-folded and whitespace-collapsed, but otherwise the same string — the
    Keyword_Analyzer relies on free-text TF-IDF terms surviving this call.
    """
    assert lexicon.normalize(raw) == expected
    # And it is genuinely unknown — not silently promoted to a canonical.
    assert not lexicon.is_canonical(raw)


def test_alias_lookups_reach_the_canonical_entry_and_weight(lexicon: Skill_Lexicon) -> None:
    """Per-term metadata/weight lookups follow alias rules to the canonical entry.

    Normalizing terms is only useful if downstream lookups agree: an alias and
    its canonical must resolve to the same :class:`SkillEntry` and weight.
    """
    assert lexicon.weight("py") == lexicon.weight("python") == pytest.approx(1.0)

    alias_entry = lexicon.entry("ml")
    canonical_entry = lexicon.entry("machine learning")
    assert alias_entry is not None
    assert alias_entry is canonical_entry
    assert alias_entry.canonical == "machine learning"


# ---------------------------------------------------------------------------
# Scorer_Version composition (Requirement 10.4)
# ---------------------------------------------------------------------------


def test_scorer_version_matches_documented_formula(lexicon: Skill_Lexicon) -> None:
    """``scorer_version`` is ``f"{ALGORITHM_VERSION}+lex.{lexicon_version}"``."""
    assert lexicon.lexicon_version == "1.0.0"
    assert lexicon.scorer_version == f"{ALGORITHM_VERSION}+lex.1.0.0"
    # The free function and the property share the single formula home.
    assert lexicon.scorer_version == scorer_version(lexicon.lexicon_version)


def test_scorer_version_changes_when_lexicon_version_changes() -> None:
    """A different ``lexicon_version`` yields a different ``scorer_version``.

    This is the heart of Requirement 10.4: the lexicon version flows into the
    Scorer_Version so that re-building the lexicon (new content version) changes
    the ``scorer_version`` stamped on subsequently created Match_Results — even
    when the algorithm version and the skill set are byte-for-byte identical.
    """
    v1 = Skill_Lexicon(_artifact(lexicon_version="1.0.0"))
    v2 = Skill_Lexicon(_artifact(lexicon_version="2.0.0"))

    assert v1.lexicon_version != v2.lexicon_version
    assert v1.scorer_version != v2.scorer_version
    # Both still share the same algorithm prefix — only the lexicon segment moved.
    assert v1.scorer_version == f"{ALGORITHM_VERSION}+lex.1.0.0"
    assert v2.scorer_version == f"{ALGORITHM_VERSION}+lex.2.0.0"


def test_scorer_version_free_function_distinguishes_versions() -> None:
    """The module-level ``scorer_version`` free function is injective in its input."""
    assert scorer_version("1.0.0") != scorer_version("1.0.1")
    assert scorer_version("1.0.0") == f"{ALGORITHM_VERSION}+lex.1.0.0"


# ---------------------------------------------------------------------------
# load_lexicon() smoke test against the real committed artifact
# ---------------------------------------------------------------------------


def test_load_lexicon_reads_committed_artifact() -> None:
    """``load_lexicon()`` loads the shipped package-data artifact end-to-end.

    Confirms the importlib.resources read, JSON parse, and validation all
    succeed against the real file, and that its ``scorer_version`` is composed
    from its own ``lexicon_version`` via the documented formula.
    """
    lex = load_lexicon()

    assert isinstance(lex, Skill_Lexicon)
    assert lex.lexicon_version  # non-empty content version
    assert len(lex.canonical_terms) > 0
    assert lex.scorer_version == scorer_version(lex.lexicon_version)
    assert lex.scorer_version == f"{ALGORITHM_VERSION}+lex.{lex.lexicon_version}"


def test_load_lexicon_alias_rules_resolve_real_terms() -> None:
    """Known aliases from the shipped artifact normalize to their canonicals.

    Spot-checks a few alias rules that exist in the committed lexicon so the
    normalization contract is exercised against real data, not just fixtures.
    """
    lex = load_lexicon()

    assert lex.normalize("js") == "javascript"
    assert lex.normalize("k8s") == "kubernetes"
    assert lex.normalize("postgres") == "postgresql"
    # An unknown free-text term still passes through normalized.
    assert lex.normalize("Some Unlisted Skill") == "some unlisted skill"


# ---------------------------------------------------------------------------
# Phase 2 — v2 artifact loads and exceeds the v1 canonical-term count
# (Requirements 5.2, 5.4 — task 5.4)
# ---------------------------------------------------------------------------

_DATA_PACKAGE = "matchlayer_api.scoring.data"


def _read_committed_artifact(name: str) -> dict[str, Any]:
    """Parse a committed package-data lexicon artifact by file name."""
    text = resources.files(_DATA_PACKAGE).joinpath(name).read_text(encoding="utf-8")
    document: Any = json.loads(text)
    assert isinstance(document, dict)
    return document


def test_v2_artifact_loads_through_the_unchanged_loader() -> None:
    """The committed v2 artifact loads without error, versioned ``"v2"``.

    Requirement 5.4: the expanded artifact loads through the Scoring_Core
    ``Skill_Lexicon`` loader preserved from Phase 1 — the same constructor,
    the same schema version — and Requirement 5.2's version fields are intact.
    """
    lex = Skill_Lexicon(_read_committed_artifact("skill_lexicon.v2.json"))

    assert lex.lexicon_version == "v2"
    assert len(lex.canonical_terms) > 0
    # The lexicon version flows into the Scorer_Version (Requirement 5.2).
    assert lex.scorer_version == scorer_version("v2")


def test_v2_artifact_exceeds_v1_canonical_term_count() -> None:
    """The v2 lexicon has strictly more canonical terms than v1 (Req 5.2)."""
    v1 = Skill_Lexicon(_read_committed_artifact("skill_lexicon.v1.json"))
    v2 = Skill_Lexicon(_read_committed_artifact("skill_lexicon.v2.json"))

    assert len(v2.canonical_terms) > len(v1.canonical_terms)
    # Same schema (the loader accepted both), distinct content versions —
    # hence distinct Scorer_Version stamps.
    assert v1.lexicon_version != v2.lexicon_version
    assert v1.scorer_version != v2.scorer_version


# ---------------------------------------------------------------------------
# Phase 2 — unsupported schema versions are rejected without partial load
# (Requirement 5.8 — task 5.4)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_schema_version", [0, 2, 999])
def test_loader_rejects_unsupported_schema_version(bad_schema_version: int) -> None:
    """An unsupported ``schema_version`` fails with an error naming it.

    The document's skills are otherwise perfectly valid, so the rejection is
    attributable to the schema version alone.
    """
    document = _artifact()
    document["schema_version"] = bad_schema_version

    with pytest.raises(LexiconError) as excinfo:
        Skill_Lexicon(document)

    message = str(excinfo.value)
    assert "schema_version" in message
    assert str(bad_schema_version) in message
    assert str(SUPPORTED_SCHEMA_VERSION) in message


def test_schema_rejection_happens_before_any_skill_is_parsed() -> None:
    """No partial load: the schema gate fires before skills are touched.

    The document pairs an unsupported schema version with skills that would
    themselves fail parsing; the error raised is the schema-version error,
    proving the loader never began consuming skill entries.
    """
    document: dict[str, Any] = {
        "schema_version": SUPPORTED_SCHEMA_VERSION + 1,
        "lexicon_version": "v99",
        "skills": [{"canonical": ""}],  # would raise its own LexiconError
    }

    with pytest.raises(LexiconError, match="unsupported lexicon schema_version"):
        Skill_Lexicon(document)
