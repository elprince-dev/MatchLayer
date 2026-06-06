/**
 * Unit tests for `GlassNav` (Task 8.8).
 *
 * Validates the Landing_Page navigation against its acceptance criteria
 * (Req 6.2, 6.5, 6.6; design Section 7.3 "GlassNav", Section 8.2):
 *
 *   - Req 6.2 — the nav exposes ONLY the MVP targets: the in-page section
 *     anchors (Features / How It Works / About), "Sign in" → `/login`, "Get
 *     started" → `/register`, and the brand mark → `/`. There is **no
 *     "Pricing" link** and no link to any capability that does not exist in the
 *     Phase 1 MVP — asserted both by the absence of a "Pricing" link and by an
 *     allowlist over every rendered anchor's `href`.
 *   - Req 6.5 / 6.6 — over the hero the bar is transparent; once scrolled past
 *     the bottom edge of the hero it swaps to the frosted glass surface
 *     (`bg-bg-glass/65` + `backdrop-blur-md`), and back when scrolled above.
 *
 * `GlassNav` derives its `scrolled` flag through `useSyncExternalStore`: the
 * store subscribes to `scroll`/`resize` (coalesced into a `requestAnimationFrame`
 * callback) and the snapshot reads the live DOM — `window.scrollY` vs the
 * `#hero` element's bottom, falling back to `window.innerHeight` (768 in jsdom)
 * when `#hero` is absent, as it is in this isolated render. To exercise the
 * glass toggle we stub `requestAnimationFrame` to run synchronously, drive
 * `window.scrollY` past the threshold, and dispatch a `scroll` event inside
 * `act`, then assert the glass surface classes appear.
 *
 * `next/link` renders a real anchor under jsdom (per `library-page.test.tsx` /
 * `error-state.test.tsx`), so the `href` assertions exercise the real routing
 * targets and no router mock is needed. The nested {@link ThemeToggle} reads
 * `next-themes`' `useTheme`, which returns a safe default context without a
 * provider, so it renders inert here. Conventions otherwise mirror the
 * co-located results tests: render/screen/cleanup, `toBeInstanceOf`, no jest-dom
 * matchers.
 *
 * @vitest-environment jsdom
 */

import * as React from "react";

import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { GlassNav } from "@/components/landing/glass-nav";

/**
 * The complete set of `href`s the MVP nav is permitted to emit (Req 6.2): the
 * brand mark, the three in-page anchors, and the two auth surfaces. Any anchor
 * outside this set would be a non-MVP link (e.g. Pricing, a dashboard, etc.).
 */
const ALLOWED_HREFS = new Set<string>([
  "/",
  "#features",
  "#how-it-works",
  "#about",
  "/login",
  "/register",
]);

/** Mutable scroll position backing the `window.scrollY` getter for a test. */
let scrollYValue = 0;

beforeEach(() => {
  scrollYValue = 0;
  // A controllable `window.scrollY` (jsdom's default is a fixed 0). The store
  // snapshot reads this live, so re-pointing it then firing a scroll event is
  // what flips `scrolled`.
  Object.defineProperty(window, "scrollY", {
    configurable: true,
    get: () => scrollYValue,
  });
  // Run the store's rAF-coalesced listener synchronously so a dispatched scroll
  // event notifies the external store within the same `act` tick (jsdom has no
  // real frameloop).
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback): number => {
    cb(0);
    return 0;
  });
  vi.stubGlobal("cancelAnimationFrame", () => {});
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("GlassNav — MVP link surface, no Pricing / non-MVP links (Req 6.2)", () => {
  it("renders no 'Pricing' link", () => {
    render(<GlassNav />);
    expect(screen.queryByRole("link", { name: /pricing/i })).toBeNull();
  });

  it("emits only the allowlisted MVP hrefs (no link to non-existent functionality)", () => {
    render(<GlassNav />);

    const links = screen.getAllByRole("link");
    expect(links.length).toBeGreaterThan(0);

    for (const link of links) {
      expect(ALLOWED_HREFS.has(link.getAttribute("href") ?? "")).toBe(true);
    }
  });

  it("exposes the three in-page section anchors", () => {
    render(<GlassNav />);

    // Desktop + mobile clusters both render the anchor set, so each appears
    // more than once; assert at least one of each resolves to its anchor.
    for (const [name, href] of [
      [/features/i, "#features"],
      [/how it works/i, "#how-it-works"],
      [/about/i, "#about"],
    ] as const) {
      const anchors = screen.getAllByRole("link", { name });
      expect(anchors.some((a) => a.getAttribute("href") === href)).toBe(true);
    }
  });

  it("wires 'Sign in' → /login and 'Get started' → /register only", () => {
    render(<GlassNav />);

    const signIn = screen.getAllByRole("link", { name: /sign in/i });
    expect(signIn.every((a) => a.getAttribute("href") === "/login")).toBe(true);

    const getStarted = screen.getAllByRole("link", { name: /get started/i });
    expect(
      getStarted.every((a) => a.getAttribute("href") === "/register"),
    ).toBe(true);
  });
});

describe("GlassNav — transparent-over-hero → glass-when-scrolled (Req 6.5, 6.6)", () => {
  /** The fixed top bar is the component's root `<header>`. */
  function header(container: HTMLElement): HTMLElement {
    const el = container.querySelector("header");
    expect(el).toBeInstanceOf(HTMLElement);
    return el as HTMLElement;
  }

  it("is transparent at the top of the page (no glass surface)", () => {
    const { container } = render(<GlassNav />);

    const cls = header(container).className;
    expect(cls).toContain("bg-transparent");
    expect(cls).not.toContain("bg-bg-glass/65");
    expect(cls).not.toContain("backdrop-blur-md");
  });

  it("swaps to the glass surface once scrolled past the hero bottom", () => {
    const { container } = render(<GlassNav />);

    // No `#hero` element here ⇒ threshold falls back to window.innerHeight
    // (768 in jsdom). Drive scrollY past it and notify the store.
    act(() => {
      scrollYValue = 1000;
      window.dispatchEvent(new Event("scroll"));
    });

    const cls = header(container).className;
    expect(cls).toContain("bg-bg-glass/65");
    expect(cls).toContain("backdrop-blur-md");
    expect(cls).not.toContain("bg-transparent");
  });

  it("returns to transparent when scrolled back above the hero bottom", () => {
    const { container } = render(<GlassNav />);

    act(() => {
      scrollYValue = 1000;
      window.dispatchEvent(new Event("scroll"));
    });
    expect(header(container).className).toContain("bg-bg-glass/65");

    act(() => {
      scrollYValue = 0;
      window.dispatchEvent(new Event("scroll"));
    });
    expect(header(container).className).toContain("bg-transparent");
  });
});

describe("GlassNav — mobile hamburger accessibility (Req 6.4)", () => {
  it("exposes aria-expanded / aria-controls on the menu toggle", () => {
    render(<GlassNav />);

    const toggle = screen.getByRole("button", { name: /open menu/i });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(toggle.getAttribute("aria-controls")).toBe("glass-nav-mobile-menu");

    act(() => {
      toggle.click();
    });

    const open = screen.getByRole("button", { name: /close menu/i });
    expect(open.getAttribute("aria-expanded")).toBe("true");
  });
});
