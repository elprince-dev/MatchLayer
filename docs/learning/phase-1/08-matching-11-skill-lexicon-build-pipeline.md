# The skill-lexicon build pipeline

## Introduction

A _skill lexicon_ is a curated dictionary of recognized skill names together with their alternate spellings, categories, and importance weights; a matching engine consults it to decide which skills a job description asks for and which ones a resume actually contains. This document explains the _build pipeline_ that produces that dictionary: a small program that holds the curated skill data in one place and writes it out as a committed data file, so the dictionary the application reads at run time is generated rather than hand-edited. You will learn why a project keeps a generated file under version control, how a build step makes that file reproducible byte-for-byte, and how an automated check stops the committed copy from drifting out of sync with the source it came from.

A _build pipeline_ here means a deterministic script that turns curated source data into one or more output files. An _artifact_ is a generated file that is committed to the repository instead of being produced fresh on every request. A file is _deterministic_ when the same input always yields the exact same bytes out. Throughout this document, "regenerate" means re-running the script to overwrite the artifact from its source.

**Learning outcomes** — after reading this document you will be able to:

- Explain why a project commits a generated data artifact instead of building it at run time or editing it by hand.
- Describe how deterministic serialization makes a generated file reproducible across machines, and how a drift check relies on that property.
- Distinguish a schema version from a content version, and explain why a change to the curated data must bump the content version.

Prerequisites: [Applicant Tracking System scoring in Phase 1](08-matching-01-ats-scoring-overview.md).

## Problem it solves

The concrete problem is keeping a hand-curated reference table both easy for a person to edit and trustworthy as a machine input. A skill dictionary is exactly the kind of data a human wants to curate directly — adding a new framework, fixing an alternate spelling, nudging a weight — yet the program that reads it needs a clean, validated, predictable file. When a person edits the machine-readable file by hand, two things go wrong: typos slip in unnoticed (a duplicated entry, a weight outside the allowed range, an alternate spelling claimed by two different skills), and the file's exact byte layout becomes whatever the editor happened to produce that day.

Before a build pipeline exists, the pre-existing state is a single data file edited in place. Whoever needs to change a skill opens the file, edits the structured data directly, and commits it. There is no validation step, so a malformed entry is discovered only when the application loads it and misbehaves. There is no canonical formatting, so two people editing the same file produce diffs full of incidental reordering and whitespace churn that hide the real change. And when the same data must exist in more than one place — for example a source-of-truth copy and a second copy shipped inside an application package — nothing guarantees the two copies stay identical.

A build pipeline solves this by moving the human-edited data into a source program and making the committed file a pure output of running that program. The human edits curated data in one obvious location; the program validates it, formats it identically every time, and writes every copy from the same bytes. A reviewer reading a change sees a small, meaningful diff in the source data and a corresponding regenerated artifact, not a hand-tangled mixture of the two.

## Mental model

Think of the build pipeline as a **bakery, not a pantry**. The curated data inside the script is the recipe; the committed file is the loaf on the shelf. Nobody carves new ingredients into a finished loaf — they change the recipe and bake again. Because the recipe and the oven settings are fixed, baking the same recipe twice produces loaves that are identical down to the crumb, so anyone can tell at a glance whether the loaf on the shelf came from the current recipe.

Walked through step by step, regenerating the artifact looks like this:

1. A maintainer edits the curated data in the source program — the single recipe everyone shares.
2. The maintainer runs the build script. The script validates the data, rejecting duplicates, out-of-range weights, and ambiguous alternate spellings before anything is written.
3. The script serializes the validated data the same way every time — keys in sorted order, fixed indentation, a trailing newline — and writes each committed copy from those identical bytes.
4. The maintainer commits both the source change and the regenerated file together, so the loaf on the shelf always matches the recipe in the kitchen.

A separate verification mode bakes the loaf in memory and compares it to the one on the shelf without writing anything; if they differ, it fails loudly. That mode is what an automated check runs to catch a stale artifact.

## How it works

A generated data artifact has two parts that live in different places: the _source of truth_, which a human curates, and the _built file_, which a program emits. The source of truth might be a table embedded in a script, a set of spreadsheets, or a directory of small input files. The build program reads that source, checks it for internal consistency, and writes the result in a fixed, machine-friendly format. Keeping the curated input separate from the emitted output is the core idea: people edit the input, machines read the output, and a build step is the only bridge between them.

Validation is what makes the build trustworthy. Before emitting anything, the program asserts the invariants the data must satisfy — no two entries share a primary key, every weight sits inside its allowed range, no alternate spelling maps to two different entries. A violated invariant aborts the build with an error instead of shipping a broken file. This turns a class of silent data bugs into loud, immediate failures at the moment of regeneration.

Reproducibility comes from _deterministic serialization_. The program writes the output with a canonical layout: object keys sorted into a stable order, a fixed indentation width, consistent text encoding, and a trailing newline. Given the same input, this produces the same bytes on any machine and any interpreter version. Deterministic output is what lets a project store the generated file in version control and get clean, reviewable diffs, because an unrelated edit cannot reshuffle the whole file.

