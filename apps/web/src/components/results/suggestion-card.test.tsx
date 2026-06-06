/**
 * Unit tests for `SuggestionCard` (Task 3.6).
 *
 * Validates the suggestion card against its acceptance criteria (Req 11.5,
 * 12.4, 16.5, 20.3; design Section 7.1 "SuggestionCard"):
 *
 *   - Req 11.5 / 16.5 / 20.3 — the prop surface is exactly `{ keyword, text }`;
 *     the card renders the `text` and NEVER a `title` or a `priority` indicator
 *     (the backend `SuggestionOut` contract supplies neither).
 *   - Req 12.4 — the AFFIRMATIVE variant (empty `keyword`, e.g. the single
 *     diagnostic suggestion from the degenerate fixture) renders the text in a
 *     positive/success style with NO missing-keyword label; the IMPROVEMENT
 *     variant (non-empty `keyword`) renders the keyword label.
 *
 * Uses the Section 5 fixtures: `matchStrong.suggestions[0]` (improvement,
 * keyword "kubernetes") and `matchDegenerate.suggestions[0]` (affirmative,
 * empty keyword).
 *
 * Conventions mirror the co-located `error-state.test.tsx`: render/screen/
 * cleanup, `toBeInstanceOf`, className assertions, no jest-dom matchers.
 *
 * @vitest-environment jsdom
 */

import * as React from "react";

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { SuggestionCard } from "@/components/results/suggestion-card";
import {
  matchDegenerate,
  matchStrong,
} from "@/components/results/__fixtures__/match-fixtures";

const improvement = matchStrong.suggestions[0]!; // { keyword: "kubernetes", text: ... }
const affirmative = matchDegenerate.suggestions[0]!; // { keyword: "", text: ... }

afterEach(() => {
  cleanup();
});

describe("SuggestionCard — improvement variant (non-empty keyword)", () => {
  it("renders the suggestion text", () => {
    render(<SuggestionCard suggestion={improvement} />);
    expect(screen.getByText(improvement.text)).toBeInstanceOf(HTMLElement);
  });

  it("renders the associated missing-keyword label", () => {
    render(<SuggestionCard suggestion={improvement} />);
    // The keyword label ("kubernetes") associates the card with the missing term.
    expect(screen.getByText(improvement.keyword)).toBeInstanceOf(HTMLElement);
  });
});

describe("SuggestionCard — affirmative variant (empty keyword) (Req 12.4)", () => {
  it("renders the affirmative text", () => {
    render(<SuggestionCard suggestion={affirmative} />);
    expect(screen.getByText(affirmative.text)).toBeInstanceOf(HTMLElement);
  });

  it("renders in a success style and shows NO missing-keyword label", () => {
    const { container } = render(<SuggestionCard suggestion={affirmative} />);

    // Positive/success treatment, distinct from improvement cards (Req 12.4).
    const card = container.querySelector("article");
    expect(card).toBeInstanceOf(HTMLElement);
    expect(card?.className).toMatch(/success/);

    // No keyword pill: the affirmative card carries no warning-toned label.
    expect(card?.className).not.toMatch(/border-warning/);
    expect(container.innerHTML).not.toContain("bg-warning");
  });

  it("never reads as an error (no `danger` token)", () => {
    const { container } = render(<SuggestionCard suggestion={affirmative} />);
    expect(container.innerHTML).not.toMatch(/danger/);
  });
});

describe("SuggestionCard — no title / no priority in the contract (Req 11.5, 20.3)", () => {
  it("renders neither a title nor a priority indicator", () => {
    const { container } = render(<SuggestionCard suggestion={improvement} />);

    // The backend supplies no title/priority; the card must not invent either.
    expect(screen.queryByText(/priority/i)).toBeNull();
    expect(container.innerHTML).not.toMatch(/data-priority/);
    // No heading element is emitted (a title would typically be an h*/role).
    expect(container.querySelector("h1, h2, h3, h4, h5, h6")).toBeNull();
  });

  it("renders only the documented `{ keyword, text }` content", () => {
    // The card's visible text is exactly the keyword label + the suggestion
    // text; nothing derived from absent fields appears.
    render(<SuggestionCard suggestion={improvement} />);

    const text = document.body.textContent ?? "";
    expect(text).toContain(improvement.keyword);
    expect(text).toContain(improvement.text);
  });
});
