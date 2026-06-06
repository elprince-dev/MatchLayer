/**
 * Unit tests for the shared `SkeletonLoader` (Task 2.7).
 *
 * Validates that each variant renders the expected content-mirroring shape
 * (Req 17.1, 17.2; design Section 7.3 "SkeletonLoader", Testing Strategy):
 *
 *   - `results` → a circular gauge placeholder + a qualitative-label bar, two
 *     breakdown-bar placeholders, and matched/missing keyword pill rows —
 *     mirroring the ATS Results success layout.
 *   - `upload` → a drop-zone block, a job-description field (label + textarea +
 *     character-count), and a submit-button block — mirroring the Upload page.
 *
 * Assertions target the structural contract the component deliberately exposes:
 * the `data-slot="skeleton-loader"` + `data-variant` attributes on the root, the
 * `role="status"` / `aria-busy` loading semantics, and the distinctive shaped
 * descendant `[data-slot=skeleton]` blocks (gauge circle, pill rows, drop-zone,
 * textarea). These are the same class/shape names the design enumerates, so the
 * utility names ARE the contract (jsdom does not apply Tailwind CSS) — mirroring
 * the approach in `tests/auth-card.test.tsx`.
 *
 * @vitest-environment jsdom
 */

import * as React from "react";

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { SkeletonLoader } from "@/components/skeleton-loader";

afterEach(() => {
  cleanup();
});

/** All descendant skeleton blocks within a rendered loader. */
function skeletons(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>("[data-slot=skeleton]"));
}

describe("SkeletonLoader — loading semantics (Req 17.2)", () => {
  it("exposes a busy status region labelled for assistive tech, for both variants", () => {
    for (const variant of ["results", "upload"] as const) {
      const { unmount } = render(<SkeletonLoader variant={variant} />);

      const status = screen.getByRole("status");
      expect(status.getAttribute("data-slot")).toBe("skeleton-loader");
      expect(status.getAttribute("data-variant")).toBe(variant);
      expect(status.getAttribute("aria-busy")).toBe("true");
      // An sr-only "Loading…" sentence carries the state to screen readers.
      expect(screen.getByText("Loading…")).toBeInstanceOf(HTMLElement);

      unmount();
    }
  });

  it("re-points the shimmer cadence to 1.5s on the root (Req 17.1)", () => {
    const { container } = render(<SkeletonLoader variant="results" />);
    const root = container.querySelector<HTMLElement>(
      "[data-slot=skeleton-loader]",
    );
    expect(root).toBeInstanceOf(HTMLElement);
    // The descendant-variant utility that overrides Tailwind's 2s animate-pulse.
    expect(root?.className).toContain("[animation-duration:1.5s]");
  });
});

describe("SkeletonLoader — results shape (Req 10.5, 17.2)", () => {
  it("renders the gauge circle, label bar, two breakdown bars, and keyword pill rows", () => {
    const { container } = render(<SkeletonLoader variant="results" />);
    const root = container.querySelector<HTMLElement>(
      "[data-slot=skeleton-loader]",
    )!;

    // Gauge: the only circular (`rounded-full`) placeholder, sized ≥ mobile min.
    const circles = root.querySelectorAll("[data-slot=skeleton].rounded-full");
    expect(circles).toHaveLength(1);

    // Pill-shaped placeholders: the qualitative-label bar, the two breakdown
    // bars, and every keyword pill all use `rounded-pill`.
    const pills = root.querySelectorAll("[data-slot=skeleton].rounded-pill");
    // 1 label + 2 breakdown bars + 8 matched pills + 4 missing pills = 15.
    expect(pills.length).toBe(15);

    // Matched (8) and missing (4) keyword pills are the distinctive `h-7` tags.
    const keywordPills = root.querySelectorAll(
      "[data-slot=skeleton].h-7.rounded-pill",
    );
    expect(keywordPills.length).toBe(12);

    // The results shape carries the most placeholders of the two variants.
    expect(skeletons(root).length).toBe(20);
  });

  it("does not render the upload-only drop-zone / textarea shapes", () => {
    const { container } = render(<SkeletonLoader variant="results" />);
    const root = container.querySelector<HTMLElement>(
      "[data-slot=skeleton-loader]",
    )!;

    // `rounded-hero` is the upload drop-zone; `h-32` is the JD textarea.
    expect(root.querySelectorAll(".rounded-hero")).toHaveLength(0);
    expect(root.querySelectorAll("[data-slot=skeleton].h-32")).toHaveLength(0);
  });
});

describe("SkeletonLoader — upload shape (Req 17.2)", () => {
  it("renders the drop-zone, the JD field (label + textarea + count), and a submit block", () => {
    const { container } = render(<SkeletonLoader variant="upload" />);
    const root = container.querySelector<HTMLElement>(
      "[data-slot=skeleton-loader]",
    )!;

    // Drop-zone: the single `rounded-hero` block, tall enough to read as a zone.
    const dropZone = root.querySelectorAll(
      "[data-slot=skeleton].rounded-hero.h-48",
    );
    expect(dropZone).toHaveLength(1);

    // Job-description textarea: the `h-32` `rounded-card` field block.
    const textarea = root.querySelectorAll(
      "[data-slot=skeleton].h-32.rounded-card",
    );
    expect(textarea).toHaveLength(1);

    // Submit button: an ≥44px (`h-11`) full-width block.
    const submit = root.querySelectorAll("[data-slot=skeleton].h-11");
    expect(submit).toHaveLength(1);

    // Upload mirrors a single-column form: far fewer placeholders than results.
    expect(skeletons(root).length).toBe(7);
  });

  it("does not render the results-only gauge / keyword pill shapes", () => {
    const { container } = render(<SkeletonLoader variant="upload" />);
    const root = container.querySelector<HTMLElement>(
      "[data-slot=skeleton-loader]",
    )!;

    // No circular gauge and no `rounded-pill` keyword/label/bar placeholders.
    expect(root.querySelectorAll(".rounded-full")).toHaveLength(0);
    expect(
      root.querySelectorAll("[data-slot=skeleton].rounded-pill"),
    ).toHaveLength(0);
  });
});
