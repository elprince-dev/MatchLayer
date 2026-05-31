/**
 * Component test for the (app) Authenticated_Shell layout + AppShellClient.
 *
 * Post-split-origin behavior (see `(app)/layout.tsx` and `shell-client.tsx`):
 *
 *   - The layout is an async Server Component that calls
 *     `verifySessionFromRefreshCookie({ headers, cookies })` and ALWAYS renders
 *     `<AppShellClient accessToken={...} user={...}>` — passing the
 *     server-acquired token/user when verification succeeded (same-origin
 *     production path), or `null`/`null` when it could not (split-origin local
 *     dev, where the refresh cookie lives on the API origin and never reaches
 *     the Next.js server). The layout no longer calls `redirect()` — gating on
 *     the unverifiable case is delegated to the client.
 *
 *   - `AppShellClient`:
 *       • server-verified (token non-null) → injects the token via
 *         `setAccessToken` and renders the chrome + children immediately;
 *       • not server-verified (token null) → attempts client recovery via
 *         `useAuth().refresh()`; while pending it shows a loading state; if the
 *         client is authenticated it renders the shell; otherwise it
 *         `router.replace("/login?next=<path>")`.
 *
 * @vitest-environment jsdom
 */

import * as React from "react";

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const setAccessTokenMock = vi.fn();
const getAccessTokenMock = vi.fn<() => string | null>(() => null);
const verifySessionMock = vi.fn();

// next/navigation: the client shell uses useRouter().replace and usePathname.
const replaceMock = vi.fn();
const pushMock = vi.fn();
let pathnameValue = "/dashboard";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: replaceMock, push: pushMock }),
  usePathname: () => pathnameValue,
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

// useAuth is driven per-test via this mutable object so each test can simulate
// "client has a session" vs "client is anonymous after recovery".
interface MockAuth {
  user: { id: string; email: string; display_name: string } | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  refresh: ReturnType<typeof vi.fn>;
  signOut: ReturnType<typeof vi.fn>;
}

let mockAuth: MockAuth;

vi.mock("@/lib/auth", () => ({
  setAccessToken: (...args: unknown[]) => setAccessTokenMock(...args),
  getAccessToken: () => getAccessTokenMock(),
  useAuth: () => mockAuth,
}));

vi.mock("@/lib/auth-server", () => ({
  verifySessionFromRefreshCookie: (...args: unknown[]) =>
    verifySessionMock(...args),
}));

import AppLayout from "@/app/(app)/layout";
import { AppShellClient } from "@/app/(app)/shell-client";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

beforeEach(() => {
  pathnameValue = "/dashboard";
  getAccessTokenMock.mockReturnValue(null);
  mockAuth = {
    user: null,
    isAuthenticated: false,
    isLoading: false,
    refresh: vi.fn(async () => {}),
    signOut: vi.fn(async () => {}),
  };
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

const SERVER_USER = {
  id: "01938f00-0000-7000-8000-000000000001",
  email: "alice@example.com",
  display_name: "alice",
  created_at: "2025-01-01T00:00:00Z",
  updated_at: "2025-01-01T00:00:00Z",
};

describe("(app)/layout — Authenticated_Shell (server verification path)", () => {
  it("renders the shell with the server token + user when verification succeeds", async () => {
    verifySessionMock.mockResolvedValue({
      accessToken: "fresh.access.token",
      user: SERVER_USER,
    });

    const tree = await AppLayout({
      children: <div data-testid="child-content">protected content</div>,
    });

    const Wrapper = makeWrapper();
    render(<Wrapper>{tree}</Wrapper>);

    // Server-acquired token is injected; chrome + children render immediately.
    expect(setAccessTokenMock).toHaveBeenCalledWith("fresh.access.token");
    expect(screen.getByTestId("child-content")).toBeInstanceOf(HTMLElement);
    expect(screen.getByText("alice")).toBeInstanceOf(HTMLElement);
    // No client-side redirect when the server verified.
    expect(replaceMock).not.toHaveBeenCalled();
  });

  it("does NOT redirect from the server even when verification returns null", async () => {
    // Split-origin dev: server can't see the cookie. The layout must still
    // render (delegating the gate to the client), never throw a redirect.
    verifySessionMock.mockResolvedValue(null);

    const tree = await AppLayout({ children: <div>protected</div> });
    // The layout returned a tree rather than throwing NEXT_REDIRECT.
    expect(tree).toBeDefined();
  });
});

describe("AppShellClient — server-verified path (Requirement 12.5)", () => {
  it("injects the access token and renders display name + children", () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <AppShellClient
          accessToken="injected.token.value"
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

    expect(setAccessTokenMock).toHaveBeenCalledWith("injected.token.value");
    expect(screen.getByText("Alice Wonderland")).toBeInstanceOf(HTMLElement);
    expect(screen.getByTestId("dash-content")).toBeInstanceOf(HTMLElement);
    expect(replaceMock).not.toHaveBeenCalled();
  });
});

describe("AppShellClient — split-origin client recovery", () => {
  it("renders the shell when the client already holds a token (post-login)", async () => {
    // No server token, but the login form put a token in the closure and
    // useAuth reports authenticated.
    getAccessTokenMock.mockReturnValue("client.token");
    mockAuth = {
      user: { id: "u1", email: "a@b.co", display_name: "Bob" },
      isAuthenticated: true,
      isLoading: false,
      refresh: vi.fn(async () => {}),
      signOut: vi.fn(async () => {}),
    };

    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <AppShellClient accessToken={null} user={null}>
          <div data-testid="dash">Dashboard</div>
        </AppShellClient>
      </Wrapper>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("dash")).toBeInstanceOf(HTMLElement);
    });
    expect(screen.getByText("Bob")).toBeInstanceOf(HTMLElement);
    expect(replaceMock).not.toHaveBeenCalled();
  });

  it("redirects to /login?next=<path> when recovery finds no session", async () => {
    pathnameValue = "/dashboard/settings";
    // No server token, no client token, recovery refresh resolves anonymous.
    getAccessTokenMock.mockReturnValue(null);
    mockAuth = {
      user: null,
      isAuthenticated: false,
      isLoading: false,
      refresh: vi.fn(async () => {}),
      signOut: vi.fn(async () => {}),
    };

    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <AppShellClient accessToken={null} user={null}>
          <div data-testid="dash">Dashboard</div>
        </AppShellClient>
      </Wrapper>,
    );

    await waitFor(() => {
      expect(replaceMock).toHaveBeenCalledWith(
        `/login?next=${encodeURIComponent("/dashboard/settings")}`,
      );
    });
  });
});
