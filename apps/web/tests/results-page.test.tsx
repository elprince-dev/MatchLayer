/**
 * Integration tests for the ATS Results data view — `results/results-view.tsx`,
 * rendered by the `(app)/matches/[id]` Server Component shell (Tasks 4.1, 4.2).
 *
 * Task 4.1 replaced the monolithic client page with a Server Component shell
 * (`page.tsx`) that reads the route `id` and renders the `'use client'`
 * `ResultsView`. The shell is an async Server Component and cannot be rendered
 * directly in jsdom, so this test targets `ResultsView` (the unit that owns the
 * fetch + state machine) with the same mocked `apiFetch` boundary the old test
 * used.
 *
 * Task 4.2 expands the original assertions into the full **Section 5 fixture
 * matrix** + the complete fetch-state machine, driving the view through the
 * mocked `@/lib/api` `apiFetch` boundary (the repo does not use MSW — the
 * design's "mocked `apiFetch` / MSW" is satisfied here with the existing
 * `apiFetch` `vi.mock`, matching every other test in `apps/web/tests`):
 *
 *   - **Fixture A — strong (85):** the gauge score + qualitative label, both
 *     breakdown bars, the matched + missing keyword sections, and the
 *     suggestions all render (Req 11.1–11.5, 11.8).
 *   - **Fixture B — partial (52):** renders correctly (score, breakdown,
 *     keywords) (Req 11.1–11.4, 11.8).
 *   - **Fixture C — degenerate (0/0):** renders the `EmptyResultState`, a
 *     valid-but-empty result that is **not** styled as an error (no `danger`)
 *     (Req 12.5, 12.6, 12.7).
 *   - **Loading / pending:** shows `SkeletonLoader variant="results"` — the
 *     skeleton, never a spinner (Req 13.4).
 *   - **5xx / network error / timeout:** shows the retryable `ErrorState` with
 *     a working Retry affordance that re-runs the query in place (Req 13.5,
 *     17.6, 17.7).
 *   - **404:** shows the **non-enumerable** not-found copy (never revealing
 *     whether the match exists for another user) + a `/upload` recovery link
 *     (Req 13.6).
 *   - **Security (Req 20.5):** `job_description_text` appears nowhere in the
 *     DOM or in the payload the view consumes.
 *
 * It also keeps the original focused assertions (sr-only score sentence,
 * matched/missing token families, two-component breakdown, reduced-motion
 * resolved state, and the source-level `dangerouslySetInnerHTML` guard).
 *
 * Conventions mirror the rest of `apps/web/tests`: `@testing-library/react`
 * render/screen/waitFor/fireEvent/cleanup, `vi.mock`, `vi.stubGlobal`,
 * `afterEach(cleanup)`, `toBeInstanceOf(HTMLElement)` assertions, and **no**
 * jest-dom matchers. The `@vitest-environment jsdom` pragma below switches this
 * one file from the repo-default `node` environment to jsdom.
 *
 * @vitest-environment jsdom
 */

import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import * as React from "react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MatchResponseSchema } from "@matchlayer/shared-types";

// @/lib/api: replace apiFetch with a vi.fn whose resolved Response is set per
// test. The view imports only apiFetch from this module.
vi.mock("@/lib/api", () => ({
  apiFetch: vi.fn(),
}));

import { apiFetch } from "@/lib/api";

import { ResultsView } from "@/components/results/results-view";

import {
  matchDegenerate,
  matchPartial,
  matchStrong,
} from "@/components/results/__fixtures__/match-fixtures";

const apiFetchMock = vi.mocked(apiFetch);

const here = path.dirname(fileURLToPath(import.meta.url));

const MATCH_ID = "01938f00-0000-7000-8000-0000000000aa";

/**
 * Render `ResultsView` inside a throwaway `QueryClient` (retries disabled so an
 * error state resolves synchronously, matching the app-root provider config).
 */
function renderView(id: string = MATCH_ID): ReturnType<typeof render> {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ResultsView id={id} />
    </QueryClientProvider>,
  );
}

/**
 * A distinctive, schema-valid Match_Result fixture. The score (92), matched
 * term ("python"), missing term ("kubernetes"), and breakdown values are all
 * chosen to be unambiguous so assertions can target them precisely.
 */
