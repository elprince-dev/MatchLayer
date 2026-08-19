import * as React from "react";

import { CircleCheck, CircleX, TriangleAlert } from "lucide-react";

import type {
  AgentName,
  AgentStepStatus,
  JobStep,
} from "@matchlayer/shared-types";

import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

/**
 * AnalysisProgress — the five-step Agent_Job progress display
 * (phase-4-agentic Task 15.3; Requirement 15.1; design §10).
 *
 * ## Exactly five labeled steps
 * One step per Agent_Graph node, always all five, in graph order
 * ({@link AGENT_STEP_ORDER}) with the Requirement 15.1 labels
 * ({@link AGENT_STEP_LABELS}). The component renders the full list from its
 * first mount — before the first poll lands every step shows its skeleton
 * pending state, so the layout never jumps as statuses arrive.
 *
 * ## Statuses derive solely from the polled steps (Req 15.1)
 * The `steps` prop is `useAgentJob(...).steps` — the per-agent statuses of
 * the latest Zod-parsed `GET /api/v1/jobs/{id}` body. This component holds
 * no state and infers nothing: an agent with no polled entry is `pending`
 * (mirroring the backend's "pending when no agent_runs row" rule), and each
 * re-render with fresh steps updates the display — i.e. within the same
 * render pass as the poll response that changed it, well inside the
 * one-polling-interval bound.
 *
 * ## Accessibility (Req 15.1; design.md baseline)
 * Status changes are announced via a visually hidden `aria-live="polite"`
 * region (`role="status"`, `aria-atomic` so the whole summary is read):
 * its text is a deterministic sentence per non-pending step, so it changes
 * exactly when a polled status changes. Each step also carries visible or
 * `sr-only` status text, so the list reads correctly on its own.
 *
 * ## Styling (design.md "calm app-shell")
 * Token-only Tailwind on the elevated card surface; the only motion is the
 * skeleton pulse (a sanctioned loading state). Pending states are
 * content-shaped skeletons, never spinners.
 *
 * Presentation-only (no state, effects, or browser APIs), so no
 * `"use client"` directive — it renders inside the client-side analysis
 * flow of Task 15.5.
 */

/** The five Agent_Graph nodes in graph (display) order. */
export const AGENT_STEP_ORDER = [
  "resume_analysis",
  "ats",
  "skill_gap",
  "improvement",
  "synthesizer",
] as const satisfies readonly AgentName[];

/** Requirement 15.1's example labels, one per agent, verbatim. */
export const AGENT_STEP_LABELS: Record<AgentName, string> = {
  resume_analysis: "Analyzing resume…",
  ats: "ATS scoring…",
  skill_gap: "Finding skill gaps…",
  improvement: "Generating improvements…",
  synthesizer: "Combining results…",
};

/** Screen-reader wording per step status (announcement + sr-only text). */
const STATUS_ANNOUNCEMENTS: Record<AgentStepStatus, string> = {
  pending: "pending",
  completed: "completed",
  degraded: "completed with fallback content",
  failed: "failed",
};

/** Props for {@link AnalysisProgress}. */
export interface AnalysisProgressProps {
  /**
   * Per-agent step statuses from the latest poll (`useAgentJob(...).steps`).
   * May be empty before the first poll resolves — every agent then renders
   * as `pending`. Statuses are read from here and nowhere else.
   */
  steps: JobStep[];
  /** Composition hook — extends (never replaces) the base layout. */
  className?: string;
}

/**
 * Resolve the displayed status for every agent from the polled steps:
 * the agent's polled status when present, else `pending`.
 */
function resolveStatuses(steps: JobStep[]): Record<AgentName, AgentStepStatus> {
  const statuses: Record<AgentName, AgentStepStatus> = {
    resume_analysis: "pending",
    ats: "pending",
    skill_gap: "pending",
    improvement: "pending",
    synthesizer: "pending",
  };
  for (const step of steps) {
    statuses[step.agent_name] = step.status;
  }
  return statuses;
}

/**
 * The `aria-live` summary sentence: one clause per step whose status has
 * moved past `pending`, in graph order. Deterministic in the statuses, so
 * it changes iff a polled status changed — which is precisely when the
 * polite region should announce.
 */
function buildAnnouncement(
  statuses: Record<AgentName, AgentStepStatus>,
): string {
  const clauses = AGENT_STEP_ORDER.filter(
    (name) => statuses[name] !== "pending",
  ).map(
    (name) =>
      `${AGENT_STEP_LABELS[name]} ${STATUS_ANNOUNCEMENTS[statuses[name]]}.`,
  );
  return clauses.length > 0 ? clauses.join(" ") : "Analysis in progress.";
}

/** One step's status slot: skeleton, check icon, or a labeled pill. */
function StepStatus({
  status,
}: {
  status: AgentStepStatus;
}): React.JSX.Element {
  if (status === "pending") {
    return (
      <>
        {/* Content-shaped placeholder, not a spinner (design.md). */}
        <Skeleton aria-hidden="true" className="h-4 w-16 rounded-pill" />
        <span className="sr-only">Pending</span>
      </>
    );
  }

  if (status === "completed") {
    return (
      <>
        <CircleCheck
          aria-hidden="true"
          className="size-4 shrink-0 text-success"
        />
        <span className="sr-only">Completed</span>
      </>
    );
  }

  if (status === "degraded") {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-pill border border-warning/30 bg-warning/10 px-2.5 py-0.5 text-xs font-medium text-warning">
        <TriangleAlert aria-hidden="true" className="size-3.5 shrink-0" />
        Degraded
      </span>
    );
  }

  return (
    <span className="inline-flex items-center gap-1.5 rounded-pill border border-danger/30 bg-danger/10 px-2.5 py-0.5 text-xs font-medium text-danger">
      <CircleX aria-hidden="true" className="size-3.5 shrink-0" />
      Failed
    </span>
  );
}

export function AnalysisProgress({
  steps,
  className,
}: AnalysisProgressProps): React.JSX.Element {
  const statuses = resolveStatuses(steps);

  return (
    <section
      data-slot="analysis-progress"
      aria-label="Analysis progress"
      className={cn(
        "w-full space-y-4 rounded-card border border-border bg-bg-elevated p-6 shadow-resting",
        className,
      )}
    >
      <h2 className="text-lg font-semibold tracking-tight text-text">
        Analysis progress
      </h2>

      {/* Status changes announced politely (Req 15.1). `aria-atomic` reads
          the whole summary so a mid-list change is never announced without
          its step label. */}
      <p
        role="status"
        aria-live="polite"
        aria-atomic="true"
        className="sr-only"
      >
        {buildAnnouncement(statuses)}
      </p>

      <ol className="list-none space-y-3 p-0">
        {AGENT_STEP_ORDER.map((name) => (
          <li
            key={name}
            data-slot="analysis-step"
            data-agent={name}
            data-status={statuses[name]}
            className="flex min-h-6 items-center justify-between gap-3"
          >
            <span
              className={cn(
                "text-sm",
                statuses[name] === "pending"
                  ? "text-text-muted"
                  : "font-medium text-text",
              )}
            >
              {AGENT_STEP_LABELS[name]}
            </span>
            <StepStatus status={statuses[name]} />
          </li>
        ))}
      </ol>
    </section>
  );
}
