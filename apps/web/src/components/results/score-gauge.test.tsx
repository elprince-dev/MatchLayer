/**
 * Unit tests for `ScoreGauge` + co-located `ScoreLabel` (Task 3.6).
 *
 * Validates the flagship score reveal against its acceptance criteria
 * (Req 10.4, 10.6, 18.5; design Section 7.1 "ScoreGauge/ScoreLabel", Testing
 * Strategy):
 *
 *   - Req 10.6 — under `prefers-reduced-motion` the final score and the fully
 *     resolved gauge stroke render immediately, with NO count-up from 0 and no
 *     imperative tween scheduled.
 *   - Req 18.5 — the gauge container carries the mobile sizing threshold
 *     (`size-30` = 7.5rem = 120px) up to `md:size-40` (160px), and the score
 *     number is rendered at `text-6xl` (≥24px on mobile).
 *   - Req 10.4 / 12.1 — `ScoreLabel` maps the score to the correct band via
 *     `lib/score-label`, and a 0 score still yields a label ("Needs Work").
 *
 * The reveal is gated by framer-motion's `useReducedMotion()` and driven by its
 * imperative `animate()`. jsdom has no real `requestAnimationFrame` frameloop,
 * so framer-motion is mocked here (as the task directs): `useReducedMotion`
 * returns a per-test controlled value, and `animate` is a deterministic
 * stand-in that drives its subscriber straight to the final value. This lets us
 * assert BOTH the reduced-motion final-state branch and the count-up wiring
 * without flakiness.
 *
 * Conventions mirror `tests/results-page.test.tsx` and the co-located
 * `error-state.test.tsx`: render/screen/waitFor/cleanup, `toBeInstanceOf`, no
 * jest-dom matchers.
 *
 * @vitest-environment jsdom
 */

import * as React from "react";

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Shared mutable state for the hoisted framer-motion mock factory. `vi.mock`
// factories are hoisted above imports, so the controllable reduced-motion value
// must live in a `vi.hoisted()` holder rather than a plain closure variable.
const fmState = vi.hoisted(() => ({
  reduced: { value: true as boolean | null },
}));

vi.mock("framer-motion", () => ({
  // Per-test reduced-motion decision (Req 10.6 / 15.4).
  useReducedMotion: () => fmState.reduced.value,
  // Synchronous stand-in for framer-motion's imperative `animate`: drive the
  // subscriber straight to the final value (jsdom has no rAF frameloop) and
  // return no-op playback controls so the effect's cleanup `.stop()` is safe.
  animate: vi.fn(
    (_from: number, to: number, opts: { onUpdate?: (v: number) => void }) => {
      opts.onUpdate?.(to);
      return { stop: vi.fn() };
    },
  ),
}));

import { animate } from "framer-motion";

import { ScoreGauge, ScoreLabel } from "@/components/results/score-gauge";
import {
  matchDegenerate,
  matchStrong,
} from "@/components/results/__fixtures__/match-fixtures";

const animateMock = vi.mocked(animate);

// Mirror the gauge geometry constants so the resolved stroke offset can be
// asserted precisely (RADIUS=42, stroke fills clockwise; offset shrinks from
// the full circumference toward 0 as the score rises).
const CIRCUMFERENCE = 2 * Math.PI * 42;

beforeEach(() => {
  fmState.reduced.value = true;
  animateMock.mockClear();
});

afterEach(() => {
  cleanup();
});

/** The score progress arc is the second `<circle>` (after the bg-elevated well). */
function progressDashOffset(container: HTMLElement): number {
  const circles = container.querySelectorAll("circle");
  const arc = circles[1];
  return Number(arc?.getAttribute("stroke-dashoffset"));
}

