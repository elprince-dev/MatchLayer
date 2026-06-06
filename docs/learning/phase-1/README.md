# MatchLayer Phase 1 — Learning Docs

## Introduction

This sub-library explains every concept used to build Phase 1 of MatchLayer, written for a developer meeting each topic for the first time. The implementation it documents lives in three sequential specs that together deliver Phase 1, and those specs are the implementation source of truth: `.kiro/specs/phase-1-foundation/` (the monorepo, the FastAPI and Next.js scaffold, Docker and Compose, CI, and the OpenAPI codegen pipeline), `.kiro/specs/phase-1-auth/` (the authentication and account surface), and `.kiro/specs/phase-1-matching/` (resume upload and the deterministic, non-LLM scoring surface). No single one of them covers Phase 1 on its own. Each Topic_Doc explains one idea in depth and anchors its examples to a real file inside one of those three implementations.

## Topic coverage

This table records every entry in the Phase 1 coverage list and the Topic_Doc that will explain it. The `Topic_Doc filename` column is intentionally blank until the corresponding Topic_Doc is authored; the compliance validator flags any populated row whose filename does not yet exist.

| Coverage entry                                                       | Requirement clause | Topic_Doc filename                                          | Thematic section            |
| -------------------------------------------------------------------- | ------------------ | ----------------------------------------------------------- | --------------------------- |
| Monorepo concept and apps-vs-packages split                          | 4.2                | 01-foundations-01-monorepo-layout.md                        | Foundation and tooling      |
| pnpm and pnpm workspaces                                             | 4.2                | 01-foundations-02-pnpm-and-workspaces.md                    | Foundation and tooling      |
| uv as a Python package manager                                       | 4.2                | 01-foundations-03-uv-python-package-manager.md              | Foundation and tooling      |
| Node.js + Python version pinning                                     | 4.2                | 01-foundations-04-language-version-pinning.md               | Foundation and tooling      |
| Root `package.json` and `tsconfig.base.json`                         | 4.2                | 01-foundations-05-root-package-and-tsconfig.md              | Foundation and tooling      |
| `.editorconfig`                                                      | 4.2                | 01-foundations-06-editorconfig.md                           | Foundation and tooling      |
| Lockfiles and frozen-lockfile installs                               | 4.2                | 01-foundations-07-lockfiles-and-frozen-installs.md          | Foundation and tooling      |
| `.env`, `.env.example`, env-drift script                             | 4.2                | 01-foundations-08-env-files-and-drift-detection.md          | Foundation and tooling      |
| Pre-commit hooks                                                     | 4.2                | 01-foundations-09-pre-commit-hooks.md                       | Foundation and tooling      |
| corepack pin activating the root `packageManager` pnpm version       | 4.2                | 01-foundations-10-corepack-and-packagemanager-pin.md        | Foundation and tooling      |
| Next.js `output: "standalone"` build mode                            | 4.2                | 01-foundations-11-nextjs-standalone-build.md                | Foundation and tooling      |
| Next.js App Router + Server vs Client Components                     | 4.3                | 02-frontend-01-nextjs-app-router-and-rsc.md                 | Frontend                    |
| TypeScript strict mode + repo compiler options                       | 4.3                | 02-frontend-02-typescript-strict-mode.md                    | Frontend                    |
| Tailwind v4 + `@theme inline` token strategy                         | 4.3                | 02-frontend-03-tailwind-v4-and-theme-tokens.md              | Frontend                    |
| shadcn/ui as a copy-in primitive library                             | 4.3                | 02-frontend-04-shadcn-ui-as-copy-in-primitives.md           | Frontend                    |
| Geist Sans + Geist Mono via `next/font`                              | 4.3                | 02-frontend-05-geist-fonts-via-next-font.md                 | Frontend                    |
| Framer Motion + reduced-motion pattern                               | 4.3                | 02-frontend-06-framer-motion-and-reduced-motion.md          | Frontend                    |
| `next-themes` + system-default theme                                 | 4.3                | 02-frontend-07-next-themes-and-system-default.md            | Frontend                    |
| Security-headers proxy (Next.js 16 `proxy.ts`)                       | 4.3                | 02-frontend-08-nextjs-proxy-security-headers.md             | Frontend                    |
| WCAG AA color contrast                                               | 4.3                | 02-frontend-09-wcag-aa-color-contrast.md                    | Frontend                    |
| FastAPI as async ASGI + application-factory pattern                  | 4.4                | 03-backend-01-fastapi-application-factory.md                | Backend                     |
| Pydantic v2 + `pydantic-settings`                                    | 4.4                | 03-backend-02-pydantic-and-pydantic-settings.md             | Backend                     |
| Async Python and the asyncio model                                   | 4.4                | 03-backend-03-async-python-and-asyncio.md                   | Backend                     |
| SQLAlchemy 2.x async + per-request session                           | 4.4                | 03-backend-04-sqlalchemy-async-and-session-dependency.md    | Backend                     |
| Connection pooling + `pool_pre_ping`                                 | 4.4                | 03-backend-05-connection-pooling-and-pre-ping.md            | Backend                     |
| Alembic migrations + empty baseline                                  | 4.4                | 03-backend-06-alembic-migrations-and-empty-baseline.md      | Backend                     |
| `structlog` and structured JSON logging                              | 4.4                | 03-backend-07-structlog-and-json-logging.md                 | Backend                     |
| Request-id ASGI middleware + `X-Request-Id`                          | 4.4                | 03-backend-08-request-id-middleware.md                      | Backend                     |
| RFC 7807 error envelope                                              | 4.4                | 03-backend-09-rfc-7807-error-envelope.md                    | Backend                     |
| OpenAPI dump CLI                                                     | 4.4                | 03-backend-10-openapi-dump-cli.md                           | Backend                     |
| Security headers (CSP, HSTS, etc.)                                   | 4.5                | 05-security-01-security-headers-explained.md                | Security                    |
| CORS allowlists                                                      | 4.5                | 05-security-02-cors-allowlists.md                           | Security                    |
| Structured logging as PII defense + redaction                        | 4.5                | 05-security-03-structured-logging-as-pii-defense.md         | Security                    |
| Secrets management, gitleaks, .env discipline                        | 4.5                | 05-security-04-secrets-management.md                        | Security                    |
| Dependency + supply-chain scanning                                   | 4.5                | 05-security-05-dependency-and-supply-chain-scanning.md      | Security                    |
| Threat-model categories                                              | 4.5                | 05-security-06-threat-model-categories.md                   | Security                    |
| Non-indexing of PII surfaces as a privacy control                    | 4.5                | 05-security-07-pii-non-indexing.md                          | Security                    |
| JWT and PyJWT; access vs refresh tokens; HS256 allowlist             | 4.6                | 06-auth-01-jwt-and-pyjwt.md                                 | Authentication and accounts |
| Argon2id password hashing + common-password blocklist                | 4.6                | 06-auth-02-password-hashing-argon2id.md                     | Authentication and accounts |
| Refresh-token rotation + family reuse detection                      | 4.6                | 06-auth-03-refresh-token-rotation.md                        | Authentication and accounts |
| Double-submit-cookie CSRF + HttpOnly/Secure/SameSite                 | 4.6                | 06-auth-04-csrf-and-secure-cookies.md                       | Authentication and accounts |
| Redis sliding-window rate limiting + account lockout                 | 4.6                | 06-auth-05-rate-limiting-and-account-lockout.md             | Authentication and accounts |
| Append-only audit log                                                | 4.6                | 06-auth-06-append-only-audit-log.md                         | Authentication and accounts |
| Password-reset tokens + dev-only reset surface                       | 4.6                | 06-auth-07-password-reset-tokens.md                         | Authentication and accounts |
| TanStack Query + `useAuth` server-state hook                         | 4.6                | 06-auth-08-tanstack-query-and-useauth.md                    | Authentication and accounts |
| Authenticated route-group shell `(app)` + redirect                   | 4.6                | 06-auth-09-authenticated-route-group-shell.md               | Authentication and accounts |
| No-account-enumeration via dummy-hash timing + generic error         | 4.6                | 06-auth-10-no-account-enumeration.md                        | Authentication and accounts |
| PostgreSQL 16 fundamentals                                           | 4.7                | 07-database-01-postgresql-fundamentals.md                   | Database and storage        |
| Postgres vs MinIO and why Phase 1 uses both                          | 4.7                | 07-database-02-postgres-vs-minio.md                         | Database and storage        |
| Redis fundamentals + Phase 1 standby                                 | 4.7                | 07-database-03-redis-fundamentals.md                        | Database and storage        |
| Named Docker volumes + persistence                                   | 4.7                | 07-database-04-named-docker-volumes.md                      | Database and storage        |
| Future addition of pgvector in Phase 2                               | 4.7                | 07-database-05-pgvector-and-the-phase-2-boundary.md         | Database and storage        |
| ATS match score in Phase 1 + deterministic non-LLM approach          | 4.8                | 08-matching-01-ats-scoring-overview.md                      | Matching and scoring        |
| TF-IDF + cosine similarity via scikit-learn                          | 4.8                | 08-matching-02-tf-idf-and-cosine-similarity.md              | Matching and scoring        |
| Keyword/skill overlap + committed Skill_Lexicon                      | 4.8                | 08-matching-03-skill-lexicon-and-keyword-overlap.md         | Matching and scoring        |
| Rule-based suggestion generation                                     | 4.8                | 08-matching-04-rule-based-suggestions.md                    | Matching and scoring        |
| File-upload safety (magic-byte MIME, size, UUID keys)                | 4.8                | 08-matching-05-file-upload-safety.md                        | Matching and scoring        |
| Bounded PDF/DOCX text extraction                                     | 4.8                | 08-matching-06-pdf-docx-text-extraction.md                  | Matching and scoring        |
| S3/MinIO storage abstraction                                         | 4.8                | 08-matching-07-s3-minio-storage-abstraction.md              | Matching and scoring        |
| Per-user daily upload/scoring quotas (cost-as-DoS)                   | 4.8                | 08-matching-08-usage-quotas-cost-as-dos.md                  | Matching and scoring        |
| `ml/` vs `apps/api` separation + Scorer_Version                      | 4.8                | 08-matching-09-ml-vs-api-separation-and-scorer-version.md   | Matching and scoring        |
| Zod runtime validation generated from OpenAPI                        | 4.8                | 08-matching-10-zod-runtime-validation.md                    | Matching and scoring        |
| Skill-lexicon build pipeline (`ml/pipelines/build_skill_lexicon.py`) | 4.8                | 08-matching-11-skill-lexicon-build-pipeline.md              | Matching and scoring        |
| Lexicon drift check (`tools/check_lexicon_drift.py`)                 | 4.8                | 08-matching-12-lexicon-drift-check.md                       | Matching and scoring        |
| Zip-bomb / decompression-bomb defense                                | 4.8                | 08-matching-13-zip-bomb-defense.md                          | Matching and scoring        |
| Containers vs virtual machines                                       | 4.9                | 10-containers-01-containers-vs-vms.md                       | Containerization            |
| Docker images, layers, build cache                                   | 4.9                | 10-containers-02-docker-images-layers-and-cache.md          | Containerization            |
| Dockerfiles + multi-stage builds                                     | 4.9                | 10-containers-03-dockerfiles-and-multi-stage-builds.md      | Containerization            |
| `docker compose` + healthchecks + `--wait`                           | 4.9                | 10-containers-04-docker-compose-and-healthchecks.md         | Containerization            |
| Production Dockerfiles in `infra/docker/`                            | 4.9                | 10-containers-05-production-dockerfiles.md                  | Containerization            |
| Distroless + non-root + read-only runtime                            | 4.9                | 10-containers-06-distroless-and-runtime-hardening.md        | Containerization            |
| Image digest pinning                                                 | 4.9                | 10-containers-07-image-digest-pinning.md                    | Containerization            |
| OpenAPI generation by FastAPI                                        | 4.10               | 11-contracts-01-openapi-from-fastapi.md                     | Contracts and codegen       |
| Codegen orchestrator + `execa`                                       | 4.10               | 11-contracts-02-codegen-orchestrator-and-execa.md           | Contracts and codegen       |
| `openapi-typescript`                                                 | 4.10               | 11-contracts-03-openapi-typescript-codegen.md               | Contracts and codegen       |
| `openapi-zod-client`                                                 | 4.10               | 11-contracts-04-openapi-zod-client-codegen.md               | Contracts and codegen       |
| Curated `index.ts` re-export pattern                                 | 4.10               | 11-contracts-05-shared-types-curated-reexports.md           | Contracts and codegen       |
| OpenAPI drift check in CI                                            | 4.10               | 11-contracts-06-openapi-drift-check.md                      | Contracts and codegen       |
| GitHub Actions workflow structure                                    | 4.11               | 12-hosting-01-github-actions-workflow-structure.md          | Hosting and deploy          |
| The five Phase 1 CI jobs                                             | 4.11               | 12-hosting-02-phase-1-ci-jobs.md                            | Hosting and deploy          |
| Dependabot configuration                                             | 4.11               | 12-hosting-03-dependabot-configuration.md                   | Hosting and deploy          |
| Branch protection + required-checks aggregator                       | 4.11               | 12-hosting-04-branch-protection-and-required-checks.md      | Hosting and deploy          |
| Vercel hobby tier as Phase 1 frontend host                           | 4.11               | 12-hosting-05-vercel-hobby-tier-hosting.md                  | Hosting and deploy          |
| Fly.io as Phase 1 backend host                                       | 4.11               | 12-hosting-06-flyio-backend-hosting.md                      | Hosting and deploy          |
| AWS S3 as Phase 1 file-storage backend                               | 4.11               | 12-hosting-07-aws-s3-in-phase-1.md                          | Hosting and deploy          |
| Phase 6 AWS migration-path preservation                              | 4.11               | 12-hosting-08-aws-migration-path-preservation.md            | Hosting and deploy          |
| UUIDv7 time-ordered opaque identifiers                               | 4.12               | 04-api-conventions-01-uuidv7-identifiers.md                 | API and data conventions    |
| Soft-delete via `deleted_at` timestamp                               | 4.12               | 04-api-conventions-02-soft-delete-and-deleted-at.md         | API and data conventions    |
| Cursor-based pagination (`?limit=&cursor=`)                          | 4.12               | 04-api-conventions-03-cursor-pagination.md                  | API and data conventions    |
| Idempotency keys via `Idempotency-Key` header                        | 4.12               | 04-api-conventions-04-idempotency-keys.md                   | API and data conventions    |
| `/api/v1` versioning, plural resources, ISO 8601 UTC timestamps      | 4.12               | 04-api-conventions-05-api-versioning-and-resource-naming.md | API and data conventions    |
| pytest, pytest-asyncio, httpx backend testing                        | 4.13               | 09-testing-01-pytest-and-httpx-backend-testing.md           | Testing and quality         |
| Integration testing against real Postgres in Docker                  | 4.13               | 09-testing-02-integration-testing-with-real-postgres.md     | Testing and quality         |
| Vitest + Testing Library frontend component tests                    | 4.13               | 09-testing-03-vitest-and-testing-library.md                 | Testing and quality         |
| Playwright end-to-end (E2E) tests                                    | 4.13               | 09-testing-04-playwright-e2e-testing.md                     | Testing and quality         |
| Hypothesis property-based testing for the Reader                     | 4.13               | 09-testing-05-property-based-testing-with-hypothesis.md     | Testing and quality         |
| Test taxonomy of layers across Phase 1                               | 4.13               | 09-testing-06-test-taxonomy-and-layers.md                   | Testing and quality         |
| axe-core accessibility tests                                         | 4.13               | 09-testing-07-axe-core-accessibility-testing.md             | Testing and quality         |
| Import-boundary tests enforcing apps/packages/ml separation          | 4.13               | 09-testing-08-import-boundary-tests.md                      | Testing and quality         |
| Timing-category tests for no-account-enumeration equalization        | 4.13               | 09-testing-09-timing-equalization-tests.md                  | Testing and quality         |

