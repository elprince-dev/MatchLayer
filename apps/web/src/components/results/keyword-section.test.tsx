/**
 * Unit tests for `KeywordSection` (Task 3.6).
 *
 * Validates the matched/missing keyword group against its acceptance criteria
 * (Req 11.3, 11.4, 12.2, 12.3, 12.7; design Section 7.1 "KeywordSection",
 * Section 10.2):
 *
 *   - Req 12.2 / 12.3 / 12.7 — an EMPTY `keywords` array renders the defined
 *     `emptyMessage` (distinct copy for matched vs missing), as a VALID result,
 *     and NEVER uses the `danger` token.
 *   - Req 11.3 / 11.4 — a non-empty array renders one `KeywordTag` per keyword
 *     in the order received (the API already sorts by descending weight; the
 *     section does no sorting of its own).
 *
 * Uses the Section 5 fixtures: `matchStrong` (populated matched/missing) and
 * `matchDegenerate` (both arrays empty → the empty-state copy).
 *
 * Conventions mirror the co-located `error-state.test.tsx`: render/screen/
 * within/cleanup, `toBeInstanceOf`, className assertions, no jest-dom matchers.
 *
 * @vitest-environment jsdom
 */

import * as React from "react";

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { KeywordSection } from "@/components/results/keyword-section";
import {
  matchDegenerate,
  matchStrong,
} from "@/components/results/__fixtures__/match-fixtures";

const MATCHED_EMPTY = "No matching keywords were found.";
const MISSING_EMPTY = "Your resume covers the analyzed keywords.";

afterEach(() => {
  cleanup();
});

describe("KeywordSection — populated renders tags in received order (Req 11.3, 11.4)", () => {
  it("renders one pill per matched keyword", () => {
    render(
      <KeywordSection
        title="Matched keywords"
        keywords={matchStrong.matched_keywords}
        variant="success"
        emptyMessage={MATCHED_EMPTY}
      />,
    );

    for (const kw of matchStrong.matched_keywords) {
      expect(screen.getByText(kw.term)).toBeInstanceOf(HTMLElement);
    }
    // No empty-state copy when the list is populated.
    expect(screen.queryByText(MATCHED_EMPTY)).toBeNull();
  });

  it("preserves the API order (weight-descending) without re-sorting", () => {
    const { container } = render(
      <KeywordSection
        title="Matched keywords"
        keywords={matchStrong.matched_keywords}
        variant="success"
        emptyMessage={MATCHED_EMPTY}
      />,
    );

    const rendered = Array.from(
      container.querySelectorAll("[data-slot=keyword-tag]"),
    ).map((el) => el.textContent?.trim());
    const expected = matchStrong.matched_keywords.map((kw) => kw.term);
    // Order is identical to the fixture (already descending by weight).
    expect(rendered).toEqual(expected);
  });
});

describe("KeywordSection — empty renders the defined message, never `danger` (Req 12.2, 12.3, 12.7)", () => {
  it("renders the matched-empty copy for an empty matched array (degenerate fixture)", () => {
    render(
      <KeywordSection
        title="Matched keywords"
        keywords={matchDegenerate.matched_keywords}
        variant="success"
        emptyMessage={MATCHED_EMPTY}
      />,
    );

    expect(screen.getByText(MATCHED_EMPTY)).toBeInstanceOf(HTMLElement);
    // No pills are rendered for an empty section.
    expect(screen.queryByText("python")).toBeNull();
  });

  it("renders the missing-empty copy (distinct from matched) for an empty missing array", () => {
    render(
      <KeywordSection
        title="Missing keywords"
        keywords={matchDegenerate.missing_keywords}
        variant="warning"
        emptyMessage={MISSING_EMPTY}
      />,
    );

    expect(screen.getByText(MISSING_EMPTY)).toBeInstanceOf(HTMLElement);
  });

  it("never uses the `danger` token for an empty (valid) section", () => {
    const { container } = render(
      <KeywordSection
        title="Matched keywords"
        keywords={matchDegenerate.matched_keywords}
        variant="success"
        emptyMessage={MATCHED_EMPTY}
      />,
    );

    // A sparse-but-valid result must not read as an error (Req 12.7): no danger
    // token anywhere in the rendered subtree.
    expect(container.innerHTML).not.toMatch(/danger/);
  });
});

describe("KeywordSection — layout wraps rather than scrolling (Req 11.6)", () => {
  it("renders the populated list as a flex-wrap group with gap-2", () => {
    const { container } = render(
      <KeywordSection
        title="Missing keywords"
        keywords={matchStrong.missing_keywords}
        variant="warning"
        emptyMessage={MISSING_EMPTY}
      />,
    );

    const list = container.querySelector("ul");
    expect(list).toBeInstanceOf(HTMLElement);
    expect(list?.className).toContain("flex-wrap");
    expect(list?.className).toContain("gap-2");
  });
});
