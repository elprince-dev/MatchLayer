# The skill-lexicon drift check in continuous integration

## Introduction

This document explains an automated guard that fails a build whenever two copies
of the same data file fall out of agreement: the project's single source of
truth for its skill lexicon and a second copy of that file bundled inside the
running application. A skill lexicon is a curated list of canonical skill names
together with their aliases (for example, recording that "js" and "ECMAScript"
both mean "JavaScript"), used to recognise skills mentioned in free text. The
guard runs inside Continuous Integration (CI) — the practice of merging every
change into a shared branch and automatically building and checking it on a
server rather than only on a developer's laptop — and it compares the two copies
byte for byte, refusing to pass if they differ or if either one has gone stale.

**Learning outcomes** — after reading this document you will be able to:

- Explain why a project sometimes keeps two copies of the same artifact, and what "drift" between those copies means.
- Distinguish the two failure modes a copy can suffer: divergence from its source and staleness relative to the program that generates it.
- Describe how an automated check detects both failure modes and reports the exact command that fixes them.
- Recognise the common mistakes that trigger or defeat this check and recover from them.

Prerequisites: this document builds on
[the Phase 1 CI jobs and the required-checks aggregator](12-hosting-02-phase-1-ci-jobs.md),
which introduces the build pipeline and the named units of work that this guard
runs inside. No other prerequisites.

## Problem it solves

Some artifacts in a project have one logical owner but need to live in more than
one place. A data file produced by a separate generation step belongs, by
ownership, next to that step; but the program that consumes it at runtime often
needs its own copy packaged alongside it, so the program can load the file
without reaching across the project into code it should not depend on. The
concrete problem is that, once the same content exists in two locations, the two
can quietly disagree. A developer edits one copy by hand, or regenerates the
file and commits only one of the two paths, and now the repository holds two
versions of the same data that claim to be identical but are not.

The prior approaches each have a weakness:

- **Keep a single copy and import it directly across the project.** This removes the duplication, but it forces the runtime program to depend on the layout and code of an unrelated part of the project, which is exactly the coupling the two-copy arrangement exists to avoid.
- **Keep two copies and rely on discipline.** Every contributor is trusted to regenerate both files and commit them together. Under deadlines or across many contributors that step is skipped, and a reviewer sees only what was committed, not the copy that should have changed alongside it.

A drift check removes the reliance on memory. It treats one location as the
source of truth, treats the other as a copy that must mirror it exactly, and
fails the build the moment the two disagree — or the moment both copies fall
behind the generator that is supposed to produce them. The duplication stays,
but the silent-disagreement failure mode is gone.

## Mental model

Think of a master document kept in a locked archive and an official photocopy
posted on a public notice board. The photocopy exists so that passers-by can
read it without being let into the archive, but it is only useful while it says
exactly what the master says. A proofreader walks past every morning with two
jobs: hold the photocopy against the master to confirm they match
character-for-character, and confirm that the master itself is the latest version
the print shop would produce today. If the photocopy was altered, or the master
is an old printout, the proofreader pins a red flag to the board and the notice
is taken down until it is reprinted from the current original.

When the check runs, it performs these steps in order:

1. Read the source-of-truth file and read the copy, then compare their raw bytes; if either file is missing, record that as an error immediately.
2. If the bytes differ, record a **divergence** error naming both paths, because the copy no longer mirrors its source.
3. Ask the generation program whether the committed files match what it would produce right now; if they do not, record a **staleness** error.
4. If no errors were recorded, print a one-line confirmation and exit successfully; otherwise print every error followed by the exact command that regenerates and fixes the files, and exit with a failure status.

Steps 2 and 3 are two independent questions. A copy can perfectly mirror its
source while both are stale, and a fresh source can have a copy that was edited
by hand — so the check asks both questions every run.

## How it works

A duplicated artifact is governed by three roles: a **source of truth**, a
**copy** that must mirror it exactly, and a **generator** — the program that
produces the canonical content from some underlying input. In a healthy
repository the source and the copy are byte-identical, and both equal whatever
the generator would emit today. A drift check is a small program that asserts
that invariant and fails when it is broken.

There are two distinct ways the invariant can break, and a thorough check tests
for both:

- **Divergence** is disagreement between the copy and its source. It happens when someone edits one of the two files by hand, or regenerates the content but commits only one path. The check detects this by reading both files and comparing their bytes directly; an exact comparison is appropriate because the two are meant to be identical, so any difference at all is a defect.
- **Staleness** is disagreement between the committed files and the generator. It happens when the generator's underlying input changes — a new entry is curated into the data — but nobody re-runs the generator, so both committed copies are now out of date even though they still agree with each other. Detecting this requires consulting the generator itself.

The cleanest way to test for staleness is to let the generator answer the
question rather than re-implementing its rules. A well-designed generator offers
a check mode: instead of overwriting the files, it computes what it would write
and reports, through its exit status, whether the committed files already match.
The drift check invokes that mode and trusts its verdict. This delegation matters
because the content is usually serialised to a text format such as JavaScript
Object Notation (JSON), and a serialiser makes many small formatting choices —
key ordering, whitespace, trailing newlines. If the drift check tried to predict
those choices on its own, it could disagree with the real generator and produce
false alarms. By asking the generator, the two stay in lockstep by construction.