## Foundation and tooling

- [Monorepo layout and the apps-vs-packages split](01-foundations-01-monorepo-layout.md) — Explains why one repository holds every app and package, and how the apps-versus-packages split keeps deployable units and shared libraries cleanly separated.
- [pnpm and pnpm workspaces](01-foundations-02-pnpm-and-workspaces.md) — Introduces pnpm as the JavaScript package manager and shows how workspaces link the repository's multiple packages together under one install.
- [uv — a fast Python package and project manager](01-foundations-03-uv-python-package-manager.md) — Covers uv, the fast Python package and project manager that installs dependencies and manages virtual environments for the backend.
- [Language version pinning with .nvmrc and .python-version](01-foundations-04-language-version-pinning.md) — Shows how .nvmrc and .python-version pin exact Node.js and Python versions so every contributor and CI runner uses the same toolchain.
- [The root package.json and shared tsconfig.base.json](01-foundations-05-root-package-and-tsconfig.md) — Explains the root package.json scripts and the shared tsconfig.base.json that every TypeScript project extends for consistent compiler settings.
- [EditorConfig and consistent editor settings](01-foundations-06-editorconfig.md) — Describes how the .editorconfig file enforces consistent indentation, line endings, and whitespace across every editor and contributor.
- [Lockfiles and frozen-lockfile installs](01-foundations-07-lockfiles-and-frozen-installs.md) — Covers what lockfiles record and why frozen-lockfile installs guarantee reproducible dependency trees in continuous integration.
- [Environment files and env-drift detection](01-foundations-08-env-files-and-drift-detection.md) — Explains the .env and .env.example pattern and the script that detects when required environment variables drift out of sync.
- [Pre-commit hooks and the hook framework](01-foundations-09-pre-commit-hooks.md) — Introduces the pre-commit framework and each hook it runs, including secret scanning, formatting, linting, and file-hygiene checks.
- [Corepack and the packageManager version pin](01-foundations-10-corepack-and-packagemanager-pin.md) — Explains how Corepack reads the root packageManager field to activate the exact pnpm version the repository declares.
- [The Next.js standalone build output](01-foundations-11-nextjs-standalone-build.md) — Describes the Next.js standalone build mode and the self-contained server bundle it emits for small, fast container images.

