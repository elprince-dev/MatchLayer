/**
 * Unit tests for `KeywordTag` (Task 3.6).
 *
 * Validates the mandated light-mode contrast mitigation (design Section 10.2
 * mandate #1; Req 11.3, 11.4, 11.6, 16.4): `success`/`warning` used as
 * *foreground text* on a light surface fail WCAG AA, so a keyword pill must use
 *
 *   - a **tinted background fill** at low opacity (`bg-success/15` / `bg-warning/15`),
 *   - a **full-token border** carrying the semantic hue (`border-success` / `border-warning`),
 *   - and the **label in the primary `text` token** — NOT colored text.
 *
 * These tests therefore assert the pill carries the tinted-fill + token-border
 * classes and the primary `text` class, and explicitly assert it does NOT rely
 * on `text-success` / `text-warning` colored text. This is the exact treatment
 * the Section 9.3 / 3.6 gate pins, so it must not regress to colored text.
 *
 * Uses the Section 5 fixtures (`matchStrong` matched/missing keywords).
 *
 * Conventions mirror the co-located `error-state.test.tsx`: render/screen/
 * cleanup, `toBeInstanceOf`, className string assertions, no jest-dom matchers.
 *
 * @vitest-environment jsdom
 */

import * as React from "react";

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { KeywordTag } from "@/components/results/keyword-tag";
import { matchStrong } from "@/components/results/__fixtures__/match-fixtures";

afterEach(() => {
  cleanup();
});

/** The pill element carrying the variant styling (the labelled wrapper span). */
function pillFor(term: string): HTMLElement {
  return screen
    .getByText(term)
    .closest("[data-slot=keyword-tag]") as HTMLElement;
}

describe("KeywordTag — success (matched) tinted-fill + primary-text treatment", () => {
  const matched = matchStrong.matched_keywords[0]!; // { term: "python", ... }

  it("renders the term text", () => {
    render(<KeywordTag keyword={matched} variant="success" />);
    expect(screen.getByText(matched.term)).toBeInstanceOf(HTMLElement);
  });

  it("uses a tinted success fill + full success border (the semantic hue carrier)", () => {
    render(<KeywordTag keyword={matched} variant="success" />);
    const pill = pillFor(matched.term);

    expect(pill.className).toContain("bg-success/15");
    expect(pill.className).toContain("border-success");
  });

  it("renders the label in the primary `text` token, NOT colored success text", () => {
    render(<KeywordTag keyword={matched} variant="success" />);
    const pill = pillFor(matched.term);

    // Primary high-contrast label (≥7:1 both themes).
    expect(pill.className).toContain("text-text");
    // The mitigation forbids colored *text*: the success hue is fill/border only.
    expect(pill.className).not.toMatch(/(^|\s)text-success(\s|$|\/)/);
  });
});

describe("KeywordTag — warning (missing) tinted-fill + primary-text treatment", () => {
  const missing = matchStrong.missing_keywords[0]!; // { term: "kubernetes", ... }

  it("uses a tinted warning fill + full warning border", () => {
    render(<KeywordTag keyword={missing} variant="warning" />);
    const pill = pillFor(missing.term);

    expect(pill.className).toContain("bg-warning/15");
    expect(pill.className).toContain("border-warning");
  });

  it("renders the label in the primary `text` token, NOT colored warning text", () => {
    render(<KeywordTag keyword={missing} variant="warning" />);
    const pill = pillFor(missing.term);

    expect(pill.className).toContain("text-text");
    expect(pill.className).not.toMatch(/(^|\s)text-warning(\s|$|\/)/);
  });
});

describe("KeywordTag — shared pill shape (Req 11.6)", () => {
  it("is a rounded-full pill of uniform height", () => {
    render(
      <KeywordTag
        keyword={matchStrong.matched_keywords[0]!}
        variant="success"
      />,
    );
    const pill = pillFor(matchStrong.matched_keywords[0]!.term);

    // rounded-pill (full-round) + a fixed height so every pill in a group lines up.
    expect(pill.className).toContain("rounded-pill");
    expect(pill.className).toMatch(/(^|\s)h-7(\s|$)/);
  });

  it("exposes the variant via a data attribute for downstream styling/assertions", () => {
    render(
      <KeywordTag
        keyword={matchStrong.matched_keywords[0]!}
        variant="success"
      />,
    );
    const pill = pillFor(matchStrong.matched_keywords[0]!.term);
    expect(pill.getAttribute("data-variant")).toBe("success");
  });
});
