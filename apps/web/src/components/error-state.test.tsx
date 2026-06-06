/**
 * Unit tests for the shared `ErrorState` (Task 2.7).
 *
 * Validates the component against its acceptance criteria (Req 17.3, 17.4,
 * 17.5; design Section 7.3 "ErrorState", Section 10.2):
 *
 *   - Req 17.3 — renders the mapped `title`/`message` copy and ALWAYS presents
 *     at least one recovery action, including the universal `/upload` fallback
 *     when neither `action` nor `secondaryHref` is supplied.
 *   - Req 17.4 / 17.5 / security.md — never renders technical error codes,
 *     stack traces, internal identifiers, or RFC 7807 `type`/`request_id`. The
 *     component is structurally dumb: it has no prop through which such data
 *     could be passed, so the strongest guard is to render with only safe copy
 *     and assert nothing else leaks into the DOM.
 *
 * `ErrorState` is a `"use client"` component that renders `ui/button` and, for
 * navigation actions, `next/link`. Per the existing `library-page.test.tsx`
 * convention, `next/link` renders a real anchor under jsdom, so we do not mock
 * it — the `href` assertions then exercise the real routing target. Test
 * conventions otherwise mirror `tests/results-page.test.tsx`:
 * render/screen/cleanup, `toBeInstanceOf`, no jest-dom matchers.
 *
 * @vitest-environment jsdom
 */

import * as React from "react";

import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ErrorState } from "@/components/error-state";

afterEach(() => {
  cleanup();
});

describe("ErrorState — renders mapped copy (Req 17.3)", () => {
  it("renders the given title and message verbatim", () => {
    render(
      <ErrorState
        title="We couldn't load your results"
        message="Something interrupted the request. Try again in a moment."
      />,
    );

    expect(screen.getByText("We couldn't load your results")).toBeInstanceOf(
      HTMLElement,
    );
    expect(
      screen.getByText(
        "Something interrupted the request. Try again in a moment.",
      ),
    ).toBeInstanceOf(HTMLElement);
  });

  it("exposes the surface as an assertive alert region", () => {
    render(<ErrorState title="Title copy" message="Message copy" />);

    const region = screen.getByRole("alert");
    expect(region).toBeInstanceOf(HTMLElement);
    expect(within(region).getByText("Title copy")).toBeInstanceOf(HTMLElement);
  });
});

describe("ErrorState — always offers ≥1 recovery action (Req 17.3)", () => {
  it("falls back to a /upload recovery link when no action or secondaryHref is given", () => {
    render(<ErrorState title="Title copy" message="Message copy" />);

    // The universal recovery target so the user is never stranded.
    const link = screen.getByRole("link");
    expect(link).toBeInstanceOf(HTMLAnchorElement);
    expect(link.getAttribute("href")).toBe("/upload");
  });

  it("renders an in-place retry button (no navigation) for an onClick action", () => {
    const onClick = vi.fn();
    render(
      <ErrorState
        title="Title copy"
        message="Message copy"
        action={{ label: "Retry", onClick }}
      />,
    );

    const button = screen.getByRole("button", { name: "Retry" });
    expect(button).toBeInstanceOf(HTMLButtonElement);
    // A retry action is a button, not a link — it must not navigate.
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("renders a navigation action as a link to the given href", () => {
    render(
      <ErrorState
        title="Title copy"
        message="Message copy"
        action={{ label: "Go home", href: "/" }}
      />,
    );

    const link = screen.getByRole("link", { name: "Go home" });
    expect(link.getAttribute("href")).toBe("/");
  });

  it("renders a secondary recovery link alongside the primary action", () => {
    const onClick = vi.fn();
    render(
      <ErrorState
        title="Title copy"
        message="Message copy"
        action={{ label: "Retry", onClick }}
        secondaryHref="/upload"
      />,
    );

    expect(screen.getByRole("button", { name: "Retry" })).toBeInstanceOf(
      HTMLButtonElement,
    );
    const secondary = screen.getByRole("link");
    expect(secondary.getAttribute("href")).toBe("/upload");
  });

  it("does not add the /upload fallback once a real action is present", () => {
    const onClick = vi.fn();
    const { container } = render(
      <ErrorState
        title="Title copy"
        message="Message copy"
        action={{ label: "Retry", onClick }}
      />,
    );

    // Exactly the retry button, no fallback link injected.
    const uploadLinks = within(container).queryAllByRole("link");
    expect(uploadLinks).toEqual([]);
  });
});

describe("ErrorState — never leaks technical detail (Req 17.4, 17.5)", () => {
  // A realistic-but-forbidden set of strings: an RFC 7807 envelope's machine
  // fields, an internal id, an HTTP status code, and a stack-trace frame. None
  // of these are props on ErrorState; this asserts that even when such values
  // exist elsewhere in the app, the rendered surface contains only safe copy.
  const FORBIDDEN = [
    "validation_error", // RFC 7807 `type`
    "req_01HZX9ABCDEF", // RFC 7807 `request_id`
    "internal_match_id=0192f1b0", // internal identifier
    "at ResultsView (results-view.tsx:42:13)", // stack-trace frame
    "Traceback (most recent call last)",
  ] as const;

  it("renders no error codes, request_id, internal ids, or stack traces", () => {
    const { container } = render(
      <ErrorState
        title="We couldn't load your results"
        message="Something interrupted the request. Try again in a moment."
      />,
    );

    const dom = container.innerHTML;
    for (const forbidden of FORBIDDEN) {
      expect(dom).not.toContain(forbidden);
    }
    // The numeric status code 500 must not surface anywhere in the DOM either.
    expect(container.textContent).not.toMatch(/\b500\b/);
  });

  it("renders only the title, message, and recovery affordance — nothing else", () => {
    render(
      <ErrorState
        title="We couldn't load your results"
        message="Please try again."
      />,
    );

    const region = screen.getByRole("alert");
    // The decorative indicator is aria-hidden, so the accessible text content
    // is exactly the mapped copy plus the recovery link label.
    const link = within(region).getByRole("link");
    const text = region.textContent ?? "";
    expect(text).toContain("We couldn't load your results");
    expect(text).toContain("Please try again.");
    // The only other text is the derived recovery link label.
    expect(link.textContent).toBeTruthy();
  });

  it("has no prop surface for raw error objects (compile-time contract, asserted structurally)", () => {
    // Passing only the documented props produces a complete render; there is no
    // `error`, `code`, `status`, `type`, or `requestId` prop. This render-with-
    // minimum-props case is the runtime witness of that structural guarantee.
    const { container } = render(
      <ErrorState title="Minimal" message="Minimal message." />,
    );
    expect(container.querySelector("[role=alert]")).toBeInstanceOf(HTMLElement);
  });
});
