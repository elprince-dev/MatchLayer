/**
 * Security-headers proxy test (phase-1-foundation §7.9, AC 6.7).
 *
 * This test asserts that every header set by `apps/web/src/proxy.ts`
 * (per Requirement 6, AC 6.1–6.6) is present with its exact value on the
 * response to `GET /`. It runs against a *real, built, running* Next.js
 * server — Vitest does NOT start the server. The proxy must be
 * exercised at the edge (the same place production runs it), not via a
 * mocked `NextResponse`, because the headers we're verifying are what real
 * browsers will see.
 *
 * Next.js 16 renamed the `middleware` file convention to `proxy`; this
 * test exercises the renamed file (`apps/web/src/proxy.ts`) and lives
 * alongside it as `apps/web/tests/proxy.test.ts`.
 *
 * ## Running locally
 *
 * Two terminals are required.
 *
 * Terminal 1 — build and start the server:
 *
 *   pnpm --filter @matchlayer/web build
 *   pnpm --filter @matchlayer/web start
 *   # Server listens on http://127.0.0.1:3000
 *
 * Terminal 2 — run this test:
 *
 *   pnpm --filter @matchlayer/web test
 *
 * If the server isn't running (or isn't reachable within a short probe
 * window), this suite is **skipped**, not failed — mirroring the backend's
 * "skip-if-no-infra" convention (integration/property tests skip when
 * Postgres/Redis aren't reachable). The suite still runs for real in CI,
 * where the `frontend` job builds and starts the server before invoking
 * Vitest, so the security-header assertions are exercised against a live
 * edge exactly as production serves them.
 *
 * ## Running in CI
 *
 * The `frontend` job in `.github/workflows/ci.yml` (§7.2) handles the
 * orchestration end-to-end:
 *
 *   1. pnpm --filter @matchlayer/web build
 *   2. pnpm --filter @matchlayer/web start &     (background)
 *   3. wait-on http://127.0.0.1:3000              (block until ready)
 *   4. pnpm --filter @matchlayer/web test         (this file)
 *
 * The base URL can be overridden with `MATCHLAYER_WEB_TEST_URL` if CI
 * runs the server on a different host/port (e.g., a sidecar container).
 *
 * Default uses `127.0.0.1` rather than `localhost` to dodge any IPv6 vs
 * IPv4 surprises in Node's `fetch` implementation across platforms.
 */

import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { type NextRequest } from "next/server";

import { proxy } from "@/proxy";

const BASE_URL = process.env.MATCHLAYER_WEB_TEST_URL ?? "http://127.0.0.1:3000";

/**
 * Probe the target server once, with a short timeout, to decide whether this
 * suite can run. Returns `true` only when the server answers the probe.
 *
 * Why this exists: the assertions below require a real, built, running Next.js
 * server (Vitest does not start one). Locally that server is usually absent —
 * or, worse, a stale process is bound to the port but not actually serving, so
 * a bare `fetch` hangs until Vitest's 10s `hookTimeout` and the suite fails.
 * Rather than fail in that environment, we mirror the backend's
 * "skip-if-no-infra" convention and skip when the server isn't reachable. CI's
 * `frontend` job builds + starts the server before running Vitest, so the
 * probe succeeds there and every assertion runs for real.
 *
 * The probe uses a 2s `AbortSignal.timeout`, well under the 10s hook budget, so
 * an unreachable or hung server resolves quickly to a skip instead of a hang.
 */
async function serverIsReachable(): Promise<boolean> {
  try {
    await fetch(`${BASE_URL}/`, { signal: AbortSignal.timeout(2000) });
    return true;
  } catch {
    return false;
  }
}

const serverUp = await serverIsReachable();

// Skip (don't fail) when the server isn't reachable — see `serverIsReachable`.
const describeIfServer = serverUp ? describe : describe.skip;

