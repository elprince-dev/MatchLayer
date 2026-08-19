/**
 * Unit tests for the typed agent-jobs API client
 * (phase-4-agentic Task 15.1; Requirement 15.5).
 *
 * What is covered, and which acceptance criterion each block serves:
 *
 * - **Happy paths** — `startAnalysis` returns the Zod-validated 202 body
 *   and issues `POST /api/v1/matches/{id}/analyze`; `fetchJob` returns the
 *   Zod-validated job body from `GET /api/v1/jobs/{id}` (Req 15.5: every
 *   polled response parsed with the generated schema).
 * - **Typed HTTP errors** — non-2xx responses throw `AgentJobRequestError`
 *   carrying the status the polling hook branches on (404 / 429 / 5xx per
 *   Req 15.4), the tolerantly coerced RFC 7807 problem, and the parsed
 *   `Retry-After` seconds on 429.
 * - **Contract drift** — a 2xx body that fails the generated Zod schema
 *   (or is not JSON at all) throws `AgentJobParseError`, the distinct
 *   signal Req 15.5 requires the hook to stop polling on.
 *
 * `apiFetch` is mocked (same pattern as `llm-components.test.tsx`): these
 * tests validate the client's own status-mapping and parse pipeline, not
 * the Bearer/refresh wrapper, which has its own suite.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({ apiFetch: vi.fn() }));

import { apiFetch } from "@/lib/api";

import {
  AgentJobParseError,
  AgentJobRequestError,
  fetchJob,
  startAnalysis,
} from "@/lib/api/agent-jobs";

const apiFetchMock = vi.mocked(apiFetch);

afterEach(() => {
  vi.clearAllMocks();
});

// ---------------------------------------------------------------------------
// Fixtures — conform exactly to the generated contract (no invented fields)
// ---------------------------------------------------------------------------

/** Build a JSON `Response` with the given status, body, and headers. */
function jsonResponse(
  status: number,
  body: unknown,
  headers: Record<string, string> = {},
): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

const JOB_ID = "01936d2e-0000-7000-8000-000000000001";

/** A valid 202 body for `POST /api/v1/matches/{id}/analyze` (Req 10.1). */
const analyzeAccepted = {
  id: JOB_ID,
  status: "queued",
  job_url: `/api/v1/jobs/${JOB_ID}`,
};

/** A valid queued job body: five pending steps, no result, no error. */
const queuedJob = {
  id: JOB_ID,
  status: "queued",
  created_at: "2026-02-01T12:00:00Z",
  started_at: null,
  completed_at: null,
  steps: [
    { agent_name: "resume_analysis", status: "pending" },
    { agent_name: "ats", status: "pending" },
    { agent_name: "skill_gap", status: "pending" },
    { agent_name: "improvement", status: "pending" },
    { agent_name: "synthesizer", status: "pending" },
  ],
  result: null,
  error: null,
};

/** An RFC 7807 envelope as the API emits it (`conventions.md`). */
function problemBody(status: number, detail: string): unknown {
  return {
    type: "rate_limited",
    title: "Too many requests",
    detail,
    status,
    request_id: "req-123",
  };
}

// ---------------------------------------------------------------------------
// startAnalysis
// ---------------------------------------------------------------------------

