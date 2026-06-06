/**
 * Canonical Section 5 fixture data for the ATS Results screen.
 *
 * These are the single source of realistic sample responses imported by the
 * Results component tests, the integration tests (mocked `apiFetch` / MSW), and
 * the Playwright visual/layout gates. They conform **exactly** to the backend
 * response contract as generated into `@matchlayer/shared-types` from the
 * FastAPI OpenAPI spec (Req 20.1, 20.2): correct field names, types, and value
 * ranges, and **no invented fields** — no suggestion `title`/`priority`, no
 * third score dimension, and no `job_description_text` anywhere (Req 20.3,
 * 20.5).
 *
 * Each fixture is annotated with the curated generated type, so any drift from
 * the real contract (a renamed field, a removed component, an added required
 * field) becomes a compile error here rather than a runtime surprise in a
 * screen (Req 20.7 — the generated types are authoritative).
 *
 * Conventions (conventions.md): ids are UUIDv7 strings; timestamps are ISO 8601
 * UTC with the `Z` suffix. Keyword lists are ordered by descending weight, and
 * suggestions by descending missing-keyword weight, mirroring what the scorer
 * returns.
 *
 * Source of truth for the values: design.md Section 5 (5.1–5.4).
 */

import type { MatchResponse, ResumeResponse } from "@matchlayer/shared-types";

// ---------------------------------------------------------------------------
// 5.1 — ResumeResponse fixtures (extraction_status: succeeded / pending / failed)
// ---------------------------------------------------------------------------

/** A text-based PDF whose extraction succeeded — the ready-to-score case. */
export const resumeSucceeded: ResumeResponse = {
  id: "0192f1a2-7c3d-7e10-9b8a-4f2c1d6e7a01",
  original_filename: "ada-lovelace-backend-engineer.pdf",
  content_type: "application/pdf",
  byte_size: 248913,
  extraction_status: "succeeded",
  created_at: "2025-02-18T14:32:07Z",
  updated_at: "2025-02-18T14:32:09Z",
};

/** A DOCX still being processed — extraction has not yet completed. */
export const resumePending: ResumeResponse = {
  id: "0192f1a2-9d44-7a21-8c10-2b9e3f4a5c02",
  original_filename: "resume-final-v3.docx",
  content_type:
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  byte_size: 91344,
  extraction_status: "pending",
  created_at: "2025-02-18T14:40:01Z",
  updated_at: "2025-02-18T14:40:01Z",
};

/** A scanned-image PDF that yielded no readable text — extraction failed. */
export const resumeFailed: ResumeResponse = {
  id: "0192f1a2-b0c5-7f32-9d21-7a1c2e3b4d03",
  original_filename: "scanned-resume-image.pdf",
  content_type: "application/pdf",
  byte_size: 4733120,
  extraction_status: "failed",
  created_at: "2025-02-18T15:02:55Z",
  updated_at: "2025-02-18T15:03:10Z",
};

// ---------------------------------------------------------------------------
// 5.2 — ATS fixture A: strong match (score ≈ 85)
// ---------------------------------------------------------------------------
//
// Recompute check (Req 11.2 explainability):
//   round(100 × (0.6 × 0.8123 + 0.4 × 0.9000))
//     = round(100 × (0.48738 + 0.36)) = round(84.738) = 85
// so final_score === score.

/** Strong match: high similarity and keyword coverage, two minor gaps. */
export const matchStrong: MatchResponse = {
  id: "0192f1b0-1a2b-7c3d-8e4f-5a6b7c8d9e10",
  resume_id: "0192f1a2-7c3d-7e10-9b8a-4f2c1d6e7a01",
  score: 85,
  score_breakdown: {
    similarity_component: 0.8123,
    keyword_coverage_component: 0.9,
    weight_similarity: 0.6,
    weight_keyword: 0.4,
    final_score: 85,
  },
  matched_keywords: [
    { term: "python", weight: 0.97 },
    { term: "fastapi", weight: 0.91 },
    { term: "postgresql", weight: 0.88 },
    { term: "rest api", weight: 0.82 },
    { term: "docker", weight: 0.78 },
    { term: "sqlalchemy", weight: 0.71 },
    { term: "ci/cd", weight: 0.64 },
    { term: "pytest", weight: 0.55 },
    { term: "aws", weight: 0.52 },
  ],
  missing_keywords: [
    { term: "kubernetes", weight: 0.69 },
    { term: "terraform", weight: 0.41 },
  ],
  suggestions: [
    {
      keyword: "kubernetes",
      text: "Mention any experience deploying or operating containers on Kubernetes, including local clusters or managed services.",
    },
    {
      keyword: "terraform",
      text: "If you have provisioned infrastructure as code, name Terraform explicitly and describe the resources you managed.",
    },
  ],
  scorer_version: "tfidf-keyword@1.3.0+lexicon.2025-02-01",
  created_at: "2025-02-18T14:33:12Z",
  updated_at: "2025-02-18T14:33:12Z",
};