## Frontend

- [The Next.js App Router and React Server Components](02-frontend-01-nextjs-app-router-and-rsc.md) — Explains the Next.js App Router and the difference between Server Components that render on the server and Client Components that run in the browser.
- [TypeScript strict mode and the repo compiler options](02-frontend-02-typescript-strict-mode.md) — Covers TypeScript strict mode and the specific compiler options this repository enables to catch type errors early.
- [Tailwind CSS v4 and the theme-token strategy](02-frontend-03-tailwind-v4-and-theme-tokens.md) — Introduces Tailwind CSS v4 and the theme-token strategy that maps design tokens to utility classes through the global stylesheet.
- [shadcn/ui as a copy-in primitive library](02-frontend-04-shadcn-ui-as-copy-in-primitives.md) — Explains why shadcn/ui components are copied into the repository as owned source rather than installed as an external dependency.
- [Geist Sans and Geist Mono via next/font](02-frontend-05-geist-fonts-via-next-font.md) — Shows how next/font loads the Geist Sans and Geist Mono typefaces with zero layout shift and no external font requests.
- [Framer Motion and the reduced-motion accessibility pattern](02-frontend-06-framer-motion-and-reduced-motion.md) — Covers Framer Motion animations and the reduced-motion pattern that respects a user's preference to minimize movement.
- [next-themes and the system-default theme](02-frontend-07-next-themes-and-system-default.md) — Explains how next-themes manages dark and light modes while defaulting to the operating system's preferred color scheme.
- [The Next.js proxy and security response headers](02-frontend-08-nextjs-proxy-security-headers.md) — Describes the Next.js proxy convention and how it attaches security response headers to every HTML response.
- [WCAG AA color contrast in practice](02-frontend-09-wcag-aa-color-contrast.md) — Explains WCAG AA color-contrast requirements and how the design-token palette satisfies them in both light and dark themes.

