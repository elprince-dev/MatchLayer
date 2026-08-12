/**
 * Component tests for the results-page LLM experience
 * (phase-3-llm-layer Task 11.7; Requirements 17.1, 17.3, 17.4, 17.5,
 * 17.6, 17.8, 17.9).
 *
 * Coverage, one describe block per task bullet:
 *
 * - **Tab composition (17.1):** `LlmTabs` renders the three features as
 *   WAI-ARIA tabs; switching moves selection and visibility while every
 *   panel stays mounted (no re-fired persisted-result GET).
 * - **FallbackBadge both branches (17.4):** the label renders iff
 *   `isFallback === true`.
 * - **429/503 messages (17.5):** `LlmErrorState` quota copy states the
 *   daily limit and its UTC reset (preferring the API's RFC 7807
 *   `detail` when present); the unavailable copy states AI features are
 *   temporarily disabled.
 * - **Skeleton state (17.6):** `StreamingText` shows content-shaped
 *   skeleton lines while streaming before the first delta.
 * - **Load-persisted-without-POST (17.8):** mounting `CoachPanel`
 *   issues only the `GET ...?limit=1` and displays the stored result —
 *   no POST without user action.
 * - **Regenerate (17.9):** the Regenerate action issues the
 *   `POST ...?stream=true` request and the new result (with its prompt
 *   version and timestamp meta line) replaces the previous one in place.
 * - **Static check (17.3):** no component source file under
 *   `components/llm/` uses `dangerouslySetInnerHTML` (the DOM-level
 *   injection property is Task 11.4's
 *   `llm-content-html-injection.property.test.tsx` — not duplicated
 *   here).
 *
 * Conventions mirror the rest of the suite (`tests/results-page.test.tsx`,
 * the co-located property test): `@testing-library/react`
 * render/screen/waitFor/fireEvent/cleanup, a `vi.mock`ed `@/lib/api`
 * `apiFetch` boundary, throwaway `QueryClient` with retries disabled,
 * fixtures parsed through the generated Zod schemas (drift guard),
 * `toBeInstanceOf` assertions, and **no** jest-dom matchers. All fixture
 * data is synthetic (security.md).
 *
 * @vitest-environment jsdom
 */

import { readdirSync, readFileSync } from "node:fs";
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
import { afterEach, describe, expect, it, vi } from "vitest";

import { CoachingReportEnvelopeSchema } from "@matchlayer/shared-types";

// Replace apiFetch with a vi.fn configured per test. Both data sources of
// `useLlmFeature` flow through it: the persisted-result GET (directly) and
// the streaming POST (via `requestSseStream` in lib/llm/sse.ts).
vi.mock("@/lib/api", () => ({
  apiFetch: vi.fn(),
}));

import { apiFetch } from "@/lib/api";

import { CoachPanel } from "@/components/llm/CoachPanel";
import { FallbackBadge } from "@/components/llm/FallbackBadge";
import { LlmErrorState } from "@/components/llm/LlmErrorStates";
import { LlmTabs } from "@/components/llm/LlmTabs";
import { StreamingText } from "@/components/llm/StreamingText";

const apiFetchMock = vi.mocked(apiFetch);

const here = path.dirname(fileURLToPath(import.meta.url));

const MATCH_ID = "01938f00-0000-7000-8000-0000000000aa";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

// ---------------------------------------------------------------------------
// Helpers & fixtures
// ---------------------------------------------------------------------------

/** Render inside a throwaway QueryClient (retries off, matching app config). */
function renderWithQuery(node: React.ReactElement): ReturnType<typeof render> {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>{node}</QueryClientProvider>,
  );
}

/** Build a JSON Response with the given status and body. */
function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/**
 * Build a 200 SSE Response whose body streams the given events in wire
 * format — consumed by `readSseStream` exactly like a real backend stream.
 */