A check that runs unattended must also fail safely. A missing file is reported as
its own error rather than crashing, so an accidental deletion produces a clear
message instead of an opaque stack trace. When everything agrees the program
exits with a success status and a short confirmation; when anything is wrong it
writes each specific problem and the single remediation command, then exits with
a failure status that the surrounding build step propagates into a failed build.
That turns an easy-to-forget manual step into an enforced gate.

## MatchLayer Phase 1 usage

In MatchLayer the source of truth for the skill lexicon is
`ml/lexicon/skill_lexicon.v1.json`, and the runtime copy bundled with the
back-end application — the program that exposes the project's Application
Programming Interface (API), the set of endpoints other programs call — is
`apps/api/src/matchlayer_api/scoring/data/skill_lexicon.v1.json`. Both are
produced from one curated dataset by the build pipeline (the generation script
that serialises the lexicon) at `ml/pipelines/build_skill_lexicon.py`, so in a
healthy repository the two files are byte-identical.

The drift check itself is `tools/check_lexicon_drift.py`. It depends on nothing
outside the language's standard library, so it can run before any project
dependencies are installed. Its top-of-file documentation states the two failure
modes the check defends against:

Source: `tools/check_lexicon_drift.py`

```python
1. **Divergence** — the copy differs from the source (someone edited one file
   by hand, or regenerated without committing both).
2. **Staleness** — neither file matches what the build pipeline would emit
   today (the curated data in ``build_skill_lexicon.py`` changed but the
   artifacts were not regenerated). This second check delegates to the
   pipeline's own ``--check`` mode so the two stay in lockstep without this
   tool duplicating the lexicon's serialization rules.
```

The entry point runs both checks, collects their errors, and reports them
together. A clean run prints a one-line confirmation and returns a success code:

Source: `tools/check_lexicon_drift.py`

```python
def main() -> int:
    errors = check_copy_matches_source()
    errors += check_artifacts_are_current()

    if not errors:
        print("OK: skill_lexicon source and API package copy agree (byte-identical, current).")
        return 0
```

The staleness check delegates to the build pipeline's `--check` mode rather than
re-implementing the lexicon's serialisation, exactly as the documentation above
describes, so the two never disagree about formatting.

This script is wired into the build pipeline as a step in the `backend` CI job,
defined in `.github/workflows/ci.yml`. A CI job is a named, independently
runnable unit of work in the pipeline. The step sits alongside the other
drift gates and fails the build if the copy and source no longer agree:

Source: `.github/workflows/ci.yml`

```yaml
# tools/check_lexicon_drift.py is stdlib-only too. It fails the build if
# the committed API copy of the Skill_Lexicon
# (apps/api/src/matchlayer_api/scoring/data/skill_lexicon.v1.json) is no
# longer byte-identical to the ml/ source of truth
# (ml/lexicon/skill_lexicon.v1.json), or if either is stale vs the build
# pipeline. Mirrors the .env / OpenAPI drift gates (phase-1-matching
# Requirement 10.3).
- name: Check skill_lexicon drift
  run: python3 tools/check_lexicon_drift.py
```

Because the step is `python3 tools/check_lexicon_drift.py` with no arguments, the
script resolves both artifact paths relative to its own location, so the same
invocation works from the repository root in CI and from a developer's machine.

## Common pitfalls

- **Mistake:** Editing the runtime copy `apps/api/src/matchlayer_api/scoring/data/skill_lexicon.v1.json` by hand to make a quick fix, instead of changing the curated data and regenerating.
  **Symptom:** The drift check reports a divergence error because the hand-edited copy no longer matches the source of truth, and the build fails.
  **Recovery:** Revert the hand edit, change the curated dataset instead, run `python3 ml/pipelines/build_skill_lexicon.py` to regenerate both files, and commit them together.

- **Mistake:** Regenerating the lexicon and committing only one of the two paths.
  **Symptom:** The build fails with a divergence error naming the path that was left behind, because only one copy advanced.
  **Recovery:** Run the build pipeline again and commit both `ml/lexicon/skill_lexicon.v1.json` and the API package copy in the same change, so the two move in lockstep.

- **Mistake:** Changing the curated source data inside the build pipeline but never re-running it, so both committed files still agree with each other but lag behind the generator.
  **Symptom:** Divergence passes (the two files match), but the staleness check fails because neither file matches what the pipeline would emit today.
  **Recovery:** Run `python3 ml/pipelines/build_skill_lexicon.py`, confirm both artifacts changed, and commit the regenerated files.

- **Mistake:** Assuming the drift check has been run locally and skipping it before pushing, treating it as a CI-only concern.
  **Symptom:** The push is accepted but the `backend` job fails minutes later, blocking the pull request until a follow-up commit fixes the artifacts.
  **Recovery:** Run `python3 tools/check_lexicon_drift.py` locally before pushing; a clean run prints its one-line confirmation, and any failure prints the exact remediation command to run.

## External reading

- [GitHub Actions: about workflows](https://docs.github.com/en/actions/writing-workflows/about-workflows)
- [Python: the runpy module for running code as a script](https://docs.python.org/3/library/runpy.html)
- [Python: sys.argv and sys.exit](https://docs.python.org/3/library/sys.html)
