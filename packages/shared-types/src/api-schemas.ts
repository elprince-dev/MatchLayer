import { makeApi, Zodios, type ZodiosOptions } from "@zodios/core";
import { z } from "zod";

const HealthResponse = z
  .object({ status: z.string().default("ok") })
  .partial()
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
    response: z
      .object({ status: z.string().default("ok") })
      .partial()
      .passthrough(),
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
