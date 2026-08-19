/**
 * Unit tests for the agent-job polling hook
 * (phase-4-agentic Task 15.2; Requirements 15.1, 15.4, 15.5, 15.7).
 *
 * Coverage, one describe block per task bullet:
 *
 * - **Interval + clamping (15.1):** default 2 000 ms cadence; overrides
 *   below 1 000 ms clamp up, above 5 000 ms clamp down (plus pure unit
 *   tests on `clampPollIntervalMs`).
 * - **Stop on terminal within one interval (15.4):** a `completed` or
 *   `failed` poll response stops polling — no further fetch is ever
 *   scheduled — and maps to the matching UI state.
 * - **Stop on 404 / 429 / 5xx / parse failure (15.4, 15.5):** each typed
 *   client error stops polling and maps to its distinct state
 *   (`not-found`, `rate-limited` with reset info, `server-error`,
 *   `parse-error`); network errors map to `server-error`.
 * - **120 s cap (15.7):** unbroken non-terminal responses flip to
 *   `timed-out` at 120 s and polling halts.
 * - **Recovery:** `retry()` resumes polling from any stopped state and
 *   restarts the 120 s clock.
 *
 * Conventions mirror the suite: fake timers drive the polling clock, the
 * typed client (`@/lib/api/agent-jobs`) is mocked at the module boundary
 * (its own suite covers the fetch/Zod pipeline), a throwaway QueryClient
 * per test, `toBe`/`toEqual` assertions, no jest-dom. All fixture data is
 * synthetic (security.md). The Property 20 fast-check test is Task 15.6,
 * not duplicated here.
 *
 * @vitest-environment jsdom
 */

import * as React from "react";

import {
  QueryClient,
  QueryClientProvider,
  notifyManager,
} from "@tanstack/react-query";
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { JobResponseSchema, type JobResponse } from "@matchlayer/shared-types";

// Mock only `fetchJob`; keep the real error classes so the hook's
// `instanceof` branching runs against the same constructors.
vi.mock("@/lib/api/agent-jobs", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api/agent-jobs")>(
    "@/lib/api/agent-jobs",
  );
  return { ...actual, fetchJob: vi.fn() };
});

import {
  AgentJobParseError,
  AgentJobRequestError,
  fetchJob,
  type AgentJobProblem,
} from "@/lib/api/agent-jobs";
import {
  POLL_INTERVAL_DEFAULT_MS,
  POLL_INTERVAL_MAX_MS,
  POLL_INTERVAL_MIN_MS,
  POLL_TIMEOUT_MS,
  clampPollIntervalMs,
  useAgentJob,
} from "@/hooks/use-agent-job";

const fetchJobMock = vi.mocked(fetchJob);

// ---------------------------------------------------------------------------
// Fixtures — conform to the generated contract (no invented fields)
// ---------------------------------------------------------------------------

const JOB_ID = "01936d2e-0000-7000-8000-000000000001";

/**
 * Build a job body with the five per-agent steps in the given status,
 * parsed through the generated Zod schema (drift guard, suite convention).
 * The cast bridges the same generator asymmetry the client documents.
 */
function jobBody(
  status: "queued" | "running" | "completed" | "failed",
): JobResponse {
  return JobResponseSchema.parse({
    id: JOB_ID,
    status,
    created_at: "2026-02-01T12:00:00Z",
    started_at: status === "queued" ? null : "2026-02-01T12:00:01Z",
    completed_at:
      status === "completed" || status === "failed"
        ? "2026-02-01T12:00:20Z"
        : null,
    steps: [
      { agent_name: "resume_analysis", status: "pending" },
      { agent_name: "ats", status: "pending" },
      { agent_name: "skill_gap", status: "pending" },
      { agent_name: "improvement", status: "pending" },
      { agent_name: "synthesizer", status: "pending" },
    ],
    result: null,
    error: status === "failed" ? { type: "job_failed", detail: "Boom" } : null,
  }) as JobResponse;
}

/** An RFC 7807 problem as the typed client coerces it. */
function problem(status: number, detail: string): AgentJobProblem {
  return {
    type: "about:blank",
    title: "Request failed",
    detail,
    status,
    request_id: "req-1",
  };
}

