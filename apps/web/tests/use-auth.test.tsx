/**
 * Hook test for `useAuth()` (Frontend Authentication Surface, Requirements
 * 12.5 + 12.6, Frontend Architecture §13.3 / §13.4).
 *
 * Two contract surfaces are under test in this file:
 *
 *   1. **The `UseAuth` shape (Requirement 12.5).** The hook must expose
 *      `user`, `isAuthenticated`, `isLoading`, `signIn`, `signOut`, and
 *      `refresh`. A consumer driving the three async mutations through a
 *      complete login / refresh / logout cycle should see the booleans and
 *      the `user` field flip in lockstep with the underlying access-token
 *      closure that backs the hook (`apps/web/src/lib/auth.ts` §13.4).
 *
 *   2. **The in-memory-only-token property (Requirement 12.6 + `security.md`
 *      "Tokens — never in localStorage").** Across the full sign-in →
 *      refresh → sign-out flow, the hook must never write the access token
 *      to `window.localStorage`, `window.sessionStorage`, or
 *      `document.cookie`. We assert this by installing setter spies on all
 *      three surfaces *before* the hook ever runs and confirming, after
 *      every mutation has settled, that no recorded write argument contains
 *      the access-token bytes.
 *
 * Why hand-rolled fetch + cookie spies instead of msw/cookie-jar libraries:
 * Phase 1 already pulls in TanStack Query, React Hook Form, Zod, and
 * axe-core for the wider auth surface; adding msw or a cookie-jar shim for
 * one hook test would widen the dependency surface beyond what
 * `tech.md` "Frontend testing" enumerates. The hook calls exactly four
 * endpoints with one happy-path response each, which is well below the
 * complexity threshold where msw earns its weight.
 *
 * Why the `cookieJar` getter still returns a valid `matchlayer_csrf` value:
 * `lib/auth.ts`'s `readCsrfCookie()` runs synchronously inside `signOut`
 * and `refresh` to populate the `X-CSRF-Token` header (Requirement 9.3).
 * If the getter returned an empty string, those mutations would still send
 * the request without the header, and the test would still pass, but it
 * would diverge from production cookie state in a way that hides future
 * regressions. Mirroring real cookie state on the read side keeps the
 * write-side assertion the only thing the test is gating on.
 *
 * Why module-closure reset lives in both `beforeEach` and `afterEach`:
 * `lib/auth.ts` keeps the access token in a module-level closure that
 * survives across `vi.test` invocations in the same file. `beforeEach`
 * starts every test from a clean anonymous state (`token === null`);
 * `afterEach` clears the closure again so a leaked subscriber from a
 * later React test (e.g. one that asserts pre-mount defaults) cannot
 * observe a token from a prior test's sign-in. Using both ends keeps
 * either-leak symmetric.
 *
 * @vitest-environment jsdom
 */

import * as React from "react";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { setAccessToken, useAuth } from "@/lib/auth";

// ---------------------------------------------------------------------------
// Test fixtures
// ---------------------------------------------------------------------------

/**
 * The two distinct access tokens minted across the flow. Two values rather
 * than one so the Requirement 12.6 assertion catches a regression that
 * accidentally persists *either* token (e.g. a future change that writes
 * the post-refresh token while skipping the post-login one).
 */
const ACCESS_TOKEN_LOGIN = "test.access.token.minted-by-login.v1";
const ACCESS_TOKEN_REFRESH = "test.access.token.minted-by-refresh.v2";

/**
 * Shape mirrors the `AuthUser` interface defined in `lib/auth.ts`. Hand-
 * typed to match the inline `AuthUser` declaration there until codegen
 * lifts the type into `@matchlayer/shared-types` (see the file-level
 * docstring on `lib/auth.ts`).
 */
const USER = {
  id: "01938f00-0000-7000-8000-000000000001",
  email: "alice@example.com",
  display_name: "alice",
  created_at: "2025-01-01T00:00:00Z",
  updated_at: "2025-01-01T00:00:00Z",
} as const;

/**
 * The CSRF cookie value the read side mirrors. The actual value doesn't
 * matter — `readCsrfCookie` only echoes whatever it finds — but we keep it
 * obviously test-only so a stray log or error message points back to this
 * file rather than implying a leaked production cookie.
 */
const CSRF_COOKIE_VALUE = "csrf-test-value";

// ---------------------------------------------------------------------------
// Storage / cookie write spies
// ---------------------------------------------------------------------------

let cookieSetSpy: ReturnType<typeof vi.fn>;
let localSetItemSpy: ReturnType<typeof vi.spyOn>;
let sessionSetItemSpy: ReturnType<typeof vi.spyOn>;

