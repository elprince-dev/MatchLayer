/**
 * Unit tests for `lib/score-label.ts` (Task 2.7).
 *
 * `scoreLabel` is a pure, framework-agnostic boundary map from a numeric match
 * score to one of four qualitative bands (Req 10.4; design Section 7.1
 * "ScoreLabel", Testing Strategy). The design's Testing Strategy explicitly
 * classifies this as a fixed 4-bucket boundary map best verified with example
 * boundary tests rather than property-based testing:
 *
 *   | Band         | Range   |
 *   | ------------ | ------- |
 *   | "Needs Work" | 0–39    |
 *   | "Fair"       | 40–59   |
 *   | "Good"       | 60–79   |
 *   | "Excellent"  | 80–100  |
 *
 * The REQUIRED core is the eight band-edge values (0, 39, 40, 59, 60, 79, 80,
 * 100). The documented out-of-domain decisions (negatives, >100, NaN, ±∞,
 * fractional edges) are asserted as well so the total-function contract in the
 * module docstring can't silently regress — e.g. NaN must never read as the
 * passing "Excellent" band.
 *
 * Pure function, no DOM — runs under the repo's default `node` Vitest
 * environment (no jsdom pragma needed).
 */

import { describe, expect, it } from "vitest";

import { scoreLabel, type ScoreLabel } from "@/lib/score-label";

describe("scoreLabel — required band boundaries (Req 10.4)", () => {
  // The eight canonical boundary values the task and design enumerate. Each
  // pair pins exactly one edge of a contiguous, inclusive integer band.
  const boundaries: ReadonlyArray<readonly [number, ScoreLabel]> = [
    [0, "Needs Work"], // low edge of the domain
    [39, "Needs Work"], // top of "Needs Work"
    [40, "Fair"], // bottom of "Fair"
    [59, "Fair"], // top of "Fair"
    [60, "Good"], // bottom of "Good"
    [79, "Good"], // top of "Good"
    [80, "Excellent"], // bottom of "Excellent"
    [100, "Excellent"], // top of the domain
  ];

  it.each(boundaries)("maps %i → %s", (score, expected) => {
    expect(scoreLabel(score)).toBe(expected);
  });
});

describe("scoreLabel — documented out-of-domain handling", () => {
  it("collapses values below 0 to the lowest band", () => {
    // Negatives can never reach "Excellent" (module docstring decision).
    expect(scoreLabel(-1)).toBe("Needs Work");
    expect(scoreLabel(-1000)).toBe("Needs Work");
  });

  it("collapses values above 100 to the highest band", () => {
    expect(scoreLabel(101)).toBe("Excellent");
    expect(scoreLabel(1000)).toBe("Excellent");
  });

  it("maps NaN to the lowest band, never 'Excellent'", () => {
    // The explicit NaN guard: a non-finite score must not pass as "Excellent".
    expect(scoreLabel(Number.NaN)).toBe("Needs Work");
  });

  it("maps infinities to the band their sign falls into", () => {
    expect(scoreLabel(Number.POSITIVE_INFINITY)).toBe("Excellent");
    expect(scoreLabel(Number.NEGATIVE_INFINITY)).toBe("Needs Work");
  });

  it("keeps fractional values within the band of their floor edge", () => {
    // No gap exists for fractional inputs: 39.5 stays "Needs Work", 59.9 stays
    // "Fair", 79.9 stays "Good" — the `< threshold` comparisons are exact.
    expect(scoreLabel(39.5)).toBe("Needs Work");
    expect(scoreLabel(39.999)).toBe("Needs Work");
    expect(scoreLabel(59.9)).toBe("Fair");
    expect(scoreLabel(79.9)).toBe("Good");
    expect(scoreLabel(80.0001)).toBe("Excellent");
  });
});
