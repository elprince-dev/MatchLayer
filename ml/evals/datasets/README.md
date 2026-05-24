# Eval datasets

Resume + JD pairs used for validating the matching pipeline across phases.

## Structure

```
datasets/
├── eyeball/                # 5–10 hand-curated pairs for quick checks during Phase 1 & 2
├── golden/                 # 50–100 hand-annotated pairs for Phase 5 DeepEval suites
├── adversarial/            # Prompt-injection + keyword-stuffing test cases (Phase 5)
└── private/                # gitignored — your real resume + JDs you actually applied to
```

Only `eyeball/`, `golden/`, and `adversarial/` are committed. `private/` is gitignored — keep your own data there if you use it for testing.

## Schema

Each pair is one JSON file:

```json
{
  "id": "eyeball-001",
  "label": "Backend SWE — strong match",
  "resume_text": "...",
  "jd_text": "...",
  "expected": {
    "score_band": "high",          // "low" | "medium" | "high"
    "must_match_skills": ["python", "fastapi", "postgres"],
    "must_miss_skills": []
  },
  "notes": "Used as the canonical good-match case."
}
```

Resumes/JDs sourced from public datasets (Kaggle, HuggingFace) should be cited in `notes`. Anything personal goes in `private/`.

## When each is used

- **eyeball/** — populated late in Phase 1 (3–5 pairs covering: strong match, clear mismatch, partial match, keyword-stuffed adversarial). Used for "does the score look reasonable?" sanity checks.
- **eyeball/** expanded in Phase 2 to ~20 pairs, used to validate that Phase 2's semantic scoring beats Phase 1's TF-IDF on the same pairs.
- **golden/** — built in Phase 5. Hand-annotated, broader, used in DeepEval suites that gate prompt-change PRs.
- **adversarial/** — built in Phase 5. Specifically tries to break things (prompt injection, keyword stuffing, contradictory content).
