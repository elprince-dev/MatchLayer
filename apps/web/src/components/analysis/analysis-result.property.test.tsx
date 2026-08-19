// Feature: phase-4-agentic, Property 21: Degraded indicators track degraded outputs exactly
/**
 * Property 21: Degraded indicators track degraded outputs exactly (frontend)
 * (phase-4-agentic Task 15.7; fast-check + Vitest).
 *
 * **Validates: Requirements 15.2**
 *
 * Subject: `AnalysisResultView` rendering a completed job's
 * `Analysis_Result`. The property statement (design.md): *for any*
 * combination of per-agent degraded flags in a completed `Analysis_Result`,
 * the rendered result shows a visible degraded indicator on exactly the
 * sections whose contributing agent degraded, and on no others.
 *
 * Requirement 15.2 names two independent degradation signals per section:
 * the output's own `degraded` marker field (Req 8.2) **or** a `degraded`
 * status in the contributing agent's trace summary. The generator varies
 * both independently — each of the four outputs' `degraded` flags, and
 * each of the five agents' trace presence and status (including the
 * synthesizer, which contributes to no section and must never produce a
 * badge) — so the expected badge set is the OR of the two signals.
 * `derived_from_degraded_input` (the downstream-consumption marker on the
 * three outputs that carry it) is generated independently of both signals
 * to prove it never triggers a badge on its own (non-interference):
 *
 * | Section           | Contributing agent |
 * | ----------------- | ------------------ |
 * | ATS score         | `ats`              |
 * | Candidate profile | `resume_analysis`  |
 * | Skill gaps        | `skill_gap`        |
 * | Improvements      | `improvement`      |
 *
 * Assertions: exactly four `[data-slot="analysis-section"]` sections
 * render; each section contains the visible `[data-slot="degraded-badge"]`
 * indicator iff its expected flag is true (no false negatives); and the
 * total badge count equals the number of expected-degraded sections (no
 * false positives anywhere — reasoning is hidden by default, so sections
 * are the only badge hosts).
 *
 * Fixtures are built through the generated `AnalysisResultSchema` Zod
 * parse (drift guard, mirroring `use-agent-job.property.test.tsx`), and
 * all data is synthetic (security.md). Conventions mirror the co-located
 * component property test
 * (`llm/llm-content-html-injection.property.test.tsx`).
 *
 * @vitest-environment jsdom
 */

import * as React from "react";

import { cleanup, render } from "@testing-library/react";
import fc from "fast-check";
import { afterEach, describe, expect, it } from "vitest";

import {
  AnalysisResultSchema,
  type AnalysisResult,
} from "@matchlayer/shared-types";

import { AnalysisResultView } from "./analysis-result";

afterEach(() => {
  cleanup();
});

// ---------------------------------------------------------------------------
// Model
// ---------------------------------------------------------------------------

/** The four output sections and their contributing agents (Req 15.2). */
const SECTIONS = [
  { title: "ATS score", output: "ats", agent: "ats" },
  { title: "Candidate profile", output: "profile", agent: "resume_analysis" },
  { title: "Skill gaps", output: "skill_gaps", agent: "skill_gap" },
  { title: "Improvements", output: "improvements", agent: "improvement" },
] as const;

/** All five graph agents — the synthesizer contributes to no section. */
const TRACE_AGENTS = [
  "resume_analysis",
  "ats",
  "skill_gap",
  "improvement",
  "synthesizer",
] as const;

type TraceAgent = (typeof TRACE_AGENTS)[number];

/** One generated scenario: the two independent degradation signals. */
interface Scenario {
  /** Per-output `degraded` marker flags (Req 8.2 signal). */
  outputDegraded: {
    ats: boolean;
    profile: boolean;
    skill_gaps: boolean;
    improvements: boolean;
  };
  /** Per-agent trace status; absent = no trace summary for that agent. */
  traceStatus: Record<TraceAgent, "completed" | "degraded" | null>;
  /**
   * Per-output `derived_from_degraded_input` markers, generated
   * independently of the two badge signals: they must never affect the
   * badge (non-interference; the ATS output does not carry the field).
   */
  derivedFromDegradedInput: {
    profile: boolean;
    skill_gaps: boolean;
    improvements: boolean;
  };
  /** Light content variation so badges are asserted across layouts. */
  gapCount: number;
  skillCount: number;
  score: number;
}

