# Applicant Tracking System scoring in Phase 1

## Introduction

When you apply for a job online, the software that receives your resume and ranks
it against the role is called an Applicant Tracking System (ATS) — a tool
employers use to collect, search, and rank job applications. A _match score_ is
that system's headline output: a single number that estimates how well one
resume fits one job description. This document explains what that score means in
Phase 1, how it is produced by a deterministic (always-same-output-for-the-same-input)
calculation that uses no Large Language Model (LLM) — a Large Language Model
being an Artificial Intelligence (AI) system that predicts text and whose outputs
vary and cost money per call — and why the first phase of the product
deliberately chooses that plain
approach over anything cleverer.

This is the entry-point document for the matching and scoring track. Later
documents drill into the individual techniques (text statistics, keyword
overlap, suggestion generation); this one gives you the shape of the whole
scoring surface and the reasoning behind its design.

**Learning outcomes** — after reading this document you will be able to:

- Explain what an ATS match score represents and what it does not represent.
- Describe what makes the Phase 1 scorer deterministic and why that property is valuable.
- Explain the two reasons the first phase avoids machine-learning models: building working infrastructure before adding intelligence, and a strict monthly cost ceiling.
- Identify where the scoring logic lives and how it stays reproducible across runs.

Prerequisites: No prerequisites.

## Problem it solves

A job seeker who submits a resume into an automated hiring pipeline gets almost
no feedback. The ranking is opaque, so candidates tweak their resumes blindly,
guessing at which words or skills the system rewards. The concrete problem is
making that hidden matching process visible: turning "your application
disappeared into a black box" into "here is a score, here is what matched, and
here is what is missing".

The prior state, before any scoring exists, is manual guesswork. A candidate
reads the job posting, eyeballs their resume, and hopes the overlap is good
enough. There is no number to act on and no list of missing terms to fix. The
opposite extreme — reaching straight for a large, paid AI model to "read" both
documents and judge fit — looks attractive but carries real costs: every
evaluation calls an external service, the answers drift from one call to the
next, and the bill grows with usage.

The product takes a middle path framed by two principles. The first is
_infrastructure before intelligence_: get the full end-to-end flow — upload,
score, show results — working with simple, predictable logic, then layer
smarter models on top in later phases once the plumbing is proven. The second is
a hard cost constraint: total monthly spend across the early phases must stay
under twenty dollars, which rules out per-request paid model calls as the
scoring engine. A deterministic, locally computed score solves the visibility
problem while honoring both principles.

## Mental model

Think of the Phase 1 scorer as a **standardized exam grader**, not a human
interviewer. A human interviewer forms a holistic judgment that might differ
slightly each time they read the same resume. A standardized grader runs every
paper through the identical rubric and arithmetic, so the same paper always
earns the same mark, and anyone can re-check the arithmetic by hand. The grader
does not understand the candidate's career story — it measures concrete, defined
signals and adds them up.

Two signals feed the grade. The first is **term overlap weighted by rarity**:
words that appear in both the resume and the job description count for more when
they are rare across the pair, because a rare shared word (a specific framework
name) is stronger evidence of fit than a common one ("experience"). This is the
idea behind Term Frequency–Inverse Document Frequency (TF-IDF), a way of turning
text into numbers where each word's weight rises with how often it occurs in one
document and falls with how common it is overall. The second signal is **skill
coverage**: of the meaningful skills named in the job description, what fraction
also appear in the resume.

Walked through step by step, scoring one resume against one job description looks
like this:

1. Normalize both texts — lowercase them and collapse runs of whitespace so that
   formatting differences do not affect the result.
2. Turn the two documents into TF-IDF number vectors and measure their _cosine
   similarity_ — the cosine of the angle between the two vectors, a value from 0
   (nothing in common) to 1 (identical direction).
3. Compare the job's meaningful skills against the resume and compute the
   covered fraction.
4. Combine the two signals with fixed weights, scale the result to a 0–100
   integer, and round it.
5. From the skills that were required but missing, generate plain-language
   suggestions — no model, no guessing, one suggestion per gap.

The number you get out is repeatable: feed the same two texts in tomorrow and
you get the identical score.

## How it works

An ATS match score is a compact summary of alignment between two documents: a
candidate's resume and a role's job description. A deterministic scorer computes
that summary purely from the text in front of it, using fixed arithmetic and no
trained model, so identical inputs always yield an identical output. That
property — same input, same output, every time — is what _deterministic_ means,
and it stands in contrast to a statistical model whose answers can shift between
runs or between versions.

The first ingredient is a text-similarity measure. Term Frequency–Inverse
Document Frequency (TF-IDF) converts each document into a vector of numbers, one
per word, where a word's weight grows with how often it appears in that document
and shrinks with how widely it appears across the document set. Common filler
words end up with low weight; distinctive terms end up with high weight. Once
both documents are vectors, _cosine similarity_ measures the angle between them:
because TF-IDF weights are never negative, the cosine falls between 0 and 1,
where 1 means the two documents point in the same direction (highly similar) and
0 means they share no weighted vocabulary at all.

The second ingredient is keyword coverage. A curated list of meaningful skills —
sometimes called a skill lexicon, a versioned dictionary of recognized skill
terms and their alternate spellings — is used to find which skills the job
description asks for and which of those the resume actually contains. Coverage is
then the size of the matched set divided by the size of the analyzed set, a
fraction from 0 to 1. Because the lexicon knows that, for example, a skill can be
written several ways, this half catches overlaps that raw word matching alone
would miss.

