import { NextResponse, type NextRequest } from "next/server";

/**
 * Security-headers proxy.
 *
 * Next.js 16 renamed the `middleware` file convention to `proxy` (see
 * https://nextjs.org/docs/app/api-reference/file-conventions/proxy and the
 * `@next/codemod middleware-to-proxy` migration). The file lives at
 * `apps/web/src/proxy.ts` and exports a function named `proxy`; behavior,
 * `config.matcher` semantics, and the `NextRequest`/`NextResponse` imports
 * are unchanged from the prior `middleware` convention.
 *
 * Implements the per-response security headers required by Phase 1 design
 * §7.7 and Requirement 6 (AC 6.1–6.6) of the phase-1-foundation spec. Runs
 * on every request matched by `config.matcher` below, attaching headers to
 * the response that `NextResponse.next()` produces so they apply to every
 * route in the app — App Router pages, API routes, and static-rendered HTML.
 *
 * Phase 1 CSP intentionally allows `'unsafe-inline'` for both `style-src`
 * and `script-src`. Next.js's runtime injects inline bootstrap scripts and
 * inline styles for streaming/hydration; without `'unsafe-inline'` the
 * landing page would not render. Tightening this to nonces (and dropping
 * `'unsafe-inline'` entirely) is tracked for Phase 6 alongside the CDN
 * cutover, where we'll inject a per-request nonce here and thread it
 * through Next's `next/script` and inline-style hooks.
 *
 * `'unsafe-eval'` is added to `script-src` **only in development**. Next.js
 * dev mode (React's dev build, the Fast Refresh runtime, and the error
 * overlay) uses `eval()` for debugging features like reconstructing
 * callstacks; without it the dev server throws "eval() is not supported in
 * this environment". React never uses `eval()` in production, so the
 * production CSP omits `'unsafe-eval'` and stays strict — the dev-only
 * allowance never ships.
 *
 * `Strict-Transport-Security` is only emitted on HTTPS requests. Browsers
 * ignore HSTS sent over plain HTTP, but emitting it there is still poor
 * form (and confusing in dev where we run on http://localhost:3000). In
 * production behind Vercel/CloudFront the edge will also set HSTS — that's
 * fine, the values match and HTTP headers are idempotent on duplicate set.
 */
export function proxy(request: NextRequest): NextResponse {
  const response = NextResponse.next();

  // `'unsafe-eval'` is needed only by the Next.js dev runtime (see docstring);
  // production stays strict and never receives it.
  const scriptSrc =
    process.env.NODE_ENV === "development"
      ? "script-src 'self' 'unsafe-inline' 'unsafe-eval'"
      : "script-src 'self' 'unsafe-inline'";

  // §7.7 — Content Security Policy.
  response.headers.set(
    "Content-Security-Policy",
    [
      "default-src 'self'",
      "img-src 'self' data:",
      "style-src 'self' 'unsafe-inline'",
      scriptSrc,
      "font-src 'self' data:",
      "connect-src 'self' http://localhost:8000",
    ].join("; "),
  );

  response.headers.set("X-Content-Type-Options", "nosniff");
  response.headers.set("X-Frame-Options", "DENY");
  response.headers.set("Referrer-Policy", "strict-origin-when-cross-origin");
  response.headers.set(
    "Permissions-Policy",
    "camera=(), microphone=(), geolocation=()",
  );

  // HSTS — HTTPS only. AC 6.2: "WHILE the Web_App is served over HTTPS".
  if (request.nextUrl.protocol === "https:") {
    response.headers.set(
      "Strict-Transport-Security",
      "max-age=31536000; includeSubDomains; preload",
    );
  }

  return response;
}

/**
 * Match every request path so the headers cover the landing page, all app
 * routes, and any future API routes. The exclusions skip Next.js's internal
 * static and image asset endpoints plus the favicon, where running this
 * proxy would only burn invocations without protecting user-facing
 * HTML. The §4.9 proxy test fetches `/`, which this matcher covers.
 */
export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
