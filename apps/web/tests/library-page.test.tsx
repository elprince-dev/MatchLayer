/**
 * Component test for the Library_View — `(app)/library/page.tsx`
 * (Requirement 13.8; design "frontend components").
 *
 * Requirement 13.8: "THE Library_View SHALL list the User_Account's resumes
 * and recent Match_Results within the Authenticated_Shell, link each
 * Match_Result to its Results_Page, and pass WCAG AA color-contrast in both
 * light and dark themes." (Color-contrast is a visual/manual concern; this
 * suite covers the structural guarantees: the resume list renders, the match
 * list renders, and each match links to its Results_Page at `/matches/{id}`.)
 *
 * The page is a Client Component that, on mount, fetches the two
 * cursor-paginated list endpoints concurrently via `apiFetch`:
 *
 *   - `GET /api/v1/resumes` → `ResumeListResponse` ({ items, next_cursor })
 *   - `GET /api/v1/matches`  → `MatchListResponse`  ({ items, next_cursor })
 *
 * validates each body with the generated Zod schemas from
 * `@matchlayer/shared-types`, and renders a "Resumes" section (filename +
 * extraction-status badge) and a "Recent matches" section (each row a
 * `next/link` to `/matches/{id}` showing the score). Empty and error states
 * fall back to friendly copy with a CTA to `/upload`.
 *
 * We mock `@/lib/api` so no network happens; `next/link` renders a real anchor
 * in jsdom, so the href assertion exercises the actual routing target.
 *
 * @vitest-environment jsdom
 */

import * as React from "react";

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Mock the API client before importing the page. The page calls
// `apiFetch("/api/v1/resumes")` and `apiFetch("/api/v1/matches")`; the mock
// returns the right JSON body per path.
vi.mock("@/lib/api", () => ({ apiFetch: vi.fn() }));

import { apiFetch } from "@/lib/api";
import LibraryPage from "@/app/(app)/library/page";

const apiFetchMock = vi.mocked(apiFetch);

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

beforeEach(() => {
  apiFetchMock.mockReset();
});

// A simple UUID so `encodeURIComponent(id)` is the identity — the rendered
// href is exactly `/matches/${MATCH_ID}`.
const MATCH_ID = "01938f00-0000-7000-8000-000000000abc";
const RESUME_ID = "01938f00-0000-7000-8000-000000000def";

/** A valid `ResumeResponse` list item satisfying `ResumeListResponseSchema`. */
function resumeFixture() {
  return {
    id: RESUME_ID,
    original_filename: "resume.pdf",
    content_type: "application/pdf",
    byte_size: 12345,
    extraction_status: "succeeded" as const,
    created_at: "2025-01-01T00:00:00Z",
    updated_at: "2025-01-01T00:00:00Z",
  };
}

/** A valid `MatchListItem` satisfying `MatchListResponseSchema` (no
 *  `job_description_text` by contract). */
function matchFixture() {
  return {
    id: MATCH_ID,
    resume_id: RESUME_ID,
    score: 88,
    created_at: "2025-01-02T00:00:00Z",
  };
}

/** Build a 200 JSON `Response` the way `apiFetch` callers expect. */
function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

/**
 * Wire `apiFetch` to answer each list endpoint with the supplied body. The
 * page fetches both concurrently, so the mock keys off the request path.
 */
function stubLists(resumesBody: unknown, matchesBody: unknown): void {
  apiFetchMock.mockImplementation(async (path: string) =>
    path.includes("/resumes")
      ? jsonResponse(resumesBody)
      : jsonResponse(matchesBody),
  );
}

describe("Library_View (Requirement 13.8 — lists resumes & matches, links each match to its Results_Page)", () => {
  it("renders the resume list under the Resumes section after load", async () => {
    stubLists(
      { items: [resumeFixture()], next_cursor: null },
      { items: [matchFixture()], next_cursor: null },
    );

    render(<LibraryPage />);

    // The "Resumes" section heading exists immediately (it wraps the loading
    // skeleton too); the row content appears once the fetch settles.
    expect(screen.getByRole("heading", { name: /^resumes$/i })).toBeInstanceOf(
      HTMLElement,
    );

    await waitFor(() => {
      expect(screen.getByText("resume.pdf")).toBeInstanceOf(HTMLElement);
    });
  });

  it("renders the match score under the Recent matches section after load", async () => {
    stubLists(
      { items: [resumeFixture()], next_cursor: null },
      { items: [matchFixture()], next_cursor: null },
    );

    render(<LibraryPage />);

    expect(
      screen.getByRole("heading", { name: /recent matches/i }),
    ).toBeInstanceOf(HTMLElement);

    await waitFor(() => {
      expect(screen.getByText("88")).toBeInstanceOf(HTMLElement);
    });
  });

  it("links each match to its Results_Page at /matches/{id}", async () => {
    stubLists(
      { items: [resumeFixture()], next_cursor: null },
      { items: [matchFixture()], next_cursor: null },
    );

    render(<LibraryPage />);

    const matchLink = await waitFor(() =>
      screen.getByRole("link", { name: /match result/i }),
    );

    expect(matchLink).toBeInstanceOf(HTMLAnchorElement);
    expect(matchLink.getAttribute("href")).toBe(`/matches/${MATCH_ID}`);
  });

  it("shows friendly empty states with a CTA to /upload when both lists are empty", async () => {
    stubLists(
      { items: [], next_cursor: null },
      { items: [], next_cursor: null },
    );

    render(<LibraryPage />);

    // Friendly empty copy from the component for each section.
    await waitFor(() => {
      expect(
        screen.getByText("You haven't uploaded a resume yet."),
      ).toBeInstanceOf(HTMLElement);
    });
    expect(screen.getByText("You haven't run a match yet.")).toBeInstanceOf(
      HTMLElement,
    );

    // Each empty state offers a CTA linking to /upload.
    const uploadCta = screen.getByRole("link", { name: /upload a resume/i });
    expect(uploadCta.getAttribute("href")).toBe("/upload");

    const matchCta = screen.getByRole("link", {
      name: /run your first match/i,
    });
    expect(matchCta.getAttribute("href")).toBe("/upload");
  });
});