The two fractions are combined with fixed weights that sum to 1, multiplied by
100, rounded, and clamped into the 0–100 range so the output is always a valid
percentage. A degenerate case — an empty resume or an empty job description —
produces a score of 0 rather than an error. Finally, the terms the job required
but the resume lacked are fed into a rule-based suggestion step: each missing
term yields one concrete, templated piece of advice. No part of this pipeline
invokes a language model, so there is no per-request inference cost and no
run-to-run variation. The whole calculation can be re-derived by hand from the
two component fractions and the weights, which makes the score explainable rather
than mysterious.

## MatchLayer Phase 1 usage

In MatchLayer the deterministic scorer is a small, framework-free core under
`apps/api/src/matchlayer_api/scoring/scorer.py`. The class `Match_Scorer` owns the
algorithm: it normalizes the two texts, computes the TF-IDF cosine similarity,
asks a sibling analyzer for keyword coverage, and folds the two into a final
0–100 integer. The combining step is the whole "deterministic, non-LLM" claim in
four lines — weight, scale, round, clamp, with no randomness and no external
call:

Source: `apps/api/src/matchlayer_api/scoring/scorer.py`

```python
    def _combine(self, similarity: float, coverage: float) -> int:
        weighted = self._w_similarity * similarity + self._w_keyword * coverage
        scaled = round(100 * weighted)
        return max(0, min(100, scaled))
```

The scoring core imports only scikit-learn (for TF-IDF and cosine similarity),
the Python standard library, and its sibling scoring modules. It never imports
the web framework, the database layer, or the settings object. That separation is
deliberate: the framework-facing world reaches the scorer through a thin adapter
at `apps/api/src/matchlayer_api/ml/scorer_adapter.py`, which reads the configured
weights and caps, builds one shared `Match_Scorer` for the whole process, and
then does nothing but hand text in and pass the result back out:

Source: `apps/api/src/matchlayer_api/ml/scorer_adapter.py`

```python
def score(resume_text: str, job_description: str) -> ScoreResult:
    return get_scorer().score(resume_text, job_description)
```

This split mirrors the repository's larger `ml/`-versus-`apps/api/` boundary —
training and lexicon-building code lives under the top-level `ml/` directory
(for example the lexicon build pipeline `ml/pipelines/build_skill_lexicon.py`),
while the request-path scorer lives inside the
Application Programming Interface (API) package and consumes the committed
artifact at
`apps/api/src/matchlayer_api/scoring/data/skill_lexicon.v1.json`. Every score is
stamped with a `Scorer_Version` derived from that lexicon, so a stored result
records exactly which scorer produced it and can be reproduced later.

The reason this stays deliberately plain is the product's phasing strategy.
Phase 1 follows _infrastructure before intelligence_: it ships the entire
upload-score-suggest flow on top of arithmetic that any developer can read and
verify, deferring Sentence Transformers and embeddings to Phase 2 and any
language-model coaching to Phase 3. It also respects a hard cost ceiling — total
monthly spend for the early phases must stay under twenty dollars — which a
per-request paid model call would blow through immediately. A locally computed,
deterministic score costs nothing per request and never varies, which is exactly
what an early, cost-capped, end-to-end product needs.

## Common pitfalls

- **Mistake:** Expecting TF-IDF and cosine similarity to understand meaning, so
  that synonyms or paraphrases are treated as matches.
  **Symptom:** A genuinely strong candidate scores low because their resume uses
  different wording than the job description — describing Representational State
  Transfer (REST) work as "built REST services" while the posting says "API
  development" — even though the experience is equivalent.
  **Recovery:** Lean on the skill-lexicon coverage half, which maps known
  alternate spellings to the same skill, and treat semantic matching as a Phase 2
  embeddings concern rather than a defect in the Phase 1 scorer.

- **Mistake:** Reading the 0–100 number as a calibrated probability of getting
  hired or as objective ground truth about the candidate.
  **Symptom:** Product copy or downstream logic states things like "you have a
  72% chance" or gates decisions on the raw number, and users over-trust a value
  that is only a weighted text-overlap measure.
  **Recovery:** Present the score as an explainable signal backed by its matched
  and missing terms, and surface the component breakdown so a reader can see what
  drove the number rather than treating it as a verdict.

- **Mistake:** Introducing run-to-run variation into the scorer — for example
  adding randomness, reading mutable global state, or swapping in an unpinned
  model.
  **Symptom:** Scoring the same resume and job description twice returns
  different numbers, and stored results can no longer be reproduced or trusted in
  tests.
  **Recovery:** Keep the scoring core pure and free of external state, inject all
  configuration through the constructor, and stamp every result with the
  `Scorer_Version` so identical inputs under one version always yield one output.

- **Mistake:** Assuming a candidate cannot game the score by stuffing the job
  description's keywords into their resume.
  **Symptom:** A keyword-stuffed resume with no real coherence earns an
  inflated coverage fraction and a misleadingly high score.
  **Recovery:** Keep both halves in the formula — the TF-IDF similarity component
  resists raw term stuffing because it weighs whole-document structure — and cap
  the number of analyzed keywords so a flood of repeated terms cannot dominate.

## External reading

- [scikit-learn: TfidfVectorizer](https://scikit-learn.org/stable/modules/generated/sklearn.feature_extraction.text.TfidfVectorizer.html)
- [scikit-learn: cosine_similarity](https://scikit-learn.org/stable/modules/generated/sklearn.metrics.pairwise.cosine_similarity.html)
- [scikit-learn: Text feature extraction guide](https://scikit-learn.org/stable/modules/feature_extraction.html)
