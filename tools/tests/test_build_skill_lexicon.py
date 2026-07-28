"""Property test for the Skill_Lexicon pipeline's invariant rejection.

Anchors:
    * ``.kiro/specs/phase-2-nlp-embeddings/design.md`` → "Correctness
      Properties" → Property 16: *For any assembled lexicon document
      violating at least one artifact invariant — a duplicate canonical
      term, an alias mapped to more than one canonical term, an alias
      colliding with a canonical term, or a weight outside 0 < w ≤ 1 —
      the lexicon pipeline's validation rejects the document (non-zero
      exit, no artifact written).*
    * ``.kiro/specs/phase-2-nlp-embeddings/tasks.md`` → task 5.3.

**Validates: Requirements 5.6**

The pipeline under test (``ml/pipelines/build_skill_lexicon.py``) is
intentionally stdlib-only and lives outside any installable package, so this
module loads it by file path with :mod:`importlib` — mirroring how the repo's
other stdlib-only scripts are exercised from ``tools/tests``.

Test layers:

* ``test_property_16_invariant_violations_rejected`` — the Hypothesis
  property: generate a *valid* lexicon skill list, inject exactly one
  violation of a generated kind (one of the four invariants), and assert
  :func:`validate_invariants` accepts the valid baseline but raises
  :class:`LexiconInvariantError` on the mutated document.
* ``test_main_exits_nonzero_and_writes_nothing`` — end-to-end examples: with
  ``SOURCES`` monkeypatched to a source violating each invariant kind and the
  artifact paths pointed at a tmp directory, ``main([])`` exits non-zero and
  nothing at all is written to disk.
"""

from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Load the stdlib-only pipeline module by path (it is not on sys.path).
# ---------------------------------------------------------------------------

_WORKSPACE_MARKER = "pnpm-workspace.yaml"


