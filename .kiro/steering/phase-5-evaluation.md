---
inclusion: manual
---

# Phase 5 — AI Testing & Evaluation

**Status:** not started
**Depends on:** Phase 4 shipped.
**Goal:** build a real evaluation framework for the LLM and agent layers. Track prompt quality over time, catch regressions, surface hallucinations, and prove (with data) that changes improve outputs.

## Why this phase exists
Almost no portfolio project does this. Almost every real AI company does. Building it demonstrates LLMOps maturity — that you understand AI systems can't be tested with traditional unit tests alone, and that prompt changes need their own CI loop.

## In scope
- **Evaluation framework**
  - DeepEval as the core library.
  - Test suites organized per feature: resume coach, interview questions, skill gap, ATS scoring.
  - Metrics:
    - **Hallucination** — does the rewrite invent skills not in the resume?
    - **Faithfulness** — do interview questions reference the actual JD content?
    - **Relevance** — does the skill gap match the JD's requirements?
    - **Consistency** — same input → similar output across runs (using semantic similarity, not exact match).
    - **JSON schema validity** — outputs always parse against the Pydantic schema.
  - Each metric has a passing threshold; suites fail CI when any metric drops below threshold.
- **Test cases**
  - Curated golden dataset: 50–100 resume/JD pairs with expected behaviors annotated.
  - Mix of: senior/junior, multiple industries, well-written/poorly-written resumes.
  - Stored in `ml/evals/datasets/` versioned in git.
- **Prompt versioning + replay**
  - Every prompt change runs the full eval suite for that feature in CI.
  - Past LLM calls (logged in Phase 3's `llm_calls` table) can be replayed against new prompts to measure deltas on real user data.
- **Evaluation dashboard**
  - Internal-only page (admin route): per-prompt-version pass rates, score distributions, failed cases drill-down.
  - Cost-per-quality view: cost per accepted output, useful for model-selection decisions.
- **CI integration**
  - GitHub Actions workflow: on PR touching `prompts/` or `ml/agents/`, run evals on a representative subset (~20 cases). Full suite runs nightly on `main`.

## Explicitly out of scope
- Auto-prompt-tuning / DSPy / similar automated prompt optimization.
- Reinforcement learning from human feedback (RLHF). 
- Human-in-the-loop labeling UI (consider for Phase 7).
- Evaluating frontend UX.

## Deliverables
1. DeepEval suites for every LLM-touching feature.
2. Golden dataset committed to repo.
3. Evaluation dashboard accessible to admins.
4. CI gate that blocks prompt-change PRs failing eval thresholds.
5. ADR (`0005-evaluation-strategy.md`) covering metric choices and thresholds.

## Success criteria
- Eval suite runs in < 5 minutes on the PR-subset, < 30 minutes on the full nightly.
- A deliberate prompt regression is caught by CI before merge.
- Dashboard surfaces at least one real quality issue you didn't notice (this is the value-validation moment).
- 100% of LLM outputs in production pass schema validation (already required from Phase 3).

## Skills demonstrated
LLMOps · DeepEval · evaluation pipelines · prompt versioning · CI for AI systems · quality dashboards · golden datasets

## Risks & gotchas
- **Eval-as-LLM-judge bias.** DeepEval often uses an LLM to judge outputs. The judge has its own biases. Use a different model for judging than for production (e.g., Claude judge for OpenAI outputs).
- **Cost.** Running a 100-case eval on every PR is expensive. The PR-subset / nightly-full split is the mitigation.
- **Threshold gaming.** If thresholds are too lenient, regressions slip through. Too strict, and you can't ship anything. Start lenient, tighten over time, document each change.
- **Dataset rot.** As prompts improve, hard cases become easy. Periodically retire trivial cases and add new harder ones.
- **No silver-bullet metric.** Hallucination scores correlate with quality but don't replace human review. Spot-check 10% of outputs by hand even when metrics pass.

## Folder additions
```
ml/evals/
  datasets/
    coach_golden.json
    interview_golden.json
    skill_gap_golden.json
  suites/
    test_resume_coach.py
    test_interview_questions.py
    test_skill_gap.py
    test_consistency.py
  judge/                            # custom DeepEval judge configs
  runners/
    run_pr_subset.py
    run_full_suite.py
    replay_production.py
apps/web/src/app/(admin)/evals/     # dashboard pages
apps/api/src/matchlayer_api/api/admin/evals/
.github/workflows/llm-evals.yml
docs/adr/0005-evaluation-strategy.md
```

DB additions:
- `eval_runs` (id, suite_name, prompt_version, model, run_started_at, run_completed_at, total_cases, passed_cases, failed_cases, metrics_json)
- `eval_case_results` (id, eval_run_id, case_id, metrics_json, output_json, passed)

## Work breakdown
1. Build the golden dataset — 50 cases minimum, hand-annotated.
2. Stand up DeepEval, write the first hallucination metric for resume coach.
3. Add JSON schema validity test (already free since Phase 3 enforces this).
4. Implement consistency metric using sentence-transformers similarity.
5. Wire CI: PR workflow runs subset on changed prompts.
6. Build replay-against-production-data script.
7. Implement admin dashboard reading from `eval_runs` and `eval_case_results`.
8. Add nightly full-suite workflow.
9. Write the ADR explaining metric choices and thresholds.
10. Stress-test: deliberately regress a prompt and verify CI catches it.

## Definition of done
Every prompt change in a PR triggers an eval run. Failing thresholds block merge. The admin dashboard shows quality trends over time. There's at least one documented case of the eval suite catching a regression before it reached users.