function sseResponse(events: { event: string; data: string }[]): Response {
  const wire = events
    .map(({ event, data }) => `event: ${event}\ndata: ${data}\n\n`)
    .join("");
  return new Response(wire, {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
}

/** An empty newest-first list body (no persisted result yet). */
function emptyList(): Response {
  return jsonResponse(200, { items: [], next_cursor: null });
}

/**
 * A schema-valid persisted Coaching_Report envelope. Parsed through the
 * generated schema so contract drift fails loudly here, not as a
 * misleading UI assertion. Sentinel strings are distinctive so
 * assertions target them precisely.
 */
const persistedEnvelope = CoachingReportEnvelopeSchema.parse({
  id: "01938f00-0000-7000-8000-0000000000cc",
  is_fallback: false,
  fallback_reason: null,
  prompt_template_version: 1,
  created_at: "2025-06-01T12:00:00Z",
  result: {
    summary: "Persisted coaching summary sentinel.",
    strengths: ["Strong Python background"],
    gaps: ["No Kubernetes exposure"],
    improvements: [
      { priority: 3, action: "Add a Kubernetes project." },
      { priority: 2, action: "Quantify your Python impact." },
      { priority: 1, action: "Tighten the summary line." },
    ],
  },
});

/** The envelope a regenerate stream completes with (newer version/time). */
const regeneratedEnvelope = CoachingReportEnvelopeSchema.parse({
  id: "01938f00-0000-7000-8000-0000000000dd",
  is_fallback: false,
  fallback_reason: null,
  prompt_template_version: 2,
  created_at: "2025-06-02T08:30:00Z",
  result: {
    summary: "Regenerated coaching summary sentinel.",
    strengths: ["Strong Python background"],
    gaps: ["No Kubernetes exposure"],
    improvements: [
      { priority: 3, action: "Add a Kubernetes project." },
      { priority: 2, action: "Quantify your Python impact." },
      { priority: 1, action: "Lead with your strongest role." },
    ],
  },
});

/** Was this apiFetch call the streaming POST? (design D3 negotiation). */
function isStreamPost(call: [string, ...unknown[]]): boolean {
  return call[0].includes("stream=true");
}

// ---------------------------------------------------------------------------
// Tab composition (Req 17.1)
// ---------------------------------------------------------------------------

describe("LlmTabs — tab composition (Requirement 17.1)", () => {
  it("renders the three LLM features as tabs with the coach selected, switches on click, and keeps every panel mounted", async () => {
    apiFetchMock.mockImplementation(async () => emptyList());

    const { container } = renderWithQuery(<LlmTabs matchId={MATCH_ID} />);

    // The three feature tabs, in a tablist.
    const tablist = screen.getByRole("tablist", { name: "AI tools" });
    expect(tablist).toBeInstanceOf(HTMLElement);
    const coachTab = screen.getByRole("tab", { name: "Coach" });
    const bulletsTab = screen.getByRole("tab", { name: "Bullet Rewrites" });
    const interviewTab = screen.getByRole("tab", { name: "Interview Prep" });
    expect(coachTab.getAttribute("aria-selected")).toBe("true");
    expect(bulletsTab.getAttribute("aria-selected")).toBe("false");
    expect(interviewTab.getAttribute("aria-selected")).toBe("false");

    // All three panels are mounted; only the coach panel is visible.
    const panels = Array.from(container.querySelectorAll('[role="tabpanel"]'));
    expect(panels).toHaveLength(3);
    expect(panels.map((panel) => panel.hasAttribute("hidden"))).toEqual([
      false,
      true,
      true,
    ]);

    // Each mounted panel issued its one persisted-result GET on mount.
    await waitFor(() => {
      expect(apiFetchMock).toHaveBeenCalledTimes(3);
    });
    const paths = apiFetchMock.mock.calls.map((call) => call[0]);
    expect(paths.some((p) => p.includes("coaching-reports?limit=1"))).toBe(
      true,
    );
    expect(paths.some((p) => p.includes("bullet-rewrites?limit=1"))).toBe(true);
    expect(
      paths.some((p) => p.includes("interview-question-sets?limit=1")),
    ).toBe(true);

    // Switching activates the target and hides — never unmounts — the rest.
    fireEvent.click(interviewTab);
    expect(interviewTab.getAttribute("aria-selected")).toBe("true");
    expect(coachTab.getAttribute("aria-selected")).toBe("false");
    expect(panels.map((panel) => panel.hasAttribute("hidden"))).toEqual([
      true,
      true,
      false,
    ]);
    // Every panel is still in the document (mounted)...
    for (const panel of panels) {
      expect(panel.isConnected).toBe(true);
    }
    // ...so tab navigation re-fires no GET (and never POSTs).
    expect(apiFetchMock).toHaveBeenCalledTimes(3);
  });
});

// ---------------------------------------------------------------------------
// FallbackBadge — both branches (Req 17.4)
// ---------------------------------------------------------------------------

describe("FallbackBadge — both branches (Requirement 17.4)", () => {
  it("renders the label when is_fallback is true", () => {
    render(<FallbackBadge isFallback={true} />);
    const badge = screen.getByText("Generated without AI assistance");
    expect(badge).toBeInstanceOf(HTMLElement);
  });

  it("renders nothing when is_fallback is false", () => {
    const { container } = render(<FallbackBadge isFallback={false} />);
    expect(container.firstChild).toBeNull();
    expect(container.textContent).toBe("");
  });
});

// ---------------------------------------------------------------------------
// 429 / 503 messages (Req 17.5)
// ---------------------------------------------------------------------------

describe("LlmErrorState — 429 and 503 messages (Requirement 17.5)", () => {
  it("quota (429) states the daily limit and when it resets", () => {
    render(<LlmErrorState kind="quota" />);
    const alert = screen.getByRole("alert");
    expect(alert).toBeInstanceOf(HTMLElement);
    expect(alert.textContent).toContain("Daily AI limit reached");
    // The copy names both halves of Req 17.5: the daily limit concept and
    // the UTC reset.
    expect(alert.textContent).toContain(
      "Your daily limit resets at midnight UTC.",
    );
  });

  it("quota (429) prefers the API's RFC 7807 detail, which names the configured limit", () => {
    const detail =
      "Daily LLM request limit of 20 reached. The quota resets at 00:00 UTC.";
    render(<LlmErrorState kind="quota" detail={detail} />);
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain(detail);
    // The generic copy is replaced, not stacked.
    expect(alert.textContent).not.toContain(
      "Your daily limit resets at midnight UTC.",
    );
  });

  it("unavailable (503) states that AI features are temporarily disabled", () => {
    render(<LlmErrorState kind="unavailable" />);
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain("AI features are temporarily disabled");
  });
});

// ---------------------------------------------------------------------------
// Skeleton state (Req 17.6)
// ---------------------------------------------------------------------------

describe("StreamingText — skeleton state (Requirement 17.6)", () => {
  it("shows content-shaped skeleton lines while streaming before the first delta", () => {
    const { container } = render(<StreamingText text="" streaming={true} />);
    const skeletons = container.querySelectorAll('[data-slot="skeleton"]');
    expect(skeletons.length).toBeGreaterThan(0);
    // The text paragraph is not rendered yet — the skeleton stands in.
    expect(container.querySelector("p")).toBeNull();
  });

  it("replaces the skeleton with the progressive text once a delta arrives", () => {
    const { container } = render(
      <StreamingText text="First tokens of the answer" streaming={true} />,
    );
    expect(container.querySelectorAll('[data-slot="skeleton"]')).toHaveLength(
      0,
    );
    const paragraph = container.querySelector("p");
    expect(paragraph).toBeInstanceOf(HTMLElement);
    expect(paragraph?.textContent).toBe("First tokens of the answer");
  });
});

// ---------------------------------------------------------------------------
// Load persisted result without a POST (Req 17.8)
// ---------------------------------------------------------------------------

describe("CoachPanel — persisted result loads without a POST (Requirement 17.8)", () => {
  it("displays the newest stored result from the on-load GET and issues no POST", async () => {
    apiFetchMock.mockImplementation(async (requestPath: string) => {
      if (requestPath.includes("coaching-reports?limit=1")) {
        return jsonResponse(200, {
          items: [persistedEnvelope],
          next_cursor: null,
        });
      }
      throw new Error(`Unexpected apiFetch call: ${requestPath}`);
    });

    renderWithQuery(<CoachPanel matchId={MATCH_ID} />);

    // The stored report renders on load...
    const summary = await screen.findByText(
      "Persisted coaching summary sentinel.",
    );
    expect(summary).toBeInstanceOf(HTMLElement);

    // ...via exactly the one persisted-result GET — no streaming POST was
    // issued without user action.
    expect(apiFetchMock).toHaveBeenCalledTimes(1);
    const [firstCall] = apiFetchMock.mock.calls;
    expect(firstCall).toBeDefined();
    expect(firstCall?.[0]).toContain(
      `/api/v1/matches/${MATCH_ID}/coaching-reports?limit=1`,
    );
    expect(apiFetchMock.mock.calls.some(isStreamPost)).toBe(false);
    const methods = apiFetchMock.mock.calls.map(
      (call) => (call[1] as RequestInit | undefined)?.method,
    );
    expect(methods).not.toContain("POST");
  });
});

// ---------------------------------------------------------------------------
// Regenerate interaction (Req 17.9)
// ---------------------------------------------------------------------------

describe("CoachPanel — regenerate interaction (Requirement 17.9)", () => {
  it("shows the version/timestamp meta line and replaces the result in place after Regenerate POSTs", async () => {
    apiFetchMock.mockImplementation(async (requestPath: string) => {
      if (requestPath.includes("stream=true")) {
        return sseResponse([
          { event: "complete", data: JSON.stringify(regeneratedEnvelope) },
        ]);
      }
      return jsonResponse(200, {
        items: [persistedEnvelope],
        next_cursor: null,
      });
    });

    renderWithQuery(<CoachPanel matchId={MATCH_ID} />);

    // Persisted result on load, with its prompt version + created_at meta
    // line (Req 17.9's display half).
    await screen.findByText("Persisted coaching summary sentinel.");
    expect(screen.getByText(/Prompt v1/).textContent).toContain("Prompt v1");
    const timeBefore = document.querySelector("time");
    expect(timeBefore).toBeInstanceOf(HTMLElement);
    expect(timeBefore?.getAttribute("datetime")).toBe("2025-06-01T12:00:00Z");

    // Regenerate: an explicit user action re-running the streaming flow.
    fireEvent.click(screen.getByRole("button", { name: /Regenerate/ }));

    // The new result replaces the previous one in place.
    await screen.findByText("Regenerated coaching summary sentinel.");
    expect(
      screen.queryByText("Persisted coaching summary sentinel."),
    ).toBeNull();

    // Its meta line reflects the new prompt version and timestamp.
    expect(screen.getByText(/Prompt v2/).textContent).toContain("Prompt v2");
    const timeAfter = document.querySelector("time");
    expect(timeAfter?.getAttribute("datetime")).toBe("2025-06-02T08:30:00Z");

    // Exactly one streaming POST was issued, by the click.
    const streamCalls = apiFetchMock.mock.calls.filter(isStreamPost);
    expect(streamCalls).toHaveLength(1);
    const streamInit = streamCalls[0]?.[1] as RequestInit | undefined;
    expect(streamInit?.method).toBe("POST");
    expect(streamCalls[0]?.[0]).toContain(
      `/api/v1/matches/${MATCH_ID}/coaching-reports?stream=true`,
    );
  });
});

// ---------------------------------------------------------------------------
// Static check — no dangerouslySetInnerHTML in components/llm/ (Req 17.3)
// ---------------------------------------------------------------------------

/**
 * Strip JS/TS block and line comments so the guard inspects executable code
 * only — the components legitimately *name* `dangerouslySetInnerHTML` in
 * their docstrings to document that it is never used (same pattern as the
 * `results-view` guard in `tests/results-page.test.tsx`).
 */
function stripComments(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/(^|[^:])\/\/[^\n]*/g, "$1");
}

describe("components/llm — no dangerouslySetInnerHTML (Requirement 17.3, security.md)", () => {
  it("no component source file under components/llm/ uses dangerouslySetInnerHTML", () => {
    const componentFiles = readdirSync(here).filter(
      (file) => file.endsWith(".tsx") && !file.includes(".test."),
    );
    // Guard the guard: the directory actually contains the components.
    expect(componentFiles.length).toBeGreaterThanOrEqual(7);

    for (const file of componentFiles) {
      const code = stripComments(
        readFileSync(path.resolve(here, file), "utf8"),
      );
      // No code-level mention of the dangerous sink, and none of its usage
      // markers (the `{ __html }` payload every real call carries).
      expect(code, file).not.toContain("dangerouslySetInnerHTML");
      expect(code, file).not.toContain("__html");
    }
  });
});
