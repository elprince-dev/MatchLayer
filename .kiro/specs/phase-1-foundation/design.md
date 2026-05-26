# Design Document — phase-1-foundation

## 1. Overview

`phase-1-foundation` lays the floor for everything else in Phase 1. By the end of this spec the repository contains a working monorepo, a one-command local development stack, two scaffolded apps that build and pass CI, a typed contract pipeline between them, hardened production container images, and the documentation a returning developer needs.

It deliberately ships **no domain logic**. There are no users, no resumes, no JWT issuance, no scoring, and no production deployment. Those concerns belong to `phase-1-auth` and `phase-1-matching`. The only HTTP endpoint exposed is `/healthz`, and the only frontend surface is a placeholder landing page that already wears the design system so neither sibling spec has to litigate visual identity again.

Every design choice below is anchored to one or more acceptance criteria from `requirements.md`. Where a choice exists primarily for a downstream spec's benefit, it is labeled as such.

## 2. High-level architecture

```mermaid
flowchart LR
  subgraph monorepo["matchlayer/ (single git repo)"]
    direction TB
    web["apps/web<br/>(Next.js, TS)"]
    api["apps/api<br/>(FastAPI, Py 3.13)"]
    shared["packages/shared-types<br/>(TS types + Zod)"]
    docker["infra/docker/<br/>api.Dockerfile, web.Dockerfile"]
    ci[".github/workflows/ci.yml"]
    docs["docs/, .kiro/"]
  end

  subgraph stack["docker-compose: local dev"]
    pg[("Postgres 16")]
    redis[("Redis 7")]
    minio[("MinIO")]
  end

  api -- "OpenAPI JSON" --> codegen["pnpm codegen"]
  codegen -- "openapi-typescript" --> shared
  codegen -- "openapi-zod-client" --> shared
  shared -. "imports types & schemas" .-> web
  api -- "asyncpg" --> pg
  api -. "future: cache, ratelimit" .-> redis
  api -. "future: file storage" .-> minio
  web -- "fetch" --> api

  ci -- "lint / typecheck / test / audit / drift" --> monorepo
```

Key data flows:

- **Contract flow:** the FastAPI app is the only source of truth for the API contract. A codegen script dumps its OpenAPI spec, then produces both TypeScript types and Zod schemas into `packages/shared-types`. The frontend never hand-writes API types.
- **Local dev flow:** developers run `docker compose up -d` to bring up Postgres/Redis/MinIO, then run the API and the web app on the host. No Docker-for-the-app during day-to-day development; containers exist for production deploys and CI parity.
- **CI flow:** every PR runs lint/typecheck/test/audit/drift across both apps in parallel. Failures block merge.

## 3. Repository layout

The tree below shows everything this spec creates or modifies. Files marked `(g)` are generated; `(c)` are committed by hand; `(c+g)` are committed and regenerated, with a CI drift check.

