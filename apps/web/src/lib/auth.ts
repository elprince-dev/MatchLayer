"use client";

/**
 * Auth state hook + closure-backed access-token store for the MatchLayer web
 * app. This module is the single source of truth for authentication state on
 * the frontend and the bridge between server-rendered Authenticated_Shell
 * verification and the client tree's React subscribers.
 *
 * What this file owns
 * --------------------
 * 1. The in-memory **access token store**. The token lives in a module-level
 *    closure (`accessToken`) plus a `Set` of subscriber callbacks. Per
 *    Requirement 12.6 — the access token is NEVER written to
 *    `window.localStorage`, `window.sessionStorage`, or `document.cookie`. The
 *    only persistent surface is the refresh cookie (`matchlayer_refresh`,
 *    `HttpOnly`, set by the API), which the browser sends back on its own.
 *
 * 2. The `useAuth()` hook (Requirement 12.5). Composed via React's
 *    `useSyncExternalStore` over the closure store plus a TanStack Query
 *    `useQuery` for `GET /api/v1/auth/me`. Mutations for `signIn`, `signOut`,
 *    and `refresh` keep the closure and the query cache in lockstep.
 *
 * 3. The server-side helper `verifySessionFromRefreshCookie` consumed by the
 *    `(app)/layout.tsx` Server Component (Frontend Architecture §13.5). It
 *    forwards the inbound `Cookie` header (and `X-CSRF-Token` from the sibling
 *    `matchlayer_csrf` cookie, since the API double-submit check sees the
 *    refresh cookie present) to `POST /api/v1/auth/refresh`. On success it
 *    returns the fresh access token + user payload so the layout can hand them
 *    down. On 401/403/network-error it returns null so the layout redirects
 *    to `/login?next=...`.
 *
 * Why a module closure instead of a React Context
 * -----------------------------------------------
 * A Context provider would force every consumer of `useAuth()` to live under a
 * `<Provider>` boundary. That pushes one extra `'use client'` boundary up the
 * tree and breaks the Server-Component-first layout pattern. The closure +
 * `useSyncExternalStore` pair lets the Authenticated_Shell stay a pure Server
 * Component while the leaf interactive forms reach into this module from
 * inside their own `'use client'` boundary. Per Requirement 12.6, neither
 * pattern would write to `localStorage`; the closure simply matches the
 * SC-first house style better.
 *
 * Why both halves coexist in one `'use client'` file
 * --------------------------------------------------
 * The file has no server-only imports — `verifySessionFromRefreshCookie`
 * receives `headers` and `cookies` as function references from its caller
 * (which imports them from `next/headers`) rather than importing them itself.
 * The closure state lives in whichever bundle imports the module, which means:
 *   - In the client bundle the closure holds the active session token.
 *   - In the server bundle the closure exists but is never written; the server
 *     helper is a pure function over its inputs and the network.
 * That isolation is the security property — the access token never persists
 * across server requests.
 *
 * Why a hand-rolled `AuthUser` type for now
 * -----------------------------------------
 * Per `conventions.md` "Shared schemas — single source of truth", the
 * canonical `AuthUser` type is `paths["/api/v1/auth/me"]["get"]...` from
 * `@matchlayer/shared-types`. Tasks 10.1/10.2 of `phase-1-auth` regenerate
 * those bindings off the FastAPI OpenAPI spec; until that codegen pass lands
 * the auth endpoints, this file mirrors the documented Requirement 12.5 shape
 * inline so the web app typechecks. Once the curated `AuthUser` re-export
 * exists in `@matchlayer/shared-types`, swap the local `AuthUser` declaration
 * for an `import type { AuthUser } from "@matchlayer/shared-types"`.
 */

import { useSyncExternalStore } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiBaseUrl } from "./api";

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

/**
 * Shape of the authenticated user returned by `GET /api/v1/auth/me` per
 * Requirement 6.3 and Requirement 12.5. See the file-level docstring for the
 * `@matchlayer/shared-types` migration plan.
 */
export interface AuthUser {
  id: string;
  email: string;
  display_name: string;
  created_at: string;
  updated_at: string;
}

/**
 * The contract returned by `useAuth()` (Requirement 12.5). The minimum surface
 * the spec requires: current user (or null), boolean status flags, and the
 * three auth mutations (`signIn`, `signOut`, `refresh`). Everything else
 * downstream — form-error rendering, retry-after countdown, redirect-after-
 * login — composes on top of this.
 */
export interface UseAuth {
  user: AuthUser | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  signIn(email: string, password: string): Promise<void>;
  signOut(): Promise<void>;
  refresh(): Promise<void>;
}

