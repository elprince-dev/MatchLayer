/** @type {import('next').NextConfig} */
// Standalone output is required by `infra/docker/web.Dockerfile` (§11.2 of the
// phase-1-foundation design): the production image copies `.next/standalone`
// and runs `node apps/web/server.js`, which only exists when this option is on.
const nextConfig = {
  output: "standalone",
  reactStrictMode: true,

  /**
   * Same-origin API proxy for LOCAL DEVELOPMENT only.
   *
   * The phase-1-auth design assumes the web app and API are same-origin
   * (production: both behind one domain). Local dev runs them on different
   * ports (web :3000, API :8000), which is cross-origin — and that breaks the
   * cookie-based auth:
   *   - the browser can't read the `matchlayer_csrf` cookie set on the API
   *     origin, so it can't echo it as `X-CSRF-Token` → every `/refresh`
   *     returns 403 csrf_mismatch;
   *   - the Next.js server can't see the refresh cookie either.
   *
   * Rewriting `/api/*` to the API server makes the browser talk to the API
   * through the SAME origin as the page (`localhost:3000/api/...`), so cookies
   * are first-party and the double-submit CSRF works exactly as designed.
   *
   * Scoped to non-production: in production the web and API are genuinely
   * same-origin behind a real gateway/CDN, so no Next-level proxy is wanted.
   * The target is overridable via `MATCHLAYER_API_PROXY_TARGET` for setups
   * that run the API on a non-default host/port.
   */
  async rewrites() {
    if (process.env.NODE_ENV === "production") {
      return [];
    }
    const target =
      process.env.MATCHLAYER_API_PROXY_TARGET ?? "http://localhost:8000";
    return [
      {
        source: "/api/:path*",
        destination: `${target}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
