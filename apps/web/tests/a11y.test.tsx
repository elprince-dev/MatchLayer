/**
 * axe-core + semantic-structure accessibility gate for all four MVP screens,
 * in BOTH themes (frontend-redesign Task 9.3; Req 14.6, 16.10, 16.11, 17.8,
 * 19.1–19.7; design Section 10).
 *
 * This is the consolidated Section 10 automated accessibility gate. It renders
 * each of the four screens — **ATS Results, Upload, Auth (login + register),
 * and Landing** — in **Dark_Mode and Light_Mode**, and for each asserts:
 *
 *   - **axe-core: no violations** for the WCAG 2 A/AA structural rule pack —
 *     landmarks, roles, names, ARIA, heading semantics, list structure, etc.
 *     (Req 19.5, 16.10, 16.11).
 *   - **exactly one `<h1>`** and **sequential heading levels** (no skipped
 *     level) within the rendered subtree (Req 19.5; design 10.4).
 *   - **landmark roles** present for the screen (Req 19.5; design 10.4).
 *   - **icon-only buttons carry an `aria-label`** (theme toggle, remove-file,
 *     hamburger) (Req 19.7; design 10.4).
 *   - **form errors announce via an `aria-live` region within 1s** — the live
 *     region exists in the DOM and a set error is announced as a content
 *     mutation, asserted to surface well under the 1s budget (Req 19.4; design
 *     10.4).
 *   - **2px branded focus ring** present on focusable elements
 *     (`focus-visible:ring-2` + `ring-brand`) (Req 19.2; design 10.3).
 *   - **keyboard tab order follows reading order** — the natural DOM order
 *     places focusables in reading order with no positive `tabindex` reordering
 *     (Req 19.3; design 10.3).
 *   - the **mandated light-mode pill mitigation** — KeywordTag uses a
 *     tinted-fill + full-token `border` + primary `text` label, NOT colored
 *     text (design 10.2 #1). (The numeric contrast backing this is in
 *     `contrast.test.ts`.)
 *
 * ## Why `color-contrast` is disabled in the axe runs (the repo precedent)
 * jsdom never applies Tailwind's stylesheet, so axe cannot read real computed
 * colors here — running `color-contrast` would test the absence of styles, not
 * the design tokens. `tests/auth-card.test.tsx` disables it for exactly this
 * reason; this file follows that precedent and verifies contrast separately as
 * pure WCAG math over the `globals.css` token values in `contrast.test.ts`.
 *
 * ## Theme toggling
 * The design tokens switch purely on the `.dark` class
 * (`globals.css` → `@custom-variant dark (&:where(.dark, .dark *))`), so a
 * theme is selected by toggling `class="dark"` on the rendered wrapper +
 * `document.documentElement`. There is no per-component theme branching to
 * exercise — both themes share identical markup, so the structural axe/heading
 * assertions hold per theme by construction; running both themes guards against
 * any future theme-conditional DOM and satisfies the Req 14.6 "both themes"
 * mandate for the flagship.
 *
 * ## MANUAL CHECKLIST (required for full WCAG validation — design 10.6)
 * Automated checks (axe structure, the `contrast.test.ts` math, and these
 * keyboard/aria assertions) catch most violations, but full WCAG validation
 * REQUIRES manual testing with assistive technologies. The following must be
 * performed and **documented in the PR before the accessibility gate closes**:
 *
 *   1. **Screen-reader pass** — VoiceOver (macOS/Safari) AND NVDA
 *      (Windows/Firefox): each screen's headings, landmarks, form labels,
 *      live-region error announcements, and the icon-only button labels read
 *      correctly.
 *   2. **Keyboard-only task completion** — drive Landing → Register → Upload →
 *      Results with the keyboard alone: every interactive element is reachable
 *      and operable, the 2px branded focus ring is visibly rendered, the skip
 *      link works, and tab order follows the visual reading order.
 *   3. **OS reduced-motion** — enable the OS "reduce motion" setting and
 *      confirm all entrance/score/scroll animations render in their final state
 *      instantly (loading/progress indicators and focus-ring transitions are
 *      the only motion permitted).
 *   4. **Zoom-to-200% reflow** — at 200% browser zoom no content is lost or
 *      clipped and no horizontal scrolling is introduced (WCAG 1.4.10 Reflow).
 *
 * Conventions mirror the rest of `apps/web/tests`: `@testing-library/react`
 * render/cleanup, `vi.mock`/`vi.stubGlobal`, `afterEach(cleanup)`,
 * `toBeInstanceOf` assertions, no jest-dom matchers. The default Vitest `node`
 * environment is overridden to jsdom for this DOM-rendering file via the pragma
 * below.
 *
 * @vitest-environment jsdom
 */

