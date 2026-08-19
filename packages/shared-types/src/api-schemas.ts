import { makeApi, Zodios, type ZodiosOptions } from "@zodios/core";
import { z } from "zod";

const HealthResponse = z
  .object({
    status: z.string().optional().default("ok"),
    semantic_scoring: z.enum(["available", "unavailable"]),
    llm: z.enum(["available", "unavailable"]),
    agents: z.enum(["available", "unavailable"]),
  })
  .passthrough();
const HealthUnhealthyResponse = z
  .object({
    status: z.string().optional().default("unhealthy"),
    reason: z.string(),
  })
  .passthrough();
const RegisterRequest = z.object({
  email: z.string().email(),
  password: z.string().min(12),
  display_name: z.union([z.string(), z.null()]).optional(),
});
const UserResponse = z
  .object({
    id: z.string(),
    email: z.string(),
    display_name: z.string(),
    created_at: z.string().datetime({ offset: true }),
    updated_at: z.string().datetime({ offset: true }),
  })
  .passthrough();
const TokenPairResponse = z.object({
  access_token: z.string(),
  user: UserResponse,
});
const ValidationError = z
  .object({
    loc: z.array(z.union([z.string(), z.number()])),
    msg: z.string(),
    type: z.string(),
    input: z.unknown().optional(),
    ctx: z.object({}).partial().passthrough().optional(),
  })
  .passthrough();
const HTTPValidationError = z
  .object({ detail: z.array(ValidationError) })
  .partial()
  .passthrough();
const LoginRequest = z.object({
  email: z.string().email(),
  password: z.string().min(1),
});
const MeResponse = z
  .object({
    id: z.string(),
    email: z.string(),
    display_name: z.string(),
    created_at: z.string().datetime({ offset: true }),
    updated_at: z.string().datetime({ offset: true }),
  })
  .passthrough();
const MePatchRequest = z
  .object({ display_name: z.union([z.string(), z.null()]) })
  .partial();
const PasswordResetRequestRequest = z.object({ email: z.string().email() });
const PasswordResetConfirmRequest = z.object({
  token: z.string().min(1),
  new_password: z.string().min(12),
});
const Body_create_resume_api_v1_resumes_post = z
  .object({ file: z.string() })
  .passthrough();
const Idempotency_Key = z.union([z.string(), z.null()]).optional();
const ResumeResponse = z
  .object({
    id: z.string(),
    original_filename: z.string(),
    content_type: z.string(),
    byte_size: z.number().int(),
    extraction_status: z.enum(["pending", "succeeded", "failed"]),
    created_at: z.string().datetime({ offset: true }),
    updated_at: z.string().datetime({ offset: true }),
  })
  .passthrough();
const ResumeListResponse = z.object({
  items: z.array(ResumeResponse),
  next_cursor: z.union([z.string(), z.null()]).optional(),
});
const CreateMatchRequest = z.object({
  resume_id: z.string().min(1),
  job_description: z.string().min(1),
});
const ScoreBreakdownOut = z
  .object({
    similarity_component: z.number(),
    keyword_coverage_component: z.number(),
    weight_similarity: z.number(),
    weight_keyword: z.number(),
    final_score: z.number().int(),
    similarity_method: z.union([z.string(), z.null()]).optional(),
  })
  .passthrough();
const KeywordOut = z
  .object({ term: z.string(), weight: z.number() })
  .passthrough();
const SuggestionOut = z
  .object({ keyword: z.string(), text: z.string() })
  .passthrough();
const MatchResponse = z
  .object({
    id: z.string(),
    resume_id: z.string(),
    score: z.number().int(),
    score_breakdown: ScoreBreakdownOut,
    matched_keywords: z.array(KeywordOut),
    missing_keywords: z.array(KeywordOut),
    suggestions: z.array(SuggestionOut),
    scorer_version: z.string(),
    created_at: z.string().datetime({ offset: true }),
    updated_at: z.string().datetime({ offset: true }),
  })
  .passthrough();
const MatchListItem = z
  .object({
    id: z.string(),
    resume_id: z.string(),
    score: z.number().int(),
    created_at: z.string().datetime({ offset: true }),
  })
  .passthrough();
const MatchListResponse = z.object({
  items: z.array(MatchListItem),
  next_cursor: z.union([z.string(), z.null()]).optional(),
});
const AnalyzeAcceptedResponse = z.object({
  id: z.string(),
  status: z.enum(["queued", "running"]),
  job_url: z.string(),
});
const FailureReason = z.enum([
  "provider_error",
  "timeout",
  "schema_validation_failed",
  "redaction_failed",
  "prompt_template_missing",
  "quota_accounting_unavailable",
  "llm_unavailable",
]);
const matchlayer_api__services__llm__schemas__ImprovementAction = z.object({
  priority: z.number().int().gte(1),
  action: z.string().min(1),
});
const CoachingReport = z.object({
  summary: z.string().min(1),
  strengths: z.array(z.string()),
  gaps: z.array(z.string()),
  improvements: z
    .array(matchlayer_api__services__llm__schemas__ImprovementAction)
    .min(3)
    .max(10),
});
const LLMResultEnvelope_CoachingReport_ = z.object({
  id: z.union([z.string(), z.null()]).optional(),
  is_fallback: z.boolean(),
  fallback_reason: z.union([FailureReason, z.null()]).optional(),
  prompt_template_version: z.union([z.number(), z.null()]).optional(),
  created_at: z.union([z.string(), z.null()]).optional(),
  result: CoachingReport,
});
const CoachingReportListResponse = z
  .object({
    items: z.array(LLMResultEnvelope_CoachingReport_),
    next_cursor: z.union([z.string(), z.null()]).optional(),
  })
  .passthrough();