/** Build a schema-valid AnalysisResult for the scenario (drift guard). */
function buildResult(scenario: Scenario): AnalysisResult {
  const traces = TRACE_AGENTS.flatMap((agent) => {
    const status = scenario.traceStatus[agent];
    if (status === null) {
      return [];
    }
    return [
      {
        agent_name: agent,
        status,
        latency_ms: 120,
        failure_reason:
          status === "degraded"
            ? { trigger: "timeout", detail: "Synthetic timeout." }
            : null,
      },
    ];
  });

  const raw = {
    ats: {
      score: scenario.score,
      breakdown: { keyword: 40, semantic: 35 },
      confidence: "medium",
      scorer_version: "2.0.0+test",
      degraded: scenario.outputDegraded.ats,
    },
    skill_gaps: {
      gaps: Array.from({ length: scenario.gapCount }, (_, index) => ({
        skill: `Synthetic Skill ${String(index + 1)}`,
        classification: index % 2 === 0 ? "missing" : "weak",
        rank: index + 1,
      })),
      degraded: scenario.outputDegraded.skill_gaps,
      derived_from_degraded_input: scenario.derivedFromDegradedInput.skill_gaps,
    },
    improvements: {
      actions: [{ rank: 1, text: "Add a synthetic summary section." }],
      rewrites: [],
      degraded: scenario.outputDegraded.improvements,
      derived_from_degraded_input:
        scenario.derivedFromDegradedInput.improvements,
    },
    profile: {
      sections: ["experience"],
      skills: Array.from(
        { length: scenario.skillCount },
        (_, index) => `Skill ${String(index + 1)}`,
      ),
      experiences: [],
      gaps: [],
      degraded: scenario.outputDegraded.profile,
      derived_from_degraded_input: scenario.derivedFromDegradedInput.profile,
    },
    agent_traces: traces,
  };

  return AnalysisResultSchema.parse(raw) as AnalysisResult;
}

/** The expected badge flag per section: output marker OR trace status. */
function expectedDegraded(
  scenario: Scenario,
  section: (typeof SECTIONS)[number],
): boolean {
  return (
    scenario.outputDegraded[section.output] ||
    scenario.traceStatus[section.agent] === "degraded"
  );
}

// ---------------------------------------------------------------------------
// Generators
// ---------------------------------------------------------------------------

const traceStatusArb: fc.Arbitrary<"completed" | "degraded" | null> =
  fc.constantFrom("completed", "degraded", null);

const scenarioArb: fc.Arbitrary<Scenario> = fc.record({
  outputDegraded: fc.record({
    ats: fc.boolean(),
    profile: fc.boolean(),
    skill_gaps: fc.boolean(),
    improvements: fc.boolean(),
  }),
  traceStatus: fc.record({
    resume_analysis: traceStatusArb,
    ats: traceStatusArb,
    skill_gap: traceStatusArb,
    improvement: traceStatusArb,
    synthesizer: traceStatusArb,
  }),
  derivedFromDegradedInput: fc.record({
    profile: fc.boolean(),
    skill_gaps: fc.boolean(),
    improvements: fc.boolean(),
  }),
  gapCount: fc.integer({ min: 0, max: 3 }),
  skillCount: fc.integer({ min: 0, max: 3 }),
  score: fc.integer({ min: 0, max: 100 }),
});

// ---------------------------------------------------------------------------
// Property 21
// ---------------------------------------------------------------------------

describe("Property 21: degraded indicators track degraded outputs exactly", () => {
  it("shows the visible degraded badge on exactly the sections whose contributing agent degraded", () => {
    fc.assert(
      fc.property(scenarioArb, (scenario) => {
        const result = buildResult(scenario);
        const { container, unmount } = render(
          <AnalysisResultView result={result} />,
        );
        try {
          const sections = Array.from(
            container.querySelectorAll('[data-slot="analysis-section"]'),
          );
          expect(sections).toHaveLength(SECTIONS.length);

          let expectedBadgeCount = 0;
          for (const section of SECTIONS) {
            const expected = expectedDegraded(scenario, section);
            if (expected) {
              expectedBadgeCount += 1;
            }

            const element = sections.find(
              (candidate) =>
                candidate.getAttribute("aria-label") === section.title,
            );
            expect(element).toBeInstanceOf(HTMLElement);

            // The visible indicator: present iff the contributing agent
            // degraded (no false negatives, no false positives per section).
            const badge = element?.querySelector(
              '[data-slot="degraded-badge"]',
            );
            expect(badge instanceof HTMLElement).toBe(expected);
            expect(element?.getAttribute("data-degraded")).toBe(
              String(expected),
            );
          }

          // "And on no others": with reasoning hidden (the default), the
          // four sections are the only badge hosts — the render's total
          // badge count equals the expected-degraded section count, so no
          // stray indicator exists anywhere (synthesizer traces included).
          expect(
            container.querySelectorAll('[data-slot="degraded-badge"]'),
          ).toHaveLength(expectedBadgeCount);
        } finally {
          unmount();
        }
      }),
      { numRuns: 100 },
    );
  });
});
