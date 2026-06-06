/**
 * Computed-contrast verification for the design Section 10.1 token pairs
 * (frontend-redesign Task 9.3; Req 19.1, 16.10; design Section 10.1, 10.2).
 *
 * ## Why this is a *separate* file from the axe checks (`a11y.test.tsx`)
 * axe-core's `color-contrast` rule needs the page's real, computed CSS to read
 * pixel colors. Under jsdom (the test DOM) Tailwind's stylesheet is **never
 * applied** — the `@theme inline` utilities and the `--color-*` custom
 * properties resolve to nothing — so axe would be measuring the absence of
 * styles, not the design tokens. That is exactly why every axe scan in this
 * repo disables `color-contrast` (see `tests/auth-card.test.tsx` and the note in
 * `a11y.test.tsx`). The contrast contract is therefore verified **here**, as
 * pure WCAG math over the token *values* declared in `globals.css`, rather than
 * by a renderer.
 *
 * ## What it does
 *   1. Parses the `R G B` token triplets from `apps/web/src/app/globals.css`
 *      for BOTH themes — Light_Mode (`:root`) and Dark_Mode (`.dark`) — with a
 *      regex (the same source-of-truth file the app ships; no duplicated
 *      values).
 *   2. Converts each triplet to a WCAG 2.x relative luminance and computes the
 *      contrast ratio for every token pair design Section 10.1 enumerates.
 *   3. Asserts each ratio clears the AA threshold the design assigns it
 *      (4.5:1 normal text, 3:1 large text / UI / graphical), and that the pairs
 *      the design explicitly flags as **failing** (so the Section 10.2
 *      mitigations are mandated) do indeed fail — so a future token tweak that
 *      silently regressed contrast would break this test.
 *   4. Verifies the **mandated light-mode pill mitigation** numerically: the
 *      KeywordTag label is the primary `text` token over a tinted
 *      `success`/`warning` fill (token at 15% over `bg-elevated`), which must
 *      clear 4.5:1 in *both* themes — the high-contrast-label claim of design
 *      Section 10.2 mitigation #1. (The structural side of that mitigation —
 *      that KeywordTag actually uses tinted-fill + border + `text` and not
 *      colored text — is asserted in `a11y.test.tsx`.)
 *
 * A drift guard additionally pins each computed ratio to the value published in
 * the Section 10.1 table (within a small tolerance), so a change to any token
 * triplet that shifted a ratio would surface immediately.
 *
 * Pure module-level file math — the repo-default Vitest `node` environment is
 * sufficient (no DOM, no jsdom pragma, no renderer).
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { beforeAll, describe, expect, it } from "vitest";

const here = path.dirname(fileURLToPath(import.meta.url));
const globalsPath = path.resolve(here, "../src/app/globals.css");

// ---------------------------------------------------------------------------
// Token parsing
// ---------------------------------------------------------------------------

/** A parsed `R G B` triplet (each channel 0–255). */
type Rgb = readonly [number, number, number];

/** Map of `--color-<name>` token → its `R G B` triplet, for one theme block. */
type TokenMap = Record<string, Rgb>;

let css = "";
let light: TokenMap = {};
let dark: TokenMap = {};

/**
 * Extract the `--color-*` triplets from a single CSS rule block.
 *
 * Selects the FIRST `:root { … }` / `.dark { … }` block (the top-level color
 * declarations). Both blocks are flat — they contain no nested braces — so a
 * non-greedy `[^}]*` capture is exact. The `@theme inline` re-export block
 * declares the same names with `rgb(var(--color-*) / <alpha-value>)` values,
 * which the integer-triplet regex below never matches, and the
 * `prefers-reduced-motion` media block's nested `:root` comes *after* the
 * top-level one, so the non-global `.match()` (first match) ignores it.
 */