## Backend

- [FastAPI and the application-factory pattern](03-backend-01-fastapi-application-factory.md) — Introduces FastAPI as an async ASGI framework and the application-factory pattern that builds the app instance for testing and production.
- [Pydantic and typed settings](03-backend-02-pydantic-and-pydantic-settings.md) — Covers Pydantic v2 models and pydantic-settings, which validate request data and load typed configuration from environment variables.
- [Async Python and the asyncio model](03-backend-03-async-python-and-asyncio.md) — Explains async Python and the asyncio event-loop model so the Reader understands how concurrent requests are handled without threads.
- [SQLAlchemy async engine and the per-request session](03-backend-04-sqlalchemy-async-and-session-dependency.md) — Covers the SQLAlchemy 2.x async engine, the async session factory, and the dependency that yields one database session per request.
- [Connection pooling and pre-ping](03-backend-05-connection-pooling-and-pre-ping.md) — Explains database connection pooling and the pool_pre_ping check that detects and replaces stale connections before they are used.
- [Alembic migrations and the empty baseline](03-backend-06-alembic-migrations-and-empty-baseline.md) — Describes Alembic schema migrations and the empty-baseline strategy that establishes a clean starting point for the database history.
- [structlog and structured JSON logging](03-backend-07-structlog-and-json-logging.md) — Introduces structlog and how structured JSON logging makes application logs machine-parseable and safe to query.
- [The request-id middleware and the X-Request-Id header](03-backend-08-request-id-middleware.md) — Explains the ASGI middleware that assigns each request an identifier and echoes it through the X-Request-Id header for tracing.
- [The RFC 7807 error envelope](03-backend-09-rfc-7807-error-envelope.md) — Covers the RFC 7807 problem-details format and how it shapes consistent, machine-readable error responses across the API.
- [The OpenAPI dump command-line interface](03-backend-10-openapi-dump-cli.md) — Describes the command-line tool that dumps FastAPI's generated OpenAPI document to a file for downstream code generation.

## API and data conventions

- [UUIDv7 time-ordered identifiers](04-api-conventions-01-uuidv7-identifiers.md) — Explains UUIDv7 time-ordered identifiers, why they sort by creation time, and why database sequence integers are never exposed to clients.
- [Soft delete and the `deleted_at` timestamp](04-api-conventions-02-soft-delete-and-deleted-at.md) — Covers the soft-delete pattern that marks records with a deleted_at timestamp instead of physically removing user data from the database.
- [Cursor-based pagination](04-api-conventions-03-cursor-pagination.md) — Explains cursor-based pagination with limit and cursor parameters and why it avoids the duplicate-and-skip problems of offset pagination.
- [Idempotency keys and safe retries](04-api-conventions-04-idempotency-keys.md) — Describes the Idempotency-Key header on mutating endpoints and the persistence window that makes retried requests safe to repeat.
- [Versioned API paths, plural resource names, and UTC timestamps](04-api-conventions-05-api-versioning-and-resource-naming.md) — Covers the /api/v1 versioning scheme, plural resource path naming, and the ISO 8601 UTC timestamp convention used across the API.

## Security

