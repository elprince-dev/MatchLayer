"use client";

import * as React from "react";

import { CircleAlert, Clock, RotateCcw, Sparkles } from "lucide-react";

import { AnalysisProgress } from "@/components/analysis/analysis-progress";
import { AnalysisResultView } from "@/components/analysis/analysis-result";
import { Button } from "@/components/ui/button";
import { useAgentJob } from "@/hooks/use-agent-job";
import {
  AgentJobParseError,
  AgentJobRequestError,
  startAnalysis,
  type AgentJobProblem,
} from "@/lib/api/agent-jobs";
import { cn } from "@/lib/utils";

/**
 * AnalysisPanel — wires the Phase 4 agent-analysis flow into the match
 * results page (phase-4-agentic Task 15.5; Requirements 15.1, 15.2, 15.4,
 * 15.7; design §10).
 *
 * Rendered inside `(app)/matches/[id]` via the `ResultsContent` slot
 * pattern (the exact `LlmTabs` precedent), so it inherits the route
 * group's `robots: { index: false, follow: false }` and `X-Robots-Tag`
 * header (`seo.md`) — no metadata is added here.
 *
 * ## The flow (Req 15.1)
 * A trigger button calls {@link startAnalysis}
 * (`POST /api/v1/matches/{id}/analyze`); the accepted job id feeds
 * {@link useAgentJob}, whose polling drives {@link AnalysisProgress}
 * (`steps` from every Zod-parsed poll); on `completed` the panel renders
 * {@link AnalysisResultView} with the degraded badges and "Show
 * reasoning" toggle (Req 15.2, 15.3 — owned by that component).
 *
 * ## Per-case error states, each with a recovery action (Req 15.4, 15.7)
 * Two error surfaces exist because two calls can fail:
 *
 * **Trigger errors** ({@link startAnalysis} throwing) map via
 * {@link mapTriggerError} — 404 (match not available), 429 (rate/quota
 * limit; `problem.detail` names the configured limit and UTC reset, plus
 * the rounded-up `Retry-After` seconds), 5xx/503/network (temporary
 * failure), Zod parse failure (contract drift → temporary failure per Req
 * 15.5's precedent). Every trigger error recovers via **Try again**,
 * re-invoking {@link startAnalysis}.
 *
 * **Polling states** come straight from the hook's `AgentJobUiState`
 * discriminated union — `failed` (job's display-safe `error.detail`,
 * recover by starting a new analysis), `not-found` (job not available,
 * start a new analysis), `rate-limited` (detail names the reset; retry
 * the poll), `server-error` / `parse-error` (temporary failure; retry the
 * poll), `timed-out` (Req 15.7: resume polling *or* start a new
 * analysis). "Retry" calls the hook's `retry()` (resumes polling, resets
 * the 120 s clock); "Start a new analysis" re-invokes
 * {@link startAnalysis}.
 *
 * ## Rendering safety (security.md)
 * Every displayed string is either static copy or a display-safe RFC 7807
 * `detail` / job `error.detail` field, rendered as a plain React text
 * node. No raw error object ever reaches the DOM; no
 * `dangerouslySetInnerHTML`.
 *
 * ## Styling
 * Calm app-shell treatment per `design.md`: token-only Tailwind, the
 * elevated-card surface, no decorative motion. Errors are announced via
 * `role="alert"` (the `ErrorState` / `LlmErrorState` precedent).
 */

// ---------------------------------------------------------------------------
// Trigger-call state — startAnalysis has its own error surface (Req 15.4)
// ---------------------------------------------------------------------------

/** Per-case view of a failed {@link startAnalysis} call. */
type TriggerError =
  /** HTTP 404 — the match is not available (ownership-indistinguishable). */
  | { kind: "not-found"; problem: AgentJobProblem }
  /** HTTP 429 — analyze rate limit or the Daily_Quota precheck. */
  | {
      kind: "rate-limited";
      problem: AgentJobProblem;
      retryAfterSeconds: number | null;
    }
  /** HTTP 5xx (incl. the 503 enqueue failure) or a network error. */
  | { kind: "server-error"; problem: AgentJobProblem | null }
  /** A 2xx body failed the generated Zod schema (contract drift). */
  | { kind: "parse-error" };

/** The trigger call's lifecycle, orthogonal to the polling hook's state. */
type TriggerState =
  | { kind: "idle" }
  | { kind: "starting" }
  | { kind: "started" }
  | { kind: "error"; error: TriggerError };

/**
 * Map a {@link startAnalysis} failure to its per-case view — the same
 * branching the polling hook applies to poll errors, so both surfaces
 * treat identical HTTP cases identically.
 */
