# MatchLayer — Conventions

## API design
- **Versioned base path:** `/api/v1/...`. Bump to `v2` only on breaking changes.
- **Resources are plural:** `/resumes`, `/matches`, `/users`.
- **Verbs in path are a smell.** Use HTTP methods. Exceptions allowed for actions that don't map cleanly (e.g., `POST /resumes/{id}:reanalyze`).
- **Response envelope:** return resources directly, not wrapped in `{ data: ... }`. Errors use the shape below.
- **Error shape (RFC 7807-inspired):**
  ```json
  {
    "type": "validation_error",
    "title": "Resume file too large",
    "detail": "Max upload size is 5MB",
    "status": 413,
    "request_id": "..."
  }
  ```
- **Pagination:** cursor-based for lists that can grow unbounded; `?limit=&cursor=`. Avoid offset pagination.
- **IDs:** UUIDv7 (time-ordered) everywhere, exposed as strings. Never expose DB sequence ints.
- **Timestamps:** ISO 8601 UTC with `Z` suffix. Field names: `created_at`, `updated_at`.

## Python (backend, ML)
- **Format:** `ruff format` (replaces black). **Lint:** `ruff check`. **Types:** `mypy --strict` on `services/`, `api/`, `ml/`.
- **Imports:** absolute only. No relative imports across feature boundaries.
- **Functions:** type-hint everything. No `Any` without a comment justifying it.
- **Async by default** in API and worker code. Sync code only inside ML scripts and Alembic migrations.
- **Settings:** all config via `pydantic-settings` reading env vars. No `os.environ.get` scattered in code.
- **Logging:** `structlog` with JSON output. Log `request_id`, `user_id`, `route`. Never log PII or full resume text.

## TypeScript (frontend)
- **Strict mode on.** `noImplicitAny`, `strictNullChecks`, `noUncheckedIndexedAccess`.
- **Format:** Prettier. **Lint:** ESLint with `@typescript-eslint`.
- **Components:** function components only. Props typed with `interface`.
- **Server vs client:** prefer Server Components; mark `'use client'` only when needed (state, effects, browser APIs).
- **API client:** generated from OpenAPI schema (`openapi-typescript` + small fetch wrapper). No hand-written types for API responses.
- **Forms:** React Hook Form + Zod resolver.

## Database
- **Migrations:** Alembic, one migration per logical change. Migrations are reviewed like code.
- **No raw SQL in services.** Use SQLAlchemy ORM or Core. Raw SQL only in migrations or analytics scripts.
- **Soft delete via `deleted_at` timestamp** on user-facing entities (resumes, accounts). Never hard-delete user data without explicit request.
- **Indexes:** add an index any time you add a `WHERE` or `ORDER BY` on a non-PK column. Document why in the migration.

## Git & commits
- **Branch naming:** `phase-N/short-description` for feature work, `fix/short-description` for bugs.
- **Commit style:** Conventional Commits (`feat:`, `fix:`, `chore:`, `docs:`, `refactor:`, `test:`).
- **PRs:** every change goes through a PR, even solo. PR body links to the spec task it implements.
- **Never push directly to `main`.** `main` is always deployable.

## Secrets & config
- `.env` is for local dev only and is gitignored. `.env.example` is committed and lists every required var with placeholder values.
- **Pre-commit hook (`gitleaks`)** scans staged content for secrets. Required, not optional.
- **GitHub Secret Scanning** enabled on the repo.
- Production secrets live in AWS Secrets Manager (Phase 6). Never in env vars stored in plaintext infra files.
- API keys (OpenAI, etc.) are accessed through a single config object — never read from env directly in business logic.
- Secrets must never appear in error messages, stack traces, log lines, or LLM telemetry.

## Security defaults (cross-cutting)
- See `security.md` for the full security baseline. Highlights enforced as conventions:
- **CORS:** explicit allowlist per environment. Never `*` on authenticated endpoints.
- **CSRF:** any cookie-authenticated mutating endpoint requires CSRF protection (double-submit token or `SameSite=Strict`).
- **Security headers** on every HTML response: CSP, HSTS, `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`, `Permissions-Policy`, `X-Frame-Options: DENY`.
- **Rate limiting** on every authenticated endpoint; tighter limits on auth endpoints.
- **Idempotency:** mutating endpoints accept `Idempotency-Key` header where re-execution risk matters (uploads, payments, webhooks). Persist keys for 24h.
- **Containers run as non-root** with read-only root filesystem where possible. Minimal base image (distroless or chiseled). No shell in prod images.
- **Error responses** never leak stack traces or secrets in production. RFC 7807 envelope `detail` must be safe to display.
- **File uploads** validated by magic bytes server-side, never by `Content-Type` header. Filenames sanitized; stored under UUID, original filename kept only as a display field.

## AI/LLM-specific (Phase 3+)
- **Every prompt is a versioned file** in `apps/api/src/matchlayer_api/ml/prompts/` with a semantic version in the filename (`resume_coach.v1.txt`).
- **Structured outputs only** — use OpenAI's JSON mode or function calling. Never parse free-form text in production.
- **Every LLM call is logged** with prompt version, model, input hash, output, latency, cost. Stored for evaluation replay.
- **Fallback path required** — if the LLM fails or returns invalid JSON, the system returns a degraded but useful response, never a 500.

## Testing
- **Coverage is not a goal**, but every bug fix gets a regression test.
- **Backend:** unit tests for services, integration tests for routers (with a real Postgres in Docker).
- **Frontend:** component tests for anything with logic; skip pure-presentational.
- **AI evaluation suites** (Phase 5) run on every PR that touches a prompt file or model abstraction.

## Documentation
- **Steering docs (`.kiro/steering/`)** — short, always-loaded context. Update when product/tech/structure changes.
- **ADRs (`docs/adr/`)** — long-form rationale for non-obvious decisions. Numbered, immutable once accepted.
- **READMEs in each app/package** — how to run, test, and deploy that unit. Keep short.