const BulletRewriteRequest = z.object({ bullets: z.array(z.string()).min(1) });
const BulletRewriteEntry = z.object({
  original: z.string(),
  alternatives: z.array(z.string().min(1)).min(1).max(3),
  rationale: z.string().min(1),
});
const BulletRewrite = z.object({ entries: z.array(BulletRewriteEntry).min(1) });
const LLMResultEnvelope_BulletRewrite_ = z.object({
  id: z.union([z.string(), z.null()]).optional(),
  is_fallback: z.boolean(),
  fallback_reason: z.union([FailureReason, z.null()]).optional(),
  prompt_template_version: z.union([z.number(), z.null()]).optional(),
  created_at: z.union([z.string(), z.null()]).optional(),
  result: BulletRewrite,
});
const BulletRewriteListResponse = z
  .object({
    items: z.array(LLMResultEnvelope_BulletRewrite_),
    next_cursor: z.union([z.string(), z.null()]).optional(),
  })
  .passthrough();
const InterviewQuestionCategory = z.enum([
  "technical",
  "behavioral",
  "experience-gap",
]);
const InterviewQuestion = z.object({
  question: z.string().min(1).max(300),
  category: InterviewQuestionCategory,
  reason: z.string().min(1).max(500),
});
const InterviewQuestionSet = z.object({
  questions: z.array(InterviewQuestion).min(5),
});
const LLMResultEnvelope_InterviewQuestionSet_ = z.object({
  id: z.union([z.string(), z.null()]).optional(),
  is_fallback: z.boolean(),
  fallback_reason: z.union([FailureReason, z.null()]).optional(),
  prompt_template_version: z.union([z.number(), z.null()]).optional(),
  created_at: z.union([z.string(), z.null()]).optional(),
  result: InterviewQuestionSet,
});
const InterviewQuestionSetListResponse = z
  .object({
    items: z.array(LLMResultEnvelope_InterviewQuestionSet_),
    next_cursor: z.union([z.string(), z.null()]).optional(),
  })
  .passthrough();
const JobStepOut = z.object({
  agent_name: z.enum([
    "resume_analysis",
    "ats",
    "skill_gap",
    "improvement",
    "synthesizer",
  ]),
  status: z.enum(["pending", "completed", "degraded", "failed"]),
});
const ATSOutput = z
  .object({
    score: z.number(),
    breakdown: z.record(z.number()).optional(),
    confidence: z.enum(["high", "medium", "low"]),
    scorer_version: z.string(),
    degraded: z.boolean().optional().default(false),
  })
  .passthrough();
const SkillGapEntry = z
  .object({
    skill: z.string(),
    classification: z.enum(["missing", "weak"]),
    rank: z.number().int(),
  })
  .passthrough();
const SkillGapReport = z
  .object({
    gaps: z.array(SkillGapEntry),
    degraded: z.boolean().default(false),
    derived_from_degraded_input: z.boolean().default(false),
  })
  .partial()
  .passthrough();
const matchlayer_api__ml__agents__state__ImprovementAction = z
  .object({ rank: z.number().int(), text: z.string() })
  .passthrough();
const RewriteSuggestion = z
  .object({
    excerpt: z.string(),
    replacement: z.string(),
    rationale: z.string(),
  })
  .passthrough();
const ImprovementReport = z
  .object({
    actions: z.array(matchlayer_api__ml__agents__state__ImprovementAction),
    rewrites: z.array(RewriteSuggestion),
    degraded: z.boolean().default(false),
    derived_from_degraded_input: z.boolean().default(false),
  })
  .partial()
  .passthrough();
const ExperienceEntry = z
  .object({
    role: z.union([z.string(), z.null()]),
    organization: z.union([z.string(), z.null()]),
    duration: z.union([z.string(), z.null()]),
  })
  .partial()
  .passthrough();
const CandidateProfile = z
  .object({
    sections: z.array(z.string()),
    skills: z.array(z.string()),
    experiences: z.array(ExperienceEntry),
    gaps: z.array(z.string()),
    degraded: z.boolean().default(false),
    derived_from_degraded_input: z.boolean().default(false),
  })
  .partial()
  .passthrough();
const AgentCompletion = z.enum(["completed", "degraded"]);
const FailureDetail = z
  .object({
    trigger: z.enum([
      "error",
      "timeout",
      "schema_validation",
      "quota_exhausted",
      "breaker_open",
      "empty_input",
      "degraded_construction_error",
    ]),
    detail: z.union([z.string(), z.null()]).optional(),
  })
  .passthrough();
const AgentTraceSummary = z
  .object({
    agent_name: z.string(),
    status: AgentCompletion,
    latency_ms: z.number().int(),
    failure_reason: z.union([FailureDetail, z.null()]).optional(),
  })
  .passthrough();
const AnalysisResult = z
  .object({
    ats: ATSOutput,
    skill_gaps: SkillGapReport,
    improvements: ImprovementReport,
    profile: CandidateProfile,
    agent_traces: z.array(AgentTraceSummary).optional(),
  })
  .passthrough();
const JobErrorOut = z
  .object({
    type: z.string().default("job_failed"),
    detail: z.string().default("The analysis failed."),
  })
  .partial()
  .passthrough();
