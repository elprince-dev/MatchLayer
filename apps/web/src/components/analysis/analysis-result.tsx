"use client";

import * as React from "react";

import { Eye, EyeOff, TriangleAlert } from "lucide-react";

import type {
  AgentTraceSummary,
  AnalysisResult,
} from "@matchlayer/shared-types";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * AnalysisResultView — renders a completed Agent_Job's `AnalysisResult`
 * (phase-4-agentic Task 15.4; Requirements 15.2, 15.3; design §10).
 *
 * ## Degraded indicators (Req 15.2)
 * Each of the four output sections (profile / ATS / skill gaps /
 * improvements) shows a visible {@link DegradedBadge} **exactly when** its
 * contributing agent degraded — indicated by the output's own `degraded`
 * marker or that agent's `degraded` trace status ({@link sectionDegraded},
 * ORing the two signals Requirement 15.2 names). Like the Phase 3
 * `FallbackBadge`, the show/hide gate lives inside the badge so the
 * "exactly when" rule is enforced in one place.
 *
 * ## "Show reasoning" toggle (Req 15.3)
 * A plain `useState(false)` — reasoning is hidden on every mount, with no
 * persistence anywhere (no localStorage, no cookie, no server state), so
 * every page load starts hidden. When enabled it reveals one panel per
 * entry in `result.agent_traces`: agent name, completion status, latency,
 * and the structured failure reason when the agent degraded.
 *
 * ## Rendering safety (Req 15.3; security.md "LLM output sanitization")
 * Every model- or trace-derived string in this file is interpolated as a
 * **React text child** — React escapes it, so HTML embedded in trace
 * details, rewrites, or profile fields renders as inert literal text.
 * There is no `dangerouslySetInnerHTML`, no HTML parsing, and no markdown
 * pipeline (plain text is the project's sanctioned rendering mode — the
 * exact `StreamingText` precedent from Phase 3).
 *
 * ## Styling (design.md "calm app-shell")
 * Token-only Tailwind, elevated card sections, no decorative motion.
 * Numbers use `font-mono tabular-nums` per the design typography rules.
 */

/** The four output sections' contributing agents (Req 15.2 mapping). */
const SECTION_AGENTS = {
  profile: "resume_analysis",
  ats: "ats",
  skill_gaps: "skill_gap",
  improvements: "improvement",
} as const;

/** Human-readable names for the known agents in trace panels. */
const TRACE_AGENT_LABELS: Partial<Record<string, string>> = {
  resume_analysis: "Resume analysis",
  ats: "ATS scoring",
  skill_gap: "Skill gaps",
  improvement: "Improvements",
  synthesizer: "Synthesizer",
};

/** Props for {@link AnalysisResultView}. */
export interface AnalysisResultViewProps {
  /** The completed job's Analysis_Result (`job.result`, Zod-validated). */
  result: AnalysisResult;
  /** Composition hook — extends (never replaces) the base layout. */
  className?: string;
}

/**
 * True iff the section's contributing agent degraded: the per-output
 * `degraded` marker **or** a `degraded` status in that agent's trace
 * summary (Requirement 15.2 accepts either signal; a normal output never
 * carries either, so the badge appears on exactly the degraded sections).
 */
function sectionDegraded(
  outputDegraded: boolean,
  traces: AgentTraceSummary[],
  agentName: string,
): boolean {
  return (
    outputDegraded ||
    traces.some(
      (trace) => trace.agent_name === agentName && trace.status === "degraded",
    )
  );
}

/**
 * DegradedBadge — the visible degraded indicator (Req 15.2), mirroring the
 * Phase 3 `FallbackBadge` gate pattern: renders **iff** `degraded` is true.
 * Warning tokens (caution, per design.md), not `danger` — degraded content
 * is honest fallback data, not an error.
 */
export function DegradedBadge({
  degraded,
  className,
}: {
  degraded: boolean;
  className?: string;
}): React.JSX.Element | null {
  if (!degraded) {
    return null;
  }

  return (
    <span
      data-slot="degraded-badge"
      className={cn(
        "inline-flex items-center gap-1.5 rounded-pill border border-warning/30 bg-warning/10 px-2.5 py-0.5 text-xs font-medium text-warning",
        className,
      )}
    >
      <TriangleAlert aria-hidden="true" className="size-3.5 shrink-0" />
      Degraded — fallback content
    </span>
  );
}

/** Shared section chrome: elevated card, heading row with the badge. */
function ResultSection({
  title,
  degraded,
  children,
}: {
  title: string;
  degraded: boolean;
  children: React.ReactNode;
}): React.JSX.Element {
  return (
    <section
      data-slot="analysis-section"
      data-degraded={degraded}
      aria-label={title}
      className="space-y-4 rounded-card border border-border bg-bg-elevated p-6 shadow-resting"
    >
      <div className="flex flex-wrap items-center gap-3">
        <h3 className="text-lg font-semibold tracking-tight text-text">
          {title}
        </h3>
        <DegradedBadge degraded={degraded} />
      </div>
      {children}
    </section>
  );
}

