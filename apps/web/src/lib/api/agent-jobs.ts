/**
 * Typed agent-jobs API client (phase-4-agentic Task 15.1; Requirement 15.5).
 *
 * The two calls behind the Phase 4 Progress_UI:
 *
 * - {@link startAnalysis} — `POST /api/v1/matches/{id}/analyze` (202
 *   Accepted, no request body; the match id lives in the path).
 * - {@link fetchJob} — `GET /api/v1/jobs/{id}`, the polling read.
 *
 * Both go through the existing `apiFetch` wrapper (Bearer attach + the
 * silent 401→refresh→retry, design §13.5 of `frontend-redesign`) and
 * validate every response body at the boundary with the generated Zod
 * schemas from `@matchlayer/shared-types` — no hand-written API types, per
 * `conventions.md`. Requirement 15.5 makes the runtime parse mandatory for
 * the polled response specifically: contract drift must surface as a typed
 * failure the polling hook can stop on, never as a render crash.
 *
 * ## Error surface (consumed by `hooks/use-agent-job.ts`, Task 15.2)
 *
 * Requirement 15.4 maps each observed failure to a distinct UI state, so
 * this module throws exactly two typed errors the hook can branch on:
 *
 * - {@link AgentJobRequestError} — any non-2xx HTTP response. Carries the
 *   `status` (the hook distinguishes 404 / 429 / 5xx), the tolerantly
 *   coerced RFC 7807 {@link AgentJobProblem} (the 429 `detail` names the
 *   configured limit and its UTC reset per the Phase 3 quota pattern —
 *   never hardcoded client-side), and `retryAfterSeconds` parsed from the
 *   `Retry-After` header (the `AuthRequestError` precedent in
 *   `lib/auth.ts`).
 * - {@link AgentJobParseError} — a 2xx body that is not JSON or fails the
 *   generated Zod schema (Req 15.5's "stop polling and render the
 *   temporary-failure state" trigger).
 *
 * Network failures and aborts propagate as the platform's own errors
 * (`TypeError` / `AbortError`), matching `fetchMatch` in
 * `components/results/results-view.tsx`; callers treat anything that is
 * not one of the two classes above as a temporary failure.
 *
 * Boundary note: like `lib/api.ts`, this is deliberately *not* a
 * `"use client"` module — it is plain async functions over `apiFetch`,
 * importable from hooks, components, and tests alike.
 */

import {
  AnalyzeAcceptedResponseSchema,
  JobResponseSchema,
  type AnalyzeAcceptedResponse,
  type JobResponse,
} from "@matchlayer/shared-types";

import { apiFetch } from "@/lib/api";

// ---------------------------------------------------------------------------
// Problem (RFC 7807) — tolerant client-side shape
// ---------------------------------------------------------------------------

/**
 * The RFC 7807 error body shape used across the API (`conventions.md`).
 * Coerced tolerantly (mirroring `LlmProblem` in `lib/llm/use-llm-stream.ts`):
 * a malformed body still yields a renderable problem with safe defaults, so
 * the error UI never crashes on a bad payload.
 */
export interface AgentJobProblem {
  type: string;
  title: string;
  detail: string;
  status: number;
  request_id: string | null;
}

/**
 * Coerce an unknown (already JSON-parsed) value into an
 * {@link AgentJobProblem}, defaulting each missing/mistyped field.
 * `fallbackStatus` seeds the status when the body carries none — always the
 * HTTP status here, since the agent endpoints never wrap errors in SSE.
 */
function coerceProblem(raw: unknown, fallbackStatus: number): AgentJobProblem {
  const obj =
    raw !== null && typeof raw === "object"
      ? (raw as Record<string, unknown>)
      : {};
  return {
    type: typeof obj.type === "string" ? obj.type : "about:blank",
    title: typeof obj.title === "string" ? obj.title : "Request failed",
    detail:
      typeof obj.detail === "string"
        ? obj.detail
        : "Something went wrong. Please try again.",
    status: typeof obj.status === "number" ? obj.status : fallbackStatus,
    request_id: typeof obj.request_id === "string" ? obj.request_id : null,
  };
}

/**
 * Parse the `Retry-After` header into a number of seconds (integer-second
 * form only — the FastAPI rate limiter never emits the HTTP-date form).
 * Rounded up so a fractional value never displays as "0 seconds". Mirrors
 * the `parseRetryAfterSeconds` helper in `lib/auth.ts`.
 */
function parseRetryAfterSeconds(header: string | null): number | null {
  if (header === null) {
    return null;
  }
  const seconds = Number(header);
  if (!Number.isFinite(seconds) || seconds < 0) {
    return null;
  }
  return Math.ceil(seconds);
}

// ---------------------------------------------------------------------------
// Typed errors
// ---------------------------------------------------------------------------

