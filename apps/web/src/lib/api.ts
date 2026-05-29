/**
 * API client for the MatchLayer FastAPI backend.
 *
 * This module owns two concerns:
 *
 * 1. The single read of `NEXT_PUBLIC_API_BASE_URL`, exposed as `apiBaseUrl`.
 *    Sibling specs import this constant when wiring fetch calls; the foundation
 *    docstring below is preserved verbatim because `tools/check_env_drift.py`
 *    only sees the env var as referenced if the literal string lives here.
 *
 * 2. `apiFetch`, the Bearer-attaching, 401-retry-via-refresh wrapper around
 *    the platform `fetch`. It implements two acceptance criteria from
 *    Requirement 12 (Frontend Authentication Surface) and the design contract
 *    in Frontend Architecture §13.5:
 *
 *      a. read the in-memory access token via `lib/auth.ts`'s `getAccessToken()`
 *         and attach `Authorization: Bearer <token>` to every outbound request;
 *
 *      b. on a 401 response, attempt one `POST /api/v1/auth/refresh`
 *         (forwarding cookies via `credentials: "include"`), update the
 *         in-memory token via `setAccessToken()` on success, and retry the
 *         original request exactly once. A second 401 propagates to the
 *         caller so the UI can react (typically by signing the user out).
 *
 * Boundary note: this file is intentionally *not* a `"use client"` module.
 * The design tags `lib/api.ts` as "mixed / neutral" — Server Components pass
 * the access token explicitly via the `accessToken` init field, while client
 * code reads the closure store. Importing the plain function exports
 * `getAccessToken` / `setAccessToken` from `./auth` (which itself is
 * `"use client"`) does not pull React or `next/headers` into this module's
 * surface; both are bare functions over a module-level closure.
 */

import { getAccessToken, setAccessToken } from "./auth";

/**
 * Public base URL of the MatchLayer FastAPI backend.
 *
 * This module is the single place the web app reads `NEXT_PUBLIC_API_BASE_URL`,
 * the public env var declared in `.env.example` and consumed by the browser
 * bundle. Sibling specs (`phase-1-auth`, `phase-1-matching`) import
 * `apiBaseUrl` from here when wiring real fetch calls; `apiFetch` below is
 * the canonical consumer of this constant.
 *
 * Why a fallback instead of fail-fast: Next.js statically inlines public env
 * vars (the `NEXT_PUBLIC_` prefix family) at build time, and `pnpm --filter
 * @matchlayer/web build` runs in CI without a populated `.env`. Throwing at
 * module load would break the CI build for a value that has a perfectly
 * sensible local default (the FastAPI dev server, matched by the `connect-src`
 * entry in `apps/web/src/proxy.ts`). Production deploys are responsible for
 * setting the variable explicitly through the host platform (Vercel/Fly env
 * config); a misconfiguration there will surface on the first network call
 * rather than at boot, which is acceptable for a public, non-secret URL.
 */
export const apiBaseUrl: string =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

/**
 * Init shape accepted by `apiFetch`. A `RequestInit` superset with two
 * deliberate additions:
 *
 * - `accessToken`: explicit token override for callers that hold the token
 *   outside the closure store. Server Components are the canonical case
 *   (Frontend Architecture §13.5: the `(app)/layout.tsx` shell receives the
 *   fresh token from `verifySessionFromRefreshCookie` and threads it into
 *   API calls). When the field is *present* (including `null`), `apiFetch`
 *   uses that value as the Authorization source and disables the silent
 *   refresh-and-retry path — Server Components manage their own session
 *   lifecycle and rerunning a refresh from inside an SC fetch would be
 *   surprising. When the field is *absent* (the field is `undefined`,
 *   distinct from being explicitly `null`), `apiFetch` falls back to the
 *   in-memory closure via `getAccessToken()`.
 *
 * The distinction between "absent" and "explicit null" is the reason this
 * field is typed `string | null` and parsed via `in init` rather than via
 * truthiness — passing `null` deliberately disables Bearer attachment for
 * one call without re-engaging the closure or the refresh path.
 */
export interface ApiFetchInit extends RequestInit {
  accessToken?: string | null;
}

// ---------------------------------------------------------------------------
// CSRF cookie reader (mirrors the helper in `./auth` — see boundary note)
// ---------------------------------------------------------------------------