function parseTheme(source: string, selector: ":root" | ".dark"): TokenMap {
  const escaped = selector === ":root" ? ":root" : "\\.dark";
  const blockMatch = source.match(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`));
  if (blockMatch === null) {
    throw new Error(`globals.css: could not find a \`${selector}\` block`);
  }
  const block = blockMatch[1] ?? "";

  const tokens: TokenMap = {};
  const tripletRe =
    /--color-([a-z0-9-]+)\s*:\s*(\d{1,3})\s+(\d{1,3})\s+(\d{1,3})\s*;/g;
  let m: RegExpExecArray | null;
  while ((m = tripletRe.exec(block)) !== null) {
    tokens[m[1]!] = [Number(m[2]), Number(m[3]), Number(m[4])];
  }
  return tokens;
}

beforeAll(() => {
  css = fs.readFileSync(globalsPath, "utf8");
  light = parseTheme(css, ":root");
  dark = parseTheme(css, ".dark");
});

// ---------------------------------------------------------------------------
// WCAG 2.x relative-luminance + contrast-ratio math
// ---------------------------------------------------------------------------

/** Linearize one 0–255 sRGB channel per the WCAG 2.x definition. */
function linearize(channel: number): number {
  const c = channel / 255;
  return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
}

/** WCAG relative luminance of an `R G B` triplet. */
function luminance([r, g, b]: Rgb): number {
  return 0.2126 * linearize(r) + 0.7152 * linearize(g) + 0.0722 * linearize(b);
}

/** WCAG contrast ratio between two colors (order-independent: (L₁+.05)/(L₂+.05)). */
function contrastRatio(a: Rgb, b: Rgb): number {
  const la = luminance(a);
  const lb = luminance(b);
  const hi = Math.max(la, lb);
  const lo = Math.min(la, lb);
  return (hi + 0.05) / (lo + 0.05);
}

/** Alpha-composite `fg` over `bg` at the given opacity → an opaque triplet. */
function composite(fg: Rgb, bg: Rgb, alpha: number): Rgb {
  return [
    alpha * fg[0] + (1 - alpha) * bg[0],
    alpha * fg[1] + (1 - alpha) * bg[1],
    alpha * fg[2] + (1 - alpha) * bg[2],
  ];
}

// ---------------------------------------------------------------------------
// AA thresholds + the design's published ratios (drift guard)
// ---------------------------------------------------------------------------

/** Normal-text AA minimum (below 18pt / 14pt-bold). */
const AA_NORMAL = 4.5;
/** Large-text / UI-component / graphical-object AA minimum. */
const AA_LARGE_UI = 3.0;
/** Tolerance for pinning a computed ratio to the design's published value. */
const PUBLISHED_TOLERANCE = 0.1;

/** Resolve a token triplet for a theme, failing loudly on a typo / missing token. */
function token(theme: TokenMap, name: string): Rgb {
  const rgb = theme[name];
  if (rgb === undefined) {
    throw new Error(`globals.css: missing --color-${name}`);
  }
  return rgb;
}

interface Pair {
  /** Human-readable pair name, mirroring the Section 10.1 table rows. */
  readonly label: string;
  /** Foreground token name (or a special composited fill handled inline). */
  readonly fg: string;
  /** Background token name. */
  readonly bg: string;
  /** The design's published ratio for this pair (Section 10.1). */
  readonly published: number;
}

/**
 * Pairs the design certifies as **passing normal-text AA** (≥ 4.5:1). These are
 * safe for body text in the stated theme.
 */
const PASS_NORMAL: Record<"light" | "dark", readonly Pair[]> = {
  dark: [
    { label: "text on bg", fg: "text", bg: "bg", published: 18.0 },
    {
      label: "text on bg-elevated",
      fg: "text",
      bg: "bg-elevated",
      published: 17.15,
    },
    { label: "text-muted on bg", fg: "text-muted", bg: "bg", published: 7.72 },
    {
      label: "text-muted on bg-elevated",
      fg: "text-muted",
      bg: "bg-elevated",
      published: 7.35,
    },
    { label: "brand on bg", fg: "brand", bg: "bg", published: 4.67 },
    { label: "brand-2 on bg", fg: "brand-2", bg: "bg", published: 10.95 },
    {
      label: "success on bg-elevated",
      fg: "success",
      bg: "bg-elevated",
      published: 9.8,
    },
    {
      label: "warning on bg-elevated",
      fg: "warning",
      bg: "bg-elevated",
      published: 11.29,
    },
    { label: "danger on bg", fg: "danger", bg: "bg", published: 7.15 },
  ],
  light: [
    { label: "text on bg", fg: "text", bg: "bg", published: 19.79 },
    { label: "text-muted on bg", fg: "text-muted", bg: "bg", published: 7.73 },
    {
      label: "text-muted on bg-elevated",
      fg: "text-muted",
      bg: "bg-elevated",
      published: 7.34,
    },
    {
      label: "text-subtle on bg",
      fg: "text-subtle",
      bg: "bg",
      published: 4.83,
    },
    { label: "brand on bg", fg: "brand", bg: "bg", published: 5.7 },
  ],
};