/**
 * Result of `verifySessionFromRefreshCookie`. The Authenticated_Shell layout
 * consumes both fields — the user for rendering, the access token for
 * hydrating the client closure on first paint so the leaf components don't
 * have to wait for a second `/refresh` round-trip.
 */
export interface ServerSession {
  accessToken: string;
  user: AuthUser;
}

// ---------------------------------------------------------------------------
// Module-level closure store (Requirement 12.6, Design §13.4)
// ---------------------------------------------------------------------------

/**
 * The current access token, or null when the user is not authenticated. This
 * is the only persistent storage of the access token in the browser; per
 * Requirement 12.6 the value never reaches `localStorage`, `sessionStorage`,
 * or `document.cookie`. On a full page reload the closure resets to `null`
 * and the Authenticated_Shell rehydrates it via the refresh cookie.
 */
let accessToken: string | null = null;

/**
 * React subscribers registered through `useSyncExternalStore`. Each entry is
 * a callback that asks React to re-read the snapshot. Stored in a `Set` so
 * unsubscribe is O(1) and double-subscribe is idempotent.
 */
const subscribers = new Set<() => void>();

/**
 * Read the current access token. Used by `useAuth()` and by the API client
 * (`./api.ts`) when attaching `Authorization: Bearer ...`.
 */
export function getAccessToken(): string | null {
  return accessToken;
}

/**
 * Replace the current access token and notify every subscriber. Passing
 * `null` clears the token (sign-out, expired refresh, etc.) and forces every
 * `useAuth()` consumer to re-render with `isAuthenticated === false`.
 *
 * Notification is intentionally synchronous — `useSyncExternalStore` requires
 * subscribers to receive updates before the next paint to avoid tearing.
 */
export function setAccessToken(token: string | null): void {
  accessToken = token;
  for (const cb of subscribers) {
    cb();
  }
}

/**
 * Register a subscriber. The returned function unsubscribes; React's
 * `useSyncExternalStore` calls it on unmount.
 */
export function subscribe(cb: () => void): () => void {
  subscribers.add(cb);
  return () => {
    subscribers.delete(cb);
  };
}

// ---------------------------------------------------------------------------
// Internal: cookie + network helpers
// ---------------------------------------------------------------------------

/**
 * Read the `matchlayer_csrf` cookie value (the JS-readable half of the
 * double-submit pair) so the client can echo it on `/refresh` and `/logout`
 * per Requirement 9.3. Returns `null` when the cookie is absent — the API
 * accepts a missing CSRF header iff the refresh cookie is also absent
 * (Requirement 9.4 + design §9.3), so this matches the contract.
 *
 * Lives behind a `typeof document` guard so the module is safe to import in
 * Server Components (the SSR pass returns `null`, the function is only ever
 * called from the client mutations).
 */
function readCsrfCookie(): string | null {
  if (typeof document === "undefined") {
    return null;
  }
  const match = document.cookie.match(/(?:^|;\s*)matchlayer_csrf=([^;]+)/);
  if (!match || match[1] === undefined) {
    return null;
  }
  return decodeURIComponent(match[1]);
}

/**
 * Body shape of `/api/v1/auth/login`, `/api/v1/auth/register`, and
 * `/api/v1/auth/refresh` per the design's `TokenPairResponse`. Hand-typed
 * here for the same codegen-not-yet-run reason as `AuthUser`; both will be
 * lifted to `@matchlayer/shared-types` once codegen catches up.
 */
interface TokenPairResponse {
  access_token: string;
  user: AuthUser;
}

/**
 * Error type thrown by `signIn` so the form layer can render the right
 * envelope without re-fetching. Carries:
 *   - `status`: HTTP status, used to branch on 401/423/429.
 *   - `retryAfterSeconds`: parsed from the `Retry-After` header on 429
 *     responses (Requirement 12.8).
 *   - `body`: parsed RFC 7807 envelope for direct rendering when present.
 */
export class AuthRequestError extends Error {
  readonly status: number;
  readonly retryAfterSeconds: number | null;
  readonly body: unknown;

  constructor(
    message: string,
    options: {
      status: number;
      retryAfterSeconds: number | null;
      body: unknown;
    },
  ) {
    super(message);
    this.name = "AuthRequestError";
    this.status = options.status;
    this.retryAfterSeconds = options.retryAfterSeconds;
    this.body = options.body;
  }
}