import * as React from "react";

import axe from "axe-core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// ---------------------------------------------------------------------------
// Module mocks (declared before importing the screens under test)
// ---------------------------------------------------------------------------

// next/navigation: Upload + Auth pages call useRouter().push and Login reads
// useSearchParams(). Provide inert stubs so the pages render under jsdom.
const pushMock = vi.fn();
const searchParamsStub = new URLSearchParams();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock, replace: vi.fn() }),
  useSearchParams: () => searchParamsStub,
  usePathname: () => "/",
}));

// @/lib/api: Upload + ResultsView fetch through apiFetch. A vi.fn lets each
// screen render its initial state deterministically (Upload idle; Results
// loading via a never-settling promise where needed).
vi.mock("@/lib/api", () => ({
  apiFetch: vi.fn(),
  apiBaseUrl: "",
}));

import { apiFetch } from "@/lib/api";

import { ResultsView } from "@/components/results/results-view";
import { matchStrong } from "@/components/results/__fixtures__/match-fixtures";
import { ScoreGauge } from "@/components/results/score-gauge";
import { ScoreBreakdownCard } from "@/components/results/score-breakdown-card";
import { KeywordSection } from "@/components/results/keyword-section";
import { KeywordTag } from "@/components/results/keyword-tag";

import { GlassNav } from "@/components/landing/glass-nav";
import { Hero } from "@/components/landing/hero";
import { HowItWorks } from "@/components/landing/how-it-works";
import { TrustSignals, About } from "@/components/landing/trust-signals";
import { FinalCTA } from "@/components/landing/final-cta";
import { FeatureCard } from "@/components/landing/feature-card";

import { ThemeToggle } from "@/components/theme-toggle";
import { ThemeProvider } from "@/components/theme-provider";

import UploadPage from "@/app/(app)/upload/page";
import LoginPage from "@/app/(auth)/login/page";
import RegisterPage from "@/app/(auth)/register/page";

import { ScanSearch, Sparkles } from "lucide-react";

const apiFetchMock = vi.mocked(apiFetch);

// ---------------------------------------------------------------------------
// Test setup
// ---------------------------------------------------------------------------

/**
 * Stub window.matchMedia so framer-motion's `useReducedMotion` resolves to the
 * given preference. We default to reduced motion (`true`) so animated screens
 * (ScoreGauge count-up, Hero demo, scroll reveals) render in their final state
 * synchronously — there are no animation frames to await before running axe.
 */
