# Implementation Plan: phase-1-foundation

## Overview

This spec lands as **one PR** on branch `phase-1/foundation` (per `conventions.md`) that scaffolds the monorepo, local dev stack, baseline apps, codegen pipeline, CI, pre-commit hooks, container images, and supporting docs. The PR carries no domain logic — sibling specs `phase-1-auth` and `phase-1-matching` build on this scaffold.

The implementation is the test: a green CI run on the foundation PR itself, plus the explicit `/healthz` and security-headers automated tests, plus a final manual smoke against the README, are the acceptance signal.

Languages (fixed by `tech.md` and the design): **Python 3.13** for the API, **TypeScript** for the web app, **Node.js 24 (ESM)** for the codegen orchestrator. No language selection needed — the design specifies concrete stacks, not pseudocode.

## Tasks

- [x] 1. Repo + workspace bootstrap
  - [x] 1.1 Author root tooling configs
    - Create `.editorconfig` (LF, UTF-8, 2-space indent default, 4-space for Python).
    - Create `.nvmrc` containing `24`.
    - Create `.python-version` containing `3.13`.
    - Extend `.gitignore` with `.env`, `.env.local`, `.venv/`, `node_modules/`, `.next/`, `__pycache__/`, `.pytest_cache/`, `.ruff_cache/`, `.mypy_cache/`, `dist/`, `*.egg-info/`, `coverage/`, `openapi.json` (transient codegen artifact).
    - _Requirements: 3.4_
    - _Design: §3, §12_

  - [x] 1.2 Author pnpm workspace, root `package.json`, and `tsconfig.base.json`
    - Create `pnpm-workspace.yaml` declaring `apps/*` and `packages/*`.
    - Create root `package.json` (private, `"name": "matchlayer"`) with `engines.node = ">=24"`, an exact `packageManager` pin (e.g., `"packageManager": "pnpm@9.15.0"` — corepack rejects ranges like `9.x`), dev-only deps for `prettier`, `typescript`, and the two openapi-\* codegen tools, and the top-level scripts `lint`, `typecheck`, `test`, `build`, `codegen`, `format` (the codegen script invokes `node packages/shared-types/scripts/codegen.mjs`; the others fan out via `pnpm -r --parallel run`).
    - Create `tsconfig.base.json` with `strict`, `noUncheckedIndexedAccess`, `target ES2022`, `module ESNext`, `moduleResolution Bundler`, `skipLibCheck`, and a path alias `@matchlayer/shared-types` → `packages/shared-types/src`.
    - _Requirements: 1.1, 1.2, 1.6, 1.7_
    - _Design: §3, §4_

- [x] 2. Local development stack
  - [x] 2.1 Author `docker-compose.yml`
    - Define three services: `postgres` (image `postgres:16-alpine` pinned by digest, env `POSTGRES_USER/PASSWORD/DB`, port 5432, named volume `matchlayer-postgres-data`, healthcheck `pg_isready` interval 2s × 30 retries), `redis` (image `redis:7-alpine` pinned by digest, port 6379, healthcheck `redis-cli ping`), and `minio` (image `minio/minio` pinned by digest, ports 9000/9001, env `MINIO_ROOT_USER/PASSWORD`, named volume `matchlayer-minio-data`, healthcheck against `/minio/health/live`).
    - Declare both named volumes at the top level so `docker compose down` (without `-v`) preserves them.
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8_
    - _Design: §5_

  - [x] 2.2 Author `.env.example` at the repo root
    - Include every variable from the design's environment-variable contract: `MATCHLAYER_ENVIRONMENT`, `MATCHLAYER_LOG_LEVEL`, `MATCHLAYER_DATABASE_URL`, `MATCHLAYER_REDIS_URL`, `MATCHLAYER_S3_ENDPOINT_URL`, `MATCHLAYER_S3_REGION`, `MATCHLAYER_S3_ACCESS_KEY_ID`, `MATCHLAYER_S3_SECRET_ACCESS_KEY`, `MATCHLAYER_S3_BUCKET`, `MATCHLAYER_CORS_ALLOWED_ORIGINS`, `NEXT_PUBLIC_API_BASE_URL`.
    - Use placeholder values that match the docker-compose defaults so `cp .env.example .env` is enough to run both apps locally with no further edits.
    - _Requirements: 3.1, 3.2, 3.3_
    - _Design: §12_