/**
 * Parse the `Retry-After` header into a number of seconds. The HTTP spec
 * permits both an integer-second form and an HTTP-date form; the FastAPI
 * rate-limiter only emits the integer-second form (Rate Limiting §10.5), so
 * we accept that and fall back to `null` for anything else. Rounded up so a
 * fractional value never displays as "0 seconds".
 */
function parseRetryAfterSeconds(header: string | null): number | null {
  if (header === null) {
    return null;
  }
  const seconds = Number(header);
  if (!Number.isFinite(seconds) || seconds < 0) {
    return null;
  }
  return Math.ceil(seconds);
}

/**
 * Common 4xx/5xx-to-error pipeline. Reads the response body as JSON if
 * possible (the FastAPI RFC 7807 envelope), otherwise as plain text, and
 * throws an `AuthRequestError` carrying everything the form layer needs to
 * render a faithful message.
 */
async function throwForStatus(
  res: Response,
  fallbackMessage: string,
): Promise<never> {
  let body: unknown = null;
  try {
    body = await res.clone().json();
  } catch {
    try {
      body = await res.text();
    } catch {
      body = null;
    }
  }
  const detail =
    body !== null &&
    typeof body === "object" &&
    "detail" in body &&
    typeof (body as { detail: unknown }).detail === "string"
      ? (body as { detail: string }).detail
      : fallbackMessage;
  throw new AuthRequestError(detail, {
    status: res.status,
    retryAfterSeconds: parseRetryAfterSeconds(res.headers.get("Retry-After")),
    body,
  });
}

/**
 * Call `POST /api/v1/auth/login`. Returns the parsed token pair on success,
 * throws an `AuthRequestError` carrying the full envelope on any 4xx/5xx so
 * the form layer can render the right message (Requirement 12.7, 12.8).
 *
 * `credentials: "include"` is required so the browser accepts the
 * `Set-Cookie: matchlayer_refresh=...` response header for cross-origin dev
 * (Next.js dev server vs FastAPI dev server on different ports).
 */
async function postLogin(
  email: string,
  password: string,
): Promise<TokenPairResponse> {
  const res = await fetch(`${apiBaseUrl}/api/v1/auth/login`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!res.ok) {
    await throwForStatus(res, "Email or password is incorrect.");
  }
  return (await res.json()) as TokenPairResponse;
}

/**
 * Call `POST /api/v1/auth/refresh` from the client. Returns the new token
 * pair on success, `null` on any non-2xx (which the caller treats as
 * "session ended, redirect to /login"). Forwards the `X-CSRF-Token` header
 * read from the `matchlayer_csrf` cookie to satisfy the double-submit check
 * (Requirement 9.3).
 */
async function postRefresh(): Promise<TokenPairResponse | null> {
  const csrf = readCsrfCookie();
  const headers: Record<string, string> = {};
  if (csrf !== null) {
    headers["X-CSRF-Token"] = csrf;
  }
  let res: Response;
  try {
    res = await fetch(`${apiBaseUrl}/api/v1/auth/refresh`, {
      method: "POST",
      credentials: "include",
      headers,
    });
  } catch {
    return null;
  }
  if (!res.ok) {
    return null;
  }
  return (await res.json()) as TokenPairResponse;
}

/**
 * Call `POST /api/v1/auth/logout`. The endpoint is intentionally idempotent
 * server-side (Requirement 4.2, 4.6) so we treat any failure as "best-effort
 * already-logged-out" — the client closure is cleared regardless. We do not
 * read the response body; the endpoint returns 204 on success.
 */
async function postLogout(): Promise<void> {
  const csrf = readCsrfCookie();
  const headers: Record<string, string> = {};
  if (csrf !== null) {
    headers["X-CSRF-Token"] = csrf;
  }
  try {
    await fetch(`${apiBaseUrl}/api/v1/auth/logout`, {
      method: "POST",
      credentials: "include",
      headers,
    });
  } catch {
    // Network failure during logout is non-fatal — the client clears its
    // closure regardless, and the refresh token will expire server-side
    // within `MATCHLAYER_AUTH_REFRESH_TOKEN_TTL_SECONDS`.
  }
}

/**
 * Call `GET /api/v1/auth/me` with the supplied Bearer token. Throws on any
 * non-2xx so TanStack Query records the error and downstream selectors see
 * `meQuery.data === undefined`.
 */
async function fetchMe(token: string): Promise<AuthUser> {
  const res = await fetch(`${apiBaseUrl}/api/v1/auth/me`, {
    method: "GET",
    credentials: "include",
    headers: {
      Authorization: `Bearer ${token}`,
    },
  });
  if (!res.ok) {
    await throwForStatus(res, "Could not load the current user.");
  }
  return (await res.json()) as AuthUser;
}