```
matchlayer/
├── .github/
│   ├── workflows/
│   │   └── ci.yml                                   (c)
│   └── dependabot.yml                               (c)
├── .kiro/
│   ├── steering/                                    (already exists)
│   └── specs/phase-1-foundation/                    (already exists)
├── .pre-commit-config.yaml                          (c)
├── .gitignore                                       (already exists, may extend)
├── .editorconfig                                    (c)
├── .nvmrc                                           (c) → "24"
├── .python-version                                  (c) → "3.13"
├── docker-compose.yml                               (c)
├── package.json                                     (c) — root, only scripts + devDeps
├── pnpm-workspace.yaml                              (c)
├── pnpm-lock.yaml                                   (c+g, frozen in CI)
├── tsconfig.base.json                               (c)
├── README.md                                        (already exists, will be updated)
├── .env.example                                     (c)
├── apps/
│   ├── api/
│   │   ├── pyproject.toml                           (c)
│   │   ├── uv.lock                                  (c+g)
│   │   ├── alembic.ini                              (c)
│   │   ├── alembic/
│   │   │   ├── env.py                               (c)
│   │   │   ├── script.py.mako                       (c, alembic default)
│   │   │   └── versions/
│   │   │       └── 0000_baseline.py                 (c) — empty baseline
│   │   ├── src/matchlayer_api/
│   │   │   ├── __init__.py                          (c)
│   │   │   ├── main.py                              (c) — create_app() factory
│   │   │   ├── config.py                            (c) — Settings
│   │   │   ├── core/
│   │   │   │   ├── __init__.py                      (c)
│   │   │   │   ├── logging.py                       (c) — structlog setup
│   │   │   │   ├── middleware.py                    (c) — request-id + access log
│   │   │   │   ├── errors.py                        (c) — RFC 7807 handler
│   │   │   │   └── db.py                            (c) — engine, session, dep
│   │   │   ├── api/
│   │   │   │   ├── __init__.py                      (c)
│   │   │   │   └── health.py                        (c) — GET /healthz
│   │   │   └── tools/
│   │   │       ├── __init__.py                      (c)
│   │   │       └── dump_openapi.py                  (c) — emits openapi.json
│   │   └── tests/
│   │       ├── conftest.py                          (c)
│   │       └── test_health.py                       (c)
│   └── web/
│       ├── package.json                             (c)
│       ├── tsconfig.json                            (c)
│       ├── next.config.mjs                          (c)
│       ├── postcss.config.mjs                       (c)
│       ├── components.json                          (c) — shadcn config
│       ├── eslint.config.mjs                        (c)
│       ├── vitest.config.ts                         (c)
│       ├── public/
│       └── src/
│           ├── app/
│           │   ├── layout.tsx                       (c)
│           │   ├── page.tsx                         (c) — landing
│           │   └── globals.css                      (c) — tokens + Tailwind v4
│           ├── proxy.ts                             (c) — security headers
│           ├── components/
│           │   ├── theme-provider.tsx               (c)
│           │   ├── theme-toggle.tsx                 (c)
│           │   ├── motion-safe.tsx                  (c) — reduced-motion hook
│           │   └── ui/
│           │       └── button.tsx                   (c) — shadcn primitive
│           └── lib/
│               └── utils.ts                         (c) — cn() helper
├── packages/
│   └── shared-types/
│       ├── package.json                             (c)
│       ├── tsconfig.json                            (c)
│       ├── scripts/
│       │   └── codegen.mjs                          (c) — orchestrator
│       └── src/
│           ├── api-types.ts                         (g)
│           ├── api-schemas.ts                       (g)
│           └── index.ts                             (c) — curated re-exports
├── infra/
│   └── docker/
│       ├── api.Dockerfile                           (c)
│       └── web.Dockerfile                           (c)
└── docs/
    └── runbooks/
        └── repo-setup.md                            (c)
```

## 4. Workspace topology

Two coexisting workspaces, one per language:

- **JavaScript workspace (pnpm).** `pnpm-workspace.yaml` declares `apps/*` and `packages/*`. Root `package.json` carries only scripts and dev tooling — never application dependencies. Sub-packages declare their own deps. Each gets a `name` field consistent with the steering convention:
  - `apps/web` → `@matchlayer/web`
  - `packages/shared-types` → `@matchlayer/shared-types`
  - root → `matchlayer` (private)

- **Python workspace (uv project).** `apps/api/pyproject.toml` defines a single uv project named `matchlayer-api`. `uv.lock` lives next to it. uv uses `requires-python = ">=3.13"`. Future Python packages (e.g., `ml/`) will define their own uv projects rather than joining a workspace — keeps boundaries crisp.

The two workspaces don't share a manifest. They communicate exclusively through the codegen pipeline and the OpenAPI contract.

Top-level scripts in the root `package.json` orchestrate both:

| Script | What it runs |
|---|---|
| `pnpm install` | pnpm install across the JS workspace; uv install is left to `apps/api` (covered in README) |
| `pnpm lint` | `pnpm -r --parallel run lint` |
| `pnpm typecheck` | `pnpm -r --parallel run typecheck` |
| `pnpm test` | `pnpm -r --parallel run test` |
| `pnpm build` | `pnpm -r --parallel run build` |
| `pnpm codegen` | runs the orchestrator at `packages/shared-types/scripts/codegen.mjs` |
| `pnpm format` | prettier write across the workspace |

Satisfies AC 1.1, 1.2, 1.7. AC 1.6 (unrecognized package detection) is satisfied by pnpm's default behavior — any `package.json` not matched by a workspace glob will fail the install with `ERR_PNPM_NO_MATCHING_VERSION` or similar.

## 5. Local development stack

`docker-compose.yml` defines three services on a single bridge network. The compose file lives at the repo root so `docker compose up -d` from any machine works without extra flags.

