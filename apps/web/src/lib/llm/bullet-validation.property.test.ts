/**
 * Property 24: Client-side bullet validation mirrors the server bounds
 * (phase-3-llm-layer Task 11.6; fast-check + Vitest).
 *
 * **Validates: Requirements 17.7**
 *
 * Subject: the pure `validateBullets` function in `bullet-validation.ts`
 * — the gate the Bullet_Rewriter UI runs before any request is sent.
 * The property statement (design.md): for any generated bullet list,
 * the request is submitted if and only if the list satisfies the server
 * bounds (count 1..MAX_BULLET_COUNT, no empty/whitespace-only bullet,
 * no bullet over MAX_BULLET_CHARS characters); on violation an inline
 * error identifying the violated bound is produced. Error messages
 * never echo bullet content — bullets are Restricted PII
 * (`security.md`), verified here with a planted content sentinel.
 */

import fc from "fast-check";
import { describe, expect, it } from "vitest";

import {
  MAX_BULLET_CHARS,
  MAX_BULLET_COUNT,
  validateBullets,
} from "@/lib/llm/bullet-validation";

// ---------------------------------------------------------------------------
// Oracle — an independent statement of the server bounds (Req 6.3)
// ---------------------------------------------------------------------------

function boundsHold(bullets: readonly string[]): boolean {
  return (
    bullets.length >= 1 &&
    bullets.length <= MAX_BULLET_COUNT &&
    bullets.every(
      (bullet) => bullet.trim().length > 0 && bullet.length <= MAX_BULLET_CHARS,
    )
  );
}

// ---------------------------------------------------------------------------
// Generators
// ---------------------------------------------------------------------------

/** Planted in every non-whitespace bullet so the PII property can assert
 * that no error message contains bullet content. */
const SENTINEL = "resume-bullet-pii-sentinel";

/** A bullet satisfying every per-bullet bound: non-whitespace content,
 * length anywhere in [SENTINEL.length, MAX_BULLET_CHARS] — the integer
 * generator biases toward the extremes, so the exact 500-char boundary
 * is exercised. */
const validBulletArb: fc.Arbitrary<string> = fc
  .integer({ min: SENTINEL.length, max: MAX_BULLET_CHARS })
  .map((length) => SENTINEL.padEnd(length, "x"));

/** An empty or whitespace-only bullet (violates the non-empty bound). */
const whitespaceBulletArb: fc.Arbitrary<string> = fc
  .array(fc.constantFrom(" ", "\t", "\n", "\r"), { maxLength: 20 })
  .map((chars) => chars.join(""));

/** A bullet over the character ceiling (violates the length bound). */
const overlongBulletArb: fc.Arbitrary<string> = fc
  .integer({ min: MAX_BULLET_CHARS + 1, max: MAX_BULLET_CHARS + 64 })
  .map((length) => SENTINEL.padEnd(length, "x"));

/** Any bullet — valid, whitespace-only, or over-length. */
const anyBulletArb: fc.Arbitrary<string> = fc.oneof(
  { weight: 3, arbitrary: validBulletArb },
  { weight: 1, arbitrary: whitespaceBulletArb },
  { weight: 1, arbitrary: overlongBulletArb },
);

/** A list satisfying every bound (count 1..MAX_BULLET_COUNT). */
const validListArb: fc.Arbitrary<string[]> = fc.array(validBulletArb, {
  minLength: 1,
  maxLength: MAX_BULLET_COUNT,
});

/** Any list at all: empty, within count, or over count, mixing bullet
 * kinds — covers every combination of violated bounds. */
const anyListArb: fc.Arbitrary<string[]> = fc.array(anyBulletArb, {
  maxLength: MAX_BULLET_COUNT + 3,
});

// ---------------------------------------------------------------------------
// Property 24
// ---------------------------------------------------------------------------

describe("Property 24: client-side bullet validation mirrors the server bounds", () => {
  it("accepts every list within bounds, returning the bullets unchanged", () => {
    fc.assert(
      fc.property(validListArb, (bullets) => {
        const result = validateBullets(bullets);
        expect(result).toEqual({ ok: true, bullets });
      }),
    );
  });

  it("accepts a list if and only if it satisfies the server bounds", () => {
    fc.assert(
      fc.property(anyListArb, (bullets) => {
        expect(validateBullets(bullets).ok).toBe(boundsHold(bullets));
      }),
    );
  });

  it("names each violated bound — and only violated bounds — in the errors", () => {
    fc.assert(
      fc.property(anyListArb, (bullets) => {
        const result = validateBullets(bullets);
        if (result.ok) return;

        expect(result.errors.length).toBeGreaterThan(0);
        const mentions = (needle: string): boolean =>
          result.errors.some((error) => error.includes(needle));

        // Count floor (the ≥1 bound comes from the generated schema).
        expect(mentions("at least one bullet")).toBe(bullets.length === 0);

        // Count ceiling.
        expect(mentions(`at most ${MAX_BULLET_COUNT} bullets`)).toBe(
          bullets.length > MAX_BULLET_COUNT,
        );

        // Per-bullet bounds, identified by position. A whitespace-only
        // bullet reports the emptiness bound (its length is immaterial).
        bullets.forEach((bullet, index) => {
          const empty = bullet.trim().length === 0;
          expect(mentions(`Bullet ${index + 1} is empty`)).toBe(empty);
          expect(
            mentions(`Bullet ${index + 1} exceeds ${MAX_BULLET_CHARS}`),
          ).toBe(!empty && bullet.length > MAX_BULLET_CHARS);
        });
      }),
    );
  });

  it("never echoes bullet content in an error message (Restricted PII)", () => {
    fc.assert(
      fc.property(anyListArb, (bullets) => {
        const result = validateBullets(bullets);
        if (result.ok) return;
        for (const error of result.errors) {
          expect(error).not.toContain(SENTINEL);
        }
      }),
    );
  });
});