/**
 * Pairs the design certifies for **large-text / UI / indicator use only**:
 * they clear ≥ 3:1 but fall short of the 4.5:1 normal-text bar, so the design
 * restricts them to large text, graphical objects, or non-essential hints.
 */
const PASS_LARGE_UI_ONLY: Record<"light" | "dark", readonly Pair[]> = {
  dark: [
    // text-subtle is reserved for large text / non-essential hints in dark mode.
    {
      label: "text-subtle on bg",
      fg: "text-subtle",
      bg: "bg",
      published: 4.09,
    },
  ],
  light: [
    // danger is an INDICATOR color, never body text (design 10.2 mitigation #3).
    { label: "danger on bg", fg: "danger", bg: "bg", published: 3.76 },
  ],
};

/**
 * Pairs the design certifies as **failing** even the 3:1 bar as a foreground —
 * the reason the Section 10.2 mitigations are *mandated*. `brand-2` is
 * decorative/gradient only, and `success`/`warning` must never be colored text
 * in light mode (they use the tinted-fill + primary-text pill instead).
 */
const FAIL_AS_FOREGROUND: Record<"light", readonly Pair[]> = {
  light: [
    { label: "brand-2 on bg", fg: "brand-2", bg: "bg", published: 2.43 },
    {
      label: "success on bg-elevated",
      fg: "success",
      bg: "bg-elevated",
      published: 2.41,
    },
    {
      label: "warning on bg-elevated",
      fg: "warning",
      bg: "bg-elevated",
      published: 2.04,
    },
  ],
};

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("globals.css token parsing (both themes)", () => {
  it("parses all 13 color tokens for Light_Mode (:root) and Dark_Mode (.dark)", () => {
    const expected = [
      "bg",
      "bg-elevated",
      "bg-glass",
      "border",
      "border-strong",
      "text",
      "text-muted",
      "text-subtle",
      "brand",
      "brand-2",
      "success",
      "warning",
      "danger",
    ];
    for (const name of expected) {
      expect(token(light, name)).toHaveLength(3);
      expect(token(dark, name)).toHaveLength(3);
    }
  });

  it("reads the frozen brand triplets exactly (design Section 4.1, 4.2)", () => {
    // A spot-check anchoring the parser to the design's frozen values so a
    // mis-parse (e.g. capturing the wrong block) is caught early.
    expect(token(light, "brand")).toEqual([124, 58, 237]);
    expect(token(dark, "brand")).toEqual([139, 92, 246]);
    expect(token(light, "text")).toEqual([10, 10, 11]);
    expect(token(dark, "text")).toEqual([244, 244, 245]);
  });
});

describe.each(["dark", "light"] as const)(
  "Section 10.1 contrast — %s mode: normal-text AA pairs (≥ 4.5:1)",
  (theme) => {
    const tokens = theme === "dark" ? () => dark : () => light;

    it.each(PASS_NORMAL[theme])(
      "$label clears 4.5:1 normal-text AA",
      (pair) => {
        const r = contrastRatio(
          token(tokens(), pair.fg),
          token(tokens(), pair.bg),
        );
        expect(r).toBeGreaterThanOrEqual(AA_NORMAL);
        // Drift guard: the computed ratio matches the design's published value.
        expect(Math.abs(r - pair.published)).toBeLessThan(PUBLISHED_TOLERANCE);
      },
    );
  },
);