// ---------------------------------------------------------------------------
// `useAuth()` — Auth_State_Hook (Requirement 12.5, Design §13.3, §13.4)
// ---------------------------------------------------------------------------

/**
 * `useSyncExternalStore` requires a server-snapshot getter so it can render
 * during SSR without tearing. The auth closure is empty during SSR (the
 * Authenticated_Shell hydrates it on first client paint); returning `null`
 * here is the deliberate semantic — "no token until the client says
 * otherwise."
 */
function getServerSnapshot(): string | null {
  return null;
}

/**
 * The Auth_State_Hook (Requirement 12.5). Every leaf component that needs
 * to know whether the user is signed in calls this hook. Internally:
 *   1. `useSyncExternalStore` over the module closure produces a `token`
 *      that flips synchronously when `setAccessToken` is called.
 *   2. A `useQuery` keyed off the token loads `GET /me`. The query is
 *      disabled while the token is null (so anonymous renders don't spam
 *      the API).
 *   3. Three mutations (`signIn`, `signOut`, `refresh`) wrap the network
 *      calls and synchronize the closure + the query cache so consumers
 *      see consistent state without an explicit refetch.
 */
export function useAuth(): UseAuth {
  const queryClient = useQueryClient();
  const token = useSyncExternalStore(
    subscribe,
    getAccessToken,
    getServerSnapshot,
  );

  const meQuery = useQuery<AuthUser | null>({
    // Keying on the token means a token swap (refresh, sign-in) invalidates
    // the prior cache entry without an explicit `invalidateQueries` call.
    queryKey: ["auth", "me", token ?? null],
    queryFn: async () => {
      if (token === null) {
        return null;
      }
      return fetchMe(token);
    },
    enabled: token !== null,
    // 30s of staleness is a deliberate compromise: long enough to avoid
    // re-fetching on every render of an authenticated page, short enough
    // that `display_name` edits via `PATCH /me` propagate within one
    // window-focus cycle.
    staleTime: 30_000,
    retry: false,
  });

  const signInMutation = useMutation({
    mutationFn: async (vars: { email: string; password: string }) => {
      return postLogin(vars.email, vars.password);
    },
    onSuccess: (result) => {
      setAccessToken(result.access_token);
      queryClient.setQueryData<AuthUser>(
        ["auth", "me", result.access_token],
        result.user,
      );
    },
    onError: () => {
      // Failed login leaves the closure untouched — a previous session, if
      // any, stays valid; the form renders the error envelope from the
      // thrown `AuthRequestError`.
    },
  });

  const signOutMutation = useMutation({
    mutationFn: async () => {
      await postLogout();
    },
    onSettled: () => {
      // Clear closure + query cache regardless of network outcome — see
      // the rationale on `postLogout`.
      setAccessToken(null);
      queryClient.removeQueries({ queryKey: ["auth", "me"] });
    },
  });

  const refreshMutation = useMutation({
    mutationFn: async () => {
      return postRefresh();
    },
    onSuccess: (result) => {
      if (result === null) {
        setAccessToken(null);
        queryClient.removeQueries({ queryKey: ["auth", "me"] });
        return;
      }
      setAccessToken(result.access_token);
      queryClient.setQueryData<AuthUser>(
        ["auth", "me", result.access_token],
        result.user,
      );
    },
    onError: () => {
      setAccessToken(null);
      queryClient.removeQueries({ queryKey: ["auth", "me"] });
    },
  });

  const user: AuthUser | null = token === null ? null : (meQuery.data ?? null);

  return {
    user,
    isAuthenticated: token !== null,
    // `isLoading` collapses three independent in-flight signals: any
    // pending mutation + the initial `/me` fetch. Form components disable
    // their submit buttons on this single boolean.
    isLoading:
      signInMutation.isPending ||
      signOutMutation.isPending ||
      refreshMutation.isPending ||
      (token !== null && meQuery.isLoading),
    async signIn(email: string, password: string): Promise<void> {
      await signInMutation.mutateAsync({ email, password });
    },
    async signOut(): Promise<void> {
      await signOutMutation.mutateAsync();
    },
    async refresh(): Promise<void> {
      await refreshMutation.mutateAsync();
    },
  };
}

// ---------------------------------------------------------------------------
// Server-side: `verifySessionFromRefreshCookie` (Design §13.5)
// ---------------------------------------------------------------------------

