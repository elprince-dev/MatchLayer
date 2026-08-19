/**
 * Wiring tests for the results-page analysis flow
 * (phase-4-agentic Task 15.5; Requirements 15.1, 15.2, 15.4, 15.7).
 *
 * Minimal wiring coverage only — the Progress UI internals (five labeled
 * steps, `aria-live`, show-reasoning toggle, exhaustive per-case error
 * states) are Task 15.8's component tests and Property tests 20/21, not
 * duplicated here. What this suite pins down is the *composition*:
 *
 * - **Trigger (15.1):** the panel renders the trigger button and calls
 *   `startAnalysis` only on click; the accepted job id starts polling and
 *   the progress display renders while the job is non-terminal.
 * - **Result render (15.2):** a `completed` poll renders the
 *   `AnalysisResultView`.
 * - **Trigger errors (15.4):** a 429 from `startAnalysis` surfaces the
 *   RFC 7807 detail plus the `Retry-After` seconds, and "Try again"
 *   re-invokes `startAnalysis`.
 * - **Poll errors (15.4, 15.7):** every stopped hook state renders its
 *   per-case error surface with at least one recovery action — `failed`
 *   (display-safe `error.detail`, start a new analysis), `not-found`
 *   (job not available, start a new analysis), `rate-limited` (reset
 *   detail + `Retry-After`, retry resumes polling), `server-error`
 *   (temporary failure, retry), `parse-error` (temporary failure,
 *   retry), and `timed-out` at the 120 s cap (resume polling or start a
 *   new analysis).
 *
 * Conventions mirror the suite (`use-agent-job.test.tsx`,
 * `llm-components.test.tsx`): the typed client mocked at the module
 * boundary with the real error classes kept, a throwaway QueryClient with
 * retries off, fixtures parsed through the generated Zod schemas (drift
 * guard), no jest-dom matchers. All fixture data is synthetic
 * (security.md).
 *
 * @vitest-environment jsdom
 */

import * as React from "react";

import {
  QueryClient,
  QueryClientProvider,
  notifyManager,
} from "@tanstack/react-query";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { JobResponseSchema, type JobResponse } from "@matchlayer/shared-types";

// Mock only the two calls; keep the real error classes so the panel's
// `instanceof` branching runs against the same constructors.
vi.mock("@/lib/api/agent-jobs", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api/agent-jobs")>(
    "@/lib/api/agent-jobs",
  );
  return { ...actual, startAnalysis: vi.fn(), fetchJob: vi.fn() };
});

import {
  AgentJobParseError,
  AgentJobRequestError,
  fetchJob,
  startAnalysis,
  type AgentJobProblem,
} from "@/lib/api/agent-jobs";

import { AnalysisPanel } from "@/components/analysis/analysis-panel";
import { POLL_TIMEOUT_MS } from "@/hooks/use-agent-job";

const startAnalysisMock = vi.mocked(startAnalysis);
const fetchJobMock = vi.mocked(fetchJob);

// ---------------------------------------------------------------------------
// Fixtures — conform to the generated contract (no invented fields)
// ---------------------------------------------------------------------------

const MATCH_ID = "01938f00-0000-7000-8000-0000000000aa";
const JOB_ID = "01936d2e-0000-7000-8000-000000000001";

/** A minimal, schema-valid AnalysisResult for the completed fixture. */
const ANALYSIS_RESULT = {
  ats: {
    score: 72,
    breakdown: { similarity: 40, keyword_coverage: 32 },
    confidence: "medium",
    scorer_version: "2.0.0+test",
  },
  skill_gaps: { gaps: [], degraded: false, derived_from_degraded_input: false },
  improvements: {
    actions: [],
    rewrites: [],
    degraded: false,
    derived_from_degraded_input: false,
  },
  profile: {
    sections: [],
    skills: ["python"],
    experiences: [],
    gaps: [],
    degraded: false,
    derived_from_degraded_input: false,
  },
  agent_traces: [],
};

/** Build a Zod-parsed job body in the given status (suite convention). */
function jobBody(status: JobResponse["status"]): JobResponse {
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
    result: status === "completed" ? ANALYSIS_RESULT : null,
    error:
      status === "failed"
        ? { type: "max_attempts_exhausted", detail: "The worker gave up." }
        : null,
  }) as JobResponse;
}

/** The 202 body `startAnalysis` resolves with. */
const ACCEPTED = {
  id: JOB_ID,
  status: "queued",
  job_url: `/api/v1/jobs/${JOB_ID}`,
} as const;

/** An RFC 7807 problem as the typed client coerces it. */
function problem(status: number, detail: string): AgentJobProblem {
  return {
    type: "rate_limited",
    title: "Too many requests",
    detail,
    status,
    request_id: "req-1",
  };
}