// ---------------------------------------------------------------------------
// 5.3 — ATS fixture B: partial match (score ≈ 52)
// ---------------------------------------------------------------------------
//
// Recompute check:
//   round(100 × (0.6 × 0.48 + 0.4 × 0.5833))
//     = round(100 × (0.288 + 0.23332)) = round(52.132) = 52

/** Partial match: moderate coverage with several notable missing keywords. */
export const matchPartial: MatchResponse = {
  id: "0192f1b0-3c4d-7e5f-9a6b-7c8d9e0f1a11",
  resume_id: "0192f1a2-7c3d-7e10-9b8a-4f2c1d6e7a01",
  score: 52,
  score_breakdown: {
    similarity_component: 0.48,
    keyword_coverage_component: 0.5833,
    weight_similarity: 0.6,
    weight_keyword: 0.4,
    final_score: 52,
  },
  matched_keywords: [
    { term: "javascript", weight: 0.84 },
    { term: "react", weight: 0.8 },
    { term: "css", weight: 0.61 },
    { term: "git", weight: 0.44 },
  ],
  missing_keywords: [
    { term: "typescript", weight: 0.88 },
    { term: "next.js", weight: 0.79 },
    { term: "graphql", weight: 0.66 },
    { term: "testing library", weight: 0.49 },
    { term: "accessibility", weight: 0.38 },
  ],
  suggestions: [
    {
      keyword: "typescript",
      text: "Add TypeScript to your skills if you have used it; convert a sample of your JavaScript bullet points to reference typed codebases.",
    },
    {
      keyword: "next.js",
      text: "Reference Next.js by name if you have built React apps with server-side rendering or the App Router.",
    },
    {
      keyword: "graphql",
      text: "If you have queried or built GraphQL APIs, describe the schemas or clients you worked with.",
    },
    {
      keyword: "testing library",
      text: "Call out component testing experience with Testing Library or a comparable framework.",
    },
    {
      keyword: "accessibility",
      text: "Note any work meeting WCAG or accessibility standards in your front-end roles.",
    },
  ],
  scorer_version: "tfidf-keyword@1.3.0+lexicon.2025-02-01",
  created_at: "2025-02-18T16:10:44Z",
  updated_at: "2025-02-18T16:10:44Z",
};

// ---------------------------------------------------------------------------
// 5.4 — ATS fixture C: degenerate / empty (score 0, both components 0)
// ---------------------------------------------------------------------------
//
// The trigger fixture for the Empty_Result_State (Req 12.5): both components
// are 0 and the single suggestion has an empty `keyword` (the affirmative /
// diagnostic form). It is a **valid result**, not an error (Req 12.6, 12.7) —
// it must never render with the `danger` token.

/** Degenerate match: no readable content extracted; affirmative-only suggestion. */
export const matchDegenerate: MatchResponse = {
  id: "0192f1b0-5e6f-7a7b-9c8d-9e0f1a2b3c12",
  resume_id: "0192f1a2-b0c5-7f32-9d21-7a1c2e3b4d03",
  score: 0,
  score_breakdown: {
    similarity_component: 0.0,
    keyword_coverage_component: 0.0,
    weight_similarity: 0.6,
    weight_keyword: 0.4,
    final_score: 0,
  },
  matched_keywords: [],
  missing_keywords: [],
  suggestions: [
    {
      keyword: "",
      text: "We could not extract enough readable text to analyze this match. Try uploading a text-based PDF or DOCX rather than a scanned image, then run the analysis again.",
    },
  ],
  scorer_version: "tfidf-keyword@1.3.0+lexicon.2025-02-01",
  created_at: "2025-02-18T15:03:30Z",
  updated_at: "2025-02-18T15:03:30Z",
};