- [Security headers and what each one defends against](05-security-01-security-headers-explained.md) — Explains each security response header, including CSP, HSTS, and X-Frame-Options, and the specific attack each one is meant to prevent.
- [CORS allowlists and why wildcards are unsafe](05-security-02-cors-allowlists.md) — Covers Cross-Origin Resource Sharing allowlists and why a wildcard origin is unsafe on authenticated endpoints that carry credentials.
- [Structured logging as a PII defense](05-security-03-structured-logging-as-pii-defense.md) — Explains how structured logging plus a redaction processor keeps personally identifiable information out of application logs.
- [Secrets management and keeping them out of git](05-security-04-secrets-management.md) — Describes secrets management, the gitleaks scanner, and the .env discipline that keeps credentials out of the version-controlled repository.
- [Dependency and supply-chain scanning](05-security-05-dependency-and-supply-chain-scanning.md) — Covers dependency and supply-chain scanning with pip-audit, pnpm audit, CodeQL, and Dependabot security updates.
- [Threat-model categories](05-security-06-threat-model-categories.md) — Walks through the threat-model categories the security steering doc defines so the Reader can reason about what the system defends against.
- [Non-indexing of PII surfaces as a privacy control](05-security-07-pii-non-indexing.md) — Explains how noindex metadata, response headers, robots rules, and sitemap exclusion keep personally identifiable surfaces out of search engines.

## Authentication and accounts

- [JSON Web Tokens and PyJWT](06-auth-01-jwt-and-pyjwt.md) — Introduces JSON Web Tokens and the PyJWT library, the access-versus-refresh-token split, and why Phase 1 signs with the HS256 algorithm.
- [Password hashing with Argon2id and a common-password blocklist](06-auth-02-password-hashing-argon2id.md) — Covers password hashing with Argon2id and the top-1000 common-password blocklist that rejects weak credentials at registration.
- [Refresh-token rotation and reuse detection](06-auth-03-refresh-token-rotation.md) — Explains stateful refresh-token rotation and family-based reuse detection that revokes an entire token family when a stolen token is replayed.
- [CSRF defense with the double-submit-cookie pattern and secure cookie attributes](06-auth-04-csrf-and-secure-cookies.md) — Describes the double-submit-cookie CSRF defense and the HttpOnly, Secure, and SameSite cookie attributes that protect session credentials.
- [Rate limiting and account lockout](06-auth-05-rate-limiting-and-account-lockout.md) — Covers Redis-backed sliding-window rate limiting and the account-lockout policy that throttles repeated failed login attempts.
- [The append-only audit log](06-auth-06-append-only-audit-log.md) — Explains the append-only audit_events log and why forbidding updates and deletes from application code preserves a trustworthy security record.
- [Password-reset tokens](06-auth-07-password-reset-tokens.md) — Covers password-reset tokens that are hashed at rest, single-use, and time-bounded, plus the development-only reset-link surface.
- [TanStack Query and the useAuth Server-State Hook](06-auth-08-tanstack-query-and-useauth.md) — Introduces TanStack Query and the useAuth hook that manages authenticated server state on the frontend.
- [The authenticated route-group shell and the unauthenticated redirect](06-auth-09-authenticated-route-group-shell.md) — Explains the authenticated (app) route-group shell and the pattern that redirects unauthenticated visitors away from protected pages.
- [No account enumeration](06-auth-10-no-account-enumeration.md) — Describes how verifying a dummy hash for unknown accounts equalizes response time and returns an identical generic error to prevent account enumeration.

## Database and storage

- [PostgreSQL 16 fundamentals](07-database-01-postgresql-fundamentals.md) — Introduces PostgreSQL 16 fundamentals for the Reader, covering the relational model, schemas, transactions, and indexes from first principles.
- [Postgres versus MinIO: two stores, two jobs](07-database-02-postgres-vs-minio.md) — Explains the difference between Postgres and MinIO and why Phase 1 runs both, one for structured data and one for files.
- [Redis fundamentals and the Phase 1 standby](07-database-03-redis-fundamentals.md) — Covers Redis fundamentals and why Phase 1 stands the service up even though it stays unused until Phase 4.
- [Named Docker volumes and data persistence](07-database-04-named-docker-volumes.md) — Explains named Docker volumes and how they persist database and storage data across docker compose down and up cycles.
- [pgvector and why Phase 1 stops short of it](07-database-05-pgvector-and-the-phase-2-boundary.md) — Describes the pgvector extension planned for Phase 2 and why Phase 1 deliberately stops short of vector search.

## Matching and scoring

- [Applicant Tracking System scoring in Phase 1](08-matching-01-ats-scoring-overview.md) — Explains what an Applicant Tracking System match score is in Phase 1 and why the scoring stays deterministic and free of large language models.
- [TF-IDF and cosine similarity](08-matching-02-tf-idf-and-cosine-similarity.md) — Covers Term Frequency–Inverse Document Frequency and cosine similarity, the scikit-learn techniques that measure overlap between a resume and a job description.
- [Skill lexicon and keyword overlap](08-matching-03-skill-lexicon-and-keyword-overlap.md) — Explains keyword and skill-overlap analysis against the committed, versioned skill lexicon, reporting which terms matched and which are missing.
- [Rule-based suggestions from missing terms](08-matching-04-rule-based-suggestions.md) — Describes how Phase 1 generates improvement suggestions from missing terms using deterministic rules rather than any language model.
- [File-upload safety](08-matching-05-file-upload-safety.md) — Covers file-upload safety, including server-side magic-byte MIME validation, hard size limits, UUID object keys, and display-only original filenames.
- [Bounded PDF and DOCX text extraction](08-matching-06-pdf-docx-text-extraction.md) — Explains bounded server-side PDF and DOCX text extraction as a one-way transformation with strict resource limits, not a reversible parser.
- [The S3 and MinIO storage abstraction](08-matching-07-s3-minio-storage-abstraction.md) — Describes the storage abstraction that presents the same interface over MinIO locally and AWS S3 in production.
- [Usage quotas as a cost-as-DoS defense](08-matching-08-usage-quotas-cost-as-dos.md) — Explains per-user daily upload and scoring quotas that cap spend and defend against cost-as-denial-of-service abuse.
- [Separating model code from API code and the Scorer_Version identifier](08-matching-09-ml-vs-api-separation-and-scorer-version.md) — Covers the separation between the ml/ scorer code and apps/api, plus the Scorer_Version identifier that makes scores reproducible.
- [Zod runtime validation at the API boundary](08-matching-10-zod-runtime-validation.md) — Explains Zod runtime validation, generated from OpenAPI, that checks API responses at the frontend boundary for the new endpoints.
- [The skill-lexicon build pipeline](08-matching-11-skill-lexicon-build-pipeline.md) — Describes the build pipeline that regenerates the committed skill-lexicon artifact from curated source data.
- [The skill-lexicon drift check in continuous integration](08-matching-12-lexicon-drift-check.md) — Explains the drift check that fails continuous integration when the committed lexicon and its API package copy diverge or go stale.
- [Zip-bomb defense: bounding decompressed size](08-matching-13-zip-bomb-defense.md) — Covers the decompression-bomb defense that bounds decompressed size so a small malicious upload cannot exhaust memory or disk.