describe("startAnalysis", () => {
  it("POSTs to the analyze endpoint and returns the Zod-validated 202 body", async () => {
    apiFetchMock.mockResolvedValueOnce(jsonResponse(202, analyzeAccepted));

    const accepted = await startAnalysis("match-1");

    expect(accepted).toEqual(analyzeAccepted);
    expect(apiFetchMock).toHaveBeenCalledTimes(1);
    const [path, init] = apiFetchMock.mock.calls[0]!;
    expect(path).toBe("/api/v1/matches/match-1/analyze");
    expect(init?.method).toBe("POST");
  });

  it("URL-encodes the match id in the path", async () => {
    apiFetchMock.mockResolvedValueOnce(jsonResponse(202, analyzeAccepted));

    await startAnalysis("a/b c");

    const [path] = apiFetchMock.mock.calls[0]!;
    expect(path).toBe("/api/v1/matches/a%2Fb%20c/analyze");
  });

  it("throws AgentJobRequestError with status, problem, and Retry-After on 429", async () => {
    const detail =
      "Daily analysis limit reached. The quota resets at 00:00 UTC.";
    apiFetchMock.mockResolvedValueOnce(
      jsonResponse(429, problemBody(429, detail), { "Retry-After": "17.2" }),
    );

    const error = await startAnalysis("match-1").catch((e: unknown) => e);

    expect(error).toBeInstanceOf(AgentJobRequestError);
    const requestError = error as AgentJobRequestError;
    expect(requestError.status).toBe(429);
    expect(requestError.problem.detail).toBe(detail);
    expect(requestError.problem.request_id).toBe("req-123");
    // Rounded up, never "0 seconds" for a fractional header.
    expect(requestError.retryAfterSeconds).toBe(18);
  });

  it("throws AgentJobParseError when the 202 body fails the generated schema", async () => {
    apiFetchMock.mockResolvedValueOnce(
      jsonResponse(202, { id: JOB_ID, status: "completed" }),
    );

    await expect(startAnalysis("match-1")).rejects.toBeInstanceOf(
      AgentJobParseError,
    );
  });
});

// ---------------------------------------------------------------------------
// fetchJob
// ---------------------------------------------------------------------------

describe("fetchJob", () => {
  it("GETs the job endpoint and returns the Zod-validated body (Req 15.5)", async () => {
    apiFetchMock.mockResolvedValueOnce(jsonResponse(200, queuedJob));

    const job = await fetchJob(JOB_ID);

    expect(job.id).toBe(JOB_ID);
    expect(job.status).toBe("queued");
    expect(job.steps).toHaveLength(5);
    const [path, init] = apiFetchMock.mock.calls[0]!;
    expect(path).toBe(`/api/v1/jobs/${JOB_ID}`);
    expect(init?.method).toBe("GET");
  });

  it("throws AgentJobRequestError with status 404 so the hook can render job-not-available", async () => {
    apiFetchMock.mockResolvedValueOnce(
      jsonResponse(404, {
        type: "not_found",
        title: "Not found",
        detail: "Job not found.",
        status: 404,
      }),
    );

    const error = await fetchJob(JOB_ID).catch((e: unknown) => e);

    expect(error).toBeInstanceOf(AgentJobRequestError);
    expect((error as AgentJobRequestError).status).toBe(404);
  });

  it("coerces a non-JSON 5xx body into a safe-default problem", async () => {
    apiFetchMock.mockResolvedValueOnce(
      new Response("<html>bad gateway</html>", { status: 502 }),
    );

    const error = await fetchJob(JOB_ID).catch((e: unknown) => e);

    expect(error).toBeInstanceOf(AgentJobRequestError);
    const requestError = error as AgentJobRequestError;
    expect(requestError.status).toBe(502);
    // Tolerant defaults — the error UI never crashes on a bad payload.
    expect(requestError.problem.title).toBe("Request failed");
    expect(requestError.problem.detail).toBe(
      "Something went wrong. Please try again.",
    );
    expect(requestError.retryAfterSeconds).toBeNull();
  });

  it("throws AgentJobParseError when a 200 body fails the generated schema (contract drift)", async () => {
    apiFetchMock.mockResolvedValueOnce(
      jsonResponse(200, { ...queuedJob, status: "exploded" }),
    );

    await expect(fetchJob(JOB_ID)).rejects.toBeInstanceOf(AgentJobParseError);
  });

  it("throws AgentJobParseError when a 200 body is not JSON", async () => {
    apiFetchMock.mockResolvedValueOnce(
      new Response("not json", { status: 200 }),
    );

    await expect(fetchJob(JOB_ID)).rejects.toBeInstanceOf(AgentJobParseError);
  });

  it("propagates network errors from apiFetch unchanged", async () => {
    const networkError = new TypeError("Failed to fetch");
    apiFetchMock.mockRejectedValueOnce(networkError);

    await expect(fetchJob(JOB_ID)).rejects.toBe(networkError);
  });
});
