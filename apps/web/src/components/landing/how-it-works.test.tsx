/**
 * Unit tests for `HowItWorks` (Task 8.8).
 *
 * Validates the Landing_Page "How it works" section's reduced-motion behavior
 * (Req 4.9; design Section 8.2) and its honest scoring copy (Req 5.1):
 *
 *   - Req 4.9 — under `prefers-reduced-motion` the section renders its content
 *     in the final, visible state immediately. The section's `Reveal` branches
 *     on `useReducedMotion()` and, when reduced, returns a plain `<div>` (no
 *     `whileInView` scroll trigger), so the three steps are present without
 *     waiting to scroll into view.
 *   - Req 5.1 (updated for phase-2-nlp-embeddings) — step 3 describes the
 *     output as a semantic and keyword match score (semantic shipped in
 *     phase 2), never "AI" or "LLM" (unshipped roadmap).
 *
 * The reveal reads `window.matchMedia` via framer-motion's `useReducedMotion()`,
 * stubbed to "reduced" here (mirrors `results-page.test.tsx`) so content paints
 * synchronously. Conventions mirror the co-located results tests:
 * render/screen/cleanup, `toBeInstanceOf`, no jest-dom matchers.
 *
 * @vitest-environment jsdom
 */

import * as React from "react";

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { HowItWorks } from "@/components/landing/how-it-works";

function stubMatchMedia(matches: boolean): void {
  vi.stubGlobal(
    "matchMedia",
    vi.fn().mockImplementation((query: string) => ({
      matches,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  );
}

beforeEach(() => {
  // Reduced-motion branch: the section renders its final state immediately.
  stubMatchMedia(true);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("HowItWorks — reduced motion renders the final state (Req 4.9)", () => {
  it("renders the section heading immediately", () => {
    render(<HowItWorks />);
    expect(
      screen.getByRole("heading", { name: /how it works/i }),
    ).toBeInstanceOf(HTMLElement);
  });

  it("renders all three numbered steps in their final, visible state", () => {
    render(<HowItWorks />);

    expect(screen.getByText("Upload your resume")).toBeInstanceOf(HTMLElement);
    expect(screen.getByText("Paste the job description")).toBeInstanceOf(
      HTMLElement,
    );
    expect(screen.getByText("Get your ATS score")).toBeInstanceOf(HTMLElement);
  });

  it("conveys the 1→2→3 sequence with an ordered list (<ol>), not visual badges alone", () => {
    const { container } = render(<HowItWorks />);

    const ol = container.querySelector("ol");
    expect(ol).toBeInstanceOf(HTMLOListElement);
    expect(ol?.querySelectorAll("li").length).toBe(3);
  });
});

describe("HowItWorks — honest scoring copy (Req 5.1, phase-2 update)", () => {
  it("describes the output as a semantic and keyword score, never AI/LLM", () => {
    render(<HowItWorks />);

    const text = document.body.textContent ?? "";
    expect(text).toMatch(/semantic and keyword/i);
    expect(text).not.toMatch(/\bai\b/i);
    expect(text).not.toMatch(/\bllm\b/i);
  });
});