- [x] 3. Backend baseline (`apps/api`)
  - [x] 3.1 Initialize the uv project with `pyproject.toml` and pinned dependencies
    - Create `apps/api/pyproject.toml` declaring `name = "matchlayer-api"`, `requires-python = ">=3.13"`, runtime deps with pinned majors (`fastapi`, `uvicorn[standard]`, `pydantic`, `pydantic-settings`, `sqlalchemy[asyncio]`, `asyncpg`, `alembic`, `psycopg[binary]` for Alembic sync, `structlog`, `uuid_utils`), and dev deps (`ruff`, `mypy`, `pytest`, `pytest-asyncio`, `httpx`, `pip-audit`).
    - Configure `[tool.ruff]`, `[tool.mypy]` (strict on `services`, `api`, `core`, `ml`), and `[tool.pytest.ini_options]` (asyncio mode `auto`).
    - Run `uv sync` and commit `apps/api/uv.lock`.
    - _Requirements: 1.4, 1.5_
    - _Design: §4, §6.1_

  - [x] 3.2 Implement `config.py` (Pydantic Settings)
    - Create `apps/api/src/matchlayer_api/__init__.py` and `config.py` with a single `Settings(BaseSettings)` class using `env_prefix="MATCHLAYER_"`, fields matching `.env.example`, and types `Literal[...]`, `PostgresDsn`, `RedisDsn`, `SecretStr`, `list[AnyHttpUrl]`.
    - Expose a cached accessor `get_settings()` for use as a FastAPI dependency.
    - _Requirements: 4.2, 4.3_
    - _Design: §6.2_

  - [x] 3.3 Implement structlog logging with PII redaction
    - Create `apps/api/src/matchlayer_api/core/__init__.py` and `core/logging.py` with `configure_logging(settings)` that wires a JSON renderer in non-development and a console renderer in development.
    - Include processors for `merge_contextvars`, `add_log_level`, ISO/UTC `TimeStamper`, `StackInfoRenderer`, `format_exc_info`, and a small redaction processor that scrubs keys matching `password|token|secret|email|resume_text|parsed_text`.
    - _Requirements: 4.4, 4.14_
    - _Design: §6.3_

  - [x] 3.4 Implement the request-id ASGI middleware
    - Create `core/middleware.py` exporting `RequestIdMiddleware` (pure ASGI, not `BaseHTTPMiddleware`) that reuses inbound `X-Request-Id` matching `^[A-Za-z0-9_-]{8,128}$`, otherwise generates a UUIDv7 via `uuid_utils`, binds `request_id`/`route`/`method` to a structlog contextvar, sets `X-Request-Id` on the response, and emits one structured access log line per request with `status` and `latency_ms`.
    - _Requirements: 4.4, 4.5, 4.6_
    - _Design: §6.4_

  - [x] 3.5 Implement RFC 7807 error handlers
    - Create `core/errors.py` defining a base `MatchLayerError`, a registration helper `register_exception_handlers(app)`, and handlers for `MatchLayerError`, `RequestValidationError`, and the catch-all `Exception` that emit `{type, title, detail, status, request_id}`.
    - In production, the catch-all handler returns generic `internal_server_error` text; the original exception is logged but never returned.
    - _Requirements: 4.13_
    - _Design: §6.8_

  - [x] 3.6 Implement async DB engine, session factory, and dependency
    - Create `core/db.py` exposing `engine`, `SessionLocal`, and `get_session()` (async iterator) using `pool_pre_ping=True`, `pool_size=5`, `max_overflow=5`.
    - Add a lifespan helper `verify_database_connection()` that runs `SELECT 1` once at startup; raise on failure so uvicorn exits non-zero.
    - _Requirements: 4.10, 4.12, 4.13_
    - _Design: §6.6_

  - [x] 3.7 Implement the `/healthz` router
    - Create `apps/api/src/matchlayer_api/api/__init__.py` and `api/health.py` with an async `GET /healthz` that runs `await session.execute(text("SELECT 1"))` via the `get_session` dependency.
    - On success return `200 {"status": "ok"}`; on `SQLAlchemyError` log a structured warning (no DSN/credentials) and return `503 {"status": "unhealthy", "reason": "database_unreachable"}`.
    - _Requirements: 4.7, 4.8, 4.9_
    - _Design: §6.5_

  - [x] 3.8 Implement the application factory `main.py`
    - Create `apps/api/src/matchlayer_api/main.py` exporting `create_app() -> FastAPI` and a module-level `app = create_app()`.
    - Wire: `configure_logging`, lifespan that calls `verify_database_connection`, `RequestIdMiddleware`, CORS middleware reading `cors_allowed_origins` from settings, the error handlers from §3.5, and the `/healthz` router from §3.7.
    - _Requirements: 4.1, 4.12_
    - _Design: §6.1, §6.6, §6.8_

  - [x] 3.9 Configure Alembic with an empty baseline revision
    - Create `apps/api/alembic.ini` pointing at `alembic/`.
    - Create `apps/api/alembic/env.py` that imports `Settings`, derives the sync URL by swapping `+asyncpg` for `+psycopg`, and configures the offline + online run modes.
    - Add `apps/api/alembic/script.py.mako` (Alembic default).
    - Add `apps/api/alembic/versions/0000_baseline.py` — an empty revision with no `upgrade()`/`downgrade()` operations, present so future revisions have a parent.
    - _Requirements: 4.11_
    - _Design: §6.7_

  - [x] 3.10 Implement the OpenAPI dump CLI
    - Create `apps/api/src/matchlayer_api/tools/__init__.py` and `tools/dump_openapi.py` that imports `create_app()`, calls `app.openapi()`, and writes `json.dumps(spec, indent=2)` to stdout.
    - The script must be invocable as `uv run --project apps/api python -m matchlayer_api.tools.dump_openapi`.
    - Note: `app.openapi()` does not invoke the lifespan, so this command does NOT require a running Postgres. It does require `.env` to exist (so `Settings` can validate at import time) — `cp .env.example .env` from §2.2 is sufficient.
    - _Requirements: 7.2, 7.7_
    - _Design: §6.9_

  - [x] 3.11 Write pytest tests for `/healthz`
    - Create `apps/api/tests/conftest.py` exposing an `httpx.AsyncClient` fixture against `create_app()` and a fixture that overrides `get_session` with a stub that either succeeds or raises `SQLAlchemyError`.
    - Create `apps/api/tests/test_health.py` with two cases: (a) DB probe succeeds → `200 {"status": "ok"}`; (b) DB probe raises → `503 {"status": "unhealthy", "reason": "database_unreachable"}` and the response body contains no DSN, credentials, or PII.
    - _Requirements: 4.7, 4.8, 4.9, 4.14_
    - _Design: §6.5_

