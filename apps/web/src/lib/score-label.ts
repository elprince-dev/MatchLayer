/**
 * Map a numeric match score to its qualitative label.
 *
 * The four bands are fixed by the product spec (Req 10.4) and are inclusive,
 * contiguous integer ranges over the canonical `[0, 100]` score domain:
 *
 * | Band         | Range   |
 * | ------------ | ------- |
 * | "Needs Work" | 0–39    |
 * | "Fair"       | 40–59   |
 * | "Good"       | 60–79   |
 * | "Excellent"  | 80–100  |
 *
 * The backend guarantees `MatchResponse.score` is an integer in `[0, 100]`, so
 * the common path never sees out-of-range or fractional input. The mapping is
 * nonetheless defined as a total function over all `number`s, using ascending
 * `< threshold` comparisons rather than range checks. This keeps the boundary
 * behavior exact and unambiguous — 39 → "Needs Work", 40 → "Fair",
 * 59 → "Fair", 60 → "Good", 79 → "Good", 80 → "Excellent" — and removes any
 * gap for fractional values (e.g. 39.5 → "Needs Work").
 *
 * Out-of-domain handling (documented decision):
 * - Values below 0 collapse to the lowest band ("Needs Work").
 * - Values above 100 collapse to the highest band ("Excellent").
 * - `NaN` is guarded explicitly and mapped to the lowest band ("Needs Work")
 *   so a non-finite value can never read as a passing "Excellent" score.
 *   (`Infinity`/`-Infinity` already fall through to the highest/lowest band.)
 *
 * Pure, side-effect-free, and framework-agnostic — consumed by the co-located
 * `ScoreLabel` element of `score-gauge.tsx` (design Section 7.1). A score of 0
 * still yields a label ("Needs Work"), never an empty/error state (Req 12.1).
 */

/** The fixed set of qualitative score labels (design Section 7.1). */
export type ScoreLabel = "Excellent" | "Good" | "Fair" | "Needs Work";

/**
 * Return the qualitative {@link ScoreLabel} for a numeric match score.
 *
 * @param score - A match score; canonically an integer in `[0, 100]`.
 * @returns The qualitative label for the score's band.
 */
export function scoreLabel(score: number): ScoreLabel {
  // Defensive guard: a non-finite score must not slip through to "Excellent".
  if (Number.isNaN(score)) {
    return "Needs Work";
  }
  if (score < 40) {
    return "Needs Work";
  }
  if (score < 60) {
    return "Fair";
  }
  if (score < 80) {
    return "Good";
  }
  return "Excellent";
}