/**
 * Install the three spies before the hook is allowed to run. The cookie
 * setter is replaced via `Object.defineProperty` on the document instance
 * so the prototype's getter/setter pair is shadowed for the duration of
 * the test; `restoreStorageSpies` deletes the own property to expose the
 * prototype again. The localStorage / sessionStorage spies use `vi.spyOn`
 * because the storage objects' `setItem` is a normal own method on the
 * jsdom Storage prototype.
 */
function installStorageSpies(): void {
  cookieSetSpy = vi.fn();
  Object.defineProperty(document, "cookie", {
    configurable: true,
    get: () => `matchlayer_csrf=${CSRF_COOKIE_VALUE}`,
    set: cookieSetSpy as unknown as (value: string) => void,
  });
  localSetItemSpy = vi.spyOn(window.localStorage, "setItem");
  sessionSetItemSpy = vi.spyOn(window.sessionStorage, "setItem");
}

function restoreStorageSpies(): void {
  // Delete the own property to unshadow the jsdom prototype descriptor.
  delete (document as { cookie?: unknown }).cookie;
  localSetItemSpy.mockRestore();
  sessionSetItemSpy.mockRestore();
}

/**
 * Assert that nothing recorded by the three spies includes either access
 * token. The check stringifies every captured argument because
 * `localStorage.setItem(key, value)` and `sessionStorage.setItem(key, value)`
 * accept two arguments, either of which could carry the token, while
 * `document.cookie = "..."` accepts one. Joining all arguments per call
 * means a regression that splits the token across the key+value pair (e.g.
 * `setItem("matchlayer_access", token)`) is caught the same way as one
 * that puts it all in the value.
 */
function assertAccessTokenNeverPersisted(): void {
  const forbidden = [ACCESS_TOKEN_LOGIN, ACCESS_TOKEN_REFRESH];

  for (const call of cookieSetSpy.mock.calls) {
    // `document.cookie = "..."` is a single-argument setter.
    const written = String(call[0] ?? "");
    for (const token of forbidden) {
      expect(
        written.includes(token),
        `document.cookie was written with the access token: ${written}`,
      ).toBe(false);
    }
  }

  for (const call of localSetItemSpy.mock.calls) {
    const joined = call.map((arg) => String(arg ?? "")).join("|");
    for (const token of forbidden) {
      expect(
        joined.includes(token),
        `localStorage.setItem was called with the access token: ${joined}`,
      ).toBe(false);
    }
  }

  for (const call of sessionSetItemSpy.mock.calls) {
    const joined = call.map((arg) => String(arg ?? "")).join("|");
    for (const token of forbidden) {
      expect(
        joined.includes(token),
        `sessionStorage.setItem was called with the access token: ${joined}`,
      ).toBe(false);
    }
  }
}

// ---------------------------------------------------------------------------
// fetch stub
// ---------------------------------------------------------------------------

/**
 * Outcome shape returned by the per-test `fetchHandler`. Status-only is
 * sufficient for 204 responses; bodies are JSON-serialized into the
 * `Response` so the hook's `await res.json()` calls work unchanged.
 */
interface StubResponse {
  status: number;
  body?: unknown;
}

/**
 * Per-test handler. Each test installs its own dispatch table; the default
 * routes every call to a 500 so a missed route reads as an obvious failure
 * rather than a silent fallthrough.
 */
let fetchHandler: (url: string) => StubResponse;

function buildResponse(stub: StubResponse): Response {
  if (stub.status === 204) {
    return new Response(null, { status: 204 });
  }
  return new Response(JSON.stringify(stub.body ?? null), {
    status: stub.status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  // Reset module-level closure before any subscriber observes leaked state.
  setAccessToken(null);
  installStorageSpies();
  fetchHandler = () => ({ status: 500, body: { detail: "no route" } });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL): Promise<Response> => {
      const url =
        typeof input === "string"
          ? input
          : input instanceof URL
            ? input.toString()
            : input.url;
      return buildResponse(fetchHandler(url));
    }),
  );
});

