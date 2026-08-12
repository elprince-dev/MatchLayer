// Curated public API of `@matchlayer/shared-types`.
//
// This module is the single import surface for sibling packages. The two files
// it re-exports from — `./api-types` and `./api-schemas` — are auto-generated
// by `pnpm codegen` from the FastAPI OpenAPI spec and must not be imported
// directly by app code. Keeping consumers behind named curated exports here
// (e.g. `LoginRequest`, `LoginRequestSchema`) means the ugly path-indexed
// `paths["/api/v1/auth/login"]["post"]["requestBody"]["content"]...` type and
// the `schemas.LoginRequest` Zod object never leak into the rest of the
// monorepo. Satisfies AC 7.9 (design §8.4) and OpenAPI Codegen Impact.

import type { paths } from "./api-types";
import { schemas } from "./api-schemas";

// ---------------------------------------------------------------------------
// Health
// ---------------------------------------------------------------------------

export type HealthResponse =
  paths["/healthz"]["get"]["responses"]["200"]["content"]["application/json"];

export const HealthResponseSchema = schemas.HealthResponse;

// ---------------------------------------------------------------------------
// Auth — Register
// ---------------------------------------------------------------------------

export type RegisterRequest =
  paths["/api/v1/auth/register"]["post"]["requestBody"]["content"]["application/json"];
export type RegisterResponse =
  paths["/api/v1/auth/register"]["post"]["responses"]["201"]["content"]["application/json"];

export const RegisterRequestSchema = schemas.RegisterRequest;
export const RegisterResponseSchema = schemas.TokenPairResponse;

// ---------------------------------------------------------------------------
// Auth — Login
// ---------------------------------------------------------------------------

export type LoginRequest =
  paths["/api/v1/auth/login"]["post"]["requestBody"]["content"]["application/json"];
export type LoginResponse =
  paths["/api/v1/auth/login"]["post"]["responses"]["200"]["content"]["application/json"];

export const LoginRequestSchema = schemas.LoginRequest;
export const LoginResponseSchema = schemas.TokenPairResponse;

// ---------------------------------------------------------------------------
// Auth — Refresh
// ---------------------------------------------------------------------------

export type RefreshResponse =
  paths["/api/v1/auth/refresh"]["post"]["responses"]["200"]["content"]["application/json"];

export const RefreshResponseSchema = schemas.TokenPairResponse;

// ---------------------------------------------------------------------------
// Auth — Logout (no body, 204)
// ---------------------------------------------------------------------------
// Logout has no request body and a 204 response, so no curated alias.

// ---------------------------------------------------------------------------
// Auth — Password reset
// ---------------------------------------------------------------------------

export type PasswordResetRequestRequest =
  paths["/api/v1/auth/password-reset/request"]["post"]["requestBody"]["content"]["application/json"];

export const PasswordResetRequestRequestSchema =
  schemas.PasswordResetRequestRequest;

export type PasswordResetConfirmRequest =
  paths["/api/v1/auth/password-reset/confirm"]["post"]["requestBody"]["content"]["application/json"];

export const PasswordResetConfirmRequestSchema =
  schemas.PasswordResetConfirmRequest;

// ---------------------------------------------------------------------------
// Auth — /me
// ---------------------------------------------------------------------------

export type MeResponse =
  paths["/api/v1/auth/me"]["get"]["responses"]["200"]["content"]["application/json"];

export const MeResponseSchema = schemas.MeResponse;

export type MePatchRequest =
  paths["/api/v1/auth/me"]["patch"]["requestBody"]["content"]["application/json"];

export const MePatchRequestSchema = schemas.MePatchRequest;

// ---------------------------------------------------------------------------
// Shared user response shape (embedded in token-pair responses)
// ---------------------------------------------------------------------------

export const UserResponseSchema = schemas.UserResponse;

// ---------------------------------------------------------------------------
// Resumes — upload / get (safe field set: no extracted_text or storage_key)
// ---------------------------------------------------------------------------

export type ResumeResponse =
  paths["/api/v1/resumes"]["post"]["responses"]["201"]["content"]["application/json"];

export const ResumeResponseSchema = schemas.ResumeResponse;

// ---------------------------------------------------------------------------
// Resumes — list (cursor-paginated)
// ---------------------------------------------------------------------------

export type ResumeListResponse =
  paths["/api/v1/resumes"]["get"]["responses"]["200"]["content"]["application/json"];

export const ResumeListResponseSchema = schemas.ResumeListResponse;

// ---------------------------------------------------------------------------
// Matches — create
// ---------------------------------------------------------------------------

export type CreateMatchRequest =
  paths["/api/v1/matches"]["post"]["requestBody"]["content"]["application/json"];
export type MatchResponse =
  paths["/api/v1/matches"]["post"]["responses"]["201"]["content"]["application/json"];

export const CreateMatchRequestSchema = schemas.CreateMatchRequest;
export const MatchResponseSchema = schemas.MatchResponse;

// ---------------------------------------------------------------------------
// Matches — nested value objects (curated names for the generated *Out shapes)
// ---------------------------------------------------------------------------
//
// The OpenAPI generator names these `ScoreBreakdownOut`, `KeywordOut`, and
// `SuggestionOut`. The frontend design (Data Models, §"Key interface
// boundaries") and the component tasks consume them under the curated names
// `ScoreBreakdown`, `Keyword`, and `Suggestion`. The types are derived from
// `MatchResponse` so they stay in lockstep with the generated contract and can
// never drift from the fields the API actually returns (Req 20.1, 20.7,
// 21.11). The Zod schemas alias the generated `schemas.*Out` objects.
//
// Contract guarantees surfaced through these names: `Suggestion` carries only
// `{ keyword, text }` — no `title`, no `priority`; `ScoreBreakdown` carries
// exactly the two components + their weights + `final_score` — no third score
// dimension.

