/**
 * Component test for the Results_Page — `(app)/matches/[id]` (Task 15.2).
 *
 * Validates the Phase 1 results surface against its acceptance criteria:
 *
 *   - Requirement 13.2 — the resolved match score renders (gradient count-up
 *     number + the `sr-only` "Match score: N out of 100." sentence).
 *   - Requirement 13.3 — matched terms render in the `success` token family and
 *     missing terms in the `warning` token family.
 *   - Requirement 13.4 — the explainable score breakdown renders (similarity,
 *     coverage, the two weights, final score).
 *   - Requirement 13.5 — `prefers-reduced-motion` (via a `matchMedia` mock with
 *     `matches: true`) shows the resolved score immediately, with no count-up
 *     animation from 0.
 *   - Requirement 13.6 — a 404 from the API renders the friendly "Match not
 *     found" empty state, never a raw error or stack trace.
 *   - Requirement 13.7 — a source-level guard pins that the page never uses
 *     `dangerouslySetInnerHTML` for any match-derived content.
 *
 * Conventions mirror `tests/login-form.test.tsx` and
 * `tests/authenticated-shell.test.tsx`: `@testing-library/react`
 * render/screen/waitFor/cleanup, `vi.mock`, `vi.stubGlobal`, `afterEach(cleanup)`,
 * and `toBeInstanceOf(HTMLElement)` assertions (no jest-dom setup file).
 *
 * @vitest-environment jsdom
 */

import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import * as React from "react";

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MatchResponseSchema } from "@matchlayer/shared-types";

// next/navigation: the page reads the route id via useParams. Provide a stable
// id so the load effect issues the GET /api/v1/matches/{id} request.
vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "01938f00-0000-7000-8000-0000000000aa" }),
}));

// @/lib/api: replace apiFetch with a vi.fn whose resolved Response is set per
// test. The component imports only apiFetch from this module.
vi.mock("@/lib/api", () => ({
  apiFetch: vi.fn(),
}));

import { apiFetch } from "@/lib/api";

import ResultsPage from "@/app/(app)/matches/[id]/page";

const apiFetchMock = vi.mocked(apiFetch);

const here = path.dirname(fileURLToPath(import.meta.url));

/**
 * A distinctive, schema-valid Match_Result fixture. The score (92), matched
 * term ("python"), missing term ("kubernetes"), and breakdown values are all
 * chosen to be unambiguous so assertions can target them precisely.
 */
function buildMatch(): unknown {
  const body = {
    id: "01938f00-0000-7000-8000-0000000000aa",
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
 * resolved with no count-up animation (Requirement 13.5).
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

describe("Results_Page — score render (Requirement 13.2)", () => {
  it("renders the resolved score number and the sr-only score sentence", async () => {
    apiFetchMock.mockResolvedValue(jsonResponse(200, buildMatch()));

    const { container } = render(<ResultsPage />);

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

    // apiFetch was called against the match-by-id endpoint with the route id.
    expect(apiFetchMock).toHaveBeenCalledTimes(1);
    expect(apiFetchMock.mock.calls[0]![0]).toContain(
      "/api/v1/matches/01938f00-0000-7000-8000-0000000000aa",
    );
  });
});

describe("Results_Page — matched/missing token families (Requirement 13.3)", () => {
  it("renders matched terms in the success family and missing terms in the warning family", async () => {
    apiFetchMock.mockResolvedValue(jsonResponse(200, buildMatch()));

    render(<ResultsPage />);

    const matched = await waitFor(() => screen.getByText("python"));
    // The pill carries the success token classes (text-success / bg-success/10).
    expect(matched.className).toMatch(/success/);
    expect(matched.className).not.toMatch(/warning/);

    const missing = screen.getByText("kubernetes");
    // The pill carries the warning token classes (text-warning / bg-warning/10).
    expect(missing.className).toMatch(/warning/);
    expect(missing.className).not.toMatch(/success/);
  });
});

describe("Results_Page — score breakdown (Requirement 13.4)", () => {
  it("renders the breakdown section heading and the computed component/weight values", async () => {
    apiFetchMock.mockResolvedValue(jsonResponse(200, buildMatch()));

    render(<ResultsPage />);

    await waitFor(() => {
      expect(screen.getByText("How this score was calculated")).toBeInstanceOf(
        HTMLElement,
      );
    });

    // similarity_component 0.85 → "85%", keyword_coverage_component 0.5 → "50%".
    expect(screen.getByText("85%")).toBeInstanceOf(HTMLElement);
    expect(screen.getByText("50%")).toBeInstanceOf(HTMLElement);
    // weight_similarity 0.6 → "0.60", weight_keyword 0.4 → "0.40".
    expect(screen.getByText("0.60")).toBeInstanceOf(HTMLElement);
    expect(screen.getByText("0.40")).toBeInstanceOf(HTMLElement);
  });
});

describe("Results_Page — reduced-motion resolved state (Requirement 13.5)", () => {
  it("shows the final score immediately (no count-up from 0) when prefers-reduced-motion is set", async () => {
    stubMatchMedia(true);
    apiFetchMock.mockResolvedValue(jsonResponse(200, buildMatch()));

    const { container } = render(<ResultsPage />);

    // The resolved value is present on the very first paint after load: the
    // gradient glyph reads 92 directly, never an intermediate animation value.
    await waitFor(() => {
      const gradient = container.querySelector(".text-transparent");
      expect(gradient?.textContent).toBe("92");
    });
  });
});

describe("Results_Page — friendly 404 (Requirement 13.6)", () => {
  it("renders the friendly not-found empty state on a 404 and no raw error", async () => {
    apiFetchMock.mockResolvedValue(jsonResponse(404));

    render(<ResultsPage />);

    await waitFor(() => {
      expect(screen.getByText("Match not found")).toBeInstanceOf(HTMLElement);
    });

    // The friendly explanatory copy is shown...
    expect(
      screen.getByText(
        "We couldn't find this match. It may have been deleted, or it never belonged to your account.",
      ),
    ).toBeInstanceOf(HTMLElement);

    // ...and the generic error state is NOT shown (a 404 is distinct from a
    // load failure), nor is any raw error surfaced.
    expect(screen.queryByText("Something went wrong")).toBeNull();
  });
});

/**
 * Strip JavaScript/TypeScript block (`/* … *\/`) and line (`// …`) comments so
 * the 13.7 guard inspects executable code only. The page's own docstring
 * documents the security rule by *naming* `dangerouslySetInnerHTML` in prose;
 * that mention must not be mistaken for a usage. After stripping, the identifier
 * appearing anywhere is a genuine code-level use.
 */
function stripComments(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/(^|[^:])\/\/[^\n]*/g, "$1");
}

describe("Results_Page — no dangerouslySetInnerHTML (Requirement 13.7)", () => {
  it("never uses dangerouslySetInnerHTML for match-derived content in the page source", () => {
    const source = readFileSync(
      path.resolve(here, "../src/app/(app)/matches/[id]/page.tsx"),
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