## Testing and quality

- [Backend testing with pytest, pytest-asyncio, and httpx](09-testing-01-pytest-and-httpx-backend-testing.md) — Introduces pytest, pytest-asyncio, and httpx for writing unit and integration tests against the async FastAPI backend.
- [Integration testing against a real Postgres](09-testing-02-integration-testing-with-real-postgres.md) — Explains the integration-testing approach that runs tests against a real Postgres in Docker rather than against mocked database calls.
- [Vitest and Testing Library for component tests](09-testing-03-vitest-and-testing-library.md) — Covers Vitest and Testing Library for writing frontend component tests that assert behavior rather than implementation detail.
- [Playwright for end-to-end testing](09-testing-04-playwright-e2e-testing.md) — Describes Playwright and how it drives a real browser to verify complete end-to-end user flows across the application.
- [Property-based testing with Hypothesis](09-testing-05-property-based-testing-with-hypothesis.md) — Explains property-based testing with Hypothesis and what a property is: a general assertion checked against many generated inputs.
- [Test taxonomy and layers](09-testing-06-test-taxonomy-and-layers.md) — Walks through the test taxonomy used across Phase 1, including unit, integration, property, smoke, end-to-end, accessibility, and timing layers.
- [axe-core accessibility testing](09-testing-07-axe-core-accessibility-testing.md) — Covers axe-core accessibility tests that automatically catch common accessibility violations in rendered frontend components.
- [Import-boundary tests](09-testing-08-import-boundary-tests.md) — Explains import-boundary tests that enforce the apps-versus-packages and ml-versus-apps/api separation by failing on forbidden imports.
- [Timing-equalization tests](09-testing-09-timing-equalization-tests.md) — Describes the timing-category tests that verify login response times stay equalized so failures do not leak whether an account exists.

## Containerization

- [Containers versus virtual machines](10-containers-01-containers-vs-vms.md) — Explains what a container is and how it differs from a virtual machine in isolation, startup cost, and resource overhead.
- [Docker images, layers, and the build cache](10-containers-02-docker-images-layers-and-cache.md) — Covers Docker images, the layer model, and how the build cache speeds up rebuilds when only some instructions change.
- [Dockerfiles and multi-stage builds](10-containers-03-dockerfiles-and-multi-stage-builds.md) — Describes Dockerfiles and multi-stage builds that separate build dependencies from the lean final runtime image.
- [Docker Compose, healthchecks, and the --wait flag](10-containers-04-docker-compose-and-healthchecks.md) — Explains docker compose as a multi-service local-development tool, container healthchecks, and the --wait flag that blocks until services are healthy.
- [The production Dockerfiles, instruction by instruction](10-containers-05-production-dockerfiles.md) — Walks through the production Dockerfiles in infra/docker line by line, explaining what each instruction contributes to the final image.
- [Distroless images, non-root users, and read-only runtime](10-containers-06-distroless-and-runtime-hardening.md) — Covers distroless base images, non-root users with high UIDs, and the read-only runtime contract, and why each hardens the container.
- [Image digest pinning](10-containers-07-image-digest-pinning.md) — Explains image digest pinning and why pinning by content hash protects the build against mutable-tag tampering and supply-chain drift.

## Contracts and codegen

- [OpenAPI and how FastAPI generates it](11-contracts-01-openapi-from-fastapi.md) — Explains what OpenAPI is and how FastAPI automatically generates an OpenAPI document from the backend's typed routes and models.
- [The codegen orchestrator script and execa](11-contracts-02-codegen-orchestrator-and-execa.md) — Covers the codegen orchestrator script and the role of execa in running the chain of code-generation commands in sequence.
- [openapi-typescript and the generated TypeScript types](11-contracts-03-openapi-typescript-codegen.md) — Describes openapi-typescript and the TypeScript type definitions it produces from the OpenAPI document for the frontend.
- [openapi-zod-client and the generated Zod schemas](11-contracts-04-openapi-zod-client-codegen.md) — Explains openapi-zod-client and the Zod schemas it generates so the frontend can validate API responses at runtime.
- [The curated index.ts re-export pattern for shared types](11-contracts-05-shared-types-curated-reexports.md) — Covers the curated index.ts re-export pattern that gives generated types and schemas clean, stable import paths for app code.
- [The OpenAPI drift check in continuous integration](11-contracts-06-openapi-drift-check.md) — Explains the continuous-integration drift check that fails when the committed OpenAPI document diverges from the live generated one.

## Hosting and deploy

