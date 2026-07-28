"""Unit tests for the Skill_Lexicon build pipeline (task 5.4).

Anchors:
    * ``.kiro/specs/phase-2-nlp-embeddings/requirements.md`` → Requirement 5
      (5.1 byte-identical reruns, 5.5 added/removed diff summary, 5.7
      preserved ``--check`` drift mode).
    * ``.kiro/specs/phase-2-nlp-embeddings/tasks.md`` → task 5.4.

Concrete-example coverage for ``ml/pipelines/build_skill_lexicon.py``:

* **Byte-identical rerun (Requirement 5.1).** ``serialize(build_lexicon())``
  is identical across calls, and two full ``main([])`` runs against tmp
  artifact paths write byte-identical files (both targets identical to each
  other and across runs).
* **Diff summary content (Requirement 5.5).** ``render_diff_summary`` lists
  each added and removed canonical term with per-direction counts relative to
  the previously committed artifact, and degrades to a skip message when no
  previous artifact exists.
* **``--check`` drift mode (Requirement 5.7).** With freshly written tmp
  artifacts ``--check`` exits 0 without modifying anything; with a drifted or
  missing artifact copy it exits 1 naming the stale path on stderr.

The pipeline under test is intentionally stdlib-only and lives outside any
installable package, so this module loads it by file path with
:mod:`importlib` — the same pattern as the sibling property-test module
``tools/tests/test_build_skill_lexicon.py``. A distinct ``sys.modules`` name
is used so the two test modules never clobber each other's module object.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

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
    # A module name distinct from the property-test module's registration so
    # each test module owns (and monkeypatches) its own module object.
    spec = importlib.util.spec_from_file_location("build_skill_lexicon_unit", path)
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
# Fixture: point every artifact path at a tmp directory
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Redirect the pipeline's artifact paths (and REPO_ROOT) into ``tmp_path``.

    ``REPO_ROOT`` must move too because ``main`` renders paths with
    ``relative_to(REPO_ROOT)``; ``PREVIOUS_ARTIFACT_CANDIDATES`` is narrowed
    to the tmp source artifact so the diff summary never reads the real
    committed files.
    """
    source = tmp_path / "ml" / "lexicon" / "skill_lexicon.test.json"
    package = tmp_path / "pkg" / "skill_lexicon.test.json"
    monkeypatch.setattr(bsl, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(bsl, "SOURCE_ARTIFACT", source)
    monkeypatch.setattr(bsl, "PACKAGE_ARTIFACT", package)
    monkeypatch.setattr(bsl, "PREVIOUS_ARTIFACT_CANDIDATES", (source,))
    return source, package


# ---------------------------------------------------------------------------
# Byte-identical reruns (Requirement 5.1)
# ---------------------------------------------------------------------------


class TestByteIdenticalRerun:
    """Re-running the pipeline against the same sources is byte-identical."""

    def test_serialize_build_is_identical_across_calls(self) -> None:
        """Two independent assemble+serialize passes yield the same text."""
        first = bsl.serialize(bsl.build_lexicon())
        second = bsl.serialize(bsl.build_lexicon())
        assert first == second
        # Canonical JSON: parses back to an equal document, ends in newline.
        assert json.loads(first) == json.loads(second)
        assert first.endswith("\n")

    def test_main_rerun_writes_byte_identical_artifacts(
        self, tmp_artifacts: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Two full ``main([])`` runs produce byte-identical files."""
        source, package = tmp_artifacts

        assert bsl.main([]) == 0
        first_source = source.read_bytes()
        first_package = package.read_bytes()

        assert bsl.main([]) == 0
        second_source = source.read_bytes()
        second_package = package.read_bytes()

        assert first_source == second_source
        assert first_package == second_package
        # The two targets are byte-identical to each other by construction.
        assert first_source == first_package
        # And both match what serialize(build_lexicon()) would emit directly.
        assert first_source.decode("utf-8") == bsl.serialize(bsl.build_lexicon())


# ---------------------------------------------------------------------------
# Diff summary content (Requirement 5.5)
# ---------------------------------------------------------------------------


class TestDiffSummary:
    """The build summary lists added/removed canonical terms and counts."""

    def test_render_diff_summary_lists_added_and_removed_terms(self) -> None:
        """Added and removed canonicals appear individually, with counts."""
        previous = ("ml/lexicon/skill_lexicon.v1.json (lexicon_version v1)", {"python", "cobol"})
        new_canonicals = {"python", "rust", "go"}

        summary = bsl.render_diff_summary(new_canonicals, previous)
        lines = summary.splitlines()

        assert "ml/lexicon/skill_lexicon.v1.json (lexicon_version v1)" in lines[0]
        assert "canonical terms added:   2" in summary
        assert "canonical terms removed: 1" in summary
        assert "    + go" in lines
        assert "    + rust" in lines
        assert "    - cobol" in lines
        # Unchanged terms never appear as +/- entries.
        assert "+ python" not in summary
        assert "- python" not in summary

    def test_render_diff_summary_without_previous_artifact(self) -> None:
        """No previously committed artifact degrades to an explicit skip note."""
        summary = bsl.render_diff_summary({"python"}, None)
        assert "No previously committed artifact" in summary

    def test_main_prints_diff_against_previously_committed_artifact(
        self, tmp_artifacts: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
    ) -> None:
        """End-to-end: the build's stdout diff reflects the prior artifact."""
        source, _package = tmp_artifacts

        # Seed a "previously committed" artifact: the real build minus one
        # canonical term, plus one synthetic term the build never emits.
        document = bsl.build_lexicon()
        previous_doc = json.loads(bsl.serialize(document))
        dropped = previous_doc["skills"].pop(0)["canonical"]
        previous_doc["skills"].append(
            {
                "canonical": "zz-retired-skill",
                "display": "ZZ Retired Skill",
                "category": "tool",
                "weight": 0.5,
                "aliases": [],
            }
        )
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(bsl.serialize(previous_doc), encoding="utf-8")

        assert bsl.main([]) == 0
        out = capsys.readouterr().out

        assert f"+ {dropped}" in out
        assert "- zz-retired-skill" in out
        assert "canonical terms added:   1" in out
        assert "canonical terms removed: 1" in out


# ---------------------------------------------------------------------------
# --check drift mode (Requirement 5.7)
# ---------------------------------------------------------------------------


class TestCheckMode:
    """``--check`` exits 0 on matching artifacts, 1 on drift or absence."""

    def test_check_passes_on_fresh_artifacts(
        self, tmp_artifacts: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
    ) -> None:
        source, package = tmp_artifacts
        assert bsl.main([]) == 0
        before = (source.read_bytes(), package.read_bytes())

        assert bsl.main(["--check"]) == 0

        assert "OK" in capsys.readouterr().out
        # --check never writes: both artifacts are untouched.
        assert (source.read_bytes(), package.read_bytes()) == before

    def test_check_fails_on_drifted_artifact(
        self, tmp_artifacts: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
    ) -> None:
        _source, package = tmp_artifacts
        assert bsl.main([]) == 0
        capsys.readouterr()  # discard build output

        drifted = package.read_text(encoding="utf-8") + "# drift\n"
        package.write_text(drifted, encoding="utf-8")

        assert bsl.main(["--check"]) == 1

        err = capsys.readouterr().err
        assert "stale or missing" in err
        assert "pkg/skill_lexicon.test.json" in err
        # The drifted file was reported, not rewritten.
        assert package.read_text(encoding="utf-8") == drifted

    def test_check_fails_on_missing_artifact(
        self, tmp_artifacts: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
    ) -> None:
        source, _package = tmp_artifacts
        assert bsl.main([]) == 0
        capsys.readouterr()  # discard build output

        source.unlink()

        assert bsl.main(["--check"]) == 1

        err = capsys.readouterr().err
        assert "ml/lexicon/skill_lexicon.test.json" in err
        # --check reported the missing copy without recreating it.
        assert not source.exists()