That same determinism enables a _drift check_. A drift check regenerates the artifact in memory and compares it byte-for-byte against the committed copy; if they differ, the committed copy is stale and the check fails. Run automatically on every change, it guarantees that whatever is committed is exactly what the current source would produce — so the artifact can never silently fall behind the data it claims to represent. When a project ships the same data in two locations, the drift check compares both, keeping the copies identical by construction.

Finally, generated artifacts carry versions, and two distinct kinds matter. A _schema version_ describes the shape of the file — the field names and structure a reader parses against — and changes only when that shape changes. A _content version_ describes the data itself and changes whenever the curated values change. Separating them lets downstream consumers reason about compatibility (can my reader still parse this?) independently from provenance (which exact dataset produced this result?). A consumer that stamps its outputs with the content version can later say precisely which dataset produced any given result.

## MatchLayer Phase 1 usage

In Phase 1 the build pipeline is a standard-library-only script, `ml/pipelines/build_skill_lexicon.py`, that holds the curated skill data inline and emits the committed lexicon artifact `ml/lexicon/skill_lexicon.v1.json`. The script is a derivation tool that lives under the top-level machine-learning directory and is never imported by the running application; the request path only ever reads the finished file. JavaScript Object Notation (JSON), the text format the artifact is written in, is a widely supported way to store structured data as plain text.

The reproducibility guarantee comes from one serialization helper that sorts keys, fixes the indentation, and appends a trailing newline. This is what lets the committed file be compared byte-for-byte across machines:

Source: `ml/pipelines/build_skill_lexicon.py`

```python
def serialize(document: dict[str, Any]) -> str:
    return json.dumps(document, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
```

Because keys are sorted and the layout is fixed, the emitted JSON is stable. The first lines of the committed artifact show the serializer's canonical, alphabetized output and the two version fields the document carries:

Source: `ml/lexicon/skill_lexicon.v1.json`

```json
  "lexicon_version": "1.0.0",
  "schema_version": 1,
  "skill_count": 80,
```

Those two version fields are not interchangeable. The script keeps them as separate constants: the schema version tracks the file's shape, while the lexicon (content) version tracks the curated terms and flows into the recorded scorer version, so a stored match result can be traced back to the exact dictionary that produced it:

Source: `ml/pipelines/build_skill_lexicon.py`

```python
SCHEMA_VERSION = 1
LEXICON_VERSION = "1.0.0"
```

The same script ships the data in two places: the source-of-truth copy at `ml/lexicon/skill_lexicon.v1.json` and a package-data copy the application loads at `apps/api/src/matchlayer_api/scoring/data/skill_lexicon.v1.json`. Both are written from identical bytes. A sibling drift check, `tools/check_lexicon_drift.py`, regenerates the document in memory and fails when either committed copy differs from what the current curated data would emit — the same pattern used for other generated files in the repository. The practical workflow is therefore fixed: edit the curated table in the script, run the script to regenerate both files, and commit the source change alongside the regenerated artifacts in one change.

## Common pitfalls

- **Mistake:** Editing the committed JSON artifact by hand instead of changing the curated data in the script and regenerating.
  **Symptom:** The next time the drift check runs, it fails because the committed file no longer matches what the script would emit; the hand-edit is reported as drift even though the data "looks right".
  **Recovery:** Move the change into the curated table inside the build script, run the script to regenerate every copy, and commit the regenerated files; never patch the artifact directly.

- **Mistake:** Regenerating only the source-of-truth copy and forgetting the second copy the application ships.
  **Symptom:** The two files diverge, the drift check fails on the un-regenerated copy, and the application could load skill data that differs from the source of truth.
  **Recovery:** Re-run the single build script, which writes both copies from the same bytes, then commit both; treat the two files as one unit that is always regenerated together.

- **Mistake:** Changing the curated skill terms without bumping the content version.
  **Symptom:** Two different datasets share the same version string, so a stored result can no longer be attributed to the exact dictionary that produced it, and reproductions silently use the wrong data.
  **Recovery:** Bump the lexicon content version whenever the curated terms change, keeping the schema version untouched unless the file's shape itself changed.

- **Mistake:** Introducing non-determinism into serialization — relying on insertion order, skipping the key sort, or omitting the trailing newline.
  **Symptom:** Regenerating the file on a different machine produces a different byte layout, so version control shows noisy diffs and the drift check fails even when no curated data changed.
  **Recovery:** Keep the canonical serializer — sorted keys, fixed indentation, stable encoding, trailing newline — and never bypass it when writing the artifact.

## External reading

- [Python documentation: json — JSON encoder and decoder](https://docs.python.org/3/library/json.html)
- [Python documentation: argparse — Parser for command-line options](https://docs.python.org/3/library/argparse.html)
- [Python documentation: pathlib — Object-oriented filesystem paths](https://docs.python.org/3/library/pathlib.html)