// The proxy assembles the CSP with one environment-dependent directive:
// `script-src` gains `'unsafe-eval'` ONLY in development (the Next.js dev
// runtime needs it — see `apps/web/src/proxy.ts`); a production build stays
// strict and never ships it.
//
// This suite may run against either server mode and CANNOT reliably infer the
// running server's NODE_ENV from its own process env (under Vitest, NODE_ENV
// is "test", independent of whether the target is `next dev` or `next start`).
// So instead of pinning one exact CSP string, we assert:
//   - every NON-script-src directive exactly (these are mode-invariant), and
//   - `script-src` is the strict base, optionally PLUS the dev-only
//     `'unsafe-eval'` and nothing else.
// CI documents the production guarantee separately: its `frontend` job runs a
// production server, and a dedicated unit assertion below pins that a built
// server never emits `'unsafe-eval'` (guarded so it is meaningful only when
// the target is not a dev server).

// Mode-invariant directives — identical in dev and production.
const INVARIANT_DIRECTIVES = [
  "default-src 'self'",
  "img-src 'self' data:",
  "style-src 'self' 'unsafe-inline'",
  "font-src 'self' data:",
  "connect-src 'self' http://localhost:8000",
];

/** Split a CSP header value into its trimmed directive strings. */
function splitDirectives(csp: string): string[] {
  return csp
    .split(";")
    .map((d) => d.trim())
    .filter((d) => d.length > 0);
}

describeIfServer("security headers proxy", () => {
  let response: Response;

  beforeAll(async () => {
    // Bounded timeout so a server that accepts the connection but never
    // responds can't stall the hook to its 10s limit — the probe above
    // already gated reachability, this is belt-and-suspenders.
    response = await fetch(`${BASE_URL}/`, {
      signal: AbortSignal.timeout(8000),
    });
  });

  it("responds 200 on the landing route", () => {
    expect(response.status).toBe(200);
  });

  it("sets X-Content-Type-Options: nosniff", () => {
    expect(response.headers.get("x-content-type-options")).toBe("nosniff");
  });

  it("sets X-Frame-Options: DENY", () => {
    expect(response.headers.get("x-frame-options")).toBe("DENY");
  });

  it("sets Referrer-Policy: strict-origin-when-cross-origin", () => {
    expect(response.headers.get("referrer-policy")).toBe(
      "strict-origin-when-cross-origin",
    );
  });

  it("sets Permissions-Policy with camera/microphone/geolocation locked down", () => {
    expect(response.headers.get("permissions-policy")).toBe(
      "camera=(), microphone=(), geolocation=()",
    );
  });

  it("sets every mode-invariant CSP directive exactly (incl. connect-src)", () => {
    const csp = response.headers.get("content-security-policy") ?? "";
    const directives = splitDirectives(csp);
    for (const expected of INVARIANT_DIRECTIVES) {
      expect(directives).toContain(expected);
    }
  });

  it("sets a script-src of the strict base plus at most the dev-only 'unsafe-eval'", () => {
    // Accept both the production-strict value and the dev value (which adds
    // exactly `'unsafe-eval'` for the Next.js dev runtime). Any OTHER token in
    // script-src would be a real regression and fails here.
    const csp = response.headers.get("content-security-policy") ?? "";
    const scriptSrc = splitDirectives(csp).find((d) =>
      d.startsWith("script-src "),
    );
    expect(scriptSrc).toBeDefined();
    expect([
      "script-src 'self' 'unsafe-inline'",
      "script-src 'self' 'unsafe-inline' 'unsafe-eval'",
    ]).toContain(scriptSrc);
  });

  it("does not set Strict-Transport-Security over plain HTTP", () => {
    // AC 6.2 scopes HSTS to HTTPS only. Local dev and CI run over plain
    // HTTP, so the header must be absent here. Production HSTS is
    // verified separately by the deployed-environment smoke tests.
    expect(response.headers.get("strict-transport-security")).toBeNull();
  });
});