- [x] 4. Frontend baseline (`apps/web`)
  - [x] 4.1 Scaffold the Next.js App Router project
    - Initialize `apps/web` (Next.js latest, TypeScript, App Router, ESLint, Tailwind v4, src/ layout, no experimental flags). Replace any default `tailwind.config.ts` with the design's CSS-first approach.
    - Set the package manifest fields explicitly: `"name": "@matchlayer/web"`, `"private": true`, `"type": "module"`. The `@matchlayer/web` name is referenced by every `pnpm --filter` invocation in CI and Dockerfiles.
    - Set `next.config.mjs` with `output: "standalone"` (required by §11.2 Dockerfile).
    - Configure `tsconfig.json` extending `../../tsconfig.base.json` with `strict`, `noImplicitAny`, `strictNullChecks`, `noUncheckedIndexedAccess`.
    - Add `vitest.config.ts` and `eslint.config.mjs`. Set `package.json` scripts: `dev`, `build`, `start`, `lint`, `typecheck` (`tsc --noEmit`), `test` (`vitest run`), `format` (`prettier --check`).
    - Pin Framer Motion, `next-themes`, `lucide-react`, `clsx`, `tailwind-merge`, and shadcn-friendly Radix primitives at known-good majors.
    - _Requirements: 5.1, 5.9, 5.10_
    - _Design: §7.1_

  - [x] 4.2 Wire Tailwind v4 with brand tokens via `globals.css`
    - Author `apps/web/src/app/globals.css` with `@import "tailwindcss";`, `:root` and `.dark` blocks defining every token from `design.md` (`--color-bg`, `--color-bg-elevated`, `--color-bg-glass`, `--color-border`, `--color-border-strong`, `--color-text`, `--color-text-muted`, `--color-text-subtle`, `--color-brand`, `--color-brand-2`, `--color-success`, `--color-warning`, `--color-danger`) as `R G B` triplets, plus an `@theme inline` block that re-exports them as Tailwind theme colors and binds `--font-sans`/`--font-mono` to the next/font CSS variables.
    - _Requirements: 5.2, 5.7, 5.11_
    - _Design: §7.2_

  - [x] 4.3 Initialize shadcn/ui with the Button primitive and `cn()` helper
    - Add `apps/web/components.json` (CSS-variables mode, base color `neutral`, paths under `src/components/ui`).
    - Add `apps/web/src/lib/utils.ts` exporting `cn(...inputs)` based on `clsx` + `tailwind-merge`.
    - Add `apps/web/src/components/ui/button.tsx` (the standard shadcn Button primitive).
    - _Requirements: 5.3_
    - _Design: §7.3_

  - [x] 4.4 Wire Geist Sans + Geist Mono and the root `layout.tsx`
    - In `apps/web/src/app/layout.tsx`, import `Geist` and `Geist_Mono` from `next/font/google`, expose them as `--font-geist-sans` / `--font-geist-mono`, set `<html lang="en" suppressHydrationWarning>` with both CSS variables on `className`, set `<body>` to use `bg-bg text-text font-sans antialiased`, and wrap `{children}` in the `ThemeProvider` from §4.5.
    - Import `globals.css` at the top of the file.
    - _Requirements: 5.4_
    - _Design: §7.4_

  - [x] 4.5 Add the next-themes provider and theme toggle
    - Create `apps/web/src/components/theme-provider.tsx` (`'use client'`) wrapping `next-themes`'s `ThemeProvider` with `attribute="class"`, `defaultTheme="system"`, `enableSystem`.
    - Create `apps/web/src/components/theme-toggle.tsx` (`'use client'`) using the shadcn `Button` primitive plus the `Sun` / `Moon` Lucide icons.
    - _Requirements: 5.6_
    - _Design: §7.6_

  - [x] 4.6 Add the reduced-motion-aware Framer Motion helper
    - Create `apps/web/src/components/motion-safe.tsx` (`'use client'`) exporting a `useMotionSafeProps(props)` hook that returns the input props unchanged unless `useReducedMotion()` is true, in which case it forces `animate = initial` and zero-duration transitions.
    - _Requirements: 5.5_
    - _Design: §7.5_

  - [x] 4.7 Implement the security-headers proxy
    - Create `apps/web/src/proxy.ts` that runs on every request and sets `Content-Security-Policy` (the §7.7 value), `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`, `Permissions-Policy: camera=(), microphone=(), geolocation=()`, and conditionally `Strict-Transport-Security: max-age=31536000; includeSubDomains; preload` when the request scheme is HTTPS.
    - Configure the `proxy` `matcher` to apply to all paths.
    - Note: this reflects the Next.js 16 file-convention rename (`middleware.ts` → `proxy.ts`, function `middleware` → `proxy`); behavior, `config.matcher`, and `NextRequest`/`NextResponse` imports are unchanged. See https://nextjs.org/docs/app/api-reference/file-conventions/proxy.
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6_
    - _Design: §7.7_

  - [x] 4.8 Build the placeholder landing page
    - Author `apps/web/src/app/page.tsx` rendering a hero section that contains the literal string `"MatchLayer"` rendered with the violet → cyan brand gradient (`bg-gradient-to-br from-brand to-brand-2 bg-clip-text text-transparent`), a tagline ("AI-native ATS, transparent scoring"), the theme toggle from §4.5, and a Framer Motion fade-up on the hero text wired through `useMotionSafeProps`.
    - Use only design-system tokens (`bg-bg`, `text-text`, `text-muted`, `bg-bg-elevated`); no hex literals.
    - Verify WCAG AA contrast in both themes for every text element.
    - _Requirements: 5.5, 5.7, 5.8, 5.11_
    - _Design: §7.8_

  - [x] 4.9 Write the security-headers vitest test
    - Add `apps/web/tests/proxy.test.ts` (or equivalent under `src/`) that asserts every header from §4.7 is present with its exact value on a fetch to `/`.
    - The test runs against a real built+started Next server; the CI `frontend` job (§7.2) handles `pnpm --filter @matchlayer/web build` → `pnpm --filter @matchlayer/web start &` → wait-for-server → `pnpm --filter @matchlayer/web test`. Document this convention in a top-of-file comment in the test so a developer running tests locally knows to start the server first.
    - _Requirements: 6.7_
    - _Design: §7.9_