/** Render inside a throwaway QueryClient (retries off, matching app config). */
function createWrapper(): (props: {
  children: React.ReactNode;
}) => React.JSX.Element {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  function Wrapper({
    children,
  }: {
    children: React.ReactNode;
  }): React.JSX.Element {
    return (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );
  }
  return Wrapper;
}

/**
 * Advance fake timers and flush the resulting query settlements. The
 * second zero-length advance flushes a fetch that fired on the final tick
 * of the window (its promise settles one microtask batch later).
 */
async function advance(ms: number): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
  // A fetch that fired on the final tick of the window settles its React
  // commit one act later — flush it without moving the clock.
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
}

function renderAgentJob(
  jobId: string | null = JOB_ID,
  options: { pollIntervalMs?: number } = {},
): ReturnType<typeof renderHook<ReturnType<typeof useAgentJob>, void>> {
  return renderHook(() => useAgentJob(jobId, options), {
    wrapper: createWrapper(),
  });
}

beforeEach(() => {
  // TanStack Query batches observer notifications through the
  // notifyManager's scheduler (a `setTimeout(cb, 0)` by default). Under
  // fake timers that defers the React commit of an interval-triggered
  // fetch to a *later* timer advance, making "state within one interval"
  // assertions impossible. A synchronous scheduler removes the artificial
  // lag — the pattern TanStack Query's own test suite uses.
  notifyManager.setScheduler((cb) => cb());
  vi.useFakeTimers();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.clearAllMocks();
  // Restore the library default so no other suite inherits sync batching.
  notifyManager.setScheduler((cb) => setTimeout(cb, 0));
});

// ---------------------------------------------------------------------------
// clampPollIntervalMs — pure unit tests (Req 15.1)
// ---------------------------------------------------------------------------

describe("clampPollIntervalMs", () => {
  it("defaults to 2000 ms when unset or non-finite", () => {
    expect(clampPollIntervalMs()).toBe(POLL_INTERVAL_DEFAULT_MS);
    expect(clampPollIntervalMs(Number.NaN)).toBe(POLL_INTERVAL_DEFAULT_MS);
    expect(clampPollIntervalMs(Number.POSITIVE_INFINITY)).toBe(
      POLL_INTERVAL_DEFAULT_MS,
    );
  });

  it("clamps into the [1000, 5000] window", () => {
    expect(clampPollIntervalMs(0)).toBe(POLL_INTERVAL_MIN_MS);
    expect(clampPollIntervalMs(999)).toBe(POLL_INTERVAL_MIN_MS);
    expect(clampPollIntervalMs(1000)).toBe(1000);
    expect(clampPollIntervalMs(3000)).toBe(3000);
    expect(clampPollIntervalMs(5000)).toBe(5000);
    expect(clampPollIntervalMs(60_000)).toBe(POLL_INTERVAL_MAX_MS);
  });
});

// ---------------------------------------------------------------------------
// Interval cadence (Req 15.1)
// ---------------------------------------------------------------------------

describe("polling cadence", () => {
  it("stays idle without a job id and issues no fetch", async () => {
    const { result } = renderAgentJob(null);
    await advance(0);

    expect(result.current.state.kind).toBe("idle");
    expect(fetchJobMock).not.toHaveBeenCalled();
  });

  it("polls at the 2000 ms default while the job is non-terminal", async () => {
    fetchJobMock.mockImplementation(async () => jobBody("running"));
    const { result } = renderAgentJob();

    await advance(0);
    expect(fetchJobMock).toHaveBeenCalledTimes(1);
    expect(result.current.state.kind).toBe("polling");
    expect(result.current.steps).toHaveLength(5);

    await advance(POLL_INTERVAL_DEFAULT_MS - 1);
    expect(fetchJobMock).toHaveBeenCalledTimes(1);

    await advance(1);
    expect(fetchJobMock).toHaveBeenCalledTimes(2);

    await advance(POLL_INTERVAL_DEFAULT_MS);
    expect(fetchJobMock).toHaveBeenCalledTimes(3);
  });

  it("clamps a too-fast override up to 1000 ms", async () => {
    fetchJobMock.mockImplementation(async () => jobBody("running"));
    renderAgentJob(JOB_ID, { pollIntervalMs: 100 });

    await advance(0);
    expect(fetchJobMock).toHaveBeenCalledTimes(1);

    // At the requested-but-rejected 100 ms nothing fires.
    await advance(999);
    expect(fetchJobMock).toHaveBeenCalledTimes(1);

    await advance(1);
    expect(fetchJobMock).toHaveBeenCalledTimes(2);
  });

  it("clamps a too-slow override down to 5000 ms", async () => {
    fetchJobMock.mockImplementation(async () => jobBody("running"));
    renderAgentJob(JOB_ID, { pollIntervalMs: 60_000 });

    await advance(0);
    expect(fetchJobMock).toHaveBeenCalledTimes(1);

    await advance(POLL_INTERVAL_MAX_MS - 1);
    expect(fetchJobMock).toHaveBeenCalledTimes(1);

    await advance(1);
    expect(fetchJobMock).toHaveBeenCalledTimes(2);
  });
});