/**
 * Server-independent unit coverage of the `proxy()` function's CSP.
 *
 * The suite above asserts headers on a *running* server and is skipped when
 * none is reachable, so it cannot, on its own, guarantee the production CSP in
 * CI environments that don't boot a server. These tests call `proxy()`
 * directly with a stubbed `NODE_ENV`, so they always run and pin both modes:
 *
 *   - production build → strict `script-src` with NO `'unsafe-eval'`;
 *   - development     → `script-src` includes the dev-only `'unsafe-eval'`.
 *
 * `proxy()` only reads `request.nextUrl.protocol` (for the HSTS branch) and
 * `process.env.NODE_ENV`, so a minimal `NextRequest`-shaped stub suffices.
 */
describe("proxy() CSP by NODE_ENV (no server required)", () => {
  afterEach(() => {
    // `vi.stubEnv` records the original value; this restores NODE_ENV so test
    // ordering can't leak the override.
    vi.unstubAllEnvs();
  });

  function cspForNodeEnv(nodeEnv: string): string {
    vi.stubEnv("NODE_ENV", nodeEnv);
    // Minimal NextRequest stub: proxy() touches nextUrl.protocol (HSTS branch)
    // and nextUrl.pathname (X-Robots-Tag branch). "/" is the landing page, an
    // indexable route, so the noindex header is not set on this request.
    const req = {
      nextUrl: { protocol: "http:", pathname: "/" },
    } as unknown as NextRequest;
    const res = proxy(req);
    return res.headers.get("content-security-policy") ?? "";
  }

  it("production: script-src is strict and omits 'unsafe-eval'", () => {
    const csp = cspForNodeEnv("production");
    expect(csp).toContain("script-src 'self' 'unsafe-inline';");
    expect(csp).not.toContain("'unsafe-eval'");
    // connect-src stays pinned to the API origin.
    expect(csp).toContain("connect-src 'self' http://localhost:8000");
  });

  it("development: script-src includes the dev-only 'unsafe-eval'", () => {
    const csp = cspForNodeEnv("development");
    expect(csp).toContain("script-src 'self' 'unsafe-inline' 'unsafe-eval'");
  });
});

/**
 * Server-independent coverage of the `proxy()` `X-Robots-Tag` behavior
 * (Req 8.7, 8.8; `seo.md` route classification; ADR 0006).
 *
 * The proxy stamps `X-Robots-Tag: noindex, nofollow` on the non-indexable
 * route classes only — the `(auth)` pages (`/login`, `/register`), the
 * authenticated `(app)` paths, and the `/api/` JSON surface — while the public
 * landing page (`/`) and other public routes are left indexable (Req 8.10).
 *
 * `proxy()` reads only `request.nextUrl.pathname` (for this branch) and
 * `process.env.NODE_ENV`, so a minimal `NextRequest`-shaped stub suffices.
 */
describe("proxy() X-Robots-Tag by path (no server required)", () => {
  function robotsTagForPath(pathname: string): string | null {
    const req = {
      nextUrl: { protocol: "http:", pathname },
    } as unknown as NextRequest;
    return proxy(req).headers.get("x-robots-tag");
  }

  it.each([
    "/login",
    "/register",
    "/upload",
    "/matches",
    "/matches/abc-123",
    "/library",
    "/dashboard",
    "/settings",
    "/api/v1/matches/abc",
  ])("stamps noindex, nofollow on the non-indexable path %s", (path) => {
    expect(robotsTagForPath(path)).toBe("noindex, nofollow");
  });

  it.each(["/", "/about", "/pricing", "/privacy", "/terms"])(
    "leaves the indexable public path %s without an X-Robots-Tag header",
    (path) => {
      expect(robotsTagForPath(path)).toBeNull();
    },
  );

  it("does not match a public path that merely shares a noindex prefix substring", () => {
    // `/uploads-guide` is a public page that starts with the same letters as
    // `/upload` but is not the authenticated route — the prefix check is
    // segment-aware (`/upload` or `/upload/...`), so it must NOT be flagged.
    expect(robotsTagForPath("/uploads-guide")).toBeNull();
  });
});
