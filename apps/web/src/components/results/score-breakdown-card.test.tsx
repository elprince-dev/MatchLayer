/**
 * Unit tests for `ScoreBreakdownCard` (Task 3.6).
 *
 * Validates the explainable breakdown against its acceptance criteria
 * (Req 11.1, 11.2, 16.2; design Section 7.1 "ScoreBreakdownCard", Testing
 * Strategy):
 *
 *   - Req 11.1 — renders EXACTLY TWO labeled progress bars ("TF-IDF similarity"
 *     ← `similarity_component`, "Keyword coverage" ← `keyword_coverage_component`),
 *     each `[0,1]` value scaled to a 0–100% display, and NEVER a third scoring
 *     dimension.
 *   - Req 11.2 — surfaces both component weights (`weight_similarity`,
 *     `weight_keyword`) so the composition is explainable, plus a one-line
 *     "final = weighted sum" explainer using `final_score`.
 *
 * The card composes the Radix-backed `ui/progress` primitive, which exposes
 * each bar with `role="progressbar"`; the "exactly two bars" guarantee is
 * asserted on that role count. Uses the Section 5 fixtures (`matchStrong`,
 * `matchPartial`).
 *
 * Conventions mirror the co-located `error-state.test.tsx`: render/screen/
 * cleanup, `toBeInstanceOf`, no jest-dom matchers.
 *
 * @vitest-environment jsdom
 */

import * as React from "react";

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { ScoreBreakdownCard } from "@/components/results/score-breakdown-card";
import {
  matchPartial,
  matchStrong,
} from "@/components/results/__fixtures__/match-fixtures";

afterEach(() => {
  cleanup();
});

describe("ScoreBreakdownCard — exactly two bars, never a third (Req 11.1)", () => {
  it("renders precisely two progress bars", () => {
    render(<ScoreBreakdownCard breakdown={matchStrong.score_breakdown} />);

    const bars = screen.getAllByRole("progressbar");
    expect(bars).toHaveLength(2);
  });

  it("labels the two bars 'TF-IDF similarity' and 'Keyword coverage' and nothing else", () => {
    render(<ScoreBreakdownCard breakdown={matchStrong.score_breakdown} />);

    expect(screen.getByText("TF-IDF similarity")).toBeInstanceOf(HTMLElement);
    expect(screen.getByText("Keyword coverage")).toBeInstanceOf(HTMLElement);

    // No third dimension the backend never returns (Req 11.1, 20.3).
    expect(screen.queryByText(/experience relevance/i)).toBeNull();
    expect(screen.queryByText(/seniority/i)).toBeNull();
  });

  it("scales each [0,1] component value to its 0–100% display", () => {
    // matchStrong: similarity 0.8123 → 81%, coverage 0.90 → 90%.
    render(<ScoreBreakdownCard breakdown={matchStrong.score_breakdown} />);

    expect(screen.getByText("81%")).toBeInstanceOf(HTMLElement);
    expect(screen.getByText("90%")).toBeInstanceOf(HTMLElement);
  });

  it("drives the bar fill from the same scaled value (indicator transform)", () => {
    const { container } = render(
      <ScoreBreakdownCard breakdown={matchStrong.score_breakdown} />,
    );

    // The `Progress` indicator translates by -(100 − value)% — so 81% fill ⇒
    // translateX(-19%) and 90% fill ⇒ translateX(-10%). This proves the same
    // scaled integer drives the fill that drives the visible percentage.
    const transforms = Array.from(
      container.querySelectorAll("[data-slot=progress-indicator]"),
    ).map((el) => (el as HTMLElement).style.transform);

    expect(transforms).toContain("translateX(-19%)");
    expect(transforms).toContain("translateX(-10%)");
  });
});

describe("ScoreBreakdownCard — weights + final-score explainer (Req 11.2)", () => {
  it("surfaces both component weights", () => {
    // matchStrong weights: 0.6 / 0.4 (rendered verbatim from the contract).
    render(<ScoreBreakdownCard breakdown={matchStrong.score_breakdown} />);

    // One "weight" label line beneath each of the two bars (the weight value
    // lives in a child span, so the label's own text node is exactly "weight").
    expect(screen.getAllByText("weight")).toHaveLength(2);

    expect(screen.getByText("0.6")).toBeInstanceOf(HTMLElement);
    expect(screen.getByText("0.4")).toBeInstanceOf(HTMLElement);
  });

  it("explains that the final score is the weighted sum, using final_score", () => {
    // matchStrong.final_score === 85.
    render(<ScoreBreakdownCard breakdown={matchStrong.score_breakdown} />);

    expect(screen.getByText("85")).toBeInstanceOf(HTMLElement);
    expect(
      screen.getByText(/weighted sum of these two components/i),
    ).toBeInstanceOf(HTMLElement);
  });

  it("renders correctly for a second fixture (partial match) — still exactly two bars", () => {
    // matchPartial: similarity 0.48 → 48%, coverage 0.5833 → 58%, final 52.
    render(<ScoreBreakdownCard breakdown={matchPartial.score_breakdown} />);

    expect(screen.getAllByRole("progressbar")).toHaveLength(2);
    expect(screen.getByText("48%")).toBeInstanceOf(HTMLElement);
    expect(screen.getByText("58%")).toBeInstanceOf(HTMLElement);
    expect(screen.getByText("52")).toBeInstanceOf(HTMLElement);
  });
});
