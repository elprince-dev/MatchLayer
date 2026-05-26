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
 * If the server isn't running, the test fails with a fetch network error
 * (typically `ECONNREFUSED`). That's by design: this test verifies real
 * HTTP responses, not mocked proxy behavior.
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

import { beforeAll, describe, expect, it } from "vitest";

const BASE_URL = process.env.MATCHLAYER_WEB_TEST_URL ?? "http://127.0.0.1:3000";

// The exact CSP value the proxy sets — must match
// `apps/web/src/proxy.ts` verbatim. Phase 1 keeps `'unsafe-inline'`
// for both `style-src` and `script-src`; the design (§7.7) tracks
// tightening this to nonces in Phase 6.
const EXPECTED_CSP =
  "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; font-src 'self' data:; connect-src 'self' http://localhost:8000";

describe("security headers proxy", () => {
  let response: Response;

  beforeAll(async () => {
    response = await fetch(`${BASE_URL}/`);
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

  it("sets the exact Content-Security-Policy value from §7.7", () => {
    expect(response.headers.get("content-security-policy")).toBe(EXPECTED_CSP);
  });

  it("does not set Strict-Transport-Security over plain HTTP", () => {
    // AC 6.2 scopes HSTS to HTTPS only. Local dev and CI run over plain
    // HTTP, so the header must be absent here. Production HSTS is
    // verified separately by the deployed-environment smoke tests.
    expect(response.headers.get("strict-transport-security")).toBeNull();
  });
});
