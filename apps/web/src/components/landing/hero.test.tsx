/**
 * Unit tests for `Hero` (Task 8.8).
 *
 * Validates the Landing_Page hero against its acceptance criteria (Req 3.8,
 * 5.1, 5.4; design Section 8.2):
 *
 *   - Req 5.4 — the mandated honesty note "Semantic + keyword scoring —
 *     sample preview, not a real analysis" is rendered where the scoring
 *     approach is shown.
 *   - Req 5.1 (updated for phase-2-nlp-embeddings) — semantic scoring
 *     SHIPPED, so "semantic"/"embeddings" are now truthful copy. The scoring
 *     is still NEVER described as AI- or LLM-powered (unshipped roadmap);
 *     the test asserts those tokens never appear in the rendered copy.
 *   - Req 3.8 — under `prefers-reduced-motion` the hero renders its final,
 *     visible state immediately: the `<h1>` is present and the illustrative
 *     demo gauge shows the final sample value (78) with no count-up from 0.
 *
 * The reveal/count-up is gated by framer-motion's `useReducedMotion()`, which
 * reads `window.matchMedia`. Following `results-page.test.tsx`, we stub
 * `matchMedia` so the hook resolves to "reduced" — the demo gauge's count-up
 * effect then returns early and the rendered value is the resolved sample, so
 * everything paints synchronously with no animation frames to await. The
 * staggered fade-up flows through the real `MotionSafe`/`motion` element, which
 * renders cleanly under jsdom (as proven by the Results integration test).
 *
 * `next/link` renders a real anchor under jsdom, so the CTA `href` assertion
 * exercises the real `/register` target with no router mock. Conventions mirror
 * the co-located results tests: render/screen/cleanup, `toBeInstanceOf`, no
 * jest-dom matchers.
 *
 * @vitest-environment jsdom
 */

import * as React from "react";

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Hero } from "@/components/landing/hero";

/** The illustrative sample score the demo gauge resolves to (Hero SAMPLE_SCORE). */
const SAMPLE_SCORE = "78";

/** The mandated honesty note copy (Req 5.4, updated for phase 2). */
const HONESTY_NOTE =
  "Semantic + keyword scoring — sample preview, not a real analysis";

/**
 * Stub `window.matchMedia` so framer-motion's `useReducedMotion()` resolves to
 * `matches`. `true` ⇒ reduced motion ⇒ the demo gauge renders its resolved
 * sample value with no count-up (mirrors `results-page.test.tsx`).
 */
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
  // Default to the reduced-motion branch so content paints in its final state
  // synchronously (Req 3.8) — the focus of these tests is copy + final state,
  // not the animation timeline.
  stubMatchMedia(true);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Hero — honesty note present (Req 5.4)", () => {
  it("renders the 'Semantic + keyword scoring' sample-preview note", () => {
    render(<Hero />);
    expect(screen.getByText(HONESTY_NOTE)).toBeInstanceOf(HTMLElement);
  });
});

describe("Hero — scoring never described as AI/LLM (Req 5.1, phase-2 update)", () => {
  it("contains no AI/LLM claims anywhere in the rendered copy", () => {
    render(<Hero />);

    const fullText = document.body.textContent ?? "";
    // Semantic scoring shipped in phase-2-nlp-embeddings, so "semantic" is
    // now truthful copy. AI/LLM remain unshipped roadmap and must never be
    // claimed. Word-boundary matches so standalone "AI"/"LLM" are caught
    // without flagging substrings inside ordinary words.
    expect(fullText).not.toMatch(/\bai\b/i);
    expect(fullText).not.toMatch(/\bllm\b/i);
  });

  it("describes the scoring honestly as semantic + keyword", () => {
    render(<Hero />);
    // The subheadline pins the current capability to semantic + keyword
    // matching — exactly what the Phase 2 backend does.
    expect(screen.getByText(/semantic \+ keyword ATS score/i)).toBeInstanceOf(
      HTMLElement,
    );
  });
});

describe("Hero — reduced motion renders the final state (Req 3.8)", () => {
  it("renders the single <h1> headline immediately and visibly", () => {
    const { container } = render(<Hero />);

    const h1 = container.querySelector("h1");
    expect(h1).toBeInstanceOf(HTMLHeadingElement);
    expect(h1?.textContent).toContain(
      "See how real ATS systems evaluate your resume",
    );
  });

  it("shows the demo gauge at its final sample value (78), not a count-up from 0", () => {
    const { container } = render(<Hero />);

    // The gradient-clipped count-up glyph is the only `.text-transparent`
    // element in the hero; under reduced motion it resolves straight to 78.
    const gaugeNumber = container.querySelector(".text-transparent");
    expect(gaugeNumber).toBeInstanceOf(HTMLElement);
    expect(gaugeNumber?.textContent).toBe(SAMPLE_SCORE);
  });

  it("labels the demo gauge as an illustrative sample, not a real analysis", () => {
    render(<Hero />);
    // Honesty in the accessibility tree: the gauge graphic is exposed as an
    // illustrative sample so SR users never read it as a genuine score.
    const gauge = screen.getByRole("img", { name: /illustrative sample/i });
    expect(gauge.getAttribute("aria-label")).toMatch(/not a real analysis/i);
  });

  it("renders the primary CTA → /register", () => {
    render(<Hero />);
    const cta = screen.getByRole("link", { name: /get started/i });
    expect(cta.getAttribute("href")).toBe("/register");
  });
});