describe("ScoreGauge — reduced motion renders the final state immediately (Req 10.6)", () => {
  it("shows the resolved score number on first paint, with no count-up tween", () => {
    fmState.reduced.value = true;

    const { container } = render(<ScoreGauge score={matchStrong.score} />);

    // The gradient glyph reads the final 85 directly — never an in-progress
    // count-up value from 0.
    const gradient = container.querySelector(".text-transparent");
    expect(gradient).toBeInstanceOf(HTMLElement);
    expect(gradient?.textContent).toBe("85");

    // No imperative tween is scheduled under reduced motion.
    expect(animateMock).not.toHaveBeenCalled();

    // The sr-only resolved sentence is the unambiguous carrier of the value.
    expect(screen.getByText("Match score: 85 out of 100.")).toBeInstanceOf(
      HTMLElement,
    );
  });

  it("fills the gauge stroke to the score immediately (resolved geometry, no animation)", () => {
    fmState.reduced.value = true;

    const { container } = render(<ScoreGauge score={matchStrong.score} />);

    // 85% filled ⇒ offset = circumference × (1 − 0.85). Clearly not the empty
    // (full-circumference) state, proving the stroke is resolved on first paint.
    expect(progressDashOffset(container)).toBeCloseTo(CIRCUMFERENCE * 0.15, 1);
  });

  it("renders a zero score immediately as an empty gauge labeled 'Needs Work'", () => {
    fmState.reduced.value = true;

    const { container } = render(<ScoreGauge score={matchDegenerate.score} />);

    const gradient = container.querySelector(".text-transparent");
    expect(gradient?.textContent).toBe("0");
    // 0% ⇒ offset equals the full circumference (empty ring).
    expect(progressDashOffset(container)).toBeCloseTo(CIRCUMFERENCE, 1);
    expect(screen.getByText("Needs Work")).toBeInstanceOf(HTMLElement);
  });
});

describe("ScoreGauge — animated count-up path (reduced motion off)", () => {
  it("starts a 0→score, 600ms ease-out-exponential tween and reflects its value", async () => {
    fmState.reduced.value = false;

    const { container } = render(<ScoreGauge score={matchStrong.score} />);

    expect(animateMock).toHaveBeenCalledTimes(1);
    const call = animateMock.mock.calls[0]! as unknown as [
      number,
      number,
      { duration: number; ease: readonly number[] },
    ];
    // Counts up from 0 to the score (Req 10.2) ...
    expect(call[0]).toBe(0);
    expect(call[1]).toBe(85);
    // ... over the 600ms hero-reveal duration with the ease-out-exponential curve.
    expect(call[2].duration).toBe(0.6);
    expect(call[2].ease).toEqual([0.16, 1, 0.3, 1]);

    // The deterministic stand-in drove onUpdate(85), so the glyph resolves to 85.
    await waitFor(() => {
      const gradient = container.querySelector(".text-transparent");
      expect(gradient?.textContent).toBe("85");
    });
  });
});

describe("ScoreGauge — mobile sizing thresholds (Req 18.5)", () => {
  it("sizes the gauge well at 120px on mobile (size-30) up to 160px (md:size-40)", () => {
    const { container } = render(<ScoreGauge score={matchStrong.score} />);

    // size-30 = 7.5rem = 120px (the Req 18.5 mobile minimum diameter).
    const well = container.querySelector(".size-30");
    expect(well).toBeInstanceOf(HTMLElement);
    expect(well?.className).toContain("md:size-40");
  });

  it("renders the score number at text-6xl (≥24px on mobile)", () => {
    const { container } = render(<ScoreGauge score={matchStrong.score} />);

    const gradient = container.querySelector(".text-transparent");
    expect(gradient?.className).toContain("text-6xl");
  });
});

describe("ScoreLabel — score→band mapping (Req 10.4, 12.1)", () => {
  it("maps a strong score to its band", () => {
    render(<ScoreLabel score={matchStrong.score} />);
    expect(screen.getByText("Excellent")).toBeInstanceOf(HTMLElement);
  });

  it("still labels a degenerate zero score ('Needs Work'), never a blank", () => {
    render(<ScoreLabel score={matchDegenerate.score} />);
    expect(screen.getByText("Needs Work")).toBeInstanceOf(HTMLElement);
  });
});
