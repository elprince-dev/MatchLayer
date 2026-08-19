/**
 * AnalysisResultView component tests (phase-4-agentic Task 15.8).
 *
 * Requirements covered:
 * - 15.3 — the "Show reasoning" toggle defaults to off on every load (no
 *   persistence: a fresh mount is hidden again), reveals per-agent trace
 *   panels when enabled, and renders trace content safely — HTML-bearing
 *   strings (`<script>`, `<img onerror>`) stay inert literal text with no
 *   element injection and no `dangerouslySetInnerHTML` path.
 * - 15.2 (examples) — the visible degraded badge appears for either
 *   degradation signal (output `degraded` marker or a `degraded` trace
 *   status) and is absent from a fully normal result; the exhaustive
 *   flag-combination coverage lives in the co-located Property 21 suite
 *   (`analysis-result.property.test.tsx`).
 *
 * Per-case polling error states (Req 15.4) are owned by the polling hook
 * and Task 15.5's page wiring — no error-state component exists in this
 * component's scope; see `use-agent-job.test.tsx` /
 * `use-agent-job.property.test.tsx`.
 *
 * Conventions mirror the co-located component tests
 * (`error-state.test.tsx`, `upload/upload-widget.test.tsx`):
 * `@testing-library/react` render/fireEvent/cleanup, `toBeInstanceOf`,
 * attribute assertions, no jest-dom matchers; fixtures parsed through the
 * generated Zod schema (drift guard). All fixture data is synthetic
 * (security.md).
 *
 * @vitest-environment jsdom
 */

import * as React from "react";

import { cleanup, fireEvent, render } from "@testing-library/react";
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
// Fixtures
// ---------------------------------------------------------------------------

/** Deep-merge-free fixture builder: overrides replace whole top-level keys. */
function buildResult(
  overrides: Partial<Record<keyof AnalysisResult, unknown>> = {},
): AnalysisResult {
  const raw = {
    ats: {
      score: 72,
      breakdown: { keyword: 40, semantic: 32 },
      confidence: "high",
      scorer_version: "2.0.0+test",
      degraded: false,
    },
    skill_gaps: {
      gaps: [{ skill: "Synthetic Skill", classification: "missing", rank: 1 }],
      degraded: false,
      derived_from_degraded_input: false,
    },
    improvements: {
      actions: [{ rank: 1, text: "Add a synthetic summary section." }],
      rewrites: [
        {
          excerpt: "Did synthetic things.",
          replacement: "Delivered synthetic outcomes.",
          rationale: "Outcome-focused phrasing.",
        },
      ],
      degraded: false,
      derived_from_degraded_input: false,
    },
    profile: {
      sections: ["experience"],
      skills: ["Skill One", "Skill Two"],
      experiences: [],
      gaps: [],
      degraded: false,
      derived_from_degraded_input: false,
    },
    agent_traces: [
      {
        agent_name: "resume_analysis",
        status: "completed",
        latency_ms: 1200,
        failure_reason: null,
      },
      {
        agent_name: "ats",
        status: "completed",
        latency_ms: 300,
        failure_reason: null,
      },
      {
        agent_name: "synthesizer",
        status: "completed",
        latency_ms: 50,
        failure_reason: null,
      },
    ],
    ...overrides,
  };
  return AnalysisResultSchema.parse(raw) as AnalysisResult;
}

/** The reasoning toggle button (accessible name flips with state). */
function reasoningToggle(container: HTMLElement): HTMLButtonElement {
  const button = Array.from(container.querySelectorAll("button")).find((el) =>
    /reasoning/i.test(el.textContent ?? ""),
  );
  expect(button).toBeInstanceOf(HTMLButtonElement);
  return button as HTMLButtonElement;
}

// ---------------------------------------------------------------------------
// "Show reasoning" toggle (Req 15.3)
// ---------------------------------------------------------------------------

