/**
 * API client contract for the MatchLayer FastAPI backend.
 *
 * This module is the single place the web app reads `NEXT_PUBLIC_API_BASE_URL`,
 * the public env var declared in `.env.example` and consumed by the browser
 * bundle. Sibling specs (`phase-1-auth`, `phase-1-matching`) will import
 * `apiBaseUrl` from here when wiring real fetch calls; until then this file
 * exists purely to anchor the env-var contract so `tools/check_env_drift.py`
 * sees the value as referenced rather than stale.
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
