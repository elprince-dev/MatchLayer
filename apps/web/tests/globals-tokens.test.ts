/**
 * Token-presence guard for `globals.css` — foundation wiring (Task 1.7).
 *
 * design Section 4.6–4.9 finalizes the radius / shadow / motion tokens, and
 * Req 1.4 / 1.9 require them (plus the reduced-motion override) to be declared
 * in `apps/web/src/app/globals.css`. The color/font tokens already existed and
 * are out of scope for this task.
 *
 * This is a deliberately simple read-the-file-and-assert test: CSS custom
 * properties are not exercisable as runtime behavior in jsdom (Tailwind's CSS
 * is never loaded into the test DOM — see `auth-card.test.tsx`'s note on why
 * `color-contrast` is disabled there), so the token DECLARATIONS in the source
 * are the contract. The test reads the real file relative to itself so it
 * travels with the repo regardless of the runner CWD, and it asserts on the
 * presence of each declaration rather than exact whitespace/value so a future
 * value refinement (allowed by design §4) does not make it brittle.
 *
 * Module-level file reads only — the default Vitest `node` environment is
 * sufficient (no DOM, no jsdom pragma).
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { beforeAll, describe, expect, it } from "vitest";

const here = path.dirname(fileURLToPath(import.meta.url));
const globalsPath = path.resolve(here, "../src/app/globals.css");

let css = "";

beforeAll(() => {
  css = fs.readFileSync(globalsPath, "utf8");
});

/**
 * Assert a CSS custom-property DECLARATION (`--name: <something>;`) exists,
 * tolerant of arbitrary inter-token whitespace and any value. Anchoring on the
 * declaration (name followed by a colon) avoids matching the `@theme inline`
 * re-export lines like `--radius-card: var(--radius-card);` only — those are a
 * superset and still satisfy the regex, which is fine: presence is what Req 1.4
 * requires.
 */
function declaresToken(name: string): boolean {
  // Escape regex metacharacters (incl. backslash) so the token name is matched
  // literally; then match `--name` followed by a colon.
  const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return new RegExp(`${escaped}\\s*:`).test(css);
}

describe("globals.css radius tokens (Req 1.4; design §4.6)", () => {
  it.each(["--radius-card", "--radius-hero", "--radius-pill"])(
    "declares %s",
    (token) => {
      expect(declaresToken(token)).toBe(true);
    },
  );
});

describe("globals.css shadow tokens (Req 1.4; design §4.7)", () => {
  it.each(["--shadow-resting", "--shadow-elevated"])("declares %s", (token) => {
    expect(declaresToken(token)).toBe(true);
  });

  it("uses layered, multi-stop shadow values (no single-layer uniform spread)", () => {
    // design §4.7 / Req 1.4: each elevation token stacks two shadow layers,
    // i.e. its value contains a comma separating the close-contact layer from
    // the far-spread layer. Capture the `--shadow-resting` value and assert the
    // comma-separated layering is present.
    const match = css.match(/--shadow-resting\s*:\s*([^;]+);/);
    expect(match).not.toBeNull();
    expect(match?.[1]).toContain(",");
  });
});

describe("globals.css motion tokens (Req 1.5; design §4.8)", () => {
  it.each([
    "--motion-micro",
    "--motion-layout",
    "--motion-hero",
    "--motion-ease",
  ])("declares %s", (token) => {
    expect(declaresToken(token)).toBe(true);
  });
});

describe("globals.css reduced-motion override (Req 1.9; design §4.8)", () => {
  it("declares a prefers-reduced-motion: reduce media block", () => {
    expect(
      /@media\s*\(\s*prefers-reduced-motion\s*:\s*reduce\s*\)/.test(css),
    ).toBe(true);
  });

  it("re-points the motion duration tokens to 0ms inside that block", () => {
    // Isolate the reduced-motion block and assert each duration token is
    // re-declared to `0ms` within it. `--motion-ease` is intentionally NOT
    // overridden (easing a 0ms transition is a no-op), so it is not asserted.
    const blockMatch = css.match(
      /@media\s*\(\s*prefers-reduced-motion\s*:\s*reduce\s*\)\s*\{([\s\S]*?)\n\}/,
    );
    expect(blockMatch).not.toBeNull();
    const block = blockMatch?.[1] ?? "";

    for (const token of [
      "--motion-micro",
      "--motion-layout",
      "--motion-hero",
    ]) {
      const escaped = token.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      expect(new RegExp(`${escaped}\\s*:\\s*0ms`).test(block)).toBe(true);
    }
  });
});