describe.each(["dark", "light"] as const)(
  "Section 10.1 contrast — %s mode: large-text / UI / indicator pairs (≥ 3:1, < 4.5:1)",
  (theme) => {
    const tokens = theme === "dark" ? () => dark : () => light;

    it.each(PASS_LARGE_UI_ONLY[theme])(
      "$label clears the 3:1 large/UI bar but is correctly below the 4.5 normal bar",
      (pair) => {
        const r = contrastRatio(
          token(tokens(), pair.fg),
          token(tokens(), pair.bg),
        );
        expect(r).toBeGreaterThanOrEqual(AA_LARGE_UI);
        expect(r).toBeLessThan(AA_NORMAL);
        expect(Math.abs(r - pair.published)).toBeLessThan(PUBLISHED_TOLERANCE);
      },
    );
  },
);

describe("Section 10.1 contrast — light mode: pairs that MUST fail as foreground (justify the 10.2 mitigations)", () => {
  it.each(FAIL_AS_FOREGROUND.light)(
    "$label is below the 3:1 bar, so it must never be used as text or sole indicator",
    (pair) => {
      const r = contrastRatio(token(light, pair.fg), token(light, pair.bg));
      // These FAILING ratios are the reason design 10.2 mandates the tinted-fill
      // pill + decorative-only cyan rules. Asserting they fail keeps the
      // mitigations justified and catches a token change that would (mis)lead a
      // future dev into thinking colored text had become safe.
      expect(r).toBeLessThan(AA_LARGE_UI);
      expect(Math.abs(r - pair.published)).toBeLessThan(PUBLISHED_TOLERANCE);
    },
  );
});

describe("Focus ring contrast — branded ring vs page bg (Req 19.2, ≥ 3:1 UI)", () => {
  // The 2px branded focus ring (`ring-brand`) must clear the 3:1 UI-component
  // threshold against the adjacent page background in both themes (design 10.3:
  // 4.67 dark / 5.70 light).
  it("brand focus ring clears 3:1 against bg in dark mode", () => {
    const r = contrastRatio(token(dark, "brand"), token(dark, "bg"));
    expect(r).toBeGreaterThanOrEqual(AA_LARGE_UI);
    expect(Math.abs(r - 4.67)).toBeLessThan(PUBLISHED_TOLERANCE);
  });

  it("brand focus ring clears 3:1 against bg in light mode", () => {
    const r = contrastRatio(token(light, "brand"), token(light, "bg"));
    expect(r).toBeGreaterThanOrEqual(AA_LARGE_UI);
    expect(Math.abs(r - 5.7)).toBeLessThan(PUBLISHED_TOLERANCE);
  });
});

describe("Mandated light-mode pill mitigation (design 10.2 #1) — computed", () => {
  // The KeywordTag mitigation: the label is the primary `text` token over a
  // tinted `success`/`warning` fill (token at 15% opacity over `bg-elevated`),
  // NOT colored text. That label must clear 4.5:1 normal-text AA in BOTH themes
  // — this is the numeric backing for design 10.2's "labels are high-contrast
  // (text ≥ 7:1)" claim. (The structural assertion that KeywordTag applies
  // `bg-*/15` + `border-*` + `text-text` lives in `a11y.test.tsx`.)
  const PILL_ALPHA = 0.15;

  it.each(["dark", "light"] as const)(
    "%s: `text` label over a 15%%-tinted success fill clears 4.5:1",
    (theme) => {
      const tokens = theme === "dark" ? dark : light;
      const fill = composite(
        token(tokens, "success"),
        token(tokens, "bg-elevated"),
        PILL_ALPHA,
      );
      const r = contrastRatio(token(tokens, "text"), fill);
      expect(r).toBeGreaterThanOrEqual(AA_NORMAL);
    },
  );

  it.each(["dark", "light"] as const)(
    "%s: `text` label over a 15%%-tinted warning fill clears 4.5:1",
    (theme) => {
      const tokens = theme === "dark" ? dark : light;
      const fill = composite(
        token(tokens, "warning"),
        token(tokens, "bg-elevated"),
        PILL_ALPHA,
      );
      const r = contrastRatio(token(tokens, "text"), fill);
      expect(r).toBeGreaterThanOrEqual(AA_NORMAL);
    },
  );
});
