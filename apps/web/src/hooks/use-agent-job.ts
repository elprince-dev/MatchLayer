"use client";

/**
 * Agent-job polling hook (phase-4-agentic Task 15.2; Requirements 15.1,
 * 15.4, 15.5, 15.7).
 *
 * One instance polls one Agent_Job (`GET /api/v1/jobs/{id}` via
 * {@link fetchJob}) with a TanStack Query `refetchInterval` — default
 * 2 000 ms, clamped to [1 000 ms, 5 000 ms] so the cadence always satisfies
 * Requirement 15.1's "no less than 1 s, no more than 5 s" bound and stays
 * inside the 120-polls-per-minute Rate_Limiter default.
 *
 * ## Stop conditions → UI states (Req 15.4 / 15.5 / 15.7)
 *
 * Polling stops within one interval of any of the following, each mapping
 * to a distinct member of the {@link AgentJobUiState} discriminated union
 * so the progress and result components (Tasks 15.3–15.5) can branch
 * cleanly:
 *
 * | Trigger                                   | `state.kind`     |
 * | ----------------------------------------- | ---------------- |
 * | polled `status === "completed"`           | `"completed"`    |
 * | polled `status === "failed"`              | `"failed"`       |
 * | HTTP 404 (`AgentJobRequestError`)         | `"not-found"`    |
 * | HTTP 429 (`AgentJobRequestError`)         | `"rate-limited"` |
 * | HTTP 5xx / network / unknown error        | `"server-error"` |
 * | Zod parse failure (`AgentJobParseError`)  | `"parse-error"`  |
 * | 120 s elapsed since the first poll        | `"timed-out"`    |
 *
 * The stop mechanics are twofold: the `refetchInterval` callback returns
 * `false` as soon as the query holds an error or a terminal-status body
 * (so no further poll is ever scheduled — "within one interval" by
 * construction), and the 120-second cap disables the query outright via
 * `enabled`, cancelling any in-flight request.
 *
 * ## Recovery action (Req 15.4 / 15.7)
 *
 * Every stopped state carries the same recovery affordance: {@link
 * UseAgentJobResult.retry} resets the error, the timeout clock, and the
 * query cache entry (by bumping an epoch in the query key), then resumes
 * polling the same job. "Start a new analysis" — the other recovery action
 * Requirement 15.4 allows — is a page-level concern wired in Task 15.5.
 *
 * ## Contract safety (Req 15.5)
 *
 * Every polled response is Zod-parsed inside {@link fetchJob} with the
 * generated `JobResponseSchema`; contract drift surfaces here as
 * `AgentJobParseError` → `"parse-error"`, never as a render crash.
 */

import { useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";

import type { JobResponse, JobStep } from "@matchlayer/shared-types";

import {
  AgentJobParseError,
  AgentJobRequestError,
  fetchJob,
  type AgentJobProblem,
} from "@/lib/api/agent-jobs";

// ---------------------------------------------------------------------------
// Polling constants (design §10; Requirements 15.1, 15.7)
// ---------------------------------------------------------------------------

/** Default poll cadence when the caller passes no override. */
export const POLL_INTERVAL_DEFAULT_MS = 2_000;

/** Requirement 15.1 lower bound — never poll faster than once per second. */
export const POLL_INTERVAL_MIN_MS = 1_000;

/** Requirement 15.1 upper bound — never poll slower than once per 5 s. */
export const POLL_INTERVAL_MAX_MS = 5_000;

/** Requirement 15.7 — give up 120 s after the first poll. */
export const POLL_TIMEOUT_MS = 120_000;

/**
 * Clamp a requested poll interval into the Requirement 15.1 window.
 * Non-finite or missing values fall back to the 2 000 ms default rather
 * than clamping, so a config typo degrades to the designed cadence.
 */
export function clampPollIntervalMs(ms?: number): number {
  if (ms === undefined || !Number.isFinite(ms)) {
    return POLL_INTERVAL_DEFAULT_MS;
  }
  return Math.min(POLL_INTERVAL_MAX_MS, Math.max(POLL_INTERVAL_MIN_MS, ms));
}

// ---------------------------------------------------------------------------
// UI state — one member per stop condition (Req 15.4)
// ---------------------------------------------------------------------------

/**
 * The discriminated UI state the progress/result components branch on.
 * Exactly one member per Requirement 15.4/15.5/15.7 case, plus `idle`
 * (no job yet) and `polling` (in flight). Every non-`idle`, non-`polling`
 * member is a stopped state recoverable via {@link UseAgentJobResult.retry}.
 */
export type AgentJobUiState =
  /** No job id yet — nothing to poll (pre-analyze). */
  | { kind: "idle" }
  /** Actively polling; `job` is the latest parsed body (null before it). */
  | { kind: "polling"; job: JobResponse | null }
  /** Terminal success — render the Analysis_Result from `job.result`. */
  | { kind: "completed"; job: JobResponse }
  /** Terminal failure — `job.error` carries the display-safe details. */
  | { kind: "failed"; job: JobResponse }
  /** HTTP 404 — the job is not available (ownership-indistinguishable). */
  | { kind: "not-found"; problem: AgentJobProblem }
  /** HTTP 429 — rate/quota limited; `problem.detail` names the UTC reset. */
  | {
      kind: "rate-limited";
      problem: AgentJobProblem;
      retryAfterSeconds: number | null;
    }
  /**
   * HTTP 5xx or a network-level failure — the temporary-failure state.
   * `problem` is null for network errors, which carry no response body.
   */
  | { kind: "server-error"; problem: AgentJobProblem | null }
  /** A 2xx body failed the generated Zod schema (contract drift, Req 15.5). */
  | { kind: "parse-error" }
  /** No terminal status within 120 s of the first poll (Req 15.7). */
  | { kind: "timed-out" };

/** Options for {@link useAgentJob}. */
export interface UseAgentJobOptions {
  /**
   * Poll cadence in milliseconds, clamped to
   * [{@link POLL_INTERVAL_MIN_MS}, {@link POLL_INTERVAL_MAX_MS}].
   * Defaults to {@link POLL_INTERVAL_DEFAULT_MS}.
   */
  pollIntervalMs?: number;
}

/** What {@link useAgentJob} hands the analysis components. */
export interface UseAgentJobResult {
  /** The discriminated UI state — branch on `state.kind`. */
  state: AgentJobUiState;
  /** Latest successfully parsed job body, regardless of state. */
  job: JobResponse | null;
  /** Per-agent step statuses from the latest poll (empty before data). */
  steps: JobStep[];
  /** True while polling is active (`state.kind === "polling"`). */
  isPolling: boolean;
  /**
   * The recovery action every stopped state offers (Req 15.4/15.7):
   * clears the error and the 120 s clock, then resumes polling this job.
   */
  retry: () => void;
}

// ---------------------------------------------------------------------------
// Error → state mapping
// ---------------------------------------------------------------------------

/** True iff the polled status means polling must stop (Req 15.4). */
function isTerminalStatus(status: JobResponse["status"]): boolean {
  return status === "completed" || status === "failed";
}

/**
 * Map a query error to its Requirement 15.4 UI state. Anything that is
 * not one of the two typed client errors (network `TypeError`, aborts,
 * unexpected throw) is a temporary failure, matching the client's
 * documented error surface.
 */
function mapError(error: Error): AgentJobUiState {
  if (error instanceof AgentJobParseError) {
    return { kind: "parse-error" };
  }
  if (error instanceof AgentJobRequestError) {
    if (error.status === 404) {
      return { kind: "not-found", problem: error.problem };
    }
    if (error.status === 429) {
      return {
        kind: "rate-limited",
        problem: error.problem,
        retryAfterSeconds: error.retryAfterSeconds,
      };
    }
    return { kind: "server-error", problem: error.problem };
  }
  return { kind: "server-error", problem: null };
}

// ---------------------------------------------------------------------------
// The hook
// ---------------------------------------------------------------------------

/** See the module doc. */
export function useAgentJob(
  jobId: string | null,
  options: UseAgentJobOptions = {},
): UseAgentJobResult {
  const intervalMs = clampPollIntervalMs(options.pollIntervalMs);
  const enabled = jobId !== null && jobId.length > 0;

  // `retry()` bumps the epoch: a fresh query-cache entry drops the stale
  // error/data and refetches immediately — the cleanest "reset" TanStack
  // Query offers without imperatively juggling the QueryClient.
  const [epoch, setEpoch] = useState(0);
  const [timedOut, setTimedOut] = useState(false);
  // Wall-clock timestamp of the first poll of the current epoch — the
  // Requirement 15.7 clock starts "since the first poll", not since mount.
  const firstPollAtRef = useRef<number | null>(null);

  const query = useQuery<JobResponse, Error>({
    queryKey: ["agent-job", jobId, epoch],
    queryFn: ({ signal }) => fetchJob(jobId ?? "", { signal }),
    // The timeout is a hard stop: disabling the query also aborts any
    // in-flight request via the query's AbortSignal.
    enabled: enabled && !timedOut,
    // Client errors are stop conditions (Req 15.4), never silent retries;
    // explicit here so the hook doesn't depend on app-level defaults.
    retry: false,
    refetchOnWindowFocus: false,
    // Evaluated after every fetch settles: returning `false` cancels the
    // next scheduled poll, so every stop condition takes effect within
    // one interval of the response that triggered it (Req 15.4).
    refetchInterval: (q) => {
      if (timedOut) {
        return false;
      }
      if (q.state.error !== null) {
        return false;
      }
      const data = q.state.data;
      if (data !== undefined && isTerminalStatus(data.status)) {
        return false;
      }
      return intervalMs;
    },
  });

  const status = query.data?.status;
  const terminal = status !== undefined && isTerminalStatus(status);
  const hasError = query.error !== null;
  const pollingActive = enabled && !terminal && !hasError && !timedOut;

  // The 120 s cap (Req 15.7). Armed when polling starts (the query issues
  // its first fetch on the same commit), re-armed with the remaining time
  // if polling pauses and resumes, and torn down when polling stops.
  useEffect(() => {
    if (!pollingActive) {
      return undefined;
    }
    firstPollAtRef.current ??= Date.now();
    const remaining = POLL_TIMEOUT_MS - (Date.now() - firstPollAtRef.current);
    if (remaining <= 0) {
      setTimedOut(true);
      return undefined;
    }
    const handle = setTimeout(() => {
      setTimedOut(true);
    }, remaining);
    return () => {
      clearTimeout(handle);
    };
  }, [pollingActive, epoch]);

  const retry = useCallback((): void => {
    firstPollAtRef.current = null;
    setTimedOut(false);
    setEpoch((e) => e + 1);
  }, []);

  // Precedence: a terminal body beats everything (the job *did* finish);
  // then errors, then the timeout, then live polling.
  let state: AgentJobUiState;
  if (!enabled) {
    state = { kind: "idle" };
  } else if (query.data !== undefined && query.data.status === "completed") {
    state = { kind: "completed", job: query.data };
  } else if (query.data !== undefined && query.data.status === "failed") {
    state = { kind: "failed", job: query.data };
  } else if (query.error !== null) {
    state = mapError(query.error);
  } else if (timedOut) {
    state = { kind: "timed-out" };
  } else {
    state = { kind: "polling", job: query.data ?? null };
  }

  return {
    state,
    job: query.data ?? null,
    steps: query.data?.steps ?? [],
    isPolling: state.kind === "polling",
    retry,
  };
}