/** Render inside a throwaway QueryClient (retries off, matching app config). */
function renderPanel(): void {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <AnalysisPanel matchId={MATCH_ID} />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("AnalysisPanel wiring (Task 15.5)", () => {
  it("renders the trigger button and calls nothing before it is clicked", () => {
    renderPanel();

    expect(
      screen.getByRole("button", { name: /run agent analysis/i }),
    ).toBeInstanceOf(HTMLButtonElement);
    expect(startAnalysisMock).not.toHaveBeenCalled();
    expect(fetchJobMock).not.toHaveBeenCalled();
  });

  it("starts the analysis on click and shows the progress display while polling", async () => {
    startAnalysisMock.mockResolvedValue(ACCEPTED);
    fetchJobMock.mockResolvedValue(jobBody("running"));

    renderPanel();
    fireEvent.click(
      screen.getByRole("button", { name: /run agent analysis/i }),
    );

    await waitFor(() => {
      expect(startAnalysisMock).toHaveBeenCalledTimes(1);
    });
    expect(startAnalysisMock.mock.calls[0]?.[0]).toBe(MATCH_ID);

    // Progress display fed by the polled steps (Req 15.1).
    const progress = await screen.findByRole("region", {
      name: "Analysis progress",
    });
    expect(progress).toBeInstanceOf(HTMLElement);
    await waitFor(() => {
      expect(fetchJobMock).toHaveBeenCalled();
    });
    expect(fetchJobMock.mock.calls[0]?.[0]).toBe(JOB_ID);
  });

  it("renders the AnalysisResultView when the job completes (Req 15.2)", async () => {
    startAnalysisMock.mockResolvedValue(ACCEPTED);
    fetchJobMock.mockResolvedValue(jobBody("completed"));

    renderPanel();
    fireEvent.click(
      screen.getByRole("button", { name: /run agent analysis/i }),
    );

    const result = await screen.findByRole("region", {
      name: "Analysis result",
    });
    expect(result).toBeInstanceOf(HTMLElement);
    // A field from the fixture proves the polled result reached the view.
    expect(screen.getByText("72")).toBeInstanceOf(HTMLElement);
  });

  it("surfaces a 429 from the trigger with the reset detail and Retry-After, and Try again re-invokes", async () => {
    startAnalysisMock.mockRejectedValueOnce(
      new AgentJobRequestError(
        problem(429, "Daily AI limit reached. Resets at 00:00 UTC."),
        30,
      ),
    );

    renderPanel();
    fireEvent.click(
      screen.getByRole("button", { name: /run agent analysis/i }),
    );

    const alert = await screen.findByRole("alert");
    expect(alert.getAttribute("data-kind")).toBe("trigger-rate-limited");
    expect(alert.textContent).toContain(
      "Daily AI limit reached. Resets at 00:00 UTC.",
    );
    expect(alert.textContent).toContain("30 seconds");

    // Recovery action re-invokes startAnalysis (Req 15.4).
    startAnalysisMock.mockResolvedValue(ACCEPTED);
    fetchJobMock.mockResolvedValue(jobBody("running"));
    fireEvent.click(screen.getByRole("button", { name: /try again/i }));
    await waitFor(() => {
      expect(startAnalysisMock).toHaveBeenCalledTimes(2);
    });
  });

  it("shows a failed job's display-safe error detail with a start-a-new-analysis recovery (Req 15.4)", async () => {
    startAnalysisMock.mockResolvedValue(ACCEPTED);
    fetchJobMock.mockResolvedValue(jobBody("failed"));

    renderPanel();
    fireEvent.click(
      screen.getByRole("button", { name: /run agent analysis/i }),
    );

    const alert = await screen.findByRole("alert");
    expect(alert.getAttribute("data-kind")).toBe("failed");
    expect(alert.textContent).toContain("The worker gave up.");
    expect(
      screen.getByRole("button", { name: /start a new analysis/i }),
    ).toBeInstanceOf(HTMLButtonElement);
  });
});

// ---------------------------------------------------------------------------
// Per-case poll error states (Req 15.4) — each with a recovery action
// ---------------------------------------------------------------------------

describe("AnalysisPanel poll error states (Req 15.4)", () => {
  /** Trigger succeeds, then the first poll rejects with `error`. */
  async function renderWithPollError(error: unknown): Promise<HTMLElement> {
    startAnalysisMock.mockResolvedValue(ACCEPTED);
    fetchJobMock.mockRejectedValue(error);

    renderPanel();
    fireEvent.click(
      screen.getByRole("button", { name: /run agent analysis/i }),
    );
    return screen.findByRole("alert");
  }

  it("renders the not-available state for a 404 with a start-a-new-analysis recovery", async () => {
    const alert = await renderWithPollError(
      new AgentJobRequestError(problem(404, "Job not found."), null),
    );

    expect(alert.getAttribute("data-kind")).toBe("not-found");
    expect(alert.textContent).toContain("isn't available");
    expect(
      screen.getByRole("button", { name: /start a new analysis/i }),
    ).toBeInstanceOf(HTMLButtonElement);
  });

  it("renders the rate-limited state for a poll 429 with the reset detail, Retry-After, and a retry recovery", async () => {
    const alert = await renderWithPollError(
      new AgentJobRequestError(
        problem(429, "Poll limit reached. Resets at 00:00 UTC."),
        30,
      ),
    );

    expect(alert.getAttribute("data-kind")).toBe("rate-limited");
    expect(alert.textContent).toContain(
      "Poll limit reached. Resets at 00:00 UTC.",
    );
    expect(alert.textContent).toContain("30 seconds");
    expect(screen.getByRole("button", { name: /retry/i })).toBeInstanceOf(
      HTMLButtonElement,
    );
  });

  it("renders the temporary-failure state for a poll 5xx, and Retry resumes polling", async () => {
    const alert = await renderWithPollError(
      new AgentJobRequestError(problem(503, "Temporarily unavailable."), null),
    );

    expect(alert.getAttribute("data-kind")).toBe("server-error");
    expect(alert.textContent).toContain("Temporarily unavailable.");

    // The recovery action resumes polling the same job (Req 15.4):
    // the next poll succeeds and the progress display returns.
    fetchJobMock.mockResolvedValue(jobBody("running"));
    fireEvent.click(screen.getByRole("button", { name: /retry/i }));

    const progress = await screen.findByRole("region", {
      name: "Analysis progress",
    });
    expect(progress).toBeInstanceOf(HTMLElement);
    // No new analyze call — retry resumes the poll, not the trigger.
    expect(startAnalysisMock).toHaveBeenCalledTimes(1);
  });

  it("renders the temporary-failure state for a Zod parse failure with a retry recovery (Req 15.5)", async () => {
    const alert = await renderWithPollError(
      new AgentJobParseError("job polling"),
    );

    expect(alert.getAttribute("data-kind")).toBe("parse-error");
    expect(alert.textContent).toContain("couldn't be read");
    expect(screen.getByRole("button", { name: /retry/i })).toBeInstanceOf(
      HTMLButtonElement,
    );
  });
});

// ---------------------------------------------------------------------------
// Timeout state (Req 15.7) — fake timers drive the 120 s cap
// ---------------------------------------------------------------------------

describe("AnalysisPanel timeout state (Req 15.7)", () => {
  beforeEach(() => {
    // Synchronous query notifications so interval-driven commits land
    // within the same timer advance (the `use-agent-job.test.tsx` pattern).
    notifyManager.setScheduler((cb) => cb());
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    notifyManager.setScheduler((cb) => setTimeout(cb, 0));
  });

  /** Advance fake timers and flush the resulting settlements. */
  async function advance(ms: number): Promise<void> {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(ms);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
  }

  it("renders the timed-out state at the 120 s cap with both recovery actions, and Resume polling restarts", async () => {
    startAnalysisMock.mockResolvedValue(ACCEPTED);
    fetchJobMock.mockImplementation(async () => jobBody("running"));

    renderPanel();
    fireEvent.click(
      screen.getByRole("button", { name: /run agent analysis/i }),
    );
    // Flush the trigger resolution and the first poll.
    await advance(0);
    expect(
      screen.getByRole("region", { name: "Analysis progress" }),
    ).toBeInstanceOf(HTMLElement);

    // Walk the clock to the cap: the panel flips to the timeout state.
    await advance(POLL_TIMEOUT_MS);
    const alert = screen.getByRole("alert");
    expect(alert.getAttribute("data-kind")).toBe("timed-out");
    expect(alert.textContent).toContain("longer than expected");

    // Both Requirement 15.7 recovery actions are offered.
    const resume = screen.getByRole("button", { name: /resume polling/i });
    expect(resume).toBeInstanceOf(HTMLButtonElement);
    expect(
      screen.getByRole("button", { name: /start a new analysis/i }),
    ).toBeInstanceOf(HTMLButtonElement);

    // Resume polling restarts the poll (fresh 120 s clock, same job).
    const callsAtTimeout = fetchJobMock.mock.calls.length;
    fireEvent.click(resume);
    await advance(0);
    expect(
      screen.getByRole("region", { name: "Analysis progress" }),
    ).toBeInstanceOf(HTMLElement);
    expect(fetchJobMock.mock.calls.length).toBeGreaterThan(callsAtTimeout);
    expect(startAnalysisMock).toHaveBeenCalledTimes(1);
  });
});
