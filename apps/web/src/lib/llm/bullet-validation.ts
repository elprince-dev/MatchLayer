/**
 * Client-side Bullet_Rewriter request validation
 * (phase-3-llm-layer Task 11.5; Requirement 17.7).
 *
 * Mirrors the server-side bounds of Requirement 6.3 so an invalid
 * submission is rejected with an inline error *before* any request is
 * sent — the API is never called on a violation.
 *
 * Layering (per `conventions.md` "Shared schemas"):
 *
 * - The **generated** `BulletRewriteRequestSchema` from
 *   `@matchlayer/shared-types` is the base — it carries every bound the
 *   backend exposes through OpenAPI (the request shape and the ≥1-bullet
 *   floor declared statically on the Pydantic field).
 * - The count ceiling and per-bullet character ceiling are **runtime
 *   configuration** on the backend (`MATCHLAYER_LLM_MAX_BULLETS` /
 *   `MATCHLAYER_LLM_MAX_BULLET_CHARS`, checked by a settings-reading
 *   Pydantic validator), so they structurally cannot appear in the
 *   OpenAPI schema or the generated Zod. The constants below mirror the
 *   backend defaults (design §Components: Configuration table); the
 *   server remains authoritative — a mismatch surfaces as the server's
 *   422, never as accepted-invalid input.
 *
 * Error messages identify the violated bound (Req 17.7: "an inline
 * validation error identifying the violated bound"). They never echo
 * bullet content — bullets are Restricted PII (`security.md`).
 */

import { BulletRewriteRequestSchema } from "@matchlayer/shared-types";

/**
 * Maximum bullets per request — mirrors `MATCHLAYER_LLM_MAX_BULLETS`
 * (backend default 5).
 */
export const MAX_BULLET_COUNT = 5;

/**
 * Maximum characters per bullet — mirrors
 * `MATCHLAYER_LLM_MAX_BULLET_CHARS` (backend default 500).
 */
export const MAX_BULLET_CHARS = 500;

/** Outcome of {@link validateBullets}. */
export type BulletValidationResult =
  | { ok: true; bullets: string[] }
  | { ok: false; errors: string[] };

/**
 * Validate a bullet list against the Requirement 6.3 bounds:
 * count within 1..{@link MAX_BULLET_COUNT}, no empty/whitespace-only
 * bullet, each bullet ≤ {@link MAX_BULLET_CHARS} characters.
 *
 * Pure and deterministic — Task 11.6's property test (Property 24)
 * exercises this function directly.
 *
 * @returns `{ ok: true, bullets }` when every bound holds, else
 *   `{ ok: false, errors }` with one message per violated bound, each
 *   naming the bound and position (never the bullet text).
 */
export function validateBullets(bullets: string[]): BulletValidationResult {
  const errors: string[] = [];

  // The generated schema enforces the request shape and the ≥1 floor.
  const parsed = BulletRewriteRequestSchema.safeParse({ bullets });
  if (!parsed.success) {
    errors.push("Add at least one bullet to rewrite.");
  }

  if (bullets.length > MAX_BULLET_COUNT) {
    errors.push(
      `Submit at most ${MAX_BULLET_COUNT} bullets at a time — you have ${bullets.length}.`,
    );
  }

  bullets.forEach((bullet, index) => {
    if (bullet.trim().length === 0) {
      errors.push(`Bullet ${index + 1} is empty or contains only whitespace.`);
    } else if (bullet.length > MAX_BULLET_CHARS) {
      errors.push(
        `Bullet ${index + 1} exceeds ${MAX_BULLET_CHARS} characters — it has ${bullet.length}.`,
      );
    }
  });

  return errors.length > 0 ? { ok: false, errors } : { ok: true, bullets };
}