- [x] 5. Shared types + codegen pipeline (`packages/shared-types`)
  - [x] 5.1 Initialize the `@matchlayer/shared-types` package
    - Create `packages/shared-types/package.json` (`"name": "@matchlayer/shared-types"`, `"private": true`, `"type": "module"`) with scripts `lint` (`eslint .`), `typecheck` (`tsc --noEmit`), `test` (`vitest run --passWithNoTests`), `format` (`prettier --check 'src/**/*.{ts,json,md}' 'scripts/**/*.mjs'`), `codegen` (`node ./scripts/codegen.mjs`). Every script must exist so the root `pnpm -r --parallel run` fan-outs from §1.2 don't silently skip this package and so the CI `shared-types` job (§7.2) can invoke each one.
    - Declare devDeps: `openapi-typescript`, `openapi-zod-client`, `execa`, `typescript`, `zod`, `eslint`, `prettier`, `vitest`.
    - Create `packages/shared-types/tsconfig.json` extending `../../tsconfig.base.json`, with `include: ["src"]` and `noEmit: true`.
    - Create `packages/shared-types/eslint.config.mjs` (flat config, TypeScript preset, ignores `src/api-types.ts` and `src/api-schemas.ts` since those are generated).
    - Create `packages/shared-types/vitest.config.ts` configured with `environment: "node"` (no DOM needed for type-level tests). The package may have no tests in this spec — `--passWithNoTests` keeps the CI job green until a sibling spec adds one.
    - _Requirements: 7.1, 8.5_
    - _Design: §3, §8, §9.1_

  - [x] 5.2 Author the codegen orchestrator
    - Create `packages/shared-types/scripts/codegen.mjs` (Node ESM). At the top of the script, resolve its own directory with `fileURLToPath(import.meta.url)` and set the working directory used by all subsequent shell-outs to `packages/shared-types/` (the parent of `scripts/`) so the relative paths below resolve regardless of where `pnpm codegen` is invoked from.
    - In order: (1) shell out via `execa("uv", ["run", "--project", "../../apps/api", "python", "-m", "matchlayer_api.tools.dump_openapi"], { cwd: <packages/shared-types> })` and pipe stdout into `openapi.json`; (2) run `openapi-typescript openapi.json --output src/api-types.ts`; (3) run `openapi-zod-client openapi.json --output src/api-schemas.ts --with-alias`; (4) delete `openapi.json` on success.
    - Any non-zero exit from steps 1–3 must propagate. Never read or fall back to a pre-existing `openapi.json` — always re-derive from the live FastAPI app.
    - _Requirements: 7.2, 7.3, 7.4, 7.5, 7.6, 7.7_
    - _Design: §8.1, §8.2_

  - [x] 5.3 Run `pnpm codegen` once and commit the generated artifacts
    - Prerequisites: `apps/api` deps installed (§3.1) and `.env` present at the repo root (`cp .env.example .env`). Postgres does NOT need to be running — `app.openapi()` does not invoke the lifespan or open a DB connection.
    - From the repo root, run `pnpm codegen`.
    - Commit the resulting `packages/shared-types/src/api-types.ts` and `packages/shared-types/src/api-schemas.ts`. These will be regenerated by the CI drift check on every PR.
    - Confirm both files reference the `/healthz` endpoint and no extras.
    - _Requirements: 7.3, 7.4_
    - _Design: §8.1_

  - [x] 5.4 Author the curated `index.ts`
    - Create `packages/shared-types/src/index.ts` that imports the `paths` type from `./api-types` and exports a named `HealthResponse` alias derived from `paths["/healthz"]["get"]["responses"]["200"]["content"]["application/json"]`.
    - Re-export `HealthResponseSchema` (or whatever name `openapi-zod-client` produces for the `/healthz` 200 response) from `./api-schemas` under the same curated name.
    - Verify `pnpm --filter @matchlayer/shared-types typecheck` passes.
    - _Requirements: 7.9_
    - _Design: §8.4_