afterEach(() => {
  // React's cleanup must run before we tear down the storage spies — an
  // unmounting component may run effects that touch the storage surface,
  // and we want those calls counted toward the assertions.
  cleanup();
  restoreStorageSpies();
  setAccessToken(null);
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

// ---------------------------------------------------------------------------
// Hook wrapper
// ---------------------------------------------------------------------------

/**
 * `useAuth` calls `useQuery` and `useMutation`, both of which require a
 * `QueryClientProvider` ancestor. A fresh `QueryClient` per test avoids
 * cross-test cache leakage; retries are disabled because the stub fetch is
 * deterministic and waiting out exponential backoff would just slow the
 * suite down.
 */
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

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("useAuth — Requirement 12.5 contract surface", () => {
  it("exposes the documented anonymous-state shape on first render", () => {
    // Anonymous defaults are the load-bearing baseline for every page that
    // gates on `isAuthenticated`. If this snapshot drifts, every consuming
    // form has to be re-checked, so the assertion is on the whole shape
    // rather than one field.
    const { result } = renderHook(() => useAuth(), {
      wrapper: makeWrapper(),
    });

    expect(result.current.user).toBeNull();
    expect(result.current.isAuthenticated).toBe(false);
    expect(result.current.isLoading).toBe(false);
    expect(typeof result.current.signIn).toBe("function");
    expect(typeof result.current.signOut).toBe("function");
    expect(typeof result.current.refresh).toBe("function");
  });
});

describe("useAuth — full signIn → refresh → signOut flow (Requirement 12.5)", () => {
  it("flips isAuthenticated and user in lockstep with the underlying token across the flow", async () => {
    // Wire one happy-path response per endpoint. `/me` is included for
    // completeness even though `signIn` and `refresh` pre-populate the
    // TanStack Query cache via `setQueryData` — a future refactor that
    // drops the cache priming would route through `/me` instead, and the
    // handler stays correct under that change.
    fetchHandler = (url) => {
      if (url.endsWith("/api/v1/auth/login")) {
        return {
          status: 200,
          body: { access_token: ACCESS_TOKEN_LOGIN, user: USER },
        };
      }
      if (url.endsWith("/api/v1/auth/me")) {
        return { status: 200, body: USER };
      }
      if (url.endsWith("/api/v1/auth/refresh")) {
        return {
          status: 200,
          body: { access_token: ACCESS_TOKEN_REFRESH, user: USER },
        };
      }
      if (url.endsWith("/api/v1/auth/logout")) {
        return { status: 204 };
      }
      return { status: 500, body: { detail: "no route" } };
    };

    const { result } = renderHook(() => useAuth(), {
      wrapper: makeWrapper(),
    });

    // signIn
    await act(async () => {
      await result.current.signIn("alice@example.com", "Password!12345");
    });
    expect(result.current.isAuthenticated).toBe(true);
    await waitFor(() => {
      expect(result.current.user).toEqual(USER);
    });

    // refresh — `isAuthenticated` stays true; `user` re-resolves from the
    // refreshed-token cache key. `waitFor` because the queryKey transition
    // dispatches one extra React render before the cached `setQueryData`
    // value lands on the consumer's `user` selector.
    await act(async () => {
      await result.current.refresh();
    });
    expect(result.current.isAuthenticated).toBe(true);
    await waitFor(() => {
      expect(result.current.user).toEqual(USER);
    });

    // signOut — both halves of the surface clear in one synchronous tick
    // because `setAccessToken(null)` notifies subscribers before the
    // mutation's promise resolves, and the `removeQueries` call drops the
    // cached `/me` data on the same path.
    await act(async () => {
      await result.current.signOut();
    });
    expect(result.current.isAuthenticated).toBe(false);
    expect(result.current.user).toBeNull();
  });
});

describe("useAuth — Requirement 12.6 access-token-in-memory-only", () => {
  it("never writes the access token to localStorage, sessionStorage, or document.cookie across the full flow", async () => {
    fetchHandler = (url) => {
      if (url.endsWith("/api/v1/auth/login")) {
        return {
          status: 200,
          body: { access_token: ACCESS_TOKEN_LOGIN, user: USER },
        };
      }
      if (url.endsWith("/api/v1/auth/me")) {
        return { status: 200, body: USER };
      }
      if (url.endsWith("/api/v1/auth/refresh")) {
        return {
          status: 200,
          body: { access_token: ACCESS_TOKEN_REFRESH, user: USER },
        };
      }
      if (url.endsWith("/api/v1/auth/logout")) {
        return { status: 204 };
      }
      return { status: 500, body: { detail: "no route" } };
    };

    const { result } = renderHook(() => useAuth(), {
      wrapper: makeWrapper(),
    });

    // Drive every mutation the contract exposes so a regression in any one
    // of them gets caught by the same assertion. Awaiting through `act`
    // flushes mutation `onSuccess` and `onSettled` callbacks before the
    // assertion runs, so a hypothetical "persist on success" handler would
    // already have written by the time we check.
    await act(async () => {
      await result.current.signIn("alice@example.com", "Password!12345");
    });
    await waitFor(() => {
      expect(result.current.user).toEqual(USER);
    });

    await act(async () => {
      await result.current.refresh();
    });
    await waitFor(() => {
      expect(result.current.user).toEqual(USER);
    });

    await act(async () => {
      await result.current.signOut();
    });

    // Sanity floor: the flow must have actually exercised the network. A
    // green test that never called `fetch` would pass the persistence
    // assertion vacuously, which would be a worse regression than the one
    // the test is guarding against.
    const fetchMock = vi.mocked(fetch);
    expect(fetchMock).toHaveBeenCalled();

    assertAccessTokenNeverPersisted();
  });
});