```yaml
# Shape only — full file produced during implementation
services:
  postgres:
    image: postgres:16-alpine          # pinned by digest in implementation
    environment:
      POSTGRES_USER: matchlayer
      POSTGRES_PASSWORD: dev_only_password
      POSTGRES_DB: matchlayer
    ports: ["5432:5432"]
    volumes: [matchlayer-postgres-data:/var/lib/postgresql/data]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U matchlayer -d matchlayer"]
      interval: 2s
      timeout: 3s
      retries: 30

  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 2s
      retries: 30

  minio:
    image: minio/minio:RELEASE.2025-XX-XX  # pinned in implementation
    command: server /data --console-address :9001
    environment:
      MINIO_ROOT_USER: matchlayer
      MINIO_ROOT_PASSWORD: dev_only_password
    ports: ["9000:9000", "9001:9001"]
    volumes: [matchlayer-minio-data:/data]
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:9000/minio/health/live"]
      interval: 2s
      retries: 30

volumes:
  matchlayer-postgres-data:
  matchlayer-minio-data:
```

Notes on the failure modes called out in the requirements:

- **Healthcheck failures (AC 2.6):** the per-service `retries: 30` × 2s interval gives a 60-second budget. After that, `docker compose up` exits non-zero because Compose v2 propagates the failure when used with `--wait`. The README documents `docker compose up -d --wait` as the canonical command.
- **Port conflicts (AC 2.7):** Compose surfaces the conflict via Docker's `port already allocated` error in stderr; we add a one-line README troubleshooting note rather than scripting a workaround.
- **Volume preservation (AC 2.4, 2.8):** named volumes are not removed by `docker compose down`. Removal requires `docker compose down -v` explicitly.

The MinIO bucket required by future specs is **not** auto-created here — that's a `phase-1-matching` concern. The README documents the manual `mc mb` step for now.

## 6. Backend baseline design (`apps/api`)

### 6.1 Module layout

`src/matchlayer_api/` mirrors the structure laid out in `structure.md`. For Phase 1 foundation, the populated subset is:

```
matchlayer_api/
├── main.py            # create_app() factory
├── config.py          # Settings
├── core/
│   ├── logging.py
│   ├── middleware.py
│   ├── errors.py
│   └── db.py
├── api/
│   └── health.py
└── tools/
    └── dump_openapi.py
```

Empty `auth/`, `resumes/`, `matches/` packages are NOT created yet — empty packages get cargo-culted. Later specs add them when there's content.

### 6.2 Settings

Single `pydantic-settings` `BaseSettings` subclass, loaded once at startup. Env-prefix `MATCHLAYER_` to namespace clearly. All consumers receive an injected `Settings` instance via FastAPI dependency override; nothing reads `os.environ` directly.

```python
# Shape only
class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MATCHLAYER_",
        env_file=".env",
        case_sensitive=False,
    )

    environment: Literal["development", "staging", "production"] = "development"
    log_level: Literal["debug", "info", "warning", "error"] = "info"

    database_url: PostgresDsn       # async driver: postgresql+asyncpg://...
    redis_url: RedisDsn

    s3_endpoint_url: str | None = None  # MinIO in dev, None in prod (real S3)
    s3_region: str = "us-east-1"
    s3_access_key_id: str
    s3_secret_access_key: SecretStr
    s3_bucket: str

    cors_allowed_origins: list[AnyHttpUrl] = []
```

Missing-required-var behavior (AC 4.3) is Pydantic's default: validation error at construction time, raised before the app accepts traffic.

### 6.3 Logging

structlog with a JSON renderer for non-development environments and a colorized console renderer in development. Processors:

1. `structlog.contextvars.merge_contextvars` — pulls `request_id`, `route`, `method`, `user_id` from the contextvar bound by middleware.
2. `structlog.processors.add_log_level`
3. `structlog.processors.TimeStamper(fmt="iso", utc=True)`
4. `structlog.processors.StackInfoRenderer()`
5. `structlog.processors.format_exc_info`
6. JSON renderer (prod) or `ConsoleRenderer` (dev).

A small redaction processor sits between (1) and (2) to scrub any field whose key matches `password|token|secret|email|resume_text|parsed_text` — defense-in-depth so future authors don't accidentally log PII (security.md, AC 4.14).

### 6.4 Request-id middleware

ASGI middleware (not BaseHTTPMiddleware, which has known performance issues). Responsibilities:

- If the inbound `X-Request-Id` matches `^[A-Za-z0-9_-]{8,128}$`, reuse it; otherwise generate a UUIDv7 (using `uuid_utils` since stdlib doesn't have v7 yet).
- Bind `request_id`, `route`, `method` to a structlog contextvar.
- Set `X-Request-Id` on the outbound response.
- Emit one structured access log line per request with `status` and `latency_ms`.

Satisfies AC 4.4–4.6.

### 6.5 `/healthz`

```python
# Shape only
@router.get("/healthz")
async def healthz(session: AsyncSession = Depends(get_session)) -> JSONResponse:
    try:
        await session.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        log.warning("healthz_db_probe_failed", error_code=type(exc).__name__)
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "reason": "database_unreachable"},
        )
    return JSONResponse(content={"status": "ok"})
```

Connection-string and credentials are never included in the response body, only the symbolic reason code. Satisfies AC 4.7–4.9.

### 6.6 Database wiring

```python
# Shape only — apps/api/src/matchlayer_api/core/db.py
engine: AsyncEngine = create_async_engine(
    str(settings.database_url),
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=5,
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
```

`pool_pre_ping=True` makes runtime DB-loss recoverable — broken connections are detected and replaced (AC 4.13). The `/healthz` probe uses the same dependency so it reflects real connectivity.

**Startup behavior (AC 4.12):** the FastAPI lifespan handler runs a one-time `SELECT 1` at startup. If it fails, the lifespan raises and uvicorn exits non-zero. This is fail-fast at startup, fail-soft at runtime.

### 6.7 Alembic

`alembic.ini` points at `alembic/`. `env.py` imports `Settings` and uses the sync URL form (Alembic stays sync) derived from the async URL by swapping `+asyncpg` for `+psycopg`. The baseline revision `0000_baseline` does nothing — it exists so future migrations have a parent.

This satisfies AC 4.11 and explicitly defers the first real migration to `phase-1-auth`.

### 6.8 RFC 7807 error envelope

A FastAPI exception handler for a small `MatchLayerError` base + `RequestValidationError` + `Exception` produces:

```json
{
  "type": "validation_error",
  "title": "...",
  "detail": "...",
  "status": 400,
  "request_id": "..."
}
```

In production, the catch-all `Exception` handler returns generic `internal_server_error` text and never leaks the original exception message. Stack traces are logged but not returned. Satisfies the cross-cutting rule from `conventions.md` and security.md.

### 6.9 OpenAPI dump tool

`apps/api/src/matchlayer_api/tools/dump_openapi.py` is a tiny CLI that imports `create_app()`, retrieves `app.openapi()`, and prints JSON to stdout. The codegen script invokes it as:

```
uv run --project apps/api python -m matchlayer_api.tools.dump_openapi > openapi.json
```

This satisfies AC 7.2 (always uses the live spec) and AC 7.7 (no fallback).

## 7. Frontend baseline design (`apps/web`)

### 7.1 File layout

```
apps/web/src/
├── app/
│   ├── layout.tsx           # ThemeProvider, font wiring, global styles
│   ├── page.tsx             # Landing
│   └── globals.css          # Tailwind v4 + tokens
├── proxy.ts                 # security headers
├── components/
│   ├── theme-provider.tsx
│   ├── theme-toggle.tsx
│   ├── motion-safe.tsx
│   └── ui/
│       └── button.tsx       # shadcn primitive
└── lib/
    └── utils.ts             # cn(), shared helpers
```

### 7.2 Tailwind v4 + brand tokens

Tailwind v4's `@theme inline` directive lets us declare both CSS custom properties and Tailwind theme values in one place. `globals.css`:

```css
/* Shape — full values from design.md */
@import "tailwindcss";

:root {
  --color-bg: 255 255 255;
  --color-bg-elevated: 248 249 251;
  --color-text: 10 10 11;
  /* ... */
  --color-brand: 124 58 237;
  --color-brand-2: 6 182 212;
}

.dark {
  --color-bg: 10 10 11;
  --color-bg-elevated: 17 17 20;
  --color-text: 244 244 245;
  /* ... */
  --color-brand: 139 92 246;
  --color-brand-2: 34 211 238;
}

@theme inline {
  --color-bg: rgb(var(--color-bg));
  --color-bg-elevated: rgb(var(--color-bg-elevated));
  --color-brand: rgb(var(--color-brand));
  --color-brand-2: rgb(var(--color-brand-2));
  /* ... */
  --font-sans: var(--font-geist-sans);
  --font-mono: var(--font-geist-mono);
}
```

Components use Tailwind utilities (`bg-bg`, `text-text`, `text-brand`) — never hex literals. Satisfies AC 5.7, 5.11.

### 7.3 shadcn/ui

Initialized with CSS-variables mode and base color "neutral" (the closest match to the token palette). `components.json` is committed. Only the `Button` primitive is added in Phase 1; later specs add what they need. Satisfies AC 5.3.

### 7.4 Fonts

```tsx
// app/layout.tsx — shape
import { Geist, Geist_Mono } from "next/font/google";

const geistSans = Geist({ subsets: ["latin"], variable: "--font-geist-sans" });
const geistMono = Geist_Mono({ subsets: ["latin"], variable: "--font-geist-mono" });

export default function RootLayout({ children }) {
  return (
    <html lang="en" suppressHydrationWarning className={`${geistSans.variable} ${geistMono.variable}`}>
      <body className="bg-bg text-text font-sans antialiased">
        <ThemeProvider>{children}</ThemeProvider>
      </body>
    </html>
  );
}
```

`suppressHydrationWarning` is required by `next-themes`. Satisfies AC 5.4.

### 7.5 Framer Motion + reduced-motion

```tsx
// components/motion-safe.tsx — shape
"use client";
import { useReducedMotion } from "framer-motion";

export function useMotionSafeProps(props: MotionProps): MotionProps {
  const reduced = useReducedMotion();
  return reduced ? { ...props, animate: props.initial, transition: { duration: 0 } } : props;
}
```

The landing page uses one purposeful entrance animation: a fade-up on the hero text using this hook. Satisfies AC 5.5 and the design.md motion principles.

### 7.6 next-themes

```tsx
// components/theme-provider.tsx — shape
"use client";
import { ThemeProvider as NextThemesProvider } from "next-themes";

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  return (
    <NextThemesProvider attribute="class" defaultTheme="system" enableSystem>
      {children}
    </NextThemesProvider>
  );
}
```

Theme toggle uses the shadcn `Button` primitive plus Lucide icons (`Sun`, `Moon`). Satisfies AC 5.6.

### 7.7 Security headers proxy

`proxy.ts` sets headers on every response. Phase 1 CSP is conservative but functional:

| Header | Value |
|---|---|
| `Content-Security-Policy` | `default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; font-src 'self' data:; connect-src 'self' http://localhost:8000` |
| `Strict-Transport-Security` | `max-age=31536000; includeSubDomains; preload` (only when request scheme is HTTPS — Vercel sets this automatically; we do too in proxy) |
| `X-Content-Type-Options` | `nosniff` |
| `X-Frame-Options` | `DENY` |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |
| `Permissions-Policy` | `camera=(), microphone=(), geolocation=()` |

`'unsafe-inline'` for styles and scripts is a Phase 1 concession — Next.js's inline runtime needs it. Tightening CSP to nonces is tracked for Phase 6 alongside CDN setup. Satisfies AC 6.1–6.6.

### 7.8 Landing page

Hero section with:
- "MatchLayer" wordmark using a gradient text fill (`bg-gradient-to-br from-brand to-brand-2 bg-clip-text text-transparent`).
- A short tagline ("AI-native ATS, transparent scoring").
- Theme toggle in the top-right.
- Subtle Framer Motion fade-up on the hero text (respecting reduced motion).

Satisfies AC 5.8, 5.11.

### 7.9 Tests

Vitest + Testing Library. The single asserted test in this spec validates the security headers proxy:

```ts
// shape — tests/proxy.test.ts
import { describe, it, expect } from "vitest";

describe("security headers", () => {
  it("sets all required headers on the landing route", async () => {
    const res = await fetch("http://localhost:3000/");
    expect(res.headers.get("x-content-type-options")).toBe("nosniff");
    expect(res.headers.get("x-frame-options")).toBe("DENY");
    expect(res.headers.get("referrer-policy")).toBe("strict-origin-when-cross-origin");
    expect(res.headers.get("permissions-policy")).toContain("geolocation=()");
    expect(res.headers.get("content-security-policy")).toContain("default-src 'self'");
  });
});
```

In CI, this runs against a `pnpm --filter web start` server backgrounded by the workflow. Locally, developers run `pnpm --filter web test` after starting the dev server. Satisfies AC 6.7.

## 8. Shared types and codegen pipeline

### 8.1 Flow

```mermaid
flowchart LR
  A[apps/api] -->|"uv run python -m matchlayer_api.tools.dump_openapi"| B(openapi.json)
  B -->|"openapi-typescript"| C[packages/shared-types/src/api-types.ts]
  B -->|"openapi-zod-client"| D[packages/shared-types/src/api-schemas.ts]
  C --> E[index.ts curated re-exports]
  D --> E
  E -->|"@matchlayer/shared-types"| F[apps/web]
```

### 8.2 The orchestrator

`packages/shared-types/scripts/codegen.mjs` runs three steps:

1. `execa("uv", ["run", "--project", "../../apps/api", "python", "-m", "matchlayer_api.tools.dump_openapi"], { stdout: writeStream("openapi.json") })`. Non-zero exit fails the script.
2. `execa("openapi-typescript", ["openapi.json", "--output", "src/api-types.ts"])`.
3. `execa("openapi-zod-client", ["openapi.json", "--output", "src/api-schemas.ts", "--with-alias"])`.
4. Delete `openapi.json` after both consumers succeed (it's a transient artifact).

The script never reads a pre-existing `openapi.json`; it's always a one-shot derivative of the live FastAPI app. Satisfies AC 7.7.

### 8.3 Drift check

CI runs `pnpm codegen` then `git diff --exit-code packages/shared-types/src/`. Any diff fails the build with a clear message ("Run `pnpm codegen` and commit the result"). Satisfies AC 7.8.

### 8.4 Curated re-exports

`packages/shared-types/src/index.ts` exposes named exports rather than path types:

```ts
import type { paths } from "./api-types";
export type HealthResponse = paths["/healthz"]["get"]["responses"]["200"]["content"]["application/json"];
export { HealthResponseSchema } from "./api-schemas";
```

Web code imports `HealthResponse` and `HealthResponseSchema`, never raw `paths` indexing. Satisfies AC 7.9.

## 9. CI pipeline design

### 9.1 Workflow shape

`.github/workflows/ci.yml` defines five jobs running in parallel on `pull_request` (target `main`) and `push` (`main`):

```mermaid
flowchart LR
  pr[PR / push] --> backend[backend]
  pr --> frontend[frontend]
  pr --> shared[shared-types]
  pr --> security[security]
  pr --> drift[openapi-drift]
```

Job summaries:

| Job | Key steps | Satisfies |
|---|---|---|
| `backend` | `uv sync --frozen`, `ruff format --check`, `ruff check`, `mypy`, `pytest` | AC 8.4, 8.11 |
| `frontend` | `pnpm install --frozen-lockfile`, `pnpm --filter web lint`, `pnpm --filter web typecheck`, `pnpm --filter web test`, `pnpm --filter web build` | AC 8.5, 8.11 |
| `shared` | `pnpm install --frozen-lockfile`, `pnpm --filter @matchlayer/shared-types lint`, `pnpm --filter @matchlayer/shared-types typecheck`, `pnpm --filter @matchlayer/shared-types test` | AC 8.5 |
| `security` | `pip-audit` against uv lockfile; `pnpm audit --prod`; `gitleaks` scan against PR diff; CodeQL action for `python` and `javascript-typescript` | AC 8.6–8.9 |
| `openapi-drift` | `uv sync --frozen`, `pnpm install --frozen-lockfile`, `pnpm codegen`, `git diff --exit-code packages/shared-types/src/` | AC 8.10 |

A trailing `required-checks` job depending on all five exists purely so branch protection can target a single check name.

### 9.2 Caching

- **uv:** `actions/setup-python` + `astral-sh/setup-uv@v2` with cache key `uv-${{ hashFiles('apps/api/uv.lock') }}`.
- **pnpm:** `pnpm/action-setup@v3` + `actions/setup-node@v4` with `cache: 'pnpm'`. Effective key derives from `pnpm-lock.yaml`.
- **Next.js build:** `actions/cache@v4` keyed on `apps/web/package.json` + `pnpm-lock.yaml`; restore-keys cascade to nightly base.

Cache writes are skipped on PRs from forks (GitHub default) — security boundary.

### 9.3 Concurrency

```yaml
concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}
```

Superseded PR runs are cancelled; main pushes always complete.

### 9.4 The 10-minute target

For a no-op markdown PR:
- `backend`: cache hit + ruff/mypy/pytest on a tiny suite — ~2 min.
- `frontend`: cache hit + lint/tsc/vitest/build — ~3 min.
- `shared`: cache hit + lint/tsc — ~1 min.
- `security`: pip-audit + pnpm audit + gitleaks (CodeQL runs async via default setup, doesn't block the merge gate) — ~3 min.
- `drift`: cache hit + codegen + diff — ~2 min.

All run in parallel; wall time ≤ longest job. Within budget. Satisfies AC 8.3.

### 9.5 .env drift detection

A small Python check inside the `backend` job (or a dedicated step) walks `apps/api/src` and `apps/web/src` looking for `MATCHLAYER_*` (Python) and `process.env.NEXT_PUBLIC_*` / `process.env.MATCHLAYER_*` (JS) references, then compares against `.env.example`. Two-direction failure (missing or stale). Satisfies AC 3.5, 3.6.

## 10. Pre-commit hooks

`.pre-commit-config.yaml` order:

1. `pre-commit-hooks` standard set: `trailing-whitespace`, `end-of-file-fixer`, `check-merge-conflict`, `check-yaml`, `check-json`, `check-added-large-files` (size limit 5 MB).
2. `gitleaks` (mirror at `gitleaks/gitleaks-action`).
3. `ruff` for `format` then `check --fix` (Python files only).
4. `prettier` for JS/TS/JSON/MD/YAML files only.

`pre-commit install` is documented in the README. CI re-runs the same hooks via `pre-commit run --all-files` as part of the `security` job to catch developers who skipped local install. Satisfies AC 9.1–9.7.

## 11. Container images

### 11.1 API image — `infra/docker/api.Dockerfile`

Multi-stage:

```dockerfile
# Stage 1: builder
FROM python:3.13-slim@sha256:<digest> AS builder
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /uvx /usr/local/bin/
WORKDIR /app
COPY apps/api/pyproject.toml apps/api/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY apps/api/src ./src
COPY apps/api/alembic.ini ./
COPY apps/api/alembic ./alembic
RUN uv sync --frozen --no-dev

# Stage 2: final (distroless)
FROM gcr.io/distroless/python3-debian13:nonroot@sha256:<digest>
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src
COPY --from=builder /app/alembic.ini /app/alembic.ini
COPY --from=builder /app/alembic /app/alembic
ENV PATH="/app/.venv/bin:${PATH}"
ENV PYTHONPATH="/app/src"
USER nonroot
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz').status == 200 else 1)"]
ENTRYPOINT ["uvicorn", "matchlayer_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

Notes:
- Distroless `python3-debian13:nonroot` runs as UID 65532 (≥ 10000, satisfies AC 10.4) and ships Python 3.13, matching the builder stage exactly — no version skew between builder and runtime interpreters.
- No shell in the final image — HEALTHCHECK uses the python interpreter directly.
- `--read-only` compatible; uvicorn doesn't write to disk. `/tmp` would be a tmpfs mount in production compose.
- Pinned digests with the human-readable tag in a comment for review.

### 11.2 Web image — `infra/docker/web.Dockerfile`

```dockerfile
# Stage 1: builder
FROM node:24-bookworm-slim@sha256:<digest> AS builder
RUN corepack enable
WORKDIR /repo
COPY pnpm-lock.yaml pnpm-workspace.yaml package.json ./
COPY apps/web/package.json apps/web/
COPY packages/shared-types/package.json packages/shared-types/
RUN pnpm install --frozen-lockfile
COPY . .
RUN pnpm --filter @matchlayer/web build

# Stage 2: final (distroless node)
FROM gcr.io/distroless/nodejs24-debian12:nonroot@sha256:<digest>
WORKDIR /app
COPY --from=builder /repo/apps/web/.next/standalone ./
COPY --from=builder /repo/apps/web/.next/static ./apps/web/.next/static
COPY --from=builder /repo/apps/web/public ./apps/web/public
USER nonroot
EXPOSE 3000
ENV PORT=3000
ENV HOSTNAME=0.0.0.0
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD ["/nodejs/bin/node", "-e", "fetch('http://127.0.0.1:3000/').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"]
ENTRYPOINT ["/nodejs/bin/node", "apps/web/server.js"]
```

Next is configured with `output: "standalone"` in `next.config.mjs` so the final image holds only the production server and its dependencies. Satisfies AC 10.8.

## 12. Environment variable contract

Single `.env.example` at repo root, shared between both apps. Web reads `NEXT_PUBLIC_*` for client-exposed values; everything else is server-side only.

| Variable | Owner | Default for local | Secret? | Notes |
|---|---|---|---|---|
| `MATCHLAYER_ENVIRONMENT` | api | `development` | no | Drives logging format |
| `MATCHLAYER_LOG_LEVEL` | api | `info` | no | |
| `MATCHLAYER_DATABASE_URL` | api | `postgresql+asyncpg://matchlayer:dev_only_password@localhost:5432/matchlayer` | yes (in prod) | |
| `MATCHLAYER_REDIS_URL` | api | `redis://localhost:6379/0` | yes (in prod) | |
| `MATCHLAYER_S3_ENDPOINT_URL` | api | `http://localhost:9000` | no | Empty in prod (real S3) |
| `MATCHLAYER_S3_REGION` | api | `us-east-1` | no | |
| `MATCHLAYER_S3_ACCESS_KEY_ID` | api | `matchlayer` | yes (in prod) | |
| `MATCHLAYER_S3_SECRET_ACCESS_KEY` | api | `dev_only_password` | yes | |
| `MATCHLAYER_S3_BUCKET` | api | `matchlayer-dev` | no | Manual `mc mb` |
| `MATCHLAYER_CORS_ALLOWED_ORIGINS` | api | `http://localhost:3000` | no | Comma-separated list |
| `NEXT_PUBLIC_API_BASE_URL` | web | `http://localhost:8000` | no | Frontend → backend |

The drift checker (§ 9.5) keeps this table honest. Satisfies AC 3.1–3.6.

## 13. Repo setup runbook

`docs/runbooks/repo-setup.md` is a numbered checklist intended to be re-runnable on any fork:

1. **Branch protection on `main`**
   1. Require pull request before merging (1 approval; for solo dev, allow self-approve via repo setting).
   2. Require status checks: `required-checks` (the trailing aggregator job).
   3. Require linear history.
   4. Disallow force pushes and deletions.
2. **Secret scanning**: enable in Settings → Code security & analysis.
3. **Push protection**: enable in the same panel — blocks pushes containing detected secrets.
4. **Dependabot**: confirm `.github/dependabot.yml` is committed; enable security updates in repo settings.
5. **CodeQL default setup**: enable for Python and JavaScript-TypeScript.
6. **Required environments** (deferred to Phase 6 — note here as a placeholder).
7. **Repository topics**: `nextjs`, `fastapi`, `ats`, `ai`, `monorepo` for discoverability.

The file is deliberately checklist-shaped (AC 11.5) and revisitable.

## 14. Trade-offs and decisions

- **Tailwind v4 + `@theme inline`** over a v3 `tailwind.config.ts`. v4 is a year old, the CSS-first config is cleaner, brand tokens and Tailwind theme values colocate. Risk: still a young version. Mitigation: pin minor version, watch the changelog.
- **Dump OpenAPI via a CLI command** rather than running a uvicorn server during codegen. Faster (no socket setup), more reliable in CI (no port conflicts), no flakiness.
- **Distroless over Alpine** for the API image. musl/glibc surprises with native Python deps make Alpine more pain than it's worth at our scale. Distroless gives non-root by default and minimal attack surface.
- **Single `ci.yml`** with parallel jobs over multiple workflow files. Easier to reason about; required-status-checks aggregator job keeps branch-protection config simple.
- **structlog over stdlib `logging`**. Native dict-merging, contextvar binding, JSON renderer; matches what we'll need in Phase 6 when CloudWatch ingests JSON.
- **No empty domain packages.** Empty `auth/`, `resumes/`, `matches/` get cargo-culted across files — later specs create them when there's content.
- **`'unsafe-inline'` in CSP for Phase 1.** Next's inline runtime needs it; tightening to nonces lands with the Phase 6 CDN/edge work.
- **Codegen orchestrator in JS, not Python.** It runs npm tools (`openapi-typescript`, `openapi-zod-client`); doing it from a JS script keeps execution close to the consumers.
- **Alembic baseline revision is empty.** Tempting to seed it with extensions (`pgvector`) — but pgvector is Phase 2. Discipline > convenience.

## 15. Open questions

These don't block tasks generation but should be resolved before or during implementation:

1. **CI runner choice.** GitHub-hosted `ubuntu-latest` (free, default) vs. self-hosted (faster on later phases). Default is GitHub-hosted; revisit in Phase 6.
2. **Pre-commit on Windows.** WSL works fine. Native Windows is not a target — README will say "use WSL".
3. **uv lockfile location.** `apps/api/uv.lock` is the natural place. Confirmed — but if we add a second uv project (`ml/`), each gets its own.
4. **MinIO bucket bootstrap.** Manual `mc mb` for now. Auto-create script lands in `phase-1-matching` when it's actually needed.
5. **Ratelimit strategy** for non-auth endpoints. Out of scope here; flagged so `phase-1-auth` and `phase-1-matching` can converge on a shared Redis-backed approach.