- [GitHub Actions workflow structure](12-hosting-01-github-actions-workflow-structure.md) — Introduces GitHub Actions workflow structure, covering jobs, steps, triggers, concurrency groups, and dependency caching.
- [The five Phase 1 CI jobs](12-hosting-02-phase-1-ci-jobs.md) — Walks through the five continuous-integration jobs in this repository and explains what each one verifies before a merge.
- [Dependabot configuration](12-hosting-03-dependabot-configuration.md) — Explains the Dependabot configuration that opens automated pull requests for security updates across the repository's dependencies.
- [Branch protection rules and the required-checks aggregator](12-hosting-04-branch-protection-and-required-checks.md) — Covers branch protection rules and the required-checks aggregator job pattern that gates merges to the main branch.
- [Vercel hobby tier as the Phase 1 frontend host](12-hosting-05-vercel-hobby-tier-hosting.md) — Describes Vercel's hobby tier as the Phase 1 frontend host and what its free-tier limits mean for the deployment.
- [Fly.io as the Phase 1 backend host](12-hosting-06-flyio-backend-hosting.md) — Explains Fly.io as the Phase 1 backend host and why its free-tier parity with Fargate eases the later AWS migration.
- [AWS S3 as the Phase 1 file-storage backend](12-hosting-07-aws-s3-in-phase-1.md) — Covers using real AWS S3 as the Phase 1 file-storage backend so the storage interface stays identical from day one.
- [How Phase 1 hosting preserves the AWS migration path](12-hosting-08-aws-migration-path-preservation.md) — Explains how the Phase 1 hosting choices deliberately preserve a smooth migration path to the full AWS architecture in Phase 6.

## Recommended reading order

