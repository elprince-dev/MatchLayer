/**
 * Server-side session verification for the MatchLayer web app.
 *
 * This module is deliberately NOT a `"use client"` module. It holds the single
 * server-only auth helper, `verifySessionFromRefreshCookie`, which the
 * `(app)/layout.tsx` Authenticated_Shell (a Server Component) calls on every
 * render to decide whether to render the children or `redirect("/login")`.
 *
 * Why it lives apart from `lib/auth.ts`
 * -------------------------------------
 * `lib/auth.ts` is a `"use client"` module (it owns the `useAuth()` hook and
 * the in-memory access-token closure). A `"use client"` directive tags *every*
 * export of that module as a client reference — so importing
 * `verifySessionFromRefreshCookie` from it into a Server Component and calling
 * it throws:
 *
 *   "Attempted to call verifySessionFromRefreshCookie() from the server but
 *    verifySessionFromRefreshCookie is on the client."
 *
 * The function has no client dependencies (no React, no browser globals, no
 * server-only imports) — it is a pure async function over its inputs and
 * `fetch`. Hosting it in this neutral module lets a Server Component import and
 * call it directly, while the client closure/hook stay in `lib/auth.ts`.
 *
 * Like before, it takes `headers` and `cookies` as function references
 * (supplied by the caller, which imports them from `next/headers`) so this
 * module never imports the server-only `next/headers` itself and stays
 * unit-testable without a Next.js request context.
 */

import type { AuthUser } from "./auth";

/**
 * Absolute base URL the *server* uses to reach the API.
 *
 * The browser talks to the API through same-origin relative `/api/...` URLs
 * (the Next dev proxy / production gateway), so `apiBaseUrl` in `./api` is
 * intentionally blank. But server-side `fetch` (this module runs in the Next
 * server) cannot use a relative URL — it needs an absolute origin. We read a
 * server-only var (`MATCHLAYER_API_PROXY_TARGET`, the same one the dev rewrite
 * uses) and fall back to the conventional local API origin.
 */
const serverApiBaseUrl: string =
  process.env.MATCHLAYER_API_PROXY_TARGET ?? "http://localhost:8000";

/**
 * Result of {@link verifySessionFromRefreshCookie}. The Authenticated_Shell
 * layout consumes both fields — the user for rendering, the access token for
 * hydrating the client closure on first paint so leaf components don't have to
 * wait for a second `/refresh` round-trip.
 */
export interface ServerSession {
  accessToken: string;
  user: AuthUser;
}

/**
 * Body shape of `POST /api/v1/auth/refresh` (the design's `TokenPairResponse`).
 * Defined locally so this module has no runtime dependency on the
 * `"use client"` `lib/auth.ts`; only the erased `AuthUser` type is imported.
 */
interface TokenPairResponse {
  access_token: string;
  user: AuthUser;
}

/**
 * Minimal structural type for `next/headers`'s `headers()` return value.
 * Declared locally so this module never imports server-only `next/headers`.
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
    res = await fetch(`${serverApiBaseUrl}/api/v1/auth/refresh`, {
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