/** The ATS section: score, confidence, scorer version, breakdown. */
function AtsSection({
  ats,
  degraded,
}: {
  ats: AnalysisResult["ats"];
  degraded: boolean;
}): React.JSX.Element {
  const breakdown = Object.entries(ats.breakdown ?? {});

  return (
    <ResultSection title="ATS score" degraded={degraded}>
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
        <span className="font-mono text-4xl font-semibold tabular-nums tracking-tight text-text">
          {ats.score}
        </span>
        <span className="text-sm text-text-muted">
          {ats.confidence} confidence
        </span>
      </div>

      {breakdown.length > 0 && (
        <dl className="space-y-1.5">
          {breakdown.map(([component, value]) => (
            <div
              key={component}
              className="flex items-baseline justify-between gap-3"
            >
              <dt className="text-sm text-text-muted">{component}</dt>
              <dd className="font-mono text-sm tabular-nums text-text">
                {value}
              </dd>
            </div>
          ))}
        </dl>
      )}

      <p className="font-mono text-xs text-text-subtle">
        Scorer {ats.scorer_version}
      </p>
    </ResultSection>
  );
}

/** The candidate-profile section: sections, skills, experiences, gaps. */
function ProfileSection({
  profile,
  degraded,
}: {
  profile: AnalysisResult["profile"];
  degraded: boolean;
}): React.JSX.Element {
  const sections = profile.sections ?? [];
  const skills = profile.skills ?? [];
  const experiences = profile.experiences ?? [];
  const gaps = profile.gaps ?? [];

  return (
    <ResultSection title="Candidate profile" degraded={degraded}>
      {skills.length > 0 && (
        <div className="space-y-2">
          <h4 className="text-sm font-medium text-text">Skills</h4>
          <ul className="flex list-none flex-wrap gap-2 p-0">
            {skills.map((skill, index) => (
              <li
                key={`${skill}-${index}`}
                className="rounded-pill border border-border-strong bg-bg px-2.5 py-0.5 text-xs font-medium text-text-muted"
              >
                {skill}
              </li>
            ))}
          </ul>
        </div>
      )}

      {experiences.length > 0 && (
        <div className="space-y-2">
          <h4 className="text-sm font-medium text-text">Experience</h4>
          <ul className="list-none space-y-1.5 p-0">
            {experiences.map((experience, index) => (
              <li key={index} className="text-sm text-text-muted">
                {[experience.role, experience.organization, experience.duration]
                  .filter(
                    (part): part is string =>
                      part !== null && part !== undefined,
                  )
                  .join(" — ") || "Experience entry"}
              </li>
            ))}
          </ul>
        </div>
      )}

      {sections.length > 0 && (
        <p className="text-sm text-text-muted">
          Detected sections: {sections.join(", ")}
        </p>
      )}

      {gaps.length > 0 && (
        <div className="space-y-2">
          <h4 className="text-sm font-medium text-text">Gaps and weaknesses</h4>
          <ul className="list-disc space-y-1 pl-5 text-sm text-text-muted">
            {gaps.map((gap, index) => (
              <li key={index}>{gap}</li>
            ))}
          </ul>
        </div>
      )}

      {skills.length === 0 &&
        experiences.length === 0 &&
        sections.length === 0 &&
        gaps.length === 0 && (
          <p className="text-sm text-text-muted">
            No profile details were extracted from this resume.
          </p>
        )}
    </ResultSection>
  );
}

/** The skill-gaps section: the ranked, classified gap list. */
function SkillGapsSection({
  report,
  degraded,
}: {
  report: AnalysisResult["skill_gaps"];
  degraded: boolean;
}): React.JSX.Element {
  const gaps = report.gaps ?? [];

  return (
    <ResultSection title="Skill gaps" degraded={degraded}>
      {gaps.length === 0 ? (
        // An empty gap list is a valid, positive outcome — neutral copy,
        // never the danger treatment (Phase 1 empty-state precedent).
        <p className="text-sm text-text-muted">
          No skill gaps identified — the resume covers the skills extracted from
          this job description.
        </p>
      ) : (
        <ol className="list-none space-y-2 p-0">
          {gaps.map((gap) => (
            <li
              key={`${gap.rank}-${gap.skill}`}
              className="flex items-baseline gap-3"
            >
              <span className="font-mono text-sm tabular-nums text-text-subtle">
                {gap.rank}.
              </span>
              <span className="text-sm font-medium text-text">{gap.skill}</span>
              <span className="rounded-pill border border-border-strong bg-bg px-2 py-0.5 text-xs text-text-muted">
                {gap.classification}
              </span>
            </li>
          ))}
        </ol>
      )}
    </ResultSection>
  );
}