function mapTriggerError(error: unknown): TriggerError {
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
  // Network TypeError, aborts, unexpected throws — temporary failure.
  return { kind: "server-error", problem: null };
}

// ---------------------------------------------------------------------------
// Shared inline error surface
// ---------------------------------------------------------------------------

/** One recovery action rendered as a button (Req 15.4: at least one). */
interface RecoveryAction {
  label: string;
  onClick: () => void;
}

/**
 * Inline error surface for the analysis flow. Mirrors `LlmErrorState`
 * (calm, left-aligned, `role="alert"` so the state is announced; the
 * icon is decorative and the meaning is carried by text, never color
 * alone). Receives only pre-mapped, display-safe copy — never a raw
 * error object — so there is structurally nothing to leak.
 */
function AnalysisErrorState({
  kind,
  title,
  message,
  actions,
  icon: Icon = CircleAlert,
}: {
  kind: string;
  title: string;
  message: string;
  actions: RecoveryAction[];
  icon?: React.ComponentType<{ className?: string }>;
}): React.JSX.Element {
  return (
    <div
      data-slot="analysis-error-state"
      data-kind={kind}
      role="alert"
      className="flex items-start gap-3 rounded-card border border-border bg-bg-elevated p-4"
    >
      <span
        aria-hidden="true"
        className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-full border border-danger/30 bg-danger/10 text-danger"
      >
        <Icon className="size-4" />
      </span>

      <div className="min-w-0 space-y-1">
        <p className="text-sm font-semibold tracking-tight text-text">
          {title}
        </p>
        <p className="text-sm text-text-muted">{message}</p>

        <div className="flex flex-wrap gap-3 pt-2">
          {actions.map((action) => (
            <Button
              key={action.label}
              type="button"
              size="sm"
              variant="outline"
              onClick={action.onClick}
            >
              <RotateCcw aria-hidden="true" />
              {action.label}
            </Button>
          ))}
        </div>
      </div>
    </div>
  );
}

/**
 * Append the rounded-up `Retry-After` seconds to 429 copy when the header
 * was present (Req 15.4: "including any reset time present in the
 * response"). The RFC 7807 `detail` already names the UTC reset; the
 * header adds the relative wait. Mirrors `RetryAfterMessage`'s clamping.
 */
function withRetryAfter(detail: string, seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds)) {
    return detail;
  }
  const safe = Math.max(1, Math.ceil(seconds));
  return `${detail} You can try again in ${safe} ${safe === 1 ? "second" : "seconds"}.`;
}

// ---------------------------------------------------------------------------
// The panel
// ---------------------------------------------------------------------------

/** Props for {@link AnalysisPanel}. */
export interface AnalysisPanelProps {
  /** The viewed Match_Result id the analysis is anchored to. */
  matchId: string;
  /** Composition hook — extends (never replaces) the base layout. */
  className?: string;
}