// ---------------------------------------------------------------------------
// Terminal statuses stop polling within one interval (Req 15.4)
// ---------------------------------------------------------------------------

describe("terminal statuses", () => {
  it("stops on completed and exposes the job", async () => {
    fetchJobMock
      .mockResolvedValueOnce(jobBody("running"))
      .mockResolvedValueOnce(jobBody("completed"));
    const { result } = renderAgentJob();

    await advance(0);
    expect(result.current.state.kind).toBe("polling");

    await advance(POLL_INTERVAL_DEFAULT_MS);
    expect(result.current.state.kind).toBe("completed");
    expect(result.current.job?.status).toBe("completed");
    expect(fetchJobMock).toHaveBeenCalledTimes(2);

    // No further poll is ever scheduled.
    await advance(POLL_INTERVAL_DEFAULT_MS * 5);
    expect(fetchJobMock).toHaveBeenCalledTimes(2);
  });

  it("stops on failed and surfaces the structured error via the job", async () => {
    fetchJobMock.mockResolvedValue(jobBody("failed"));
    const { result } = renderAgentJob();

    await advance(0);
    expect(result.current.state.kind).toBe("failed");
    if (result.current.state.kind === "failed") {
      expect(result.current.state.job.error).toEqual({
        type: "job_failed",
        detail: "Boom",
      });
    }

    await advance(POLL_INTERVAL_DEFAULT_MS * 5);
    expect(fetchJobMock).toHaveBeenCalledTimes(1);
    expect(result.current.isPolling).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// HTTP / parse errors stop polling, each to its own state (Req 15.4, 15.5)
// ---------------------------------------------------------------------------

describe("error stop conditions", () => {
  it("maps 404 to not-found and stops", async () => {
    fetchJobMock.mockRejectedValue(
      new AgentJobRequestError(problem(404, "Job not found."), null),
    );
    const { result } = renderAgentJob();

    await advance(0);
    expect(result.current.state.kind).toBe("not-found");
    if (result.current.state.kind === "not-found") {
      expect(result.current.state.problem.detail).toBe("Job not found.");
    }

    await advance(POLL_INTERVAL_DEFAULT_MS * 5);
    expect(fetchJobMock).toHaveBeenCalledTimes(1);
  });

  it("maps 429 to rate-limited with the reset info and stops", async () => {
    fetchJobMock.mockRejectedValue(
      new AgentJobRequestError(
        problem(429, "Rate limit reached. Resets at 00:00 UTC."),
        30,
      ),
    );
    const { result } = renderAgentJob();

    await advance(0);
    expect(result.current.state.kind).toBe("rate-limited");
    if (result.current.state.kind === "rate-limited") {
      expect(result.current.state.retryAfterSeconds).toBe(30);
      expect(result.current.state.problem.detail).toContain("00:00 UTC");
    }

    await advance(POLL_INTERVAL_DEFAULT_MS * 5);
    expect(fetchJobMock).toHaveBeenCalledTimes(1);
  });

  it("maps 5xx to server-error and stops", async () => {
    fetchJobMock.mockRejectedValue(
      new AgentJobRequestError(problem(503, "Temporarily unavailable."), null),
    );
    const { result } = renderAgentJob();

    await advance(0);
    expect(result.current.state.kind).toBe("server-error");
    if (result.current.state.kind === "server-error") {
      expect(result.current.state.problem?.status).toBe(503);
    }

    await advance(POLL_INTERVAL_DEFAULT_MS * 5);
    expect(fetchJobMock).toHaveBeenCalledTimes(1);
  });

  it("maps a Zod parse failure to parse-error and stops (Req 15.5)", async () => {
    fetchJobMock.mockRejectedValue(new AgentJobParseError("job polling"));
    const { result } = renderAgentJob();

    await advance(0);
    expect(result.current.state.kind).toBe("parse-error");

    await advance(POLL_INTERVAL_DEFAULT_MS * 5);
    expect(fetchJobMock).toHaveBeenCalledTimes(1);
  });

  it("maps a network error to server-error with a null problem", async () => {
    fetchJobMock.mockRejectedValue(new TypeError("Failed to fetch"));
    const { result } = renderAgentJob();

    await advance(0);
    expect(result.current.state.kind).toBe("server-error");
    if (result.current.state.kind === "server-error") {
      expect(result.current.state.problem).toBeNull();
    }
  });

  it("stops within one interval of a mid-stream error", async () => {
    fetchJobMock
      .mockResolvedValueOnce(jobBody("running"))
      .mockRejectedValueOnce(
        new AgentJobRequestError(problem(500, "Server error."), null),
      );
    const { result } = renderAgentJob();

    await advance(0);
    expect(result.current.state.kind).toBe("polling");

    await advance(POLL_INTERVAL_DEFAULT_MS);
    expect(result.current.state.kind).toBe("server-error");
    expect(fetchJobMock).toHaveBeenCalledTimes(2);

    await advance(POLL_INTERVAL_DEFAULT_MS * 5);
    expect(fetchJobMock).toHaveBeenCalledTimes(2);
  });
});

// ---------------------------------------------------------------------------
// 120 s cap (Req 15.7)
// ---------------------------------------------------------------------------

describe("timeout", () => {
  it("flips to timed-out at 120 s of non-terminal responses and stops", async () => {
    fetchJobMock.mockImplementation(async () => jobBody("running"));
    const { result } = renderAgentJob();

    await advance(0);
    expect(result.current.state.kind).toBe("polling");

    await advance(POLL_TIMEOUT_MS - 1);
    expect(result.current.state.kind).toBe("polling");

    await advance(1);
    expect(result.current.state.kind).toBe("timed-out");

    const callsAtTimeout = fetchJobMock.mock.calls.length;
    await advance(POLL_INTERVAL_DEFAULT_MS * 5);
    expect(fetchJobMock).toHaveBeenCalledTimes(callsAtTimeout);
  });

  it("a terminal status observed before 120 s wins over the timeout", async () => {
    fetchJobMock
      .mockResolvedValueOnce(jobBody("running"))
      .mockResolvedValue(jobBody("completed"));
    const { result } = renderAgentJob();

    await advance(0);
    await advance(POLL_INTERVAL_DEFAULT_MS);
    expect(result.current.state.kind).toBe("completed");

    // Long after the would-be timeout, the state stays completed.
    await advance(POLL_TIMEOUT_MS);
    expect(result.current.state.kind).toBe("completed");
  });
});

// ---------------------------------------------------------------------------
// Recovery action
// ---------------------------------------------------------------------------

describe("retry", () => {
  it("resumes polling after an error state", async () => {
    fetchJobMock
      .mockRejectedValueOnce(
        new AgentJobRequestError(problem(500, "Server error."), null),
      )
      .mockImplementation(async () => jobBody("running"));
    const { result } = renderAgentJob();

    await advance(0);
    expect(result.current.state.kind).toBe("server-error");

    act(() => {
      result.current.retry();
    });
    await advance(0);

    expect(result.current.state.kind).toBe("polling");
    expect(fetchJobMock).toHaveBeenCalledTimes(2);
  });

  it("resumes polling after a timeout and restarts the 120 s clock", async () => {
    fetchJobMock.mockImplementation(async () => jobBody("running"));
    const { result } = renderAgentJob();

    await advance(0);
    await advance(POLL_TIMEOUT_MS);
    expect(result.current.state.kind).toBe("timed-out");
    const callsAtTimeout = fetchJobMock.mock.calls.length;

    act(() => {
      result.current.retry();
    });
    await advance(0);
    expect(result.current.state.kind).toBe("polling");
    expect(fetchJobMock.mock.calls.length).toBeGreaterThan(callsAtTimeout);

    // The clock restarted: still polling just before the new deadline...
    await advance(POLL_TIMEOUT_MS - 1);
    expect(result.current.state.kind).toBe("polling");
    // ...and timed out again at it.
    await advance(1);
    expect(result.current.state.kind).toBe("timed-out");
  });
});