/**
 * Thrown for any non-2xx response from the analyze or job endpoints.
 *
 * The polling hook branches on `status` (404 → job-not-available, 429 →
 * rate/quota limited with `problem.detail` naming the reset time, 5xx →
 * temporary failure, per Requirement 15.4); everything the UI displays
 * comes from the display-safe RFC 7807 `problem` fields.
 */
export class AgentJobRequestError extends Error {
  readonly status: number;
  readonly problem: AgentJobProblem;
  readonly retryAfterSeconds: number | null;

  constructor(problem: AgentJobProblem, retryAfterSeconds: number | null) {
    super(problem.detail);
    this.name = "AgentJobRequestError";
    this.status = problem.status;
    this.problem = problem;
    this.retryAfterSeconds = retryAfterSeconds;
  }
}

/**
 * Thrown when a 2xx response body is not JSON or fails validation against
 * the generated Zod schema — the Requirement 15.5 contract-drift signal.
 * Deliberately carries only a coarse endpoint label (never the offending
 * body), so there is structurally nothing for an error surface to leak.
 */
export class AgentJobParseError extends Error {
  constructor(endpoint: string) {
    super(`Response from ${endpoint} failed schema validation`);
    this.name = "AgentJobParseError";
  }
}

// ---------------------------------------------------------------------------
// Shared request pipeline
// ---------------------------------------------------------------------------

/** Options accepted by both calls — the query's cancellation signal. */
export interface AgentJobRequestInit {
  signal?: AbortSignal;
}

/**
 * The common fetch → status-check → JSON → Zod pipeline for both
 * endpoints. Non-2xx throws {@link AgentJobRequestError}; a 2xx body that
 * is not JSON or whose `parse` throws (the generated Zod schema's `parse`)
 * throws {@link AgentJobParseError}.
 */
async function requestParsed<T>(
  path: string,
  init: RequestInit,
  parse: (body: unknown) => T,
  endpointLabel: string,
): Promise<T> {
  const res = await apiFetch(path, init);

  if (!res.ok) {
    let raw: unknown = null;
    try {
      raw = await res.json();
    } catch {
      // Non-JSON error body (proxy page, empty body) — defaults apply.
    }
    throw new AgentJobRequestError(
      coerceProblem(raw, res.status),
      parseRetryAfterSeconds(res.headers.get("Retry-After")),
    );
  }

  let body: unknown;
  try {
    body = await res.json();
  } catch {
    throw new AgentJobParseError(endpointLabel);
  }

  try {
    return parse(body);
  } catch {
    throw new AgentJobParseError(endpointLabel);
  }
}

// ---------------------------------------------------------------------------
// Public calls
// ---------------------------------------------------------------------------

/**
 * Trigger an agent analysis for a Match_Result:
 * `POST /api/v1/matches/{id}/analyze`.
 *
 * On 202 returns the Zod-validated `{id, status, job_url}` body — `status`
 * is `queued`, or `running` on the in-flight idempotent-reuse path (Req
 * 10.5); `job_url` is the relative poll target for {@link fetchJob}.
 * Notable non-2xx cases the caller renders per Requirement 15.4: 404
 * (ownership-indistinguishable), 429 (rate limit or the ≥2-unit
 * Daily_Quota precheck, `problem.detail` naming the UTC reset), 503
 * (enqueue failure).
 */
export async function startAnalysis(
  matchId: string,
  init: AgentJobRequestInit = {},
): Promise<AnalyzeAcceptedResponse> {
  return requestParsed(
    `/api/v1/matches/${encodeURIComponent(matchId)}/analyze`,
    { method: "POST", signal: init.signal ?? null },
    (body) => AnalyzeAcceptedResponseSchema.parse(body),
    "analyze",
  );
}

/**
 * Poll one Agent_Job: `GET /api/v1/jobs/{id}`.
 *
 * Every polled response is Zod-parsed at runtime with the generated
 * `JobResponseSchema` (Requirement 15.5) — status, the five per-agent
 * steps, `result` iff completed, and the structured display-safe `error`
 * iff failed all arrive contract-validated.
 */
export async function fetchJob(
  jobId: string,
  init: AgentJobRequestInit = {},
): Promise<JobResponse> {
  return requestParsed(
    `/api/v1/jobs/${encodeURIComponent(jobId)}`,
    { method: "GET", signal: init.signal ?? null },
    // Both the schema and the type are generated from the same OpenAPI
    // spec (task 13.1, CI drift-checked), but `openapi-zod-client` marks
    // default-carrying nested fields (`degraded`,
    // `derived_from_degraded_input`) optional via `.partial()`, while
    // `openapi-typescript` keeps them required — Pydantic serialization
    // always emits them on the wire. The cast bridges that generator
    // asymmetry only; the runtime contract has just been validated.
    (body) => JobResponseSchema.parse(body) as JobResponse,
    "job polling",
  );
}