1. [Monorepo layout and the apps-vs-packages split](01-foundations-01-monorepo-layout.md)
2. [pnpm and pnpm workspaces](01-foundations-02-pnpm-and-workspaces.md)
3. [uv — a fast Python package and project manager](01-foundations-03-uv-python-package-manager.md)
4. [Language version pinning with .nvmrc and .python-version](01-foundations-04-language-version-pinning.md)
5. [The root package.json and shared tsconfig.base.json](01-foundations-05-root-package-and-tsconfig.md)
6. [EditorConfig and consistent editor settings](01-foundations-06-editorconfig.md)
7. [Lockfiles and frozen-lockfile installs](01-foundations-07-lockfiles-and-frozen-installs.md)
8. [Environment files and env-drift detection](01-foundations-08-env-files-and-drift-detection.md)
9. [Pre-commit hooks and the hook framework](01-foundations-09-pre-commit-hooks.md)
10. [Corepack and the packageManager version pin](01-foundations-10-corepack-and-packagemanager-pin.md)
11. [The Next.js standalone build output](01-foundations-11-nextjs-standalone-build.md)
12. [The Next.js App Router and React Server Components](02-frontend-01-nextjs-app-router-and-rsc.md)
13. [TypeScript strict mode and the repo compiler options](02-frontend-02-typescript-strict-mode.md)
14. [Tailwind CSS v4 and the theme-token strategy](02-frontend-03-tailwind-v4-and-theme-tokens.md)
15. [shadcn/ui as a copy-in primitive library](02-frontend-04-shadcn-ui-as-copy-in-primitives.md)
16. [Geist Sans and Geist Mono via next/font](02-frontend-05-geist-fonts-via-next-font.md)
17. [Framer Motion and the reduced-motion accessibility pattern](02-frontend-06-framer-motion-and-reduced-motion.md)
18. [next-themes and the system-default theme](02-frontend-07-next-themes-and-system-default.md)
19. [The Next.js proxy and security response headers](02-frontend-08-nextjs-proxy-security-headers.md)
20. [WCAG AA color contrast in practice](02-frontend-09-wcag-aa-color-contrast.md)
21. [FastAPI and the application-factory pattern](03-backend-01-fastapi-application-factory.md)
22. [Pydantic and typed settings](03-backend-02-pydantic-and-pydantic-settings.md)
23. [Async Python and the asyncio model](03-backend-03-async-python-and-asyncio.md)
24. [SQLAlchemy async engine and the per-request session](03-backend-04-sqlalchemy-async-and-session-dependency.md)
25. [Connection pooling and pre-ping](03-backend-05-connection-pooling-and-pre-ping.md)
26. [Alembic migrations and the empty baseline](03-backend-06-alembic-migrations-and-empty-baseline.md)
27. [structlog and structured JSON logging](03-backend-07-structlog-and-json-logging.md)
28. [The request-id middleware and the X-Request-Id header](03-backend-08-request-id-middleware.md)
29. [The RFC 7807 error envelope](03-backend-09-rfc-7807-error-envelope.md)
30. [The OpenAPI dump command-line interface](03-backend-10-openapi-dump-cli.md)
31. [UUIDv7 time-ordered identifiers](04-api-conventions-01-uuidv7-identifiers.md)
32. [Soft delete and the `deleted_at` timestamp](04-api-conventions-02-soft-delete-and-deleted-at.md)
33. [Cursor-based pagination](04-api-conventions-03-cursor-pagination.md)
34. [Idempotency keys and safe retries](04-api-conventions-04-idempotency-keys.md)
35. [Versioned API paths, plural resource names, and UTC timestamps](04-api-conventions-05-api-versioning-and-resource-naming.md)
36. [Security headers and what each one defends against](05-security-01-security-headers-explained.md)
37. [CORS allowlists and why wildcards are unsafe](05-security-02-cors-allowlists.md)
38. [Structured logging as a PII defense](05-security-03-structured-logging-as-pii-defense.md)
39. [Secrets management and keeping them out of git](05-security-04-secrets-management.md)
40. [Dependency and supply-chain scanning](05-security-05-dependency-and-supply-chain-scanning.md)
41. [Threat-model categories](05-security-06-threat-model-categories.md)
42. [Non-indexing of PII surfaces as a privacy control](05-security-07-pii-non-indexing.md)
43. [JSON Web Tokens and PyJWT](06-auth-01-jwt-and-pyjwt.md)
44. [Password hashing with Argon2id and a common-password blocklist](06-auth-02-password-hashing-argon2id.md)
45. [Refresh-token rotation and reuse detection](06-auth-03-refresh-token-rotation.md)
46. [CSRF defense with the double-submit-cookie pattern and secure cookie attributes](06-auth-04-csrf-and-secure-cookies.md)
47. [Rate limiting and account lockout](06-auth-05-rate-limiting-and-account-lockout.md)
48. [The append-only audit log](06-auth-06-append-only-audit-log.md)
49. [Password-reset tokens](06-auth-07-password-reset-tokens.md)
50. [TanStack Query and the useAuth Server-State Hook](06-auth-08-tanstack-query-and-useauth.md)
51. [The authenticated route-group shell and the unauthenticated redirect](06-auth-09-authenticated-route-group-shell.md)
52. [No account enumeration](06-auth-10-no-account-enumeration.md)
53. [PostgreSQL 16 fundamentals](07-database-01-postgresql-fundamentals.md)
54. [Postgres versus MinIO: two stores, two jobs](07-database-02-postgres-vs-minio.md)
55. [Redis fundamentals and the Phase 1 standby](07-database-03-redis-fundamentals.md)
56. [Named Docker volumes and data persistence](07-database-04-named-docker-volumes.md)
57. [pgvector and why Phase 1 stops short of it](07-database-05-pgvector-and-the-phase-2-boundary.md)
58. [Applicant Tracking System scoring in Phase 1](08-matching-01-ats-scoring-overview.md)
59. [TF-IDF and cosine similarity](08-matching-02-tf-idf-and-cosine-similarity.md)
60. [Skill lexicon and keyword overlap](08-matching-03-skill-lexicon-and-keyword-overlap.md)
61. [Rule-based suggestions from missing terms](08-matching-04-rule-based-suggestions.md)
62. [File-upload safety](08-matching-05-file-upload-safety.md)
63. [Bounded PDF and DOCX text extraction](08-matching-06-pdf-docx-text-extraction.md)
64. [The S3 and MinIO storage abstraction](08-matching-07-s3-minio-storage-abstraction.md)
65. [Usage quotas as a cost-as-DoS defense](08-matching-08-usage-quotas-cost-as-dos.md)
66. [Separating model code from API code and the Scorer_Version identifier](08-matching-09-ml-vs-api-separation-and-scorer-version.md)
67. [Zod runtime validation at the API boundary](08-matching-10-zod-runtime-validation.md)
68. [The skill-lexicon build pipeline](08-matching-11-skill-lexicon-build-pipeline.md)
69. [The skill-lexicon drift check in continuous integration](08-matching-12-lexicon-drift-check.md)
70. [Zip-bomb defense: bounding decompressed size](08-matching-13-zip-bomb-defense.md)
71. [Backend testing with pytest, pytest-asyncio, and httpx](09-testing-01-pytest-and-httpx-backend-testing.md)
72. [Integration testing against a real Postgres](09-testing-02-integration-testing-with-real-postgres.md)
73. [Vitest and Testing Library for component tests](09-testing-03-vitest-and-testing-library.md)
74. [Playwright for end-to-end testing](09-testing-04-playwright-e2e-testing.md)
75. [Property-based testing with Hypothesis](09-testing-05-property-based-testing-with-hypothesis.md)
76. [Test taxonomy and layers](09-testing-06-test-taxonomy-and-layers.md)
77. [axe-core accessibility testing](09-testing-07-axe-core-accessibility-testing.md)
78. [Import-boundary tests](09-testing-08-import-boundary-tests.md)
79. [Timing-equalization tests](09-testing-09-timing-equalization-tests.md)
80. [Containers versus virtual machines](10-containers-01-containers-vs-vms.md)
81. [Docker images, layers, and the build cache](10-containers-02-docker-images-layers-and-cache.md)
82. [Dockerfiles and multi-stage builds](10-containers-03-dockerfiles-and-multi-stage-builds.md)
83. [Docker Compose, healthchecks, and the --wait flag](10-containers-04-docker-compose-and-healthchecks.md)
84. [The production Dockerfiles, instruction by instruction](10-containers-05-production-dockerfiles.md)
85. [Distroless images, non-root users, and read-only runtime](10-containers-06-distroless-and-runtime-hardening.md)
86. [Image digest pinning](10-containers-07-image-digest-pinning.md)
87. [OpenAPI and how FastAPI generates it](11-contracts-01-openapi-from-fastapi.md)
88. [The codegen orchestrator script and execa](11-contracts-02-codegen-orchestrator-and-execa.md)
89. [openapi-typescript and the generated TypeScript types](11-contracts-03-openapi-typescript-codegen.md)
90. [openapi-zod-client and the generated Zod schemas](11-contracts-04-openapi-zod-client-codegen.md)
91. [The curated index.ts re-export pattern for shared types](11-contracts-05-shared-types-curated-reexports.md)
92. [The OpenAPI drift check in continuous integration](11-contracts-06-openapi-drift-check.md)
93. [GitHub Actions workflow structure](12-hosting-01-github-actions-workflow-structure.md)
94. [The five Phase 1 CI jobs](12-hosting-02-phase-1-ci-jobs.md)
95. [Dependabot configuration](12-hosting-03-dependabot-configuration.md)
96. [Branch protection rules and the required-checks aggregator](12-hosting-04-branch-protection-and-required-checks.md)
97. [Vercel hobby tier as the Phase 1 frontend host](12-hosting-05-vercel-hobby-tier-hosting.md)
98. [Fly.io as the Phase 1 backend host](12-hosting-06-flyio-backend-hosting.md)
99. [AWS S3 as the Phase 1 file-storage backend](12-hosting-07-aws-s3-in-phase-1.md)
100. [How Phase 1 hosting preserves the AWS migration path](12-hosting-08-aws-migration-path-preservation.md)