/**
 * Minimal structural type for `next/headers`'s `headers()` return value.
 * Declared locally so this module never imports from `next/headers` and stays
 * safe to ship in the client bundle. The `(app)/layout.tsx` Server Component
 * imports `headers` from `next/headers` itself and passes the function in.
 */
type HeadersFnLike = () =>
  | { get(name: string): string | null }
  | Promise<{ get(name: string): string | null }>;

/**
 * Minimal structural type for `next/headers`'s `cookies()` return value. We
 * need exactly two operations: serialize as a `Cookie` request header
 * (`toString()`) and look up `matchlayer_csrf` for the double-submit echo.
 */
type CookieStoreLike = {
  toString(): string;
  get(name: string): { value: string } | undefined;
};

type CookiesFnLike = () => CookieStoreLike | Promise<CookieStoreLike>;

/**
 * Verify a session by replaying the inbound cookies against
 * `POST /api/v1/auth/refresh`. Used by `(app)/layout.tsx` (Design §13.5) to
 * decide whether to render the children or `redirect("/login?next=...")`.
 *
 * Behavior matrix:
 *   - No `matchlayer_refresh` cookie present → return `null` immediately.
 *     This avoids a guaranteed 401 round-trip and leaves the API audit log
 *     cleaner during anonymous renders.
 *   - `/refresh` returns 2xx → return `{ accessToken, user }` so the layout
 *     can hand the access token to the client tree.
 *   - `/refresh` returns 4xx/5xx, or the network call throws, or the body
 *     is not parseable → return `null`. The layout treats every non-success
 *     identically and redirects.
 *
 * The function takes `headers` and `cookies` as function references (matching
 * the design.md §13.5 call site) so this module never imports `next/headers`
 * directly. That matters for two reasons: (a) the file is a `'use client'`
 * module and `next/headers` is server-only, (b) the function is unit-testable
 * without spinning up a Next.js request context.
 */
export async function verifySessionFromRefreshCookie(input: {
  headers: HeadersFnLike;
  cookies: CookiesFnLike;
}): Promise<ServerSession | null> {
  const [hdrs, cookieStore] = await Promise.all([
    Promise.resolve(input.headers()),
    Promise.resolve(input.cookies()),
  ]);

  const cookieHeader = cookieStore.toString();
  // Fast path — no refresh cookie means we cannot recover a session and the
  // refresh endpoint will return 401 anyway. Skipping the network call also
  // keeps the audit log free of `missing_refresh_cookie` noise on anonymous
  // top-of-funnel landings into the `(app)` route group.
  if (cookieHeader === "" || !cookieHeader.includes("matchlayer_refresh=")) {
    return null;
  }

  const csrfValue = cookieStore.get("matchlayer_csrf")?.value;
  const userAgent = hdrs.get("user-agent");
  const forwardedFor = hdrs.get("x-forwarded-for");

  const requestHeaders: Record<string, string> = {
    Cookie: cookieHeader,
  };
  if (csrfValue !== undefined) {
    // Double-submit echo. Without this header the API returns 403
    // `csrf_mismatch` whenever the refresh cookie is also present.
    requestHeaders["X-CSRF-Token"] = csrfValue;
  }
  if (userAgent !== null) {
    // Forward UA so the audit row's `user_agent` column reflects the real
    // browser, not a Next.js server fingerprint.
    requestHeaders["User-Agent"] = userAgent;
  }
  if (forwardedFor !== null) {
    // Forward client IP for the same reason. The FastAPI request middleware
    // unwraps `X-Forwarded-For` per the foundation contract.
    requestHeaders["X-Forwarded-For"] = forwardedFor;
  }

  let res: Response;
  try {
    res = await fetch(`${apiBaseUrl}/api/v1/auth/refresh`, {
      method: "POST",
      headers: requestHeaders,
      // `cache: "no-store"` is mandatory — Next.js's default fetch cache is
      // shared across requests and would happily serve stale token pairs.
      cache: "no-store",
    });
  } catch {
    return null;
  }

  if (!res.ok) {
    return null;
  }

  let body: TokenPairResponse;
  try {
    body = (await res.json()) as TokenPairResponse;
  } catch {
    return null;
  }

  // Defensive shape check. The codegen pass will eventually replace this with
  // a Zod parse against the curated `TokenPairResponseSchema`, but until then
  // we validate the two fields the layout actually consumes.
  if (
    typeof body.access_token !== "string" ||
    body.user === null ||
    typeof body.user !== "object" ||
    typeof body.user.id !== "string"
  ) {
    return null;
  }

  return { accessToken: body.access_token, user: body.user };
}