- [x] 6. Checkpoint — backend, frontend, and codegen all green locally
  - Run `docker compose up -d --wait`, `uv run --project apps/api uvicorn matchlayer_api.main:app` (must start without error and `curl /healthz` returns 200), `cd apps/api && uv run pytest`, `pnpm --filter @matchlayer/web build && pnpm --filter @matchlayer/web test`, and `pnpm codegen` (zero diff). Ensure all tests pass, ask the user if questions arise.

- [x] 7. CI pipeline
  - [x] 7.1 Implement the `.env` drift-detection script
    - Create `tools/check_env_drift.py` (small standalone script, no extra deps beyond stdlib) that walks `apps/api/src` for `MATCHLAYER_*` env-var references (regex over `os.environ` and Pydantic Settings field names) and walks `apps/web/src` for `process.env.MATCHLAYER_*` and `process.env.NEXT_PUBLIC_*` references, then compares the union against the keys present in `.env.example`. Exit non-zero on either missing or stale entries with a message naming each variable.
    - _Requirements: 3.5, 3.6_
    - _Design: §9.5_

  - [x] 7.2 Author `.github/workflows/ci.yml`
    - Trigger on `pull_request` (target `main`) and `push` (`main`). Set top-level `concurrency: { group: ci-${{ github.ref }}, cancel-in-progress: ${{ github.event_name == 'pull_request' }} }`.
    - Define five parallel jobs:
      - `backend`: `astral-sh/setup-uv@v2` with cache key on `apps/api/uv.lock`, `uv sync --frozen` in `apps/api`, `ruff format --check`, `ruff check`, `mypy`, `pytest`. Run `python tools/check_env_drift.py` as a final step.
      - `frontend`: `pnpm/action-setup@v3` + `actions/setup-node@v4` (`cache: "pnpm"`), `pnpm install --frozen-lockfile`, `pnpm --filter @matchlayer/web lint`, `pnpm --filter @matchlayer/web format` (prettier --check), `pnpm --filter @matchlayer/web typecheck`, `pnpm --filter @matchlayer/web build` (`actions/cache@v4` keyed on `apps/web/package.json` + `pnpm-lock.yaml`), then `pnpm --filter @matchlayer/web start &` followed by `npx wait-on http://127.0.0.1:3000` (or an equivalent `curl --retry` poll) so the test does not race the server, then `pnpm --filter @matchlayer/web test`.
      - `shared-types`: `pnpm install --frozen-lockfile`, `pnpm --filter @matchlayer/shared-types lint`, `pnpm --filter @matchlayer/shared-types format` (prettier --check), `pnpm --filter @matchlayer/shared-types typecheck`, `pnpm --filter @matchlayer/shared-types test`. AC 8.5 requires every one of these gates to run against the Shared_Types_Package.
      - `security`:
        - `pip-audit`: run as `uv export --project apps/api --no-dev --format requirements-txt > requirements.txt && pip-audit --strict -r requirements.txt`. Fail the build on any unfixed vulnerability of `high` or `critical` severity (use `pip-audit`'s default fail behavior plus `--ignore-vuln` for any explicitly accepted CVE; track accepted CVEs in `.pip-audit-ignore` if needed).
        - `pnpm audit --prod --audit-level=high` (the `--audit-level` flag gates failures to high/critical only).
        - `gitleaks` PR-diff scan via `gitleaks/gitleaks-action`.
        - The GitHub-managed `github/codeql-action/init` + `github/codeql-action/analyze` for `python` and `javascript-typescript`.
        - Run `pre-commit run --all-files` here too, so developers who skipped `pre-commit install` locally are still caught.
      - `openapi-drift`: `astral-sh/setup-uv@v2`, `uv sync --frozen --project apps/api`, `pnpm install --frozen-lockfile`, `pnpm codegen`, `git diff --exit-code packages/shared-types/src/` (failure message: "Run `pnpm codegen` and commit the result").
    - Add a final `required-checks` job with `needs: [backend, frontend, shared-types, security, openapi-drift]` and `if: always()` so branch protection can target a single check name.
    - _Requirements: 3.5, 3.6, 7.8, 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8, 8.9, 8.10, 8.11, 8.12_
    - _Design: §9.1, §9.2, §9.3, §9.4_

  - [x] 7.3 Author `.github/dependabot.yml`
    - Enable security-only updates (`open-pull-requests-limit: 0` for non-security ecosystems, `package-ecosystem: "npm"` rooted at `/`, `package-ecosystem: "pip"` rooted at `/apps/api`, `package-ecosystem: "github-actions"` rooted at `/`).
    - _Design: §9; security.md "Dependency & supply-chain security"_

- [x] 8. Pre-commit hooks
  - [x] 8.1 Author `.pre-commit-config.yaml`
    - Hooks (in order): `pre-commit-hooks` standard set (`trailing-whitespace`, `end-of-file-fixer`, `check-merge-conflict`, `check-yaml`, `check-json`, `check-added-large-files` with `--maxkb=5120`); `gitleaks` (mirror); `ruff` for `format` then `check --fix` scoped to Python files; `prettier` scoped to JS/TS/JSON/MD/YAML files.
    - Run `pre-commit install` locally and `pre-commit run --all-files` once to verify the foundation tree passes the hooks before the foundation PR is opened.
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7_
    - _Design: §10_

- [x] 9. Production container images
  - [x] 9.1 Author `infra/docker/api.Dockerfile`
    - Multi-stage: builder `python:3.13-slim` pinned by digest, copy in `uv` from `ghcr.io/astral-sh/uv`, `uv sync --frozen --no-dev`, copy `apps/api/src` and `apps/api/alembic*`. Final stage `gcr.io/distroless/python3-debian13:nonroot` pinned by digest (this image ships Python 3.13, matching the builder), copy the resolved `.venv` and the source/alembic dirs, set `PATH` and `PYTHONPATH`, `USER nonroot`, `EXPOSE 8000`, `HEALTHCHECK` that GETs `http://127.0.0.1:8000/healthz` via `python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz').status == 200 else 1)"`, `ENTRYPOINT` running uvicorn against `matchlayer_api.main:app` on `0.0.0.0:8000`.
    - Document the `--read-only` runtime contract in a top-of-file comment: the image must be runnable as `docker run --read-only --tmpfs /tmp ...`. uvicorn does not write to disk; `/tmp` is mounted as tmpfs for any transient interpreter scratch.
    - Verify locally:
      - `docker build -f infra/docker/api.Dockerfile .` exits 0 from a fresh clone.
      - `docker run --rm --read-only --tmpfs /tmp -e MATCHLAYER_DATABASE_URL=... -p 8000:8000 <image>` starts cleanly and `curl http://127.0.0.1:8000/healthz` returns a response (200 against a real DB or 503 if no DB attached — either proves the read-only runtime didn't crash on a write).
    - _Requirements: 10.1, 10.3, 10.4, 10.5, 10.6, 10.7, 10.9_
    - _Design: §11.1_

  - [x] 9.2 Author `infra/docker/web.Dockerfile`
    - Multi-stage: builder `node:24-bookworm-slim` pinned by digest, `corepack enable`, copy lockfile + workspace files, `pnpm install --frozen-lockfile`, copy the rest, `pnpm --filter @matchlayer/web build` (relies on `output: "standalone"` from §4.1). Final stage `gcr.io/distroless/nodejs24-debian12:nonroot` pinned by digest, copy `.next/standalone`, `.next/static`, `public` from the builder, `USER nonroot`, `EXPOSE 3000`, `HEALTHCHECK` GETting `http://127.0.0.1:3000/`, `ENTRYPOINT ["/nodejs/bin/node", "apps/web/server.js"]`.
    - Document the `--read-only` runtime contract in a top-of-file comment: the image must be runnable as `docker run --read-only --tmpfs /tmp ...`. The Next.js standalone server does not write to disk at runtime; `/tmp` is mounted as tmpfs for any transient scratch.
    - Verify locally:
      - `docker build -f infra/docker/web.Dockerfile .` exits 0 from a fresh clone.
      - `docker run --rm --read-only --tmpfs /tmp -p 3000:3000 <image>` starts cleanly and `curl http://127.0.0.1:3000/` returns 200.
    - _Requirements: 10.2, 10.3, 10.4, 10.5, 10.6, 10.8, 10.9_
    - _Design: §11.2_

- [x] 10. Documentation
  - [x] 10.1 Author `docs/runbooks/repo-setup.md`
    - Numbered, re-runnable checklist covering (1) branch protection on `main` (require PR with 1 approval, require the `required-checks` aggregator job, require linear history, disallow force pushes/deletions); (2) Secret Scanning + Push Protection; (3) Dependabot security updates; (4) CodeQL default setup for Python and JavaScript-TypeScript; (5) repository topics; (6) a placeholder section for environments deferred to Phase 6; (7) a documented manual smoke test: open a throwaway branch with a deliberate lint or test failure, push it, confirm the `required-checks` aggregator fails, and confirm branch protection blocks merge — all as a post-setup validation.
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5_
    - _Design: §13_

  - [x] 10.2 Update the root `README.md`
    - Add a "Prerequisites" section: pnpm, Node 24+, Python 3.13+, uv, Docker Engine 24+, `pre-commit`, and `gitleaks` v8.x on PATH (install from https://github.com/gitleaks/gitleaks/releases — the `.pre-commit-config.yaml` uses the `gitleaks-system` hook variant which requires a prebuilt binary rather than compiling from Go source). Note WSL on Windows for the `pre-commit` and `gitleaks` install paths.
    - Replace the existing "Running locally" section with a numbered setup flow: clone → `cp .env.example .env` → `pnpm install` → `uv sync` (in `apps/api`) → `docker compose up -d --wait` → `uv run --project apps/api alembic upgrade head` (no-op against the empty baseline, but exercises wiring) → `pre-commit install` → `uv run --project apps/api uvicorn matchlayer_api.main:app --reload` (port 8000) and `pnpm --filter @matchlayer/web dev` (port 3000).
    - Add a "Branch & PR conventions" pointer that the foundation PR uses branch `phase-1/foundation` and link to `conventions.md`.
    - Add a "GitHub-side configuration" section linking to `docs/runbooks/repo-setup.md`.
    - Add a "What's next" section naming `phase-1-auth` (delivers JWT auth, register/login/refresh/logout/password-reset, rate limiting, audit log) and `phase-1-matching` (delivers resume upload, parsing, TF-IDF scoring, results UI) as the remaining Phase 1 specs.
    - _Requirements: 12.1, 12.2, 12.3, 12.4_
    - _Design: §13_

- [x] 11. Final QA / smoke
  - [x] 11.1 Walk the README setup flow on a simulated fresh clone
    - From the foundation branch, simulate a fresh clone in a way that does NOT touch the live working tree: clone the repo into a sibling temp directory (`git clone . ../matchlayer-smoke && cd ../matchlayer-smoke`), then run only `cp .env.example .env` and proceed with the README steps in order.
    - Do NOT run `git clean -xdf` on the primary working tree — it destroys uncommitted work.
    - Execute every numbered step in the README in order. Record any deviation, fix the README in this same task, and re-run until the steps pass verbatim.
    - Delete `../matchlayer-smoke` when finished.
    - _Requirements: 12.2_
    - _Design: §13_

  - [x] 11.2 Confirm `pnpm codegen` is a no-op on a clean tree
    - From a clean checkout of the foundation branch, run `pnpm codegen` and `git diff --exit-code packages/shared-types/src/`. Both must succeed.
    - _Requirements: 7.5, 7.8_
    - _Design: §8.3_

  - [x] 11.3 Open the foundation PR and verify CI is green
    - Push branch `phase-1/foundation`, open a PR targeting `main`, and confirm all five required CI jobs (`backend`, `frontend`, `shared-types`, `security`, `openapi-drift`) plus the `required-checks` aggregator pass green.
    - Then run the manual deliberate-failure smoke test documented in `docs/runbooks/repo-setup.md` §10.1 step (7) on a separate throwaway branch to confirm branch protection blocks merge on a red `required-checks`.
    - _Requirements: 8.2, 8.3, 8.12_
    - _Design: §9, §13_

  - [x] 11.4 Final checkpoint — Ensure all tests pass
    - Ensure all tests pass, ask the user if questions arise.

## Notes

- This spec lands as a single PR on branch `phase-1/foundation`. No feature flags, no multi-deploy steps. The PR's green CI run plus the explicit `/healthz` and security-headers tests are the acceptance signal.
- The design has no "Correctness Properties" section — Phase 1 foundation is configuration and scaffolding, not algorithmic. Tests are scoped to the two explicitly mandated cases (backend `/healthz`, frontend security headers) plus lint/typecheck/audit gates run by CI.
- All test sub-tasks above are mandatory (no `*` postfix) because they are required by acceptance criteria 4.7–4.9 and 6.7. There are no optional tests in this spec.
- Each task references the granular requirement IDs it satisfies and the design section it implements, so traceability is preserved without duplicating design content.
- Sequential ordering: every task can be executed without forward references. The codegen task (5.3) sits after both `apps/api` exposes the OpenAPI dump (3.10) and `packages/shared-types` exists with an orchestrator (5.1, 5.2). The CI workflow (7.2) sits after the source it tests exists.
- Sibling specs `phase-1-auth` and `phase-1-matching` consume this scaffold; do not pull their work (auth flows, resume upload, scoring, MinIO bucket bootstrap, Alembic domain migrations) into this spec.
- Tasks completed before this refresh: sections 1 through 5 (repo + workspace bootstrap, local dev stack, backend baseline, frontend baseline, shared types + codegen). The dependency graph below contains only the remaining incomplete leaf sub-tasks under sections 6 through 11.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["6", "7.1", "7.3", "8.1", "9.1", "9.2"] },
    { "id": 1, "tasks": ["7.2"] },
    { "id": 2, "tasks": ["10.1"] },
    { "id": 3, "tasks": ["10.2"] },
    { "id": 4, "tasks": ["11.1", "11.2"] },
    { "id": 5, "tasks": ["11.3"] },
    { "id": 6, "tasks": ["11.4"] }
  ]
}
```