const JobResponse = z.object({
  id: z.string(),
  status: z.enum(["queued", "running", "completed", "failed"]),
  created_at: z.string().datetime({ offset: true }),
  started_at: z.union([z.string(), z.null()]).optional(),
  completed_at: z.union([z.string(), z.null()]).optional(),
  steps: z.array(JobStepOut),
  result: z.union([AnalysisResult, z.null()]).optional(),
  error: z.union([JobErrorOut, z.null()]).optional(),
});
const LastResetLinkResponse = z
  .object({
    link: z.union([z.string(), z.null()]),
    created_at: z.union([z.string(), z.null()]),
  })
  .partial();

export const schemas = {
  HealthResponse,
  HealthUnhealthyResponse,
  RegisterRequest,
  UserResponse,
  TokenPairResponse,
  ValidationError,
  HTTPValidationError,
  LoginRequest,
  MeResponse,
  MePatchRequest,
  PasswordResetRequestRequest,
  PasswordResetConfirmRequest,
  Body_create_resume_api_v1_resumes_post,
  Idempotency_Key,
  ResumeResponse,
  ResumeListResponse,
  CreateMatchRequest,
  ScoreBreakdownOut,
  KeywordOut,
  SuggestionOut,
  MatchResponse,
  MatchListItem,
  MatchListResponse,
  AnalyzeAcceptedResponse,
  FailureReason,
  matchlayer_api__services__llm__schemas__ImprovementAction,
  CoachingReport,
  LLMResultEnvelope_CoachingReport_,
  CoachingReportListResponse,
  BulletRewriteRequest,
  BulletRewriteEntry,
  BulletRewrite,
  LLMResultEnvelope_BulletRewrite_,
  BulletRewriteListResponse,
  InterviewQuestionCategory,
  InterviewQuestion,
  InterviewQuestionSet,
  LLMResultEnvelope_InterviewQuestionSet_,
  InterviewQuestionSetListResponse,
  JobStepOut,
  ATSOutput,
  SkillGapEntry,
  SkillGapReport,
  matchlayer_api__ml__agents__state__ImprovementAction,
  RewriteSuggestion,
  ImprovementReport,
  ExperienceEntry,
  CandidateProfile,
  AgentCompletion,
  FailureDetail,
  AgentTraceSummary,
  AnalysisResult,
  JobErrorOut,
  JobResponse,
  LastResetLinkResponse,
};

