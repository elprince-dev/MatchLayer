// Feature: phase-4-agentic, Property 20: Polling terminates correctly on any response sequence
/**
 * Property 20: Polling terminates correctly on any response sequence
 * (phase-4-agentic Task 15.6; fast-check + Vitest).
 *
 * **Validates: Requirements 15.4, 15.5, 15.7**
 *
 * Subject: the real `useAgentJob` hook driven end-to-end with renderHook,
 * fake timers, and a mocked `fetchJob` that replays a generated response
 * sequence — the same harness as the co-located unit suite
 * (`use-agent-job.test.tsx`), including its synchronous notifyManager
 * scheduler.
 *
 * The property statement (design.md): for any generated sequence of poll
 * responses — non-terminal statuses followed by a terminal status, an
 * HTTP 404/429/5xx error, a Zod-unparseable body, or more than 120
 * seconds of non-terminal responses — the polling hook stops polling
 * within one interval of the triggering response and surfaces the state
 * specific to that case:
 *
 * | Trigger                       | Expected `state.kind` |
 * | ----------------------------- | --------------------- |
 * | body `status === "completed"` | `"completed"`         |
 * | body `status === "failed"`    | `"failed"`            |
 * | HTTP 404                      | `"not-found"`         |
 * | HTTP 429                      | `"rate-limited"`      |
 * | HTTP 5xx / network error      | `"server-error"`      |
 * | Zod parse failure             | `"parse-error"`       |
 * | 120 s never-terminal          | `"timed-out"`         |
 *
 * "Stops within one interval" is asserted structurally: the fetch count
 * at the moment the trigger settles is exactly `prefix + 1`, and it never
 * grows again over several further intervals. Every stopped state also
 * carries the shared recovery action (`retry`), per Requirements
 * 15.4/15.7.
 *
 * All fixture data is synthetic (security.md).
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
import fc from "fast-check";
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
  POLL_TIMEOUT_MS,
  clampPollIntervalMs,
  useAgentJob,
  type AgentJobUiState,
} from "@/hooks/use-agent-job";

const fetchJobMock = vi.mocked(fetchJob);

// ---------------------------------------------------------------------------
// Fixtures (mirroring the unit suite; synthetic per security.md)
// ---------------------------------------------------------------------------

const JOB_ID = "01936d2e-0000-7000-8000-000000000001";

/** Build a job body via the generated Zod schema (drift guard). */
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
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
}

// ---------------------------------------------------------------------------
// Poll-response model + generators
// ---------------------------------------------------------------------------

/** One modeled poll response the mocked `fetchJob` replays. */
type PollResponse =
  | { type: "body"; status: "queued" | "running" | "completed" | "failed" }
  | { type: "http-error"; status: number; retryAfterSeconds: number | null }
  | { type: "parse-error" }
  | { type: "network-error" };

/** Resolve/reject exactly as the typed client would for this response. */
function toFetchResult(response: PollResponse): Promise<JobResponse> {
  switch (response.type) {
    case "body":
      return Promise.resolve(jobBody(response.status));
    case "http-error":
      return Promise.reject(
        new AgentJobRequestError(
          problem(
            response.status,
            `Synthetic HTTP ${String(response.status)}.`,
          ),
          response.retryAfterSeconds,
        ),
      );
    case "parse-error":
      return Promise.reject(new AgentJobParseError("job polling"));
    case "network-error":
      return Promise.reject(new TypeError("Failed to fetch"));
  }
}

/** The UI state each stop trigger must map to (Req 15.4 / 15.5). */
function expectedKind(trigger: PollResponse): AgentJobUiState["kind"] {
  switch (trigger.type) {
    case "body":
      return trigger.status === "completed" ? "completed" : "failed";
    case "http-error":
      if (trigger.status === 404) {
        return "not-found";
      }
      if (trigger.status === 429) {
        return "rate-limited";
      }
      return "server-error";
    case "parse-error":
      return "parse-error";
    case "network-error":
      return "server-error";
  }
}

/** A response that keeps polling alive: a parseable non-terminal body. */
const nonTerminalArb: fc.Arbitrary<PollResponse> = fc.record({
  type: fc.constant<"body">("body"),
  status: fc.constantFrom<"queued" | "running">("queued", "running"),
});

/** Any single stop-triggering response (terminal body, 404/429/5xx,
 * parse failure, or network-level failure). */
const stopTriggerArb: fc.Arbitrary<PollResponse> = fc.oneof(
  fc.record({
    type: fc.constant<"body">("body"),
    status: fc.constantFrom<"completed" | "failed">("completed", "failed"),
  }),
  fc.record({
    type: fc.constant<"http-error">("http-error"),
    status: fc.constantFrom(404, 429, 500, 502, 503, 504),
    retryAfterSeconds: fc.option(fc.integer({ min: 1, max: 3600 })),
  }),
  fc.constant<PollResponse>({ type: "parse-error" }),
  fc.constant<PollResponse>({ type: "network-error" }),
);