/** The improvements section: prioritized actions and rewrite suggestions. */
function ImprovementsSection({
  report,
  degraded,
}: {
  report: AnalysisResult["improvements"];
  degraded: boolean;
}): React.JSX.Element {
  const actions = report.actions ?? [];
  const rewrites = report.rewrites ?? [];

  return (
    <ResultSection title="Improvements" degraded={degraded}>
      {actions.length > 0 && (
        <ol className="list-none space-y-2 p-0">
          {actions.map((action) => (
            <li key={action.rank} className="flex items-baseline gap-3">
              <span className="font-mono text-sm tabular-nums text-text-subtle">
                {action.rank}.
              </span>
              <span className="text-sm text-text">{action.text}</span>
            </li>
          ))}
        </ol>
      )}

      {rewrites.length > 0 && (
        <div className="space-y-3">
          <h4 className="text-sm font-medium text-text">Rewrite suggestions</h4>
          {rewrites.map((rewrite, index) => (
            <div
              key={index}
              className="space-y-2 rounded-card border border-border bg-bg p-4"
            >
              <p className="text-sm whitespace-pre-wrap break-words text-text-muted">
                {rewrite.excerpt}
              </p>
              <p className="text-sm whitespace-pre-wrap break-words text-text">
                {rewrite.replacement}
              </p>
              <p className="text-xs text-text-subtle">{rewrite.rationale}</p>
            </div>
          ))}
        </div>
      )}

      {actions.length === 0 && rewrites.length === 0 && (
        <p className="text-sm text-text-muted">
          No improvement suggestions were generated for this match.
        </p>
      )}
    </ResultSection>
  );
}

/**
 * One agent's trace summary panel (Req 15.3). Every string here — the
 * agent name and the failure detail — renders as a plain React text node,
 * so HTML-bearing content stays inert.
 */
function TracePanel({
  trace,
}: {
  trace: AgentTraceSummary;
}): React.JSX.Element {
  const label = TRACE_AGENT_LABELS[trace.agent_name] ?? trace.agent_name;
  const failure = trace.failure_reason ?? null;

  return (
    <div
      data-slot="agent-trace"
      className="space-y-1.5 rounded-card border border-border bg-bg p-4"
    >
      <div className="flex flex-wrap items-center gap-3">
        <p className="text-sm font-medium text-text">{label}</p>
        <DegradedBadge degraded={trace.status === "degraded"} />
        <p className="ml-auto font-mono text-xs tabular-nums text-text-subtle">
          {trace.latency_ms} ms
        </p>
      </div>
      {failure !== null && (
        <p className="text-sm whitespace-pre-wrap break-words text-text-muted">
          {failure.trigger}
          {typeof failure.detail === "string" &&
            failure.detail !== "" &&
            `: ${failure.detail}`}
        </p>
      )}
    </div>
  );
}

export function AnalysisResultView({
  result,
  className,
}: AnalysisResultViewProps): React.JSX.Element {
  // Req 15.3: defaults off on every load; deliberately no persistence.
  const [showReasoning, setShowReasoning] = React.useState(false);
  const reasoningId = React.useId();

  const traces = result.agent_traces ?? [];

  return (
    <section
      data-slot="analysis-result"
      aria-label="Analysis result"
      className={cn("w-full space-y-6", className)}
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-lg font-semibold tracking-tight text-text">
          Analysis result
        </h2>
        <Button
          type="button"
          size="sm"
          variant="outline"
          aria-expanded={showReasoning}
          aria-controls={reasoningId}
          onClick={() => {
            setShowReasoning((visible) => !visible);
          }}
        >
          {showReasoning ? (
            <EyeOff aria-hidden="true" />
          ) : (
            <Eye aria-hidden="true" />
          )}
          {showReasoning ? "Hide reasoning" : "Show reasoning"}
        </Button>
      </div>

      {showReasoning && (
        <div id={reasoningId} data-slot="agent-reasoning" className="space-y-3">
          {traces.length === 0 ? (
            <p className="text-sm text-text-muted">
              No agent traces were recorded for this run.
            </p>
          ) : (
            traces.map((trace, index) => (
              <TracePanel key={`${trace.agent_name}-${index}`} trace={trace} />
            ))
          )}
        </div>
      )}

      <AtsSection
        ats={result.ats}
        degraded={sectionDegraded(
          result.ats.degraded,
          traces,
          SECTION_AGENTS.ats,
        )}
      />
      <ProfileSection
        profile={result.profile}
        degraded={sectionDegraded(
          result.profile.degraded,
          traces,
          SECTION_AGENTS.profile,
        )}
      />
      <SkillGapsSection
        report={result.skill_gaps}
        degraded={sectionDegraded(
          result.skill_gaps.degraded,
          traces,
          SECTION_AGENTS.skill_gaps,
        )}
      />
      <ImprovementsSection
        report={result.improvements}
        degraded={sectionDegraded(
          result.improvements.degraded,
          traces,
          SECTION_AGENTS.improvements,
        )}
      />
    </section>
  );
}