const endpoints = makeApi([
  {
    method: "post",
    path: "/api/v1/auth/login",
    alias: "login_api_v1_auth_login_post",
    requestFormat: "json",
    parameters: [
      {
        name: "body",
        type: "Body",
        schema: LoginRequest,
      },
    ],
    response: TokenPairResponse,
    errors: [
      {
        status: 422,
        description: `Validation Error`,
        schema: HTTPValidationError,
      },
    ],
  },
  {
    method: "post",
    path: "/api/v1/auth/logout",
    alias: "logout_api_v1_auth_logout_post",
    requestFormat: "json",
    response: z.void(),
  },
  {
    method: "get",
    path: "/api/v1/auth/me",
    alias: "get_me_api_v1_auth_me_get",
    requestFormat: "json",
    response: MeResponse,
  },
  {
    method: "patch",
    path: "/api/v1/auth/me",
    alias: "patch_me_api_v1_auth_me_patch",
    requestFormat: "json",
    parameters: [
      {
        name: "body",
        type: "Body",
        schema: MePatchRequest,
      },
    ],
    response: MeResponse,
    errors: [
      {
        status: 422,
        description: `Validation Error`,
        schema: HTTPValidationError,
      },
    ],
  },
  {
    method: "post",
    path: "/api/v1/auth/password-reset/confirm",
    alias: "password_reset_confirm_api_v1_auth_password_reset_confirm_post",
    requestFormat: "json",
    parameters: [
      {
        name: "body",
        type: "Body",
        schema: PasswordResetConfirmRequest,
      },
    ],
    response: z.void(),
    errors: [
      {
        status: 422,
        description: `Validation Error`,
        schema: HTTPValidationError,
      },
    ],
  },
  {
    method: "post",
    path: "/api/v1/auth/password-reset/request",
    alias: "password_reset_request_api_v1_auth_password_reset_request_post",
    requestFormat: "json",
    parameters: [
      {
        name: "body",
        type: "Body",
        schema: z.object({ email: z.string().email() }),
      },
    ],
    response: z.unknown(),
    errors: [
      {
        status: 422,
        description: `Validation Error`,
        schema: HTTPValidationError,
      },
    ],
  },
  {
    method: "post",
    path: "/api/v1/auth/refresh",
    alias: "refresh_api_v1_auth_refresh_post",
    requestFormat: "json",
    response: TokenPairResponse,
  },
  {
    method: "post",
    path: "/api/v1/auth/register",
    alias: "register_api_v1_auth_register_post",
    requestFormat: "json",
    parameters: [
      {
        name: "body",
        type: "Body",
        schema: RegisterRequest,
      },
    ],
    response: TokenPairResponse,
    errors: [
      {
        status: 422,
        description: `Validation Error`,
        schema: HTTPValidationError,
      },
    ],
  },
  {
    method: "get",
    path: "/api/v1/dev/last-reset-link",
    alias: "last_reset_link_api_v1_dev_last_reset_link_get",
    requestFormat: "json",
    response: LastResetLinkResponse,
  },
  {
    method: "get",
    path: "/api/v1/jobs/:job_id",
    alias: "get_job_api_v1_jobs__job_id__get",
    description: `Return one owned Agent_Job with per-agent step statuses.

A missing job, a job owned by another User_Account, and a
syntactically invalid id all yield the identical &#x60;&#x60;not_found&#x60;&#x60;
envelope (Requirements 10.4, 12.4 — ownership indistinguishability;
the same malformed-id-as-404 mapping the matches router applies).`,
    requestFormat: "json",
    parameters: [
      {
        name: "job_id",
        type: "Path",
        schema: z.string(),
      },
    ],
    response: JobResponse,
    errors: [
      {
        status: 422,
        description: `Validation Error`,
        schema: HTTPValidationError,
      },
    ],
  },
  {
    method: "post",
    path: "/api/v1/matches",
    alias: "create_match_api_v1_matches_post",
    description: `Score a resume against a job description and persist the Match_Result.

The request body is validated by :class:&#x60;CreateMatchRequest&#x60;, whose
&#x60;&#x60;job_description&#x60;&#x60; field validator enforces the trimmed-length window
&#x60;&#x60;MATCHLAYER_JD_MIN_CHARS&#x60;&#x60;..&#x60;&#x60;MATCHLAYER_JD_MAX_CHARS&#x60;&#x60; — a violation (or
any other Pydantic failure) surfaces as 422 &#x60;&#x60;validation_error&#x60;&#x60; before this
handler runs (Requirements 8.2, 8.3).

Idempotency (Requirement 8.9): when an &#x60;&#x60;Idempotency-Key&#x60;&#x60; header matches a
record stored for this user within the preceding 24h, the original 201
response is replayed without creating a second Match_Result. Otherwise the
service creates the match, the router commits, and the response is stored
under the key for future replays.

Failure mapping:
  * &#x60;&#x60;resume_id&#x60;&#x60; that is malformed, or does not resolve to an owned,
    non-deleted resume → 404 &#x60;&#x60;not_found&#x60;&#x60; (Requirement 8.4; no disclosure).
  * referenced resume whose &#x60;&#x60;extraction_status !&#x3D; &#x27;succeeded&#x27;&#x60;&#x60; → 422
    &#x60;&#x60;resume_not_extractable&#x60;&#x60; (Requirement 8.5).
  * daily Scoring_Quota reached → 429 &#x60;&#x60;quota_exceeded&#x60;&#x60;; the service stages
    a &#x60;&#x60;quota_rejected&#x60;&#x60; audit row which this handler commits before the
    error propagates (Requirement 11.6 audit; the &#x60;&#x60;detail&#x60;&#x60; + &#x60;&#x60;Retry-After&#x60;&#x60;
    are owned by the service/dependency layer).`,
    requestFormat: "json",
    parameters: [
      {
        name: "body",
        type: "Body",
        schema: CreateMatchRequest,
      },
      {
        name: "Idempotency-Key",
        type: "Header",
        schema: Idempotency_Key,
      },
    ],
    response: MatchResponse,
    errors: [
      {
        status: 422,
        description: `Validation Error`,
        schema: HTTPValidationError,
      },
    ],
  },
  {
    method: "get",
    path: "/api/v1/matches",
    alias: "list_matches_api_v1_matches_get",
    description: `Return one cursor-paginated page of the caller&#x27;s non-deleted matches.

Ordered by &#x60;&#x60;created_at&#x60;&#x60; descending (ties broken by &#x60;&#x60;id&#x60;&#x60; descending),
scoped to the requesting user (Requirements 1.4, 9.1). &#x60;&#x60;limit&#x60;&#x60; outside
1..100 fails query validation → 422 &#x60;&#x60;validation_error&#x60;&#x60;. Each item is a
:class:&#x60;MatchListItem&#x60;, which omits &#x60;&#x60;job_description_text&#x60;&#x60; (Requirement
9.2). &#x60;&#x60;next_cursor&#x60;&#x60; is &#x60;&#x60;None&#x60;&#x60; on the last page.`,
    requestFormat: "json",
    parameters: [
      {
        name: "limit",
        type: "Query",
        schema: z.number().int().gte(1).lte(100).optional().default(20),
      },
      {
        name: "cursor",
        type: "Query",
        schema: Idempotency_Key,
      },
    ],
    response: MatchListResponse,
    errors: [
      {
        status: 422,
        description: `Validation Error`,
        schema: HTTPValidationError,
      },
    ],
  },
  {
    method: "get",
    path: "/api/v1/matches/:match_id",
    alias: "get_match_api_v1_matches__match_id__get",
    description: `Return one owned, non-deleted Match_Result.

A missing, soft-deleted, or other-owner match (or a malformed id) yields the
&#x60;&#x60;not_found&#x60;&#x60; envelope, so another account&#x27;s match is indistinguishable from
one that does not exist (Requirements 1.5, 1.6, 9.3). The match is returned
even when its referenced resume was later soft-deleted — the score and
analysis are retained independently of the resume&#x27;s lifecycle (Requirement
9.6, guaranteed by the service&#x27;s query, which does not filter on the
resume&#x27;s &#x60;&#x60;deleted_at&#x60;&#x60;).`,
    requestFormat: "json",
    parameters: [
      {
        name: "match_id",
        type: "Path",
        schema: z.string(),
      },
    ],
    response: MatchResponse,
    errors: [
      {
        status: 422,
        description: `Validation Error`,
        schema: HTTPValidationError,
      },
    ],
  },
  {
    method: "delete",
    path: "/api/v1/matches/:match_id",
    alias: "delete_match_api_v1_matches__match_id__delete",
    description: `Soft-delete an owned Match_Result; idempotent (Requirements 9.4, 9.5).

On the first delete of an owned, non-deleted match the service sets
&#x60;&#x60;deleted_at&#x60;&#x60; and stages a &#x60;&#x60;match_deleted&#x60;&#x60; audit row; the router commits
so both land together. A match that is already soft-deleted, does not exist,
is owned by another user, or carries a malformed id is a silent no-op that
emits no second audit row — every case returns 204 uniformly, disclosing
nothing about another account&#x27;s data (Requirements 1.4, 9.5).`,
    requestFormat: "json",
    parameters: [
      {
        name: "match_id",
        type: "Path",
        schema: z.string(),
      },
    ],
    response: z.void(),
    errors: [
      {
        status: 422,
        description: `Validation Error`,
        schema: HTTPValidationError,
      },
    ],
  },
  {
    method: "post",
    path: "/api/v1/matches/:match_id/analyze",
    alias: "analyze_match_api_v1_matches__match_id__analyze_post",
    description: `Accept an async multi-agent analysis of an owned Match_Result.

Executes the design §7 sequence exactly — no Agent runs synchronously
in this request path, and the handler never reads Resume
&#x60;&#x60;extracted_text&#x60;&#x60; or &#x60;&#x60;job_description_text&#x60;&#x60; (Requirement 11.8; all
resume-content processing happens in the Agent_Worker):

1. **Authn + ownership** — the route-level &#x60;&#x60;analyze&#x60;&#x60; rate limit
   composes :func:&#x60;get_current_user&#x60; (401 first), and the owned
   Match_Result is resolved through the same &#x60;&#x60;Scoring_Service&#x60;&#x60;
   lookup as &#x60;&#x60;GET /matches/{id}&#x60;&#x60;, so missing, other-owner, and
   malformed ids collapse to one indistinguishable 404 &#x60;&#x60;not_found&#x60;&#x60;
   envelope (Requirement 10.4).
2. **Rate limit** — &#x60;&#x60;MATCHLAYER_AGENT_ANALYZE_RATE_LIMIT_PER_MINUTE&#x60;&#x60;
   (default 10/min) per user; 429 &#x60;&#x60;rate_limited&#x60;&#x60; on breach
   (Requirement 10.7).
3. **Quota precheck** — read-only Daily_Quota gate requiring at least
   2 remaining units (the run&#x27;s worst-case LLM call count). Fewer →
   429 RFC 7807 with the UTC reset time; no job row is created and
   no message is enqueued (Requirement 9.4). The gate never counts
   the request — actual reservation happens per-call inside the
   LLM agents. An unreadable quota counter is treated as
   pass-through with one structured warning: the agents&#x27; atomic
   reserve remains the authoritative spend control (Requirements
   9.9, 13.8 fail-safe posture), so availability of the precheck
   never blocks or double-counts anything.
4. **In-flight idempotency** — insert-first via the partial unique
   index (D5); an existing non-terminal job is returned with 202
   and NOT re-enqueued (Requirement 10.5).
5. **Persist → commit → enqueue** (D6) — the &#x60;&#x60;queued&#x60;&#x60; row is
   committed before the SQS send so no message can ever reference an
   uncommitted job (Requirement 11.1). On enqueue failure the job is
   transitioned to &#x60;&#x60;failed&#x60;&#x60; and committed (no orphaned &#x60;&#x60;queued&#x60;&#x60;
   row) and a 503 &#x60;&#x60;job_queue_unavailable&#x60;&#x60; RFC 7807 envelope is
   returned with fixed display-safe copy (Requirement 11.6). Trace
   context is injected into the message attributes by
   :meth:&#x60;JobQueue.enqueue&#x60; itself (Requirement 13.4).
6. **202 Accepted** — &#x60;&#x60;{id, status, job_url}&#x60;&#x60; (Requirement 10.1).

&#x60;&#x60;X-Robots-Tag: noindex, nofollow&#x60;&#x60; lands on every response via the
&#x60;&#x60;ApiNoIndexMiddleware&#x60;&#x60; covering &#x60;&#x60;/api/v1/*&#x60;&#x60; (Requirement 10.6).`,
    requestFormat: "json",
    parameters: [
      {
        name: "match_id",
        type: "Path",
        schema: z.string(),
      },
    ],
    response: AnalyzeAcceptedResponse,
    errors: [
      {
        status: 422,
        description: `Validation Error`,
        schema: HTTPValidationError,
      },
    ],
  },
  {
    method: "post",
    path: "/api/v1/matches/:match_id/bullet-rewrites",
    alias:
      "create_bullet_rewrite_api_v1_matches__match_id__bullet_rewrites_post",
    description: `Rewrite the submitted bullets against an owned match (Req 6.1).

&#x60;&#x60;BulletRewriteRequest&#x60;&#x60; validation (count 1..&#x60;&#x60;llm_max_bullets&#x60;&#x60;, no
empty/whitespace bullet, each ≤ &#x60;&#x60;llm_max_bullet_chars&#x60;&#x60;) runs before
this handler; a violation is a 422 RFC 7807 response before any
redaction, quota accounting, or LLM work — and before any stream
opens (Req 6.3, 11.4). Bullets are Restricted PII and travel only
into the pipeline, which redacts them. &#x60;&#x60;stream&#x3D;true&#x60;&#x60; delivers the
outcome over SSE (Req 11.1).`,
    requestFormat: "json",
    parameters: [
      {
        name: "body",
        type: "Body",
        schema: BulletRewriteRequest,
      },
      {
        name: "match_id",
        type: "Path",
        schema: z.string(),
      },
      {
        name: "stream",
        type: "Query",
        schema: z.boolean().optional().default(false),
      },
    ],
    response: LLMResultEnvelope_BulletRewrite_,
    errors: [
      {
        status: 422,
        description: `Validation Error`,
        schema: HTTPValidationError,
      },
    ],
  },
  {
    method: "get",
    path: "/api/v1/matches/:match_id/bullet-rewrites",
    alias: "list_bullet_rewrites_api_v1_matches__match_id__bullet_rewrites_get",
    description: `One newest-first page of the match&#x27;s Bullet_Rewrites (Req 16.4).`,
    requestFormat: "json",
    parameters: [
      {
        name: "match_id",
        type: "Path",
        schema: z.string(),
      },
      {
        name: "limit",
        type: "Query",
        schema: z.number().int().gte(1).lte(100).optional().default(20),
      },
      {
        name: "cursor",
        type: "Query",
        schema: Idempotency_Key,
      },
    ],
    response: BulletRewriteListResponse,
    errors: [
      {
        status: 422,
        description: `Validation Error`,
        schema: HTTPValidationError,
      },
    ],
  },
  {
    method: "get",
    path: "/api/v1/matches/:match_id/bullet-rewrites/:result_id",
    alias:
      "get_bullet_rewrite_api_v1_matches__match_id__bullet_rewrites__result_id__get",
    description: `One persisted Bullet_Rewrite by id (Req 6.5, 16.3, 16.9).`,
    requestFormat: "json",
    parameters: [
      {
        name: "match_id",
        type: "Path",
        schema: z.string(),
      },
      {
        name: "result_id",
        type: "Path",
        schema: z.string(),
      },
    ],
    response: LLMResultEnvelope_BulletRewrite_,
    errors: [
      {
        status: 422,
        description: `Validation Error`,
        schema: HTTPValidationError,
      },
    ],
  },
  {
    method: "post",
    path: "/api/v1/matches/:match_id/coaching-reports",
    alias:
      "create_coaching_report_api_v1_matches__match_id__coaching_reports_post",
    description: `Generate (or reuse) a Coaching_Report for an owned match (Req 5.1).

Runs the shared pipeline with the Resume_Coach spec: persisted-result
reuse under the same active prompt version + model serves the stored
report with no provider call and no quota consumption (Req 5.4); any
LLM failure lands on the locally-derived fallback with 200 (Req 5.5,
9.1). &#x60;&#x60;stream&#x3D;true&#x60;&#x60; delivers the same outcome over SSE (Req 11.1).`,
    requestFormat: "json",
    parameters: [
      {
        name: "match_id",
        type: "Path",
        schema: z.string(),
      },
      {
        name: "stream",
        type: "Query",
        schema: z.boolean().optional().default(false),
      },
    ],
    response: LLMResultEnvelope_CoachingReport_,
    errors: [
      {
        status: 422,
        description: `Validation Error`,
        schema: HTTPValidationError,
      },
    ],
  },
  {
    method: "get",
    path: "/api/v1/matches/:match_id/coaching-reports",
    alias:
      "list_coaching_reports_api_v1_matches__match_id__coaching_reports_get",
    description: `One newest-first page of the match&#x27;s Coaching_Reports (Req 16.4).`,
    requestFormat: "json",
    parameters: [
      {
        name: "match_id",
        type: "Path",
        schema: z.string(),
      },
      {
        name: "limit",
        type: "Query",
        schema: z.number().int().gte(1).lte(100).optional().default(20),
      },
      {
        name: "cursor",
        type: "Query",
        schema: Idempotency_Key,
      },
    ],
    response: CoachingReportListResponse,
    errors: [
      {
        status: 422,
        description: `Validation Error`,
        schema: HTTPValidationError,
      },
    ],
  },
  {
    method: "get",
    path: "/api/v1/matches/:match_id/coaching-reports/:result_id",
    alias:
      "get_coaching_report_api_v1_matches__match_id__coaching_reports__result_id__get",
    description: `One persisted Coaching_Report by id (Req 16.3, 16.9).`,
    requestFormat: "json",
    parameters: [
      {
        name: "match_id",
        type: "Path",
        schema: z.string(),
      },
      {
        name: "result_id",
        type: "Path",
        schema: z.string(),
      },
    ],
    response: LLMResultEnvelope_CoachingReport_,
    errors: [
      {
        status: 422,
        description: `Validation Error`,
        schema: HTTPValidationError,
      },
    ],
  },
  {
    method: "post",
    path: "/api/v1/matches/:match_id/interview-question-sets",
    alias:
      "create_interview_question_set_api_v1_matches__match_id__interview_question_sets_post",
    description: `Generate an Interview_Question_Set for an owned match (Req 7.1).

&#x60;&#x60;stream&#x3D;true&#x60;&#x60; delivers the outcome over SSE (Req 11.1).`,
    requestFormat: "json",
    parameters: [
      {
        name: "match_id",
        type: "Path",
        schema: z.string(),
      },
      {
        name: "stream",
        type: "Query",
        schema: z.boolean().optional().default(false),
      },
    ],
    response: LLMResultEnvelope_InterviewQuestionSet_,
    errors: [
      {
        status: 422,
        description: `Validation Error`,
        schema: HTTPValidationError,
      },
    ],
  },
  {
    method: "get",
    path: "/api/v1/matches/:match_id/interview-question-sets",
    alias:
      "list_interview_question_sets_api_v1_matches__match_id__interview_question_sets_get",
    description: `One newest-first page of the match&#x27;s Interview_Question_Sets (Req 16.4).`,
    requestFormat: "json",
    parameters: [
      {
        name: "match_id",
        type: "Path",
        schema: z.string(),
      },
      {
        name: "limit",
        type: "Query",
        schema: z.number().int().gte(1).lte(100).optional().default(20),
      },
      {
        name: "cursor",
        type: "Query",
        schema: Idempotency_Key,
      },
    ],
    response: InterviewQuestionSetListResponse,
    errors: [
      {
        status: 422,
        description: `Validation Error`,
        schema: HTTPValidationError,
      },
    ],
  },
  {
    method: "get",
    path: "/api/v1/matches/:match_id/interview-question-sets/:result_id",
    alias:
      "get_interview_question_set_api_v1_matches__match_id__interview_question_sets__result_id__get",
    description: `One persisted Interview_Question_Set by id (Req 7.4, 16.3, 16.9).`,
    requestFormat: "json",
    parameters: [
      {
        name: "match_id",
        type: "Path",
        schema: z.string(),
      },
      {
        name: "result_id",
        type: "Path",
        schema: z.string(),
      },
    ],
    response: LLMResultEnvelope_InterviewQuestionSet_,
    errors: [
      {
        status: 422,
        description: `Validation Error`,
        schema: HTTPValidationError,
      },
    ],
  },
  {
    method: "post",
    path: "/api/v1/resumes",
    alias: "create_resume_api_v1_resumes_post",
    description: `Upload one resume; 201 with the safe field set.

Pre-service guards run in the order the design&#x27;s upload sequence
prescribes -- the per-user rate limit (dependency) and the 413
declared-length check both short-circuit before any object is written
(Requirement 2.2) -- followed by idempotency replay, then the service
orchestration (quota -&gt; MIME -&gt; zip-bomb -&gt; store -&gt; insert -&gt; extract
-&gt; audit).

On a quota breach the service stages a &#x60;&#x60;quota_rejected&#x60;&#x60; audit row and
raises :class:&#x60;QuotaExceededError&#x60;; this handler commits that row before
re-raising so the audit lands even though the 429 short-circuits the
upload (Requirement 11.6). On success the &#x60;&#x60;resumes&#x60;&#x60; row and the
&#x60;&#x60;resume_uploaded&#x60;&#x60; audit row commit together (Requirement 2.7), and the
response carries only the safe field set -- never &#x60;&#x60;extracted_text&#x60;&#x60;,
&#x60;&#x60;storage_key&#x60;&#x60;, or the raw bytes (Requirement 2.9).

Args:
    request: The active request (used for the &#x60;&#x60;Content-Length&#x60;&#x60;
        fallback in the 413 guard).
    file: The multipart &#x60;&#x60;file&#x60;&#x60; part (Requirement 2.1).
    user: The authenticated owner.
    session: The request-scoped session (this handler owns the commit).
    settings: Active settings (the &#x60;&#x60;resume_max_bytes&#x60;&#x60; ceiling).
    idempotency_store: Redis-backed store for idempotent replay.
    idempotency_key: Optional &#x60;&#x60;Idempotency-Key&#x60;&#x60; header (Requirement
        2.8).

Returns:
    The created (or replayed) :class:&#x60;ResumeResponse&#x60;.

Raises:
    PayloadTooLargeError: Declared length over &#x60;&#x60;MATCHLAYER_RESUME_MAX_BYTES&#x60;&#x60;
        (413 &#x60;&#x60;payload_too_large&#x60;&#x60;).
    QuotaExceededError: Daily Upload_Quota reached (429 &#x60;&#x60;quota_exceeded&#x60;&#x60;).`,
    requestFormat: "form-data",
    parameters: [
      {
        name: "body",
        type: "Body",
        schema: z.object({ file: z.string() }).passthrough(),
      },
      {
        name: "Idempotency-Key",
        type: "Header",
        schema: Idempotency_Key,
      },
    ],
    response: ResumeResponse,
    errors: [
      {
        status: 422,
        description: `Validation Error`,
        schema: HTTPValidationError,
      },
    ],
  },
  {
    method: "get",
    path: "/api/v1/resumes",
    alias: "list_resumes_api_v1_resumes_get",
    description: `List the caller&#x27;s non-deleted resumes (cursor-paginated).

&#x60;&#x60;limit&#x60;&#x60; is constrained to &#x60;&#x60;1..100&#x60;&#x60; by :class:&#x60;~fastapi.Query&#x60;; an
out-of-range or non-numeric value raises FastAPI&#x27;s
&#x60;&#x60;RequestValidationError&#x60;&#x60;, which the foundation handler renders as the
422 &#x60;&#x60;validation_error&#x60;&#x60; envelope (Requirement 4.3). Results are scoped
to the caller, ordered &#x60;&#x60;created_at&#x60;&#x60; descending, and projected onto the
safe :class:&#x60;ResumeResponse&#x60; shape -- no &#x60;&#x60;extracted_text&#x60;&#x60; or
&#x60;&#x60;storage_key&#x60;&#x60; (Requirements 4.1, 4.2).

Args:
    user: The authenticated owner.
    session: The request-scoped session (read-only path, no commit).
    settings: Active settings.
    limit: Page size, &#x60;&#x60;1..100&#x60;&#x60; (default 20).
    cursor: Opaque cursor from a prior page, or &#x60;&#x60;None&#x60;&#x60; for the first.

Returns:
    A :class:&#x60;ResumeListResponse&#x60; page plus the next cursor.`,
    requestFormat: "json",
    parameters: [
      {
        name: "limit",
        type: "Query",
        schema: z.number().int().gte(1).lte(100).optional().default(20),
      },
      {
        name: "cursor",
        type: "Query",
        schema: Idempotency_Key,
      },
    ],
    response: ResumeListResponse,
    errors: [
      {
        status: 422,
        description: `Validation Error`,
        schema: HTTPValidationError,
      },
    ],
  },
  {
    method: "get",
    path: "/api/v1/resumes/:resume_id",
    alias: "get_resume_api_v1_resumes__resume_id__get",
    description: `Return a single owned, non-deleted resume, or 404.

The service collapses a missing row, a soft-deleted row, and a row
owned by another User_Account into the same
:class:&#x60;~matchlayer_api.core.errors.NotFoundError&#x60; (404 &#x60;&#x60;not_found&#x60;&#x60;),
so the existence of another account&#x27;s resource is never disclosed
(Requirements 1.5, 1.6, 4.4).

Args:
    resume_id: The resume id from the path.
    user: The authenticated owner.
    session: The request-scoped session (read-only path, no commit).
    settings: Active settings.

Returns:
    The owned :class:&#x60;ResumeResponse&#x60;.`,
    requestFormat: "json",
    parameters: [
      {
        name: "resume_id",
        type: "Path",
        schema: z.string().uuid(),
      },
    ],
    response: ResumeResponse,
    errors: [
      {
        status: 422,
        description: `Validation Error`,
        schema: HTTPValidationError,
      },
    ],
  },
  {
    method: "delete",
    path: "/api/v1/resumes/:resume_id",
    alias: "delete_resume_api_v1_resumes__resume_id__delete",
    description: `Soft-delete the caller&#x27;s resume; 204, idempotent.

Delegates to the idempotent
:meth:&#x60;~matchlayer_api.services.resumes.Resume_Service.soft_delete_resume&#x60;:
an active owned row is stamped &#x60;&#x60;deleted_at&#x60;&#x60; and emits one
&#x60;&#x60;resume_deleted&#x60;&#x60; audit row (Requirement 4.5); an already-soft-deleted
owned row is a no-op with no second audit row (Requirement 4.6); a
missing or other-owner id raises
:class:&#x60;~matchlayer_api.core.errors.NotFoundError&#x60; (404, no disclosure).
The commit persists &#x60;&#x60;deleted_at&#x60;&#x60; and the audit row together; on the
no-op and 404 paths there is nothing staged to commit.

Args:
    resume_id: The resume id from the path.
    user: The authenticated owner.
    session: The request-scoped session (this handler owns the commit).
    settings: Active settings.`,
    requestFormat: "json",
    parameters: [
      {
        name: "resume_id",
        type: "Path",
        schema: z.string().uuid(),
      },
    ],
    response: z.void(),
    errors: [
      {
        status: 422,
        description: `Validation Error`,
        schema: HTTPValidationError,
      },
    ],
  },
  {
    method: "get",
    path: "/healthz",
    alias: "healthz_healthz_get",
    description: `Probe Postgres and return the canonical health envelope.

The handler intentionally returns :class:&#x60;fastapi.responses.JSONResponse&#x60;
rather than the Pydantic model directly so the failure branch can
set the 503 status code without raising an exception (which would
route through the RFC 7807 catch-all in
:mod:&#x60;matchlayer_api.core.errors&#x60; and produce the wrong response
shape for a healthcheck).

Args:
    session: Request-scoped async SQLAlchemy session, yielded by
        :func:&#x60;~matchlayer_api.core.db.get_session&#x60;. Tests override
        this dependency via FastAPI&#x27;s &#x60;&#x60;app.dependency_overrides&#x60;&#x60;
        mapping (task 3.11).

Returns:
    :class:&#x60;JSONResponse&#x60; with status 200 and body &#x60;&#x60;{&quot;status&quot;: &quot;ok&quot;}&#x60;&#x60;
    when the &#x60;&#x60;SELECT 1&#x60;&#x60; probe succeeds; status 503 and body
    &#x60;&#x60;{&quot;status&quot;: &quot;unhealthy&quot;, &quot;reason&quot;: &quot;database_unreachable&quot;}&#x60;&#x60;
    when SQLAlchemy raises any subclass of :class:&#x60;SQLAlchemyError&#x60;.`,
    requestFormat: "json",
    response: HealthResponse,
    errors: [
      {
        status: 503,
        description: `Postgres is unreachable. The probe never returns DSN or credentials in the response body.`,
        schema: HealthUnhealthyResponse,
      },
    ],
  },
]);

export const api = new Zodios(endpoints);

export function createApiClient(baseUrl: string, options?: ZodiosOptions) {
  return new Zodios(baseUrl, endpoints, options);
}
