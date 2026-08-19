/**
 * AnalysisProgress component tests (phase-4-agentic Task 15.8).
 *
 * Requirements covered:
 * - 15.1 — exactly five labeled steps (verbatim labels, graph order); each
 *   step's displayed status derives solely from the polled `steps` prop
 *   (`pending` when an agent has no polled entry); status changes are
 *   announced via an `aria-live="polite"` region whose text changes
 *   exactly when a polled status changes.
 *
 * Error states (Req 15.4) are owned by the polling hook and the page
 * wiring of Task 15.5 — no error-state component exists in this component's
 * scope, so they are covered by the co-located hook suites
 * (`use-agent-job.test.tsx`, `use-agent-job.property.test.tsx`).
 *
 * Conventions mirror the co-located component tests
 * (`error-state.test.tsx`, `results/*.test.tsx`): `@testing-library/react`
 * render/screen/cleanup, `toBeInstanceOf`, className/attribute assertions,
 * no jest-dom matchers. All fixture data is synthetic (security.md).
 *
 * @vitest-environment jsdom
 */

import * as React from "react";

import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { JobStepSchema, type JobStep } from "@matchlayer/shared-types";

import {
  AGENT_STEP_LABELS,
  AGENT_STEP_ORDER,
  AnalysisProgress,
} from "./analysis-progress";

afterEach(() => {
  cleanup();
});

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

/** Build a polled step through the generated Zod schema (drift guard). */
function step(
  agent: JobStep["agent_name"],
  status: JobStep["status"],
): JobStep {
  return JobStepSchema.parse({ agent_name: agent, status }) as JobStep;
}

/** The rendered step list items, in document order. */
function stepItems(container: HTMLElement): HTMLElement[] {
  return Array.from(
    container.querySelectorAll('[data-slot="analysis-step"]'),
  ).filter((el): el is HTMLElement => el instanceof HTMLElement);
}

/** The visually hidden `aria-live` announcement region. */
function liveRegion(container: HTMLElement): HTMLElement {
  const region = container.querySelector('[role="status"]');
  expect(region).toBeInstanceOf(HTMLElement);
  return region as HTMLElement;
}

// ---------------------------------------------------------------------------
// Five labeled steps (Req 15.1)
// ---------------------------------------------------------------------------

describe("AnalysisProgress steps", () => {
  it("renders exactly five steps with the Requirement 15.1 labels, in graph order", () => {
    const { container } = render(<AnalysisProgress steps={[]} />);

    const items = stepItems(container);
    expect(items).toHaveLength(5);

    const expectedLabels = [
      "Analyzing resume…",
      "ATS scoring…",
      "Finding skill gaps…",
      "Generating improvements…",
      "Combining results…",
    ];
    items.forEach((item, index) => {
      expect(item.getAttribute("data-agent")).toBe(AGENT_STEP_ORDER[index]);
      // Verbatim label, rendered as the step's visible text.
      expect(item.textContent).toContain(expectedLabels[index]);
    });
  });

  it("renders every step as a pending skeleton before the first poll lands", () => {
    const { container } = render(<AnalysisProgress steps={[]} />);

    for (const item of stepItems(container)) {
      expect(item.getAttribute("data-status")).toBe("pending");
      // Content-shaped skeleton, plus sr-only status text for the list.
      expect(
        item.querySelector('[data-slot="skeleton"], [class*="animate-pulse"]'),
      ).toBeInstanceOf(HTMLElement);
      expect(item.textContent).toContain("Pending");
    }
  });

  it("derives each step's status solely from the polled steps, defaulting absent agents to pending", () => {
    // A mid-run poll: two terminal-ish agents, one degraded, one failed,
    // synthesizer not yet reported (no row → pending).
    const steps: JobStep[] = [
      step("resume_analysis", "completed"),
      step("ats", "degraded"),
      step("skill_gap", "failed"),
      step("improvement", "completed"),
    ];
    const { container } = render(<AnalysisProgress steps={steps} />);

    const byAgent = new Map(
      stepItems(container).map((item) => [
        item.getAttribute("data-agent"),
        item,
      ]),
    );

    expect(byAgent.get("resume_analysis")?.getAttribute("data-status")).toBe(
      "completed",
    );
    expect(byAgent.get("ats")?.getAttribute("data-status")).toBe("degraded");
    expect(byAgent.get("skill_gap")?.getAttribute("data-status")).toBe(
      "failed",
    );
    expect(byAgent.get("improvement")?.getAttribute("data-status")).toBe(
      "completed",
    );
    // No polled entry for the synthesizer → pending (backend "no row" rule).
    expect(byAgent.get("synthesizer")?.getAttribute("data-status")).toBe(
      "pending",
    );

    // Status affordances render per status: degraded and failed pills carry
    // visible text; completed steps announce via sr-only text.
    expect(byAgent.get("ats")?.textContent).toContain("Degraded");
    expect(byAgent.get("skill_gap")?.textContent).toContain("Failed");
    expect(byAgent.get("resume_analysis")?.textContent).toContain("Completed");
  });
});

// ---------------------------------------------------------------------------
// aria-live announcements (Req 15.1)
// ---------------------------------------------------------------------------

describe("AnalysisProgress aria-live announcements", () => {
  it("exposes a polite, atomic status region", () => {
    const { container } = render(<AnalysisProgress steps={[]} />);

    const region = liveRegion(container);
    expect(region.getAttribute("aria-live")).toBe("polite");
    expect(region.getAttribute("aria-atomic")).toBe("true");
    expect(region.textContent).toBe("Analysis in progress.");
  });

  it("changes the announced text when a polled status changes", () => {
    const { container, rerender } = render(<AnalysisProgress steps={[]} />);
    const before = liveRegion(container).textContent;

    // A new poll lands: resume analysis completed.
    rerender(
      <AnalysisProgress steps={[step("resume_analysis", "completed")]} />,
    );
    const afterFirst = liveRegion(container).textContent;
    expect(afterFirst).not.toBe(before);
    expect(afterFirst).toContain(
      `${AGENT_STEP_LABELS.resume_analysis} completed.`,
    );

    // The next poll adds a degraded ATS step — the summary changes again
    // and describes the degraded status in plain words.
    rerender(
      <AnalysisProgress
        steps={[step("resume_analysis", "completed"), step("ats", "degraded")]}
      />,
    );
    const afterSecond = liveRegion(container).textContent;
    expect(afterSecond).not.toBe(afterFirst);
    expect(afterSecond).toContain(
      `${AGENT_STEP_LABELS.ats} completed with fallback content.`,
    );
  });

  it("keeps the announced text stable when a re-render carries no status change", () => {
    const steps = [step("resume_analysis", "completed")];
    const { container, rerender } = render(<AnalysisProgress steps={steps} />);
    const before = liveRegion(container).textContent;

    rerender(<AnalysisProgress steps={[...steps]} />);
    expect(liveRegion(container).textContent).toBe(before);
  });
});