export type ScoreBreakdown = MatchResponse["score_breakdown"];
export type Keyword = MatchResponse["matched_keywords"][number];
export type Suggestion = MatchResponse["suggestions"][number];

export const ScoreBreakdownSchema = schemas.ScoreBreakdownOut;
export const KeywordSchema = schemas.KeywordOut;
export const SuggestionSchema = schemas.SuggestionOut;

// ---------------------------------------------------------------------------
// Matches — list (items omit job_description_text)
// ---------------------------------------------------------------------------

export type MatchListResponse =
  paths["/api/v1/matches"]["get"]["responses"]["200"]["content"]["application/json"];

export const MatchListResponseSchema = schemas.MatchListResponse;

// ---------------------------------------------------------------------------
// Health — LLM availability (Phase 3, additive `llm` field)
// ---------------------------------------------------------------------------
//
// The two-value availability enum follows the Phase 2 `semantic_scoring`
// pattern: derived from `HealthResponse` so it can never drift from the
// contract (Req 10.1). `"unavailable"` covers both key-absent and
// spend-breaker-open states — the health surface never distinguishes them.

export type LlmHealthStatus = HealthResponse["llm"];

// ---------------------------------------------------------------------------
// LLM — shared envelope pieces (Phase 3)
// ---------------------------------------------------------------------------
//
// Every LLM feature response is an `LLMResultEnvelope[T]` parameterization
// (Req 8.4, 9.2): `is_fallback` marks Fallback_Responses built without the
// LLM, `fallback_reason` carries the closed `FailureReason` enum (null for
// LLM-produced results), and `result` is the feature payload — fallback
// content conforms to the same `result` schema. `FailureReason` is derived
// from the envelope's `fallback_reason` field so the curated name stays in
// lockstep with the generated union.

export type FailureReason = NonNullable<
  CoachingReportEnvelope["fallback_reason"]
>;

export const FailureReasonSchema = schemas.FailureReason;

// ---------------------------------------------------------------------------
// LLM — Coaching reports (Resume_Coach)
// ---------------------------------------------------------------------------

export type CoachingReportEnvelope =
  paths["/api/v1/matches/{match_id}/coaching-reports"]["post"]["responses"]["200"]["content"]["application/json"];
export type CoachingReport = CoachingReportEnvelope["result"];
export type ImprovementAction = CoachingReport["improvements"][number];
export type CoachingReportListResponse =
  paths["/api/v1/matches/{match_id}/coaching-reports"]["get"]["responses"]["200"]["content"]["application/json"];

export const CoachingReportEnvelopeSchema =
  schemas.LLMResultEnvelope_CoachingReport_;
export const CoachingReportSchema = schemas.CoachingReport;
export const ImprovementActionSchema = schemas.ImprovementAction;
export const CoachingReportListResponseSchema =
  schemas.CoachingReportListResponse;

// ---------------------------------------------------------------------------
// LLM — Bullet rewrites (Bullet_Rewriter)
// ---------------------------------------------------------------------------
//
// `BulletRewriteRequestSchema` is the generated Zod schema the frontend uses
// for client-side bullet validation (count/length/non-empty) before any
// request is sent (Req 17.7).

export type BulletRewriteRequest =
  paths["/api/v1/matches/{match_id}/bullet-rewrites"]["post"]["requestBody"]["content"]["application/json"];
export type BulletRewriteEnvelope =
  paths["/api/v1/matches/{match_id}/bullet-rewrites"]["post"]["responses"]["200"]["content"]["application/json"];
export type BulletRewrite = BulletRewriteEnvelope["result"];
export type BulletRewriteEntry = BulletRewrite["entries"][number];
export type BulletRewriteListResponse =
  paths["/api/v1/matches/{match_id}/bullet-rewrites"]["get"]["responses"]["200"]["content"]["application/json"];

export const BulletRewriteRequestSchema = schemas.BulletRewriteRequest;
export const BulletRewriteEnvelopeSchema =
  schemas.LLMResultEnvelope_BulletRewrite_;
export const BulletRewriteSchema = schemas.BulletRewrite;
export const BulletRewriteEntrySchema = schemas.BulletRewriteEntry;
export const BulletRewriteListResponseSchema =
  schemas.BulletRewriteListResponse;

// ---------------------------------------------------------------------------
// LLM — Interview question sets (Interview_Question_Generator)
// ---------------------------------------------------------------------------

export type InterviewQuestionSetEnvelope =
  paths["/api/v1/matches/{match_id}/interview-question-sets"]["post"]["responses"]["200"]["content"]["application/json"];
export type InterviewQuestionSet = InterviewQuestionSetEnvelope["result"];
export type InterviewQuestion = InterviewQuestionSet["questions"][number];
export type InterviewQuestionCategory = InterviewQuestion["category"];
export type InterviewQuestionSetListResponse =
  paths["/api/v1/matches/{match_id}/interview-question-sets"]["get"]["responses"]["200"]["content"]["application/json"];

export const InterviewQuestionSetEnvelopeSchema =
  schemas.LLMResultEnvelope_InterviewQuestionSet_;
export const InterviewQuestionSetSchema = schemas.InterviewQuestionSet;
export const InterviewQuestionSchema = schemas.InterviewQuestion;
export const InterviewQuestionCategorySchema =
  schemas.InterviewQuestionCategory;
export const InterviewQuestionSetListResponseSchema =
  schemas.InterviewQuestionSetListResponse;
