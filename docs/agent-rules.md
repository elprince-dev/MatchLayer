# Agent Rules (Phase 4 — Agentic AI)

This document is the committed, human-readable statement of the two
deterministic rules the Phase 4 agents apply: the **Confidence_Level rule**
the `ATS_Agent` attaches to every score, and the **skill-gap classification
and prioritization rules** the `Skill_Gap_Agent` applies to build its
`Skill_Gap_Report`. It fulfils requirements 4.2 and 5.2 of the
`phase-4-agentic` spec (design decision D8: confidence is a documented,
data-driven rule, not an LLM judgment).

Both rules are implemented as **pure functions** — no randomness, no clocks,
no I/O — so identical inputs always yield identical outputs, every result is
reproducible from persisted trace data alone, and each rule is directly
property-testable (design Properties 6, 7, and 8):

- Confidence_Level: `apps/api/src/matchlayer_api/ml/agents/confidence.py`
- Skill-gap rules: `apps/api/src/matchlayer_api/ml/agents/gap_rules.py`

Any change to the bounds, classification, or ordering below requires updating
this document and the corresponding rule module together.

## The Confidence_Level rule

The `ATS_Agent` tags every score it writes to Agent_State with exactly one
Confidence_Level: `high`, `medium`, or `low`. The level is derived from three
observable inputs:

| Input        | Meaning                                                                                    |
| ------------ | ------------------------------------------------------------------------------------------ |
| `semantic`   | `True` when semantic scoring produced the score; `False` when a Degraded_Mode fallback did |
| `resume_len` | Length of the resume text in characters                                                    |
| `jd_len`     | Length of the job-description text in characters                                           |

### Numeric bounds

The **lengths-in-bounds** signal holds iff both inclusive checks pass:

| Text            | Minimum | Maximum |
| --------------- | ------- | ------- |
| Resume          | 200     | 50,000  |
| Job description | 100     | 20,000  |

(Committed as `RESUME_LEN_MIN` / `RESUME_LEN_MAX` / `JD_LEN_MIN` /
`JD_LEN_MAX` in `confidence.py`.)

### The rule

With two boolean signals — `semantic` and `lengths-in-bounds`:

- **`high`** — both hold: semantic scoring produced the score **and**
  `200 ≤ resume_len ≤ 50_000` **and** `100 ≤ jd_len ≤ 20_000`.
- **`medium`** — exactly one of {`semantic`, `lengths-in-bounds`} holds.
- **`low`** — neither holds.

### Consequences baked into the rule's shape

- The function is **total**: every `(bool, int, int)` input maps to exactly
  one of the three levels (requirement 4.2 — deterministic, no gaps, no
  overlaps).
- A score produced by a Degraded_Mode fallback scorer (`semantic=False`) can
  **never** be tagged `high` (requirement 4.5): `high` structurally requires
  the semantic signal.
- The `ATS_Agent`'s own degraded output (persisted score fields) is always
  tagged `confidence="low"`.

## The skill-gap rules

The `Skill_Gap_Agent` builds its `Skill_Gap_Report` as a pure function of
three inputs: the skills extracted from the Job_Description (duplicates
included), the `Candidate_Profile` skills, and the Match_Result's persisted
matched skills. Membership is **exact string membership** — skill names come
from the Phase 2 Skill_Lexicon, which is already canonical.

### Classification rule (requirement 5.1, as amended)

For each skill extracted from the Job_Description:

- Present in the profile skills **or** the matched skills → **covered**;
  produces **no** entry in the report.
- Absent from **both** sets → classified **`missing`**.

Profile-presence counts as coverage. This is the amended form of the rule as
implemented in `gap_rules.py`: it keeps classification consistent with the
full-coverage guarantee of requirement 5.7 (every JD skill present in the
profile or matched set → an empty gap list, which is a valid outcome and
never a degradation trigger).

The **`weak`** classification remains a schema-valid value in the
`SkillGapEntry` model (for schema and ordering stability), but the
classification rule **no longer produces it** — the only classifications
emitted are "covered / no entry" and `missing`.

Duplicate JD skills are classified once: each skill name appears **at most
once** in the report, while its occurrence count still feeds prioritization
below.

### Prioritization rule (requirement 5.2)

Gap entries are ordered by the following deterministic tie-breaking chain,
applied in order:

1. **Classification** — `missing` before `weak` (retained for ordering
   stability even though the classification rule no longer emits `weak`).
2. **JD occurrence count, descending** — a skill mentioned more often in the
   Job_Description ranks higher.
3. **Case-insensitive alphabetical** — `casefold()`-compared skill names.
4. **Exact string comparison** — a final total-order guarantee so skills
   differing only in case still order deterministically.

After ordering, ranks are assigned **1..n** — sequential, unique, ascending —
and entries are returned in ascending rank order.

### Consequences baked into the rules' shape

- Both functions are **total and deterministic** (requirement 5.3):
  field-for-field identical inputs always yield field-for-field identical
  outputs, including ordering and ranks. Re-executing the agent against
  persisted input state reproduces the persisted output exactly
  (requirement 12.5).
- An **empty gap list is valid** — it means full coverage, never an error or
  a Degraded_Output trigger (requirement 5.7).
- The tie-break chain leaves **no unordered pairs**: any two distinct entries
  are strictly ordered, so ranks are never ambiguous.