/**
 * Read the `matchlayer_csrf` cookie value (the JS-readable half of the
 * double-submit pair) so the silent-refresh path can echo it on
 * `POST /api/v1/auth/refresh` per Requirement 9.3.
 *
 * Duplicated here rather than imported from `./auth` for two reasons:
 *
 * 1. Keeping `./auth`'s exported surface stable. Task 11.2 fixed
 *    `getAccessToken` / `setAccessToken` / `subscribe` / `useAuth` /
 *    `verifySessionFromRefreshCookie` as the public exports; widening that
 *    surface to expose a 6-line cookie reader would be churn for no
 *    semantic gain.
 *
 * 2. The function is the fast-path predicate for `attemptSilentRefresh`
 *    (no CSRF cookie ⇒ no refresh cookie either, since both are set
 *    together by the API ⇒ skip the network round-trip), so co-locating it
 *    keeps the refresh logic readable.
 *
 * Lives behind a `typeof document` guard so the module is safe to import
 * in Server Components (the SSR pass returns `null`, the function is only
 * ever called from the client retry path).
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

// ---------------------------------------------------------------------------
// Single-flight silent refresh
// ---------------------------------------------------------------------------

/**
 * In-flight refresh promise, used to dedupe concurrent refresh attempts.
 *
 * Without this, a page that fires N requests in parallel and gets N 401s
 * would launch N refresh round-trips and N writes to `setAccessToken`,
 * which is wasted bandwidth at best and a race condition at worst (later
 * refresh responses overwriting earlier ones with stale tokens). By
 * holding a single promise here, every concurrent caller awaits the same
 * underlying `/refresh` call and sees the same outcome.
 */
let refreshInFlight: Promise<string | null> | null = null;

/**
 * Attempt one `POST /api/v1/auth/refresh` and synchronize the closure
 * token store on success. Returns the freshly-issued access token, or
 * `null` when the refresh cannot be completed (no session cookie, network
 * failure, non-2xx response, or unparseable body).
 *
 * Why this is internal rather than reusing `useAuth().refresh()`:
 * `useAuth` is a React hook and only valid inside a component's render
 * tree. `apiFetch` is a plain async function callable from event handlers,
 * mutations, and TanStack Query callbacks where the hook contract does
 * not apply. Going through hooks would also force every consumer of
 * `apiFetch` to also be a hook, defeating the purpose of a neutral wrapper.
 *
 * Why we don't go through `apiFetch` itself: refresh is the primitive that
 * `apiFetch`'s 401 path depends on; calling `apiFetch` here would be a
 * recursion hazard. The refresh endpoint is also cookie-authenticated
 * (`matchlayer_refresh` HttpOnly cookie + `X-CSRF-Token` header double-
 * submit, design §9.3), not Bearer-authenticated, so the `apiFetch`
 * Bearer-attach path would be the wrong contract anyway.
 *
 * On any failure the closure is cleared via `setAccessToken(null)` so
 * downstream `useAuth()` consumers re-render as anonymous and the
 * Authenticated_Shell layout can redirect on the next navigation.
 */
async function attemptSilentRefresh(): Promise<string | null> {
  if (refreshInFlight !== null) {
    return refreshInFlight;
  }

  refreshInFlight = (async () => {
    try {
      // Fast path: no CSRF cookie ⇒ no refresh cookie (the API sets both
      // together via `core/security/cookies.py`) ⇒ a `/refresh` POST is
      // guaranteed to return 401 `missing_refresh_cookie`. Skipping the
      // network call here keeps the API audit log free of noise on
      // anonymous renders that happen to hit a Bearer-protected endpoint.
      const csrf = readCsrfCookie();
      if (csrf === null) {
        setAccessToken(null);
        return null;
      }

      let res: Response;
      try {
        res = await fetch(`${apiBaseUrl}/api/v1/auth/refresh`, {
          method: "POST",
          credentials: "include",
          headers: { "X-CSRF-Token": csrf },
        });
      } catch {
        // Network error (DNS failure, server down, CORS preflight reject).
        // Treat as session-ended; the layout shell will redirect on the
        // next render.
        setAccessToken(null);
        return null;
      }

      if (!res.ok) {
        setAccessToken(null);
        return null;
      }

      let body: unknown;
      try {
        body = await res.json();
      } catch {
        setAccessToken(null);
        return null;
      }

      // Defensive shape check. The codegen pass will eventually replace
      // this with a Zod parse against `TokenPairResponseSchema`; until
      // then we validate the one field this function consumes.
      if (
        body === null ||
        typeof body !== "object" ||
        !("access_token" in body) ||
        typeof (body as { access_token: unknown }).access_token !== "string"
      ) {
        setAccessToken(null);
        return null;
      }

      const newToken = (body as { access_token: string }).access_token;
      setAccessToken(newToken);
      return newToken;
    } finally {
      // Clear the in-flight slot whether the refresh succeeded or failed
      // so the *next* 401 (e.g. from a subsequent ~7-day-later session
      // expiry) gets a fresh attempt instead of a cached `null`.
      refreshInFlight = null;
    }
  })();

  return refreshInFlight;
}

// ---------------------------------------------------------------------------
// `apiFetch` — the public wrapper
// ---------------------------------------------------------------------------

