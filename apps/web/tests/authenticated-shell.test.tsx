/**
 * Component test for the (app) Authenticated_Shell layout.
 *
 * Validates Requirement 12.4 (null session → redirect to /login?next=...) and
 * Requirement 12.5 (valid session → render children with the user's display
 * name and inject the access token into the client closure).
 *
 * The shell is an async Server Component that:
 *   - Calls verifySessionFromRefreshCookie({ headers, cookies }).
 *   - On null → calls redirect(`/login?next=${encodeURIComponent(url)}`).
 *   - On session → renders <AppShellClient accessToken={...} user={...}>{children}</AppShellClient>.
 *
 * The AppShellClient injects the access token via useEffect(setAccessToken)
 * and renders the user's display name + sign-out button. We verify both the
 * server-side redirect logic (by mocking redirect + verifySessionFromRefreshCookie)
 * and the client-side render of AppShellClient directly.
 *
 * @vitest-environment jsdom
 */

import * as React from "react";

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// Mock dependencies before importing the layout.
const redirectMock = vi.fn((url: string) => {
  // Next.js redirect throws to halt rendering.
  throw new Error(`NEXT_REDIRECT:${url}`);
});

const verifySessionMock = vi.fn();

const setAccessTokenMock = vi.fn();

vi.mock("next/navigation", () => ({
  redirect: (url: string) => redirectMock(url),
}));

vi.mock("next/headers", () => ({
  headers: vi.fn(async () => ({
    get: (name: string) => {
      if (name === "x-url") return "/dashboard";
      if (name === "x-invoke-path") return "/dashboard";
      return null;
    },
  })),
  cookies: vi.fn(async () => ({
    toString: (): string => "",
  })),
}));

vi.mock("@/lib/auth", async () => {
  const actual = await vi.importActual<Record<string, unknown>>("@/lib/auth");
  return {
    ...actual,
    verifySessionFromRefreshCookie: (...args: unknown[]) =>
      verifySessionMock(...args),
    setAccessToken: (...args: unknown[]) => setAccessTokenMock(...args),
  };
});

import AppLayout from "@/app/(app)/layout";
import { AppShellClient } from "@/app/(app)/shell-client";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

beforeEach(() => {
  redirectMock.mockClear();
  verifySessionMock.mockClear();
  setAccessTokenMock.mockClear();
});

function makeWrapper(): React.FC<{ children: React.ReactNode }> {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0, staleTime: 0 },
      mutations: { retry: false },
    },
  });
  return function Wrapper({ children }) {
    return (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );
  };
}

describe("(app)/layout — Authenticated_Shell (Requirement 12.4)", () => {
  it("redirects to /login?next=<url-encoded path> when verifySessionFromRefreshCookie returns null", async () => {
    verifySessionMock.mockResolvedValue(null);

    // The layout calls redirect() which throws. We catch that to assert
    // the URL the layout attempted to redirect to.
    let redirectError: Error | null = null;
    try {
      await AppLayout({ children: <div>protected</div> });
    } catch (err) {
      redirectError = err as Error;
    }

    expect(redirectError).not.toBeNull();
    expect(redirectMock).toHaveBeenCalledTimes(1);
    const calledWith = redirectMock.mock.calls[0]![0]!;
    expect(calledWith).toMatch(/^\/login\?next=/);
    // The next param must be URL-encoded.
    expect(calledWith).toContain(encodeURIComponent("/dashboard"));
  });

  it("renders the AppShellClient with children when verifySessionFromRefreshCookie returns a session", async () => {
    verifySessionMock.mockResolvedValue({
      accessToken: "fresh.access.token",
      user: {
        id: "01938f00-0000-7000-8000-000000000001",
        email: "alice@example.com",
        display_name: "alice",
        created_at: "2025-01-01T00:00:00Z",
        updated_at: "2025-01-01T00:00:00Z",
      },
    });

    const tree = await AppLayout({
      children: <div data-testid="child-content">protected content</div>,
    });

    expect(redirectMock).not.toHaveBeenCalled();

    // The layout returns <AppShellClient>...</AppShellClient>. Render it
    // through the client wrapper to assert children + display name.
    const Wrapper = makeWrapper();
    render(<Wrapper>{tree}</Wrapper>);

    expect(screen.getByTestId("child-content")).toBeInstanceOf(HTMLElement);
    expect(screen.getByText("alice")).toBeInstanceOf(HTMLElement);
  });
});

/**
 * Option-B regression — public `/` vs gated `/dashboard`.
 *
 * The Authenticated_Shell layout wraps the `(app)` route group only. Per
 * design §13.1 / §13.5 (post-Option-B alignment, see tasks.md §15 deviation
 * note), `/` is the public marketing landing rendered by `app/page.tsx` —
 * which is *outside* `(app)/`, so this layout never runs for `/`. The gated
 * surface lives at `/dashboard` and any future `(app)/*` routes.
 *
 * These tests pin that contract: the redirect target encodes the original
 * `(app)/*` pathname so the user lands back where they came from after
 * sign-in.
 */
describe("(app)/layout — Option B redirect contract for /dashboard", () => {
  it("encodes /dashboard as %2Fdashboard in the next= query param", async () => {
    verifySessionMock.mockResolvedValue(null);

    try {
      await AppLayout({ children: <div>protected</div> });
    } catch {
      /* redirect throws */
    }

    expect(redirectMock).toHaveBeenCalledTimes(1);
    const calledWith = redirectMock.mock.calls[0]![0]!;
    expect(calledWith).toBe(`/login?next=${encodeURIComponent("/dashboard")}`);
    // Belt-and-suspenders: verify the literal encoded form.
    expect(calledWith).toContain("next=%2Fdashboard");
  });

  it("encodes nested (app)/* paths in the next= query param", async () => {
    verifySessionMock.mockResolvedValue(null);

    // Override the headers mock for this case.
    const { headers: headersMock } = await import("next/headers");
    (headersMock as unknown as ReturnType<typeof vi.fn>).mockImplementationOnce(
      async () => ({
        get: (name: string) => {
          if (name === "x-url") return "/dashboard/settings";
          return null;
        },
      }),
    );

    try {
      await AppLayout({ children: <div>protected</div> });
    } catch {
      /* redirect throws */
    }

    expect(redirectMock).toHaveBeenCalledTimes(1);
    const calledWith = redirectMock.mock.calls[0]![0]!;
    expect(calledWith).toContain("next=%2Fdashboard%2Fsettings");
  });
});

describe("AppShellClient (Requirement 12.5 — access-token injection)", () => {
  it("injects the access token into the closure via setAccessToken on mount", () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <AppShellClient
          accessToken="injected.token.value"
          user={{
            id: "01938f00-0000-7000-8000-000000000001",
            email: "alice@example.com",
            display_name: "alice",
          }}
        >
          <div>child</div>
        </AppShellClient>
      </Wrapper>,
    );

    expect(setAccessTokenMock).toHaveBeenCalledWith("injected.token.value");
  });

  it("renders the user's display name and the children", () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <AppShellClient
          accessToken="t"
          user={{
            id: "01938f00-0000-7000-8000-000000000001",
            email: "alice@example.com",
            display_name: "Alice Wonderland",
          }}
        >
          <div data-testid="dash-content">Dashboard</div>
        </AppShellClient>
      </Wrapper>,
    );

    expect(screen.getByText("Alice Wonderland")).toBeInstanceOf(HTMLElement);
    expect(screen.getByTestId("dash-content")).toBeInstanceOf(HTMLElement);
  });
});