def _repo_root() -> Path:
    """Return the repo root by walking up from this file to the workspace marker."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / _WORKSPACE_MARKER).is_file():
            return parent
    msg = f"could not locate {_WORKSPACE_MARKER!r} in any ancestor of {here}"
    raise RuntimeError(msg)


def _load_pipeline() -> ModuleType:
    path = _repo_root() / "ml" / "pipelines" / "build_skill_lexicon.py"
    spec = importlib.util.spec_from_file_location("build_skill_lexicon", path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError(f"could not load pipeline module from {path}")
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolves the defining module through sys.modules while
    # processing @dataclass, so register before exec (required on 3.13+).
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bsl = _load_pipeline()

# ---------------------------------------------------------------------------
# Strategy: a valid skill list plus exactly one injected invariant violation
# ---------------------------------------------------------------------------

# Terms are generated already-normalized (lowercase, no whitespace) because
# validate_invariants operates on the *assembled* document, after
# _normalize_term has run.
_TERMS = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789-+.#/",
    min_size=1,
    max_size=12,
)

_VIOLATION_KINDS = (
    "duplicate_canonical",
    "alias_two_owners",
    "alias_canonical_collision",
    "weight_out_of_range",
)

_BAD_WEIGHTS = st.one_of(
    # 0 and below (0 < w is strict).
    st.floats(min_value=-1000.0, max_value=0.0, allow_nan=False, allow_infinity=False),
    # Strictly above 1.
    st.floats(min_value=1.0001, max_value=1000.0, allow_nan=False, allow_infinity=False),
)

_SkillList = list[dict[str, Any]]


@st.composite
def _skills_with_one_violation(
    draw: st.DrawFn,
) -> tuple[_SkillList, _SkillList, str]:
    """Return ``(valid_skills, mutated_skills, violation_kind)``.

    ``valid_skills`` satisfies all four Phase 1 invariants by construction:
    canonical terms and aliases are drawn from one unique pool (so no term is
    reused anywhere), and every weight lies in (0, 1]. ``mutated_skills`` is
    a deep copy with exactly one injected violation of ``violation_kind``.
    """
    pool = draw(st.lists(_TERMS, min_size=6, max_size=20, unique=True))
    # Keep at least one spare term reserved for the alias_two_owners injection.
    n_skills = draw(st.integers(min_value=2, max_value=min(5, len(pool) - 1)))
    canonicals = pool[:n_skills]
    spare = pool[n_skills:]

    skills: _SkillList = []
    spare_idx = 0
    for canonical in canonicals:
        aliases: list[str] = []
        n_aliases = draw(st.integers(min_value=0, max_value=2))
        # spare[-1] stays reserved for the injection below.
        while n_aliases > 0 and spare_idx < len(spare) - 1:
            aliases.append(spare[spare_idx])
            spare_idx += 1
            n_aliases -= 1
        weight = draw(st.floats(min_value=0.05, max_value=1.0, allow_nan=False))
        skills.append(
            {
                "canonical": canonical,
                "display": canonical.title(),
                "category": "tool",
                "weight": weight,
                "aliases": sorted(aliases),
            }
        )

    kind = draw(st.sampled_from(_VIOLATION_KINDS))
    mutated = copy.deepcopy(skills)

    if kind == "duplicate_canonical":
        i = draw(st.integers(min_value=0, max_value=len(mutated) - 1))
        duplicate = dict(mutated[i])
        duplicate["aliases"] = []  # only the canonical duplication, nothing else
        mutated.append(duplicate)
    elif kind == "alias_two_owners":
        fresh_alias = spare[-1]  # unused anywhere in the valid document
        i = draw(st.integers(min_value=0, max_value=len(mutated) - 1))
        j = draw(st.integers(min_value=0, max_value=len(mutated) - 1).filter(lambda x: x != i))
        mutated[i]["aliases"] = [*mutated[i]["aliases"], fresh_alias]
        mutated[j]["aliases"] = [*mutated[j]["aliases"], fresh_alias]
    elif kind == "alias_canonical_collision":
        i = draw(st.integers(min_value=0, max_value=len(mutated) - 1))
        j = draw(st.integers(min_value=0, max_value=len(mutated) - 1))
        colliding = str(mutated[j]["canonical"])
        mutated[i]["aliases"] = [*mutated[i]["aliases"], colliding]
    else:  # weight_out_of_range
        i = draw(st.integers(min_value=0, max_value=len(mutated) - 1))
        mutated[i]["weight"] = draw(_BAD_WEIGHTS)

    return skills, mutated, kind


# ---------------------------------------------------------------------------
# Property 16 — validate_invariants rejects every injected violation
# ---------------------------------------------------------------------------


class TestProperty16InvariantRejection:
    """Property 16: Lexicon invariant violations are rejected without output.

    **Validates: Requirements 5.6**
    """

    @given(data=_skills_with_one_violation())
    def test_property_16_invariant_violations_rejected(
        self, data: tuple[_SkillList, _SkillList, str]
    ) -> None:
        valid, mutated, _kind = data

        # Baseline sanity: the un-mutated document passes validation, so the
        # rejection below is attributable to the single injected violation.
        bsl.validate_invariants(valid)

        with pytest.raises(bsl.LexiconInvariantError):
            bsl.validate_invariants(mutated)


# ---------------------------------------------------------------------------
# End-to-end: main() exits non-zero and writes no artifact on violation
# ---------------------------------------------------------------------------

# One violating source table per invariant kind. Each violation is *within* a
# single source, which assemble_skills treats as a hard error (merge policy
# only tolerates cross-source conflicts), so build_lexicon raises before
# main() ever touches the filesystem.
_VIOLATING_SOURCE_SKILLS: dict[str, tuple[tuple[str, str, str, float, list[str]], ...]] = {
    "duplicate_canonical": (
        ("python", "Python", "language", 1.0, []),
        ("python", "Python", "language", 0.9, []),
    ),
    "alias_two_owners": (
        ("python", "Python", "language", 1.0, ["py"]),
        ("java", "Java", "language", 0.9, ["py"]),
    ),
    "alias_canonical_collision": (
        ("python", "Python", "language", 1.0, []),
        ("java", "Java", "language", 0.9, ["python"]),
    ),
    "weight_out_of_range": (("python", "Python", "language", 1.5, []),),
}


class TestMainRejectsWithoutOutput:
    """End-to-end Property 16 examples: non-zero exit, nothing written.

    **Validates: Requirements 5.6**
    """

    @pytest.mark.parametrize("kind", sorted(_VIOLATING_SOURCE_SKILLS))
    def test_main_exits_nonzero_and_writes_nothing(
        self,
        kind: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        out_dir = tmp_path / "artifacts"
        bad_source = bsl.LexiconSource(
            name="test source",
            version="0.0.0",
            retrieved="2026-01-01",
            license="MIT",
            url="tools/tests/test_build_skill_lexicon.py",
            skills=_VIOLATING_SOURCE_SKILLS[kind],
        )
        monkeypatch.setattr(bsl, "SOURCES", (bad_source,))
        monkeypatch.setattr(bsl, "SOURCE_ARTIFACT", out_dir / "skill_lexicon.test.json")
        monkeypatch.setattr(bsl, "PACKAGE_ARTIFACT", out_dir / "pkg" / "skill_lexicon.test.json")

        exit_code = bsl.main([])

        assert exit_code != 0
        # Nothing was written at all — not even the parent directory.
        assert not out_dir.exists()
        assert "invariant" in capsys.readouterr().err