function buildMatch(): unknown {
  const body = {
    id: MATCH_ID,
    resume_id: "01938f00-0000-7000-8000-0000000000bb",
    score: 92,
    score_breakdown: {
      similarity_component: 0.85,
      keyword_coverage_component: 0.5,
      weight_similarity: 0.6,
      weight_keyword: 0.4,
      final_score: 92,
    },
    matched_keywords: [
      { term: "python", weight: 0.9 },
      { term: "java", weight: 0.7 },
    ],
    missing_keywords: [
      { term: "kubernetes", weight: 0.8 },
      { term: "terraform", weight: 0.6 },
    ],
    suggestions: [
      {
        keyword: "kubernetes",
        text: "Highlight any Kubernetes experience on your resume.",
      },
    ],
    scorer_version: "matchlayer-1.0+lex.v1",
    created_at: "2025-01-01T00:00:00Z",
    updated_at: "2025-01-01T00:00:00Z",
  };
  // Guard the fixture against schema drift: if the generated contract changes,
  // this parse fails loudly here rather than producing a misleading UI assertion.
  return MatchResponseSchema.parse(body);
}

/** Build a JSON Response with the given status and (optional) body. */
function jsonResponse(status: number, body?: unknown): Response {
  return new Response(body !== undefined ? JSON.stringify(body) : null, {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/**
 * Stub window.matchMedia so framer-motion's `useReducedMotion` resolves to the
 * given preference. `matches: true` ⇒ reduced motion ⇒ the score is shown
 * resolved with no count-up animation (Requirement 10.6).
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

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  apiFetchMock.mockReset();
});

beforeEach(() => {
  // Default every test to the reduced-motion branch so the resolved score is
  // rendered synchronously (no animation frames to await). Individual tests may
  // re-stub if they need the animating branch.
  stubMatchMedia(true);
});

describe("ResultsView — score render (Requirement 11.8)", () => {
  it("renders the resolved score, the sr-only sentence, and the scorer_version footnote", async () => {
    apiFetchMock.mockResolvedValue(jsonResponse(200, buildMatch()));

    const { container } = renderView();

    // The sr-only sentence is the unambiguous carrier of the resolved value.
    await waitFor(() => {
      expect(screen.getByText("Match score: 92 out of 100.")).toBeInstanceOf(
        HTMLElement,
      );
    });

    // The gradient count-up glyph (text-transparent span) shows the resolved
    // value. With reduced motion it is 92 on first paint — never an in-progress
    // count-up value.
    const gradient = container.querySelector(".text-transparent");
    expect(gradient).toBeInstanceOf(HTMLElement);
    expect(gradient?.textContent).toBe("92");

    // The scorer_version is attributed on the page (Req 11.8).
    expect(screen.getByText(/matchlayer-1\.0\+lex\.v1/)).toBeInstanceOf(
      HTMLElement,
    );

    // apiFetch was called against the match-by-id endpoint with the route id.
    expect(apiFetchMock).toHaveBeenCalledTimes(1);
    expect(apiFetchMock.mock.calls[0]![0]).toContain(
      `/api/v1/matches/${MATCH_ID}`,
    );
  });
});

describe("ResultsView — matched/missing token families (Requirement 11.3, 11.4)", () => {
  it("renders matched terms in the success family and missing terms in the warning family", async () => {
    apiFetchMock.mockResolvedValue(jsonResponse(200, buildMatch()));

    const { container } = renderView();

    await waitFor(() => screen.getByText("python"));

    // The matched section's pill carries the success token classes.
    const matchedSection = container.querySelector(
      'section[data-slot="keyword-section"][data-variant="success"]',
    );
    expect(matchedSection).toBeInstanceOf(HTMLElement);
    expect(matchedSection?.textContent).toContain("python");
    const matchedPill = matchedSection?.querySelector(
      '[data-slot="keyword-tag"]',
    );
    expect(matchedPill?.className).toMatch(/success/);
    expect(matchedPill?.className).not.toMatch(/warning/);

    // The missing section's pill carries the warning token classes.
    const missingSection = container.querySelector(
      'section[data-slot="keyword-section"][data-variant="warning"]',
    );
    expect(missingSection).toBeInstanceOf(HTMLElement);
    expect(missingSection?.textContent).toContain("kubernetes");
    const missingPill = missingSection?.querySelector(
      '[data-slot="keyword-tag"]',
    );
    expect(missingPill?.className).toMatch(/warning/);
    expect(missingPill?.className).not.toMatch(/success/);
  });
});

describe("ResultsView — score breakdown (Requirement 11.1, 11.2)", () => {
  it("renders the two breakdown components with their scaled percentages and weights", async () => {
    apiFetchMock.mockResolvedValue(jsonResponse(200, buildMatch()));

    renderView();

    await waitFor(() => {
      expect(screen.getByText("TF-IDF similarity")).toBeInstanceOf(HTMLElement);
    });

    expect(screen.getByText("Keyword coverage")).toBeInstanceOf(HTMLElement);
    // similarity_component 0.85 → "85%", keyword_coverage_component 0.5 → "50%".
    expect(screen.getByText("85%")).toBeInstanceOf(HTMLElement);
    expect(screen.getByText("50%")).toBeInstanceOf(HTMLElement);
    // The two weights (0.6, 0.4) are surfaced alongside the bars (Req 11.2).
    expect(screen.getAllByText("0.6").length).toBeGreaterThan(0);
    expect(screen.getAllByText("0.4").length).toBeGreaterThan(0);
  });
});

describe("ResultsView — reduced-motion resolved state (Requirement 10.6)", () => {
  it("shows the final score immediately (no count-up from 0) when prefers-reduced-motion is set", async () => {
    stubMatchMedia(true);
    apiFetchMock.mockResolvedValue(jsonResponse(200, buildMatch()));

    const { container } = renderView();

    // The resolved value is present on the very first paint after load: the
    // gradient glyph reads 92 directly, never an intermediate animation value.
    await waitFor(() => {
      const gradient = container.querySelector(".text-transparent");
      expect(gradient?.textContent).toBe("92");
    });
  });
});

describe("ResultsView — non-enumerable 404 (Requirement 13.6)", () => {
  it("renders the non-enumerable not-found error on a 404 and no raw error", async () => {
    apiFetchMock.mockResolvedValue(jsonResponse(404));

    renderView();

    await waitFor(() => {
      expect(screen.getByText("We couldn't find that result")).toBeInstanceOf(
        HTMLElement,
      );
    });

    // The copy never reveals whether the match exists for another user, and the
    // generic load-failure copy is NOT shown (a 404 is distinct).
    expect(screen.queryByText("We couldn't load your results")).toBeNull();
    // A recovery link to the Upload page is offered.
    const uploadLink = screen
      .getAllByRole("link")
      .find((el) => el.getAttribute("href") === "/upload");
    expect(uploadLink).toBeInstanceOf(HTMLElement);
  });
});

/**
 * Render text-content helper: the concatenated visible + sr-only text of the
 * rendered tree, used by the security assertion to prove `job_description_text`
 * (and any JD prose) is absent from the DOM.
 */
function renderedText(container: HTMLElement): string {
  return `${container.innerHTML}\n${container.textContent ?? ""}`;
}

/**
 * Collect the **active** styling-class tokens across the rendered tree, so a
 * "no `danger` token" assertion (Req 12.7) tests real styling rather than inert
 * conditional variants.
 *
 * The `Button` primitive's base classes carry `aria-invalid:border-danger` /
 * `aria-invalid:ring-danger/30` — danger utilities that apply *only* when the
 * element is `aria-invalid`, which the empty state never is. Those `aria-*:`
 * prefixed variant tokens are filtered out here so they don't trip a blunt
 * substring search; what remains is the set of classes actually painted.
 */
function activeClassTokens(container: HTMLElement): string[] {
  const tokens: string[] = [];
  for (const el of Array.from(container.querySelectorAll("*"))) {
    // SVG elements expose `className` as an object, so read the raw attribute.
    const classAttr = el.getAttribute("class");
    if (classAttr === null) {
      continue;
    }
    for (const token of classAttr.split(/\s+/)) {
      // Drop conditional variant tokens (e.g. `aria-invalid:border-danger`,
      // `hover:bg-danger/90`) — they are inert unless their state is active.
      if (token.length > 0 && !token.includes(":")) {
        tokens.push(token);
      }
    }
  }
  return tokens;
}

describe("ResultsView — fixture A: strong match 85 (Requirements 11.1–11.5, 11.8)", () => {
  it("renders the gauge score + label, both breakdown bars, matched/missing keywords, and suggestions", async () => {
    apiFetchMock.mockResolvedValue(jsonResponse(200, matchStrong));

    const { container } = renderView();

    // Gauge: the sr-only sentence carries the resolved 85; reduced motion (the
    // beforeEach default) means it is the final value on first paint.
    await waitFor(() => {
      expect(screen.getByText("Match score: 85 out of 100.")).toBeInstanceOf(
        HTMLElement,
      );
    });
    const gradient = container.querySelector(".text-transparent");
    expect(gradient?.textContent).toBe("85");

    // Qualitative label: 85 → "Excellent".
    expect(screen.getByText("Excellent")).toBeInstanceOf(HTMLElement);

    // Both breakdown bars render with their scaled percentages and weights.
    expect(screen.getByText("TF-IDF similarity")).toBeInstanceOf(HTMLElement);
    expect(screen.getByText("Keyword coverage")).toBeInstanceOf(HTMLElement);
    expect(screen.getByText("81%")).toBeInstanceOf(HTMLElement); // 0.8123 → 81
    expect(screen.getByText("90%")).toBeInstanceOf(HTMLElement); // 0.9000 → 90
    expect(screen.getAllByText("0.6").length).toBeGreaterThan(0);
    expect(screen.getAllByText("0.4").length).toBeGreaterThan(0);

    // Matched keywords (success family) and missing keywords (warning family).
    const matchedSection = container.querySelector(
      'section[data-slot="keyword-section"][data-variant="success"]',
    );
    expect(matchedSection?.textContent).toContain("python");
    expect(matchedSection?.textContent).toContain("fastapi");

    const missingSection = container.querySelector(
      'section[data-slot="keyword-section"][data-variant="warning"]',
    );
    expect(missingSection?.textContent).toContain("kubernetes");
    expect(missingSection?.textContent).toContain("terraform");

    // Suggestions section renders the suggestion text (improvement cards).
    expect(screen.getByText("Suggestions")).toBeInstanceOf(HTMLElement);
    expect(screen.getByText(matchStrong.suggestions[0]!.text)).toBeInstanceOf(
      HTMLElement,
    );

    // The scorer_version attribution footnote is shown (Req 11.8).
    expect(
      screen.getByText(
        new RegExp(matchStrong.scorer_version.replace(/[.+]/g, "\\$&")),
      ),
    ).toBeInstanceOf(HTMLElement);

    // It is NOT mistaken for the empty/degenerate state.
    expect(screen.queryByText("Not enough to analyze yet")).toBeNull();
  });
});

describe("ResultsView — fixture B: partial match 52 (Requirements 11.1–11.4, 11.8)", () => {
  it("renders the score, breakdown, and keyword sections correctly", async () => {
    apiFetchMock.mockResolvedValue(jsonResponse(200, matchPartial));

    const { container } = renderView();

    await waitFor(() => {
      expect(screen.getByText("Match score: 52 out of 100.")).toBeInstanceOf(
        HTMLElement,
      );
    });
    const gradient = container.querySelector(".text-transparent");
    expect(gradient?.textContent).toBe("52");

    // 52 → "Fair".
    expect(screen.getByText("Fair")).toBeInstanceOf(HTMLElement);

    // Breakdown: similarity 0.48 → 48%, keyword coverage 0.5833 → 58%.
    expect(screen.getByText("48%")).toBeInstanceOf(HTMLElement);
    expect(screen.getByText("58%")).toBeInstanceOf(HTMLElement);

    // Matched + missing keyword terms from fixture B render in their sections.
    const matchedSection = container.querySelector(
      'section[data-slot="keyword-section"][data-variant="success"]',
    );
    expect(matchedSection?.textContent).toContain("javascript");
    expect(matchedSection?.textContent).toContain("react");

    const missingSection = container.querySelector(
      'section[data-slot="keyword-section"][data-variant="warning"]',
    );
    expect(missingSection?.textContent).toContain("typescript");
    expect(missingSection?.textContent).toContain("graphql");
  });
});

describe("ResultsView — fixture C: degenerate 0/0 (Requirements 12.5, 12.6, 12.7)", () => {
  it("renders the EmptyResultState (a valid result) and is NOT styled as an error", async () => {
    apiFetchMock.mockResolvedValue(jsonResponse(200, matchDegenerate));

    const { container } = renderView();

    // The Empty_Result_State copy + its recovery CTA are shown.
    await waitFor(() => {
      expect(screen.getByText("Not enough to analyze yet")).toBeInstanceOf(
        HTMLElement,
      );
    });
    const recoveryLink = screen
      .getAllByRole("link")
      .find((el) => el.getAttribute("href") === "/upload");
    expect(recoveryLink).toBeInstanceOf(HTMLElement);

    // It is a *valid* empty result, not an error: the EmptyResultState is
    // announced politely as a status, never via role="alert" (the ErrorState
    // marker), and neither retryable nor not-found error copy appears.
    const status = container.querySelector('[role="status"]');
    expect(status).toBeInstanceOf(HTMLElement);
    expect(container.querySelector('[role="alert"]')).toBeNull();
    expect(screen.queryByText("We couldn't load your results")).toBeNull();
    expect(screen.queryByText("We couldn't find that result")).toBeNull();

    // Req 12.7: the empty-but-valid surface must never use the `danger` token.
    // Check active styling classes only (inert `aria-invalid:*` danger variants
    // on the CTA button never apply here).
    expect(activeClassTokens(container).some((t) => t.includes("danger"))).toBe(
      false,
    );

    // It does not render a gauge/score — the degenerate case supersedes the
    // success content entirely.
    expect(screen.queryByText("Match score: 0 out of 100.")).toBeNull();
  });
});

describe("ResultsView — loading state (Requirement 13.4)", () => {
  it("shows the results SkeletonLoader (not a spinner) while the fetch is pending", () => {
    // A never-settling promise keeps the query in its `pending` state.
    apiFetchMock.mockReturnValue(new Promise<Response>(() => {}));

    const { container } = renderView();

    // The results-shaped skeleton is the loading pattern (Req 13.4, 17.2).
    const skeleton = container.querySelector(
      '[data-slot="skeleton-loader"][data-variant="results"]',
    );
    expect(skeleton).toBeInstanceOf(HTMLElement);
    expect(skeleton?.getAttribute("aria-busy")).toBe("true");
    expect(screen.getByText("Loading…")).toBeInstanceOf(HTMLElement);

    // No resolved content yet, and no error surface.
    expect(screen.queryByText("Excellent")).toBeNull();
    expect(container.querySelector('[role="alert"]')).toBeNull();
  });
});

describe("ResultsView — 5xx error (Requirements 13.5, 17.6)", () => {
  it("shows the retryable ErrorState and the Retry affordance re-runs the query in place", async () => {
    // First attempt fails 5xx; the user-initiated Retry succeeds.
    apiFetchMock
      .mockResolvedValueOnce(jsonResponse(500))
      .mockResolvedValueOnce(jsonResponse(200, matchStrong));

    const { container } = renderView();

    await waitFor(() => {
      expect(screen.getByText("We couldn't load your results")).toBeInstanceOf(
        HTMLElement,
      );
    });

    // It is the retryable error (role="alert"), not the non-enumerable 404 copy.
    expect(container.querySelector('[role="alert"]')).toBeInstanceOf(
      HTMLElement,
    );
    expect(screen.queryByText("We couldn't find that result")).toBeNull();

    // A /upload recovery link is offered alongside Retry.
    const uploadLink = screen
      .getAllByRole("link")
      .find((el) => el.getAttribute("href") === "/upload");
    expect(uploadLink).toBeInstanceOf(HTMLElement);

    // Retry re-attempts the fetch in place (no navigation) and resolves to the
    // success content.
    const retry = screen.getByRole("button", { name: "Retry" });
    fireEvent.click(retry);

    await waitFor(() => {
      expect(screen.getByText("Match score: 85 out of 100.")).toBeInstanceOf(
        HTMLElement,
      );
    });
    expect(apiFetchMock).toHaveBeenCalledTimes(2);
  });
});

describe("ResultsView — network error (Requirement 13.5)", () => {
  it("shows the retryable ErrorState when the request rejects with a network error", async () => {
    apiFetchMock.mockRejectedValue(new TypeError("Failed to fetch"));

    const { container } = renderView();

    await waitFor(() => {
      expect(screen.getByText("We couldn't load your results")).toBeInstanceOf(
        HTMLElement,
      );
    });
    expect(container.querySelector('[role="alert"]')).toBeInstanceOf(
      HTMLElement,
    );
    expect(screen.getByRole("button", { name: "Retry" })).toBeInstanceOf(
      HTMLElement,
    );
  });
});

describe("ResultsView — timeout (Requirements 13.5, 17.7)", () => {
  it("shows the retryable ErrorState when the request is aborted by the no-response deadline", async () => {
    // The view wraps `apiFetch` in a 10s AbortController deadline (Req 13.5):
    // when no response arrives in time it aborts the request, and its `catch`
    // maps the resulting AbortError to a retryable error. Model that terminal
    // condition directly — a request rejected with an AbortError — so the
    // timeout→ErrorState mapping is exercised deterministically (no 10s wait,
    // no fake-timer coupling to React Query's async scheduler).
    apiFetchMock.mockRejectedValue(
      new DOMException("The operation was aborted.", "AbortError"),
    );

    const { container } = renderView();

    await waitFor(() => {
      expect(screen.getByText("We couldn't load your results")).toBeInstanceOf(
        HTMLElement,
      );
    });
    expect(container.querySelector('[role="alert"]')).toBeInstanceOf(
      HTMLElement,
    );
    expect(screen.getByRole("button", { name: "Retry" })).toBeInstanceOf(
      HTMLElement,
    );
  });
});

describe("ResultsView — security: job_description_text never appears (Requirement 20.5)", () => {
  it("renders no `job_description_text` in the DOM, and the consumed payload omits it", async () => {
    apiFetchMock.mockResolvedValue(jsonResponse(200, matchStrong));

    const { container } = renderView();

    await waitFor(() => screen.getByText("Match score: 85 out of 100."));

    // The Restricted PII field name is absent from the rendered DOM (markup and
    // text), so it can never leak even as a stray attribute or label.
    const text = renderedText(container);
    expect(text).not.toContain("job_description_text");
    expect(text).not.toContain("jobDescriptionText");

    // The payload the view consumes (the Section 5 fixture, conforming to the
    // generated contract) structurally has no such field — the match API never
    // returns it (Req 20.5, security.md).
    expect(Object.keys(matchStrong)).not.toContain("job_description_text");
    expect(JSON.stringify(matchStrong)).not.toContain("job_description_text");
  });
});

/**
 * Strip JavaScript/TypeScript block and line comments so
 * the guard inspects executable code only. The view's docstring documents the
 * security rule by *naming* `dangerouslySetInnerHTML` in prose; that mention
 * must not be mistaken for a usage. After stripping, the identifier appearing
 * anywhere is a genuine code-level use.
 */
function stripComments(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/(^|[^:])\/\/[^\n]*/g, "$1");
}

describe("ResultsView — no dangerouslySetInnerHTML (Requirement 11.8, security.md)", () => {
  it("never uses dangerouslySetInnerHTML for match-derived content in the view source", () => {
    const source = readFileSync(
      path.resolve(here, "../src/components/results/results-view.tsx"),
      "utf8",
    );

    // Inspect executable code only — the file's docstring legitimately names the
    // API to document that it is never used.
    const code = stripComments(source);

    // No code-level mention of the dangerous sink...
    expect(code).not.toContain("dangerouslySetInnerHTML");
    // ...and none of its usage markers (the `{ __html }` payload that every real
    // dangerouslySetInnerHTML call carries) appear anywhere in the file.
    expect(source).not.toContain("__html");
  });
});