/** A raw interval option — clamped by the hook into [1000, 5000]. */
const rawIntervalArb: fc.Arbitrary<number> = fc.integer({
  min: 500,
  max: 8_000,
});

// ---------------------------------------------------------------------------
// Harness lifecycle (mirrors the unit suite)
// ---------------------------------------------------------------------------

beforeEach(() => {
  // Synchronous scheduler so interval-triggered fetches commit within the
  // same timer advance — the pattern the unit suite documents.
  notifyManager.setScheduler((cb) => cb());
  vi.useFakeTimers();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.clearAllMocks();
  notifyManager.setScheduler((cb) => setTimeout(cb, 0));
});

/** Per-fc-run reset: fresh mock state, no leftover timers or DOM. */
function resetRun(): void {
  cleanup();
  vi.clearAllTimers();
  fetchJobMock.mockReset();
}

// ---------------------------------------------------------------------------
// Property 20
// ---------------------------------------------------------------------------

const NUM_RUNS = 100;

describe("Property 20: polling terminates correctly on any response sequence", () => {
  it("stops within one interval of the first stop trigger and maps it to its UI state", async () => {
    await fc.assert(
      fc.asyncProperty(
        fc.array(nonTerminalArb, { maxLength: 8 }),
        stopTriggerArb,
        rawIntervalArb,
        async (prefix, trigger, rawInterval) => {
          resetRun();
          const intervalMs = clampPollIntervalMs(rawInterval);

          const sequence = [...prefix, trigger];
          let call = 0;
          fetchJobMock.mockImplementation(() => {
            // Anything past the trigger repeats it — the hook must
            // never get that far, and the count assertions prove it.
            const response =
              sequence[Math.min(call, sequence.length - 1)] ?? trigger;
            call += 1;
            return toFetchResult(response);
          });

          const { result, unmount } = renderHook(
            () => useAgentJob(JOB_ID, { pollIntervalMs: rawInterval }),
            { wrapper: createWrapper() },
          );
          try {
            // First poll fires on mount.
            await advance(0);
            // Each subsequent response arrives one interval later.
            for (let i = 0; i < prefix.length; i += 1) {
              expect(result.current.state.kind).toBe("polling");
              await advance(intervalMs);
            }

            // The trigger has settled: exactly prefix+1 fetches, and
            // the state is the one this trigger maps to (Req 15.4/15.5).
            expect(fetchJobMock).toHaveBeenCalledTimes(sequence.length);
            expect(result.current.state.kind).toBe(expectedKind(trigger));
            expect(result.current.isPolling).toBe(false);

            // 429 surfaces the reset info it carried.
            if (
              trigger.type === "http-error" &&
              trigger.status === 429 &&
              result.current.state.kind === "rate-limited"
            ) {
              expect(result.current.state.retryAfterSeconds).toBe(
                trigger.retryAfterSeconds,
              );
            }

            // "Within one interval": no further poll is ever scheduled.
            await advance(intervalMs * 3);
            expect(fetchJobMock).toHaveBeenCalledTimes(sequence.length);
            expect(result.current.state.kind).toBe(expectedKind(trigger));

            // Every stopped state offers the recovery action (Req 15.4).
            expect(typeof result.current.retry).toBe("function");
          } finally {
            unmount();
          }
        },
      ),
      { numRuns: NUM_RUNS },
    );
  }, 120_000);

  it("stops at the 120 s cap for never-terminating sequences and maps to timed-out (Req 15.7)", async () => {
    await fc.assert(
      fc.asyncProperty(
        fc.array(nonTerminalArb, { minLength: 1, maxLength: 4 }),
        // Slower cadences keep the fake-timer walk to 120 s tractable
        // (24–60 polls per run) without weakening the property: the cap
        // is wall-clock, not poll-count, and the terminating property
        // above already exercises the full clamped interval range.
        fc.integer({ min: 2_000, max: 5_000 }),
        async (cycle, intervalMs) => {
          resetRun();

          let call = 0;
          fetchJobMock.mockImplementation(() => {
            const response = cycle[call % cycle.length] ?? cycle[0];
            call += 1;
            return toFetchResult(response as PollResponse);
          });

          const { result, unmount } = renderHook(
            () => useAgentJob(JOB_ID, { pollIntervalMs: intervalMs }),
            { wrapper: createWrapper() },
          );
          try {
            await advance(0);
            expect(result.current.state.kind).toBe("polling");

            // Walk the clock to exactly the cap: the hook flips to
            // timed-out and no further poll is scheduled.
            await advance(POLL_TIMEOUT_MS);
            expect(result.current.state.kind).toBe("timed-out");
            expect(result.current.isPolling).toBe(false);

            const callsAtTimeout = fetchJobMock.mock.calls.length;
            await advance(intervalMs * 3);
            expect(fetchJobMock).toHaveBeenCalledTimes(callsAtTimeout);
            expect(result.current.state.kind).toBe("timed-out");

            expect(typeof result.current.retry).toBe("function");
          } finally {
            unmount();
          }
        },
      ),
      { numRuns: NUM_RUNS },
    );
  }, 120_000);
});