describe("AnalysisResultView show-reasoning toggle", () => {
  it("defaults to off: reasoning hidden, toggle collapsed", () => {
    const { container } = render(<AnalysisResultView result={buildResult()} />);

    const toggle = reasoningToggle(container);
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(toggle.textContent).toContain("Show reasoning");
    expect(container.querySelector('[data-slot="agent-reasoning"]')).toBeNull();
    expect(container.querySelector('[data-slot="agent-trace"]')).toBeNull();
  });

  it("reveals one trace panel per agent trace when enabled, and hides them again on toggle-off", () => {
    const { container } = render(<AnalysisResultView result={buildResult()} />);

    fireEvent.click(reasoningToggle(container));

    const toggle = reasoningToggle(container);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(toggle.textContent).toContain("Hide reasoning");

    const panels = Array.from(
      container.querySelectorAll('[data-slot="agent-trace"]'),
    );
    expect(panels).toHaveLength(3);
    // Panels carry the human-readable agent label and the latency.
    expect(panels[0]?.textContent).toContain("Resume analysis");
    expect(panels[0]?.textContent).toContain("1200 ms");

    fireEvent.click(toggle);
    expect(reasoningToggle(container).getAttribute("aria-expanded")).toBe(
      "false",
    );
    expect(container.querySelector('[data-slot="agent-trace"]')).toBeNull();
  });

  it("starts hidden again on a fresh mount — no persistence across loads", () => {
    const first = render(<AnalysisResultView result={buildResult()} />);
    fireEvent.click(reasoningToggle(first.container));
    expect(
      first.container.querySelector('[data-slot="agent-reasoning"]'),
    ).toBeInstanceOf(HTMLElement);
    first.unmount();

    // A new load (fresh mount) is hidden regardless of the previous toggle.
    const second = render(<AnalysisResultView result={buildResult()} />);
    expect(
      reasoningToggle(second.container).getAttribute("aria-expanded"),
    ).toBe("false");
    expect(
      second.container.querySelector('[data-slot="agent-reasoning"]'),
    ).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Safe rendering of HTML-bearing content (Req 15.3, security.md)
// ---------------------------------------------------------------------------

describe("AnalysisResultView safe rendering", () => {
  const SCRIPT_PAYLOAD = "<script>alert(1)</script>";
  const IMG_PAYLOAD = '<img src=x onerror="alert(1)">';

  it("renders HTML-bearing trace details as inert literal text — no element injection", () => {
    const result = buildResult({
      agent_traces: [
        {
          agent_name: "resume_analysis",
          status: "degraded",
          latency_ms: 900,
          failure_reason: {
            trigger: "error",
            detail: `Synthetic failure ${SCRIPT_PAYLOAD}${IMG_PAYLOAD}`,
          },
        },
      ],
    });
    const { container } = render(<AnalysisResultView result={result} />);
    fireEvent.click(reasoningToggle(container));

    // No script-capable element originates from the content, anywhere.
    expect(
      container.querySelector("script, img, iframe, object, embed"),
    ).toBeNull();
    for (const el of Array.from(container.querySelectorAll("*"))) {
      const handlers = el
        .getAttributeNames()
        .filter((name) => name.toLowerCase().startsWith("on"));
      expect(handlers).toEqual([]);
    }

    // The payload appears verbatim as text inside the trace panel.
    const panel = container.querySelector('[data-slot="agent-trace"]');
    expect(panel).toBeInstanceOf(HTMLElement);
    expect(panel?.textContent).toContain(SCRIPT_PAYLOAD);
    expect(panel?.textContent).toContain(IMG_PAYLOAD);
  });

  it("renders HTML-bearing model output (rewrites, profile fields) as inert literal text", () => {
    const result = buildResult({
      improvements: {
        actions: [{ rank: 1, text: `Do this ${SCRIPT_PAYLOAD}` }],
        rewrites: [
          {
            excerpt: `Before ${IMG_PAYLOAD}`,
            replacement: `After ${SCRIPT_PAYLOAD}`,
            rationale: "Synthetic rationale.",
          },
        ],
        degraded: false,
        derived_from_degraded_input: false,
      },
      profile: {
        sections: [],
        skills: [`React ${SCRIPT_PAYLOAD}`],
        experiences: [],
        gaps: [IMG_PAYLOAD],
        degraded: false,
        derived_from_degraded_input: false,
      },
    });
    const { container } = render(<AnalysisResultView result={result} />);

    expect(container.querySelector("script, img, iframe")).toBeNull();
    for (const el of Array.from(container.querySelectorAll("*"))) {
      const handlers = el
        .getAttributeNames()
        .filter((name) => name.toLowerCase().startsWith("on"));
      expect(handlers).toEqual([]);
    }

    // Payloads land verbatim as text in their sections.
    const sections = Array.from(
      container.querySelectorAll('[data-slot="analysis-section"]'),
    );
    const improvements = sections.find(
      (el) => el.getAttribute("aria-label") === "Improvements",
    );
    expect(improvements?.textContent).toContain(`After ${SCRIPT_PAYLOAD}`);
    const profile = sections.find(
      (el) => el.getAttribute("aria-label") === "Candidate profile",
    );
    expect(profile?.textContent).toContain(`React ${SCRIPT_PAYLOAD}`);
  });
});

// ---------------------------------------------------------------------------
// Degraded badge examples (Req 15.2 — exhaustive coverage in Property 21)
// ---------------------------------------------------------------------------

describe("AnalysisResultView degraded badges", () => {
  it("shows no badge for a fully normal result", () => {
    const { container } = render(<AnalysisResultView result={buildResult()} />);
    expect(
      container.querySelectorAll('[data-slot="degraded-badge"]'),
    ).toHaveLength(0);
  });

  it("badges a section whose output carries the degraded marker", () => {
    const result = buildResult({
      ats: {
        score: 40,
        breakdown: {},
        confidence: "low",
        scorer_version: "2.0.0+test",
        degraded: true,
      },
    });
    const { container } = render(<AnalysisResultView result={result} />);

    const badges = container.querySelectorAll('[data-slot="degraded-badge"]');
    expect(badges).toHaveLength(1);
    const ats = Array.from(
      container.querySelectorAll('[data-slot="analysis-section"]'),
    ).find((el) => el.getAttribute("aria-label") === "ATS score");
    expect(ats?.querySelector('[data-slot="degraded-badge"]')).toBeInstanceOf(
      HTMLElement,
    );
    // The indicator carries visible, distinguishing text.
    expect(ats?.textContent).toContain("Degraded — fallback content");
  });

  it("badges a section whose contributing agent has a degraded trace status", () => {
    const result = buildResult({
      agent_traces: [
        {
          agent_name: "skill_gap",
          status: "degraded",
          latency_ms: 20,
          failure_reason: { trigger: "timeout", detail: null },
        },
      ],
    });
    const { container } = render(<AnalysisResultView result={result} />);

    const skillGaps = Array.from(
      container.querySelectorAll('[data-slot="analysis-section"]'),
    ).find((el) => el.getAttribute("aria-label") === "Skill gaps");
    expect(
      skillGaps?.querySelector('[data-slot="degraded-badge"]'),
    ).toBeInstanceOf(HTMLElement);
    expect(
      container.querySelectorAll('[data-slot="degraded-badge"]'),
    ).toHaveLength(1);
  });
});