function stubMatchMedia(matches: boolean): void {
  vi.stubGlobal(
    "matchMedia",
    vi.fn().mockImplementation((query: string) => ({
      matches,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  );
}

beforeEach(() => {
  stubMatchMedia(true);
  apiFetchMock.mockReset();
  pushMock.mockClear();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  // Ensure no theme class leaks across tests.
  document.documentElement.classList.remove("dark");
});

// ---------------------------------------------------------------------------
// Theme + axe helpers
// ---------------------------------------------------------------------------

type Theme = "dark" | "light";

/**
 * Wrap a screen in the theme context that selects Light_Mode / Dark_Mode.
 *
 * Tokens switch purely on the `.dark` class, so we (a) toggle it on a wrapper
 * `<div>` (the scope axe scans) and (b) mirror it on `document.documentElement`
 * — some tokens/utilities resolve against the document root, and a few client
 * components (e.g. the GlassNav glass surface) read `dark:` variants. The
 * wrapper also seeds `next-themes` so `ThemeToggle` resolves a concrete theme.
 */
function ThemeScope({
  theme,
  children,
}: {
  theme: Theme;
  children: React.ReactNode;
}): React.JSX.Element {
  React.useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark");
    return () => document.documentElement.classList.remove("dark");
  }, [theme]);

  return (
    <ThemeProvider forcedTheme={theme}>
      <div className={theme === "dark" ? "dark" : undefined} data-theme={theme}>
        {children}
      </div>
    </ThemeProvider>
  );
}

/**
 * Run axe-core against `container` for the WCAG 2 A + 2 AA structural rule pack,
 * with `color-contrast` disabled (jsdom applies no Tailwind CSS — see file
 * header; contrast is verified numerically in `contrast.test.ts`).
 */
async function runAxe(container: Element): Promise<axe.AxeResults> {
  return axe.run(container, {
    runOnly: { type: "tag", values: ["wcag2a", "wcag2aa"] },
    rules: { "color-contrast": { enabled: false } },
  });
}

/** Format axe violations into a readable assertion message. */
function formatViolations(results: axe.AxeResults): string {
  return results.violations
    .map((v) => `${v.id}: ${v.help} (${v.nodes.length} node(s))`)
    .join("\n");
}

// ---------------------------------------------------------------------------
// Shared structural assertions reused across every screen × theme
// ---------------------------------------------------------------------------

/** The heading element tags found in `container`, in document order. */
function headingLevels(container: HTMLElement): number[] {
  return Array.from(container.querySelectorAll("h1, h2, h3, h4, h5, h6")).map(
    (el) => Number(el.tagName.slice(1)),
  );
}

/**
 * Assert the screen has exactly one `<h1>` and that heading levels never skip
 * (each step down increases by at most 1) — design 10.4 / Req 19.5.
 */
function expectSequentialHeadings(container: HTMLElement): void {
  const levels = headingLevels(container);
  const h1Count = levels.filter((l) => l === 1).length;
  expect(h1Count).toBe(1);

  // The first heading is the h1; thereafter no jump of more than one level.
  let previous = levels[0] ?? 1;
  for (const level of levels) {
    expect(level - previous).toBeLessThanOrEqual(1);
    previous = level;
  }
}

/**
 * Assert every icon-only button has a non-empty accessible name (Req 19.7).
 *
 * An "icon-only" button is one whose visible text content is empty (only an
 * `aria-hidden` SVG / `sr-only` text inside). Its accessible name must come
 * from `aria-label` (or an `sr-only` span). We accept either: an `aria-label`
 * attribute, or a non-empty accessible name surfaced by Testing Library's
 * name computation (which includes `sr-only` text).
 */
function expectIconButtonsLabeled(container: HTMLElement): void {
  const buttons = Array.from(container.querySelectorAll("button"));
  for (const button of buttons) {
    const visibleText = (button.textContent ?? "").trim();
    const hasVisibleText = visibleText.length > 0;
    const ariaLabel = button.getAttribute("aria-label");
    // A button must have SOME accessible name. Icon-only buttons (no visible
    // text, or only sr-only/aria-hidden content) must carry an aria-label.
    const hasName =
      hasVisibleText || (ariaLabel !== null && ariaLabel.length > 0);
    expect(hasName).toBe(true);
  }
}

/**
 * Assert no element uses a positive `tabindex`, so keyboard tab order follows
 * the DOM (reading) order rather than being manually reshuffled (Req 19.3,
 * design 10.3). `tabindex="-1"` (programmatic focus targets like `<main>`) and
 * `tabindex="0"` (natural order) are allowed; any value > 0 reorders the tab
 * sequence away from reading order and is disallowed.
 */
function expectNoPositiveTabindex(container: HTMLElement): void {
  const tabbables = Array.from(container.querySelectorAll("[tabindex]"));
  for (const el of tabbables) {
    const value = Number(el.getAttribute("tabindex"));
    expect(value).toBeLessThanOrEqual(0);
  }
}

/**
 * Assert at least one focusable element carries the 2px branded focus ring
 * utility set (`focus-visible:ring-2` + `ring-brand`), so focus is always
 * visibly indicated with the brand color and never `outline:none` without a
 * replacement (Req 19.2, design 10.3). The skip link uses the `focus:` variant
 * of the same ring, so both prefixes are accepted.
 */
function expectBrandedFocusRing(container: HTMLElement): void {
  const focusables = Array.from(
    container.querySelectorAll(
      "a[href], button, input, textarea, select, [tabindex]",
    ),
  );
  const ringed = focusables.filter((el) => {
    const cls = el.getAttribute("class") ?? "";
    const hasRing2 =
      cls.includes("focus-visible:ring-2") || cls.includes("focus:ring-2");
    const hasBrand = cls.includes("ring-brand");
    return hasRing2 && hasBrand;
  });
  expect(ringed.length).toBeGreaterThan(0);
}

// ---------------------------------------------------------------------------
// Per-screen render helpers
// ---------------------------------------------------------------------------

const MATCH_ID = "0192f1b0-1a2b-7c3d-8e4f-5a6b7c8d9e10";

function jsonResponse(status: number, body?: unknown): Response {
  return new Response(body !== undefined ? JSON.stringify(body) : null, {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/**
 * Render the ATS Results screen (flagship) in its SUCCESS state, inside its own
 * `QueryClient`. The `(app)` page shell is an async Server Component, so — as in
 * `results-page.test.tsx` — we render the `ResultsView` island that owns the
 * data + state machine, wrapped in a `<main>` landmark to mirror the shell.
 */
function renderResults(theme: Theme): ReturnType<typeof render> {
  apiFetchMock.mockResolvedValue(jsonResponse(200, matchStrong));
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <ThemeScope theme={theme}>
      <QueryClientProvider client={queryClient}>
        <main>
          <ResultsView id={MATCH_ID} />
        </main>
      </QueryClientProvider>
    </ThemeScope>,
  );
}

/**
 * Render the Upload screen. The page composes the `UploadWidget` + JD textarea
 * + submit; it is wrapped in a `<main>` to provide the landmark the `(app)`
 * shell normally supplies. apiFetch stays unresolved (idle) — the screen's
 * initial render is the drop-zone + form, which is what we audit.
 */
function renderUpload(theme: Theme): ReturnType<typeof render> {
  apiFetchMock.mockReturnValue(new Promise<Response>(() => {}));
  return render(
    <ThemeScope theme={theme}>
      <main>
        <UploadPage />
      </main>
    </ThemeScope>,
  );
}

/**
 * Render an Auth screen (login or register). Auth pages are the form bodies;
 * the real `(auth)` layout supplies the `<main id="main">` landmark and brand
 * wordmark, so we wrap them the same way here so the landmark assertions hold.
 */
function renderAuth(
  Page: () => React.JSX.Element,
  theme: Theme,
): ReturnType<typeof render> {
  return render(
    <ThemeScope theme={theme}>
      <main id="main">
        <Page />
      </main>
    </ThemeScope>,
  );
}

/**
 * Render the Landing screen. The marketing `page.tsx` default export is an
 * async Server Component exporting `metadata`, which is impractical to render
 * directly under jsdom — so we compose the same section islands the page
 * assembles, in the same order and with the same `header → main → footer`
 * landmark structure and single `<h1>` (in the Hero). This mirrors the
 * production composition (see `(marketing)/page.tsx`) while staying renderable.
 */
function renderLanding(theme: Theme): ReturnType<typeof render> {
  return render(
    <ThemeScope theme={theme}>
      <GlassNav />
      <main id="main" tabIndex={-1}>
        <Hero />
        <section aria-labelledby="features-heading" className="py-16">
          <div>
            <h2 id="features-heading">
              Everything you need to read your resume like an ATS
            </h2>
            <div>
              <FeatureCard
                icon={ScanSearch}
                title="Transparent ATS score"
                description="See how an ATS reads your resume against a job — keyword and TF-IDF based."
              />
              <FeatureCard
                icon={Sparkles}
                title="Semantic analysis"
                description="Deeper meaning-based matching beyond keywords is on the roadmap."
                badge="Coming soon"
              />
            </div>
          </div>
        </section>
        <HowItWorks />
        <TrustSignals />
        <About />
        <FinalCTA />
      </main>
      <footer>
        <span>MatchLayer</span>
      </footer>
    </ThemeScope>,
  );
}

// ---------------------------------------------------------------------------
// ATS Results (flagship) — Req 14.6 "both themes"
// ---------------------------------------------------------------------------

describe.each(["dark", "light"] as const)(
  "ATS Results (flagship) accessibility — %s mode (Req 14.6, 19.5, 19.7)",
  (theme) => {
    it("passes axe-core structural checks, has one h1 + sequential headings, and a live region", async () => {
      const { container } = renderResults(theme);

      // Wait for the success content (the gauge sr-only sentence) to resolve.
      await waitFor(() => {
        expect(screen.getByText("Match score: 85 out of 100.")).toBeInstanceOf(
          HTMLElement,
        );
      });

      const results = await runAxe(container);
      expect(results.violations, formatViolations(results)).toEqual([]);

      expectSequentialHeadings(container);

      // The polite results-completion live region is present (Req 19.4).
      const live = container.querySelector(
        '[role="status"][aria-live="polite"]',
      );
      expect(live).toBeInstanceOf(HTMLElement);

      // Branded focus ring + reading-order tab order (Req 19.2, 19.3).
      expectBrandedFocusRing(container);
      expectNoPositiveTabindex(container);
      expectIconButtonsLabeled(container);
    });
  },
);

// ---------------------------------------------------------------------------
// Upload — both themes
// ---------------------------------------------------------------------------

describe.each(["dark", "light"] as const)(
  "Upload accessibility — %s mode (Req 16.10, 19.5, 19.7)",
  (theme) => {
    it("passes axe-core, has one h1 + sequential headings, labeled icon buttons, and an aria-live error region", () => {
      const { container } = renderUpload(theme);

      expectSequentialHeadings(container);

      // The match-creation error region (FormError) is an always-mounted polite
      // live region above the submit button (Req 19.4).
      const live = container.querySelector('[aria-live="polite"]');
      expect(live).toBeInstanceOf(HTMLElement);

      expectBrandedFocusRing(container);
      expectNoPositiveTabindex(container);
      expectIconButtonsLabeled(container);

      return runAxe(container).then((results) => {
        expect(results.violations, formatViolations(results)).toEqual([]);
      });
    });
  },
);

// ---------------------------------------------------------------------------
// Auth (login + register) — both themes
// ---------------------------------------------------------------------------

describe.each(["dark", "light"] as const)(
  "Auth — login accessibility — %s mode (Req 19.4, 19.5)",
  (theme) => {
    it("passes axe-core, has one h1, labeled fields, and an aria-live error region", async () => {
      const { container } = renderAuth(LoginPage, theme);

      // The Suspense fallback resolves to the real form synchronously here, but
      // wait for a stable field to be safe.
      await waitFor(() => {
        expect(screen.getByLabelText(/email/i)).toBeInstanceOf(
          HTMLInputElement,
        );
      });

      expectSequentialHeadings(container);

      // The non-enumerable banner is an always-mounted polite live region.
      const live = container.querySelector('[aria-live="polite"]');
      expect(live).toBeInstanceOf(HTMLElement);

      expectBrandedFocusRing(container);
      expectNoPositiveTabindex(container);
      expectIconButtonsLabeled(container);

      const results = await runAxe(container);
      expect(results.violations, formatViolations(results)).toEqual([]);
    });
  },
);

describe.each(["dark", "light"] as const)(
  "Auth — register accessibility — %s mode (Req 19.4, 19.5)",
  (theme) => {
    it("passes axe-core, has one h1, labeled fields, and an aria-live error region", async () => {
      const { container } = renderAuth(RegisterPage, theme);

      await waitFor(() => {
        expect(screen.getByLabelText("Email")).toBeInstanceOf(HTMLInputElement);
      });

      expectSequentialHeadings(container);

      const live = container.querySelector('[aria-live="polite"]');
      expect(live).toBeInstanceOf(HTMLElement);

      expectBrandedFocusRing(container);
      expectNoPositiveTabindex(container);
      expectIconButtonsLabeled(container);

      const results = await runAxe(container);
      expect(results.violations, formatViolations(results)).toEqual([]);
    });
  },
);

// ---------------------------------------------------------------------------
// Landing — both themes
// ---------------------------------------------------------------------------

describe.each(["dark", "light"] as const)(
  "Landing accessibility — %s mode (Req 7.2, 16.10, 19.5, 19.7)",
  (theme) => {
    it("passes axe-core, has exactly one h1 + landmarks, labeled icon buttons, and reading-order tab order", async () => {
      const { container } = renderLanding(theme);

      // The Hero owns the single <h1>.
      await waitFor(() => {
        expect(screen.getByRole("heading", { level: 1 })).toBeInstanceOf(
          HTMLElement,
        );
      });

      // Landmarks: banner (GlassNav <header>), main, contentinfo (footer),
      // and at least one nav.
      expect(container.querySelector("header")).toBeInstanceOf(HTMLElement);
      expect(container.querySelector("main")).toBeInstanceOf(HTMLElement);
      expect(container.querySelector("footer")).toBeInstanceOf(HTMLElement);
      expect(container.querySelector("nav")).toBeInstanceOf(HTMLElement);

      expectSequentialHeadings(container);
      expectBrandedFocusRing(container);
      expectNoPositiveTabindex(container);
      expectIconButtonsLabeled(container);

      const results = await runAxe(container);
      expect(results.violations, formatViolations(results)).toEqual([]);
    });
  },
);

// ---------------------------------------------------------------------------
// Icon-only buttons carry aria-label (Req 19.7) — focused, explicit checks
// ---------------------------------------------------------------------------

describe.each(["dark", "light"] as const)(
  "Icon-only buttons carry aria-label — %s mode (Req 19.7)",
  (theme) => {
    it("ThemeToggle exposes an accessible name", () => {
      render(
        <ThemeScope theme={theme}>
          <ThemeToggle />
        </ThemeScope>,
      );
      // The toggle is icon-only; its name comes from aria-label + sr-only text.
      expect(
        screen.getByRole("button", { name: /toggle theme/i }),
      ).toBeInstanceOf(HTMLButtonElement);
    });

    it("GlassNav hamburger exposes an accessible name and aria-expanded", () => {
      const { container } = render(
        <ThemeScope theme={theme}>
          <GlassNav />
        </ThemeScope>,
      );
      const hamburger = within(container).getByRole("button", {
        name: /open menu|close menu/i,
      });
      expect(hamburger).toBeInstanceOf(HTMLButtonElement);
      expect(hamburger.getAttribute("aria-expanded")).not.toBeNull();
      expect(hamburger.getAttribute("aria-controls")).not.toBeNull();
    });
  },
);

// ---------------------------------------------------------------------------
// Form-error aria-live announcement within 1s (Req 19.4)
// ---------------------------------------------------------------------------

describe("Form error announces via aria-live within 1s (Req 19.4)", () => {
  it("login: an invalid-email validation error surfaces in the polite live region well under 1s", async () => {
    const { container } = renderAuth(LoginPage, "dark");

    const email = (await screen.findByLabelText(/email/i)) as HTMLInputElement;
    const password = screen.getByLabelText(/password/i) as HTMLInputElement;
    fireEvent.change(email, { target: { value: "not-an-email" } });
    fireEvent.change(password, { target: { value: "longenough12chars" } });

    const start = Date.now();
    fireEvent.click(screen.getByRole("button", { name: /sign in/i }));

    // The FieldError renders inside an aria-live="polite" region; assert both
    // the announcement text and that it appeared within the 1s budget.
    await waitFor(() => {
      expect(screen.getByText("Enter a valid email address.")).toBeInstanceOf(
        HTMLElement,
      );
    });
    const elapsed = Date.now() - start;
    expect(elapsed).toBeLessThan(1000);

    const fieldError = screen.getByText("Enter a valid email address.");
    expect(fieldError.getAttribute("aria-live")).toBe("polite");

    // The live region lives within the rendered subtree.
    expect(container.contains(fieldError)).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Mandated light-mode pill mitigation — STRUCTURAL (design 10.2 #1)
// ---------------------------------------------------------------------------

describe("Mandated light-mode pill mitigation — KeywordTag structure (design 10.2 #1)", () => {
  // The numeric contrast backing (text-over-tinted-fill ≥ 4.5:1 in both themes)
  // is in `contrast.test.ts`. Here we assert the *structural* mitigation: the
  // pill uses a tinted FILL (`bg-*/15`) + a full-token BORDER (`border-*`) + a
  // primary `text` LABEL — and NEVER colored text (`text-success`/`text-warning`).
  it.each(["success", "warning"] as const)(
    "%s pill: tinted fill + full-token border + primary `text` label, never colored text",
    (variant) => {
      const { container } = render(
        <KeywordTag
          keyword={{ term: "python", weight: 0.9 }}
          variant={variant}
        />,
      );
      const pill = container.querySelector('[data-slot="keyword-tag"]');
      expect(pill).toBeInstanceOf(HTMLElement);
      const cls = pill?.getAttribute("class") ?? "";

      // Tinted fill at low opacity (the `/15` mandate) + full-token border.
      expect(cls).toContain(`bg-${variant}/15`);
      expect(cls).toContain(`border-${variant}`);

      // Label is the PRIMARY text token, not the status color.
      expect(cls).toContain("text-text");
      // It must NOT rely on colored text — the failing-AA path design 10.2 bans.
      expect(cls).not.toContain(`text-${variant}`);
    },
  );

  it.each(["dark", "light"] as const)(
    "%s mode: a KeywordSection of matched pills renders the tinted-fill treatment (no colored text)",
    (theme) => {
      const { container } = render(
        <ThemeScope theme={theme}>
          <KeywordSection
            title="Matched keywords"
            keywords={[
              { term: "python", weight: 0.9 },
              { term: "fastapi", weight: 0.8 },
            ]}
            variant="success"
            emptyMessage="none"
          />
        </ThemeScope>,
      );
      const pills = Array.from(
        container.querySelectorAll('[data-slot="keyword-tag"]'),
      );
      expect(pills.length).toBe(2);
      for (const pill of pills) {
        const cls = pill.getAttribute("class") ?? "";
        expect(cls).toContain("bg-success/15");
        expect(cls).toContain("border-success");
        expect(cls).toContain("text-text");
        expect(cls).not.toContain("text-success");
      }
    },
  );
});

// ---------------------------------------------------------------------------
// Component-level branded focus ring (Req 19.2) — the breakdown/gauge surface
// ---------------------------------------------------------------------------

describe("Branded focus ring on focusable results content (Req 19.2)", () => {
  it.each(["dark", "light"] as const)(
    "%s: the results CTA carries the 2px branded ring",
    async (theme) => {
      apiFetchMock.mockResolvedValue(jsonResponse(200, matchStrong));
      const queryClient = new QueryClient({
        defaultOptions: { queries: { retry: false } },
      });
      const { container } = render(
        <ThemeScope theme={theme}>
          <QueryClientProvider client={queryClient}>
            <main>
              <ResultsView id={MATCH_ID} />
            </main>
          </QueryClientProvider>
        </ThemeScope>,
      );

      await waitFor(() => {
        expect(
          screen.getByRole("link", { name: /analyze another job/i }),
        ).toBeInstanceOf(HTMLElement);
      });

      expectBrandedFocusRing(container);
    },
  );
});

// ---------------------------------------------------------------------------
// Static composition smoke: gauge + breakdown render in both themes (Req 16.10)
// ---------------------------------------------------------------------------

describe("Score gauge + breakdown render cleanly in both themes (Req 16.10)", () => {
  it.each(["dark", "light"] as const)(
    "%s: gauge and breakdown pass axe with no structural violations",
    async (theme) => {
      const { container } = render(
        <ThemeScope theme={theme}>
          <main>
            <h1 className="sr-only">Results</h1>
            <ScoreGauge score={matchStrong.score} />
            <ScoreBreakdownCard breakdown={matchStrong.score_breakdown} />
          </main>
        </ThemeScope>,
      );
      const results = await runAxe(container);
      expect(results.violations, formatViolations(results)).toEqual([]);
    },
  );
});