export function AnalysisPanel({
  matchId,
  className,
}: AnalysisPanelProps): React.JSX.Element {
  const [jobId, setJobId] = React.useState<string | null>(null);
  const [trigger, setTrigger] = React.useState<TriggerState>({ kind: "idle" });
  const { state, steps, retry } = useAgentJob(jobId);

  /**
   * Trigger (or re-trigger) the analysis. On the in-flight
   * idempotent-reuse path the same job id can come back; `retry()` then
   * resets the hook's error/timeout clock so polling resumes cleanly (on
   * a fresh id the epoch bump is harmless — the query key changed anyway).
   */
  const start = React.useCallback((): void => {
    setTrigger({ kind: "starting" });
    void startAnalysis(matchId).then(
      (accepted) => {
        setTrigger({ kind: "started" });
        setJobId(accepted.id);
        retry();
      },
      (error: unknown) => {
        setTrigger({ kind: "error", error: mapTriggerError(error) });
      },
    );
  }, [matchId, retry]);

  const startAction: RecoveryAction = {
    label: "Start a new analysis",
    onClick: start,
  };
  const retryPollAction: RecoveryAction = { label: "Retry", onClick: retry };

  let body: React.JSX.Element;

  // Precedence: an in-flight or failed trigger call is the most recent
  // user action, so its feedback wins over any stale polling state.
  if (trigger.kind === "starting") {
    body = (
      <div className="rounded-card border border-border bg-bg-elevated p-6 shadow-resting">
        <Button type="button" disabled>
          <Sparkles aria-hidden="true" />
          Starting analysis…
        </Button>
      </div>
    );
  } else if (trigger.kind === "error") {
    body = <TriggerErrorView error={trigger.error} onTryAgain={start} />;
  } else if (state.kind === "idle") {
    // No job yet — the trigger surface.
    body = (
      <div className="space-y-4 rounded-card border border-border bg-bg-elevated p-6 shadow-resting">
        <p className="text-sm text-text-muted">
          Run a multi-agent analysis of this match: resume profile, ATS scoring,
          skill gaps, and improvement suggestions — with per-agent reasoning you
          can inspect.
        </p>
        <Button type="button" onClick={start}>
          <Sparkles aria-hidden="true" />
          Run agent analysis
        </Button>
      </div>
    );
  } else if (state.kind === "polling") {
    body = <AnalysisProgress steps={steps} />;
  } else if (state.kind === "completed") {
    const result = state.job.result ?? null;
    body =
      result !== null ? (
        <AnalysisResultView result={result} />
      ) : (
        // Contract violation (`result` present iff completed) — same
        // temporary-failure treatment as a parse failure (Req 15.5).
        <AnalysisErrorState
          kind="parse-error"
          title="We couldn't read the analysis"
          message="The analysis finished but its result couldn't be read. This is usually temporary — try again in a moment."
          actions={[retryPollAction]}
        />
      );
  } else if (state.kind === "failed") {
    body = (
      <AnalysisErrorState
        kind="failed"
        title="Analysis failed"
        message={state.job.error?.detail ?? "The analysis failed."}
        actions={[startAction]}
      />
    );
  } else if (state.kind === "not-found") {
    body = (
      <AnalysisErrorState
        kind="not-found"
        title="Analysis not available"
        message="This analysis job isn't available. It may have expired, or it may belong to a different account."
        actions={[startAction]}
      />
    );
  } else if (state.kind === "rate-limited") {
    body = (
      <AnalysisErrorState
        kind="rate-limited"
        title="Too many requests"
        message={withRetryAfter(state.problem.detail, state.retryAfterSeconds)}
        actions={[retryPollAction]}
        icon={Clock}
      />
    );
  } else if (state.kind === "parse-error") {
    body = (
      <AnalysisErrorState
        kind="parse-error"
        title="We couldn't read the analysis status"
        message="The status response couldn't be read. This is usually temporary — try again in a moment."
        actions={[retryPollAction]}
      />
    );
  } else if (state.kind === "timed-out") {
    body = (
      <AnalysisErrorState
        kind="timed-out"
        title="Analysis is taking longer than expected"
        message="No result arrived within two minutes. The analysis may still be running — you can keep waiting or start over."
        actions={[{ label: "Resume polling", onClick: retry }, startAction]}
        icon={Clock}
      />
    );
  } else {
    // "server-error" — HTTP 5xx or a network failure while polling.
    body = (
      <AnalysisErrorState
        kind="server-error"
        title="Temporary problem"
        message={
          state.problem?.detail ??
          "Something went wrong while checking your analysis. Try again in a moment."
        }
        actions={[retryPollAction]}
      />
    );
  }

  return (
    <section
      data-slot="analysis-panel"
      aria-label="Agent analysis"
      className={cn("space-y-6", className)}
    >
      <h2 className="text-lg font-semibold tracking-tight text-text">
        Agent analysis
      </h2>
      {body}
    </section>
  );
}

/** The per-case trigger error surfaces — every case recovers via retry. */
function TriggerErrorView({
  error,
  onTryAgain,
}: {
  error: TriggerError;
  onTryAgain: () => void;
}): React.JSX.Element {
  const tryAgain: RecoveryAction = { label: "Try again", onClick: onTryAgain };

  if (error.kind === "not-found") {
    return (
      <AnalysisErrorState
        kind="trigger-not-found"
        title="Match not available"
        message="This match isn't available to analyze. It may have been removed, or the link may be incorrect."
        actions={[tryAgain]}
      />
    );
  }

  if (error.kind === "rate-limited") {
    return (
      <AnalysisErrorState
        kind="trigger-rate-limited"
        title="Too many requests"
        message={withRetryAfter(error.problem.detail, error.retryAfterSeconds)}
        actions={[tryAgain]}
        icon={Clock}
      />
    );
  }

  if (error.kind === "parse-error") {
    return (
      <AnalysisErrorState
        kind="trigger-parse-error"
        title="We couldn't read the response"
        message="The analysis was requested but the response couldn't be read. This is usually temporary — try again in a moment."
        actions={[tryAgain]}
      />
    );
  }

  // "server-error" — 503 enqueue failure, other 5xx, or a network error.
  return (
    <AnalysisErrorState
      kind="trigger-server-error"
      title="Couldn't start the analysis"
      message={
        error.problem?.detail ??
        "The analysis couldn't be started. Check your connection and try again in a moment."
      }
      actions={[tryAgain]}
    />
  );
}