/**
 * Resolve a caller-supplied path or URL into a fetchable URL string.
 *
 * Accepts three input shapes so `apiFetch` is ergonomic at every call site:
 *
 * - `"/api/v1/auth/me"` — relative to the API base. Joined with
 *   `apiBaseUrl` so callers don't have to interpolate the env var.
 * - `"api/v1/auth/me"` — same, with the leading slash inserted defensively
 *   so a missed slash doesn't produce `http://localhost:8000api/v1/...`.
 * - `"https://..."` or `"http://..."` — used verbatim. Lets the wrapper
 *   call non-MatchLayer endpoints (e.g. a CDN-hosted resource that
 *   nevertheless wants Bearer attachment) without contorting the call site.
 */
function buildUrl(input: string): string {
  if (input.startsWith("http://") || input.startsWith("https://")) {
    return input;
  }
  return `${apiBaseUrl}${input.startsWith("/") ? "" : "/"}${input}`;
}

/**
 * Build the `RequestInit` for one outbound call. Centralizes:
 *
 * - Bearer attachment (skipped when `token` is `null` so anonymous calls
 *   work; skipped when the caller already supplied an `Authorization`
 *   header so test fixtures and one-off override flows aren't fought).
 * - `credentials: "include"` default. The refresh and CSRF cookies have
 *   `Path=/api/v1/auth` so they're only actually sent on the auth surface;
 *   on every other path the credentials flag is a no-op. Defaulting it on
 *   every call removes a footgun where a forgotten `credentials` flag on
 *   a future cookie-authenticated endpoint would silently fail.
 * - Header normalization to a fresh `Headers` instance per call so the
 *   retry path can rewrite Authorization without mutating the caller's
 *   init object.
 */
function buildInit(restInit: RequestInit, token: string | null): RequestInit {
  const headers = new Headers(restInit.headers);
  if (token !== null && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  return {
    credentials: "include",
    ...restInit,
    headers,
  };
}

/**
 * The Bearer-attaching, 401-retry-via-refresh fetch wrapper for the
 * MatchLayer API (Requirements 12.5, 12.6; Design §13.5).
 *
 * Behavior:
 *
 * 1. Resolve the access token. By default reads the closure store via
 *    `getAccessToken()`. Callers may override with `init.accessToken`
 *    (string for an explicit token, `null` to suppress Bearer attachment
 *    entirely). When the override is present the silent-refresh-and-retry
 *    path is skipped — see `ApiFetchInit` rationale above.
 *
 * 2. Build the request. Authorization header is attached when a token is
 *    available and the caller didn't already set the header. Default
 *    `credentials: "include"` is applied (caller can override).
 *
 * 3. Issue the request. If the response status is anything other than
 *    401, return it directly.
 *
 * 4. On 401 with no override token: invoke `attemptSilentRefresh()`. If
 *    the refresh fails (no session cookie, network error, non-2xx, or
 *    unparseable body), the original 401 response is returned to the
 *    caller — exactly one network attempt, no infinite loops.
 *
 * 5. On refresh success: re-issue the original request with the freshly
 *    issued token. The second response is returned verbatim, even if it
 *    is itself 401 — the contract is "retry exactly once, then propagate"
 *    so the UI can react (typically by signing the user out).
 *
 * Body re-use note: the retry path passes the original `restInit` to
 * `fetch` a second time. For every body type defined by the WHATWG fetch
 * spec (string, `Blob`, `BufferSource`, `FormData`, `URLSearchParams`)
 * this works without copying. The single exception is `ReadableStream`,
 * which is consumable-once; callers who need streamed bodies should
 * either read the stream into memory before calling `apiFetch` or handle
 * 401 themselves. Phase 1's anticipated bodies (JSON via `JSON.stringify`,
 * form data via `FormData` for the upload spec) are all re-usable.
 *
 * @param path  Relative path (e.g. `"/api/v1/auth/me"`) or absolute URL.
 * @param init  Optional `RequestInit` extended with `accessToken`.
 * @returns The final `Response` — either the first response when its
 *          status is not 401, the retried response when refresh succeeded,
 *          or the original 401 when refresh could not recover the session.
 */
export async function apiFetch(
  path: string,
  init: ApiFetchInit = {},
): Promise<Response> {
  const { accessToken: overrideToken, ...restInit } = init;
  const overrideProvided = "accessToken" in init;

  const url = buildUrl(path);
  const initialToken = overrideProvided
    ? (overrideToken ?? null)
    : getAccessToken();

  const firstResponse = await fetch(url, buildInit(restInit, initialToken));
  if (firstResponse.status !== 401) {
    return firstResponse;
  }

  // SC callers (token explicitly supplied, including `null`) manage their
  // own session lifecycle — see the `ApiFetchInit` docstring. A silent
  // refresh from inside an SC fetch would either be a no-op (server has
  // no closure) or surprising (mutating the client closure from server
  // code), so we propagate the 401 verbatim.
  if (overrideProvided) {
    return firstResponse;
  }

  const refreshedToken = await attemptSilentRefresh();
  if (refreshedToken === null) {
    return firstResponse;
  }

  return fetch(url, buildInit(restInit, refreshedToken));
}
