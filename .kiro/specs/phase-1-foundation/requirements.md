# Requirements Document

## Introduction

`phase-1-foundation` is the first of three sequential specs that together deliver Phase 1 of the MatchLayer roadmap. It establishes the monorepo scaffold, local development environment, baseline applications, shared-type codegen pipeline, CI pipeline, pre-commit hooks, container images, and the supporting documentation that the later specs (`phase-1-auth` and `phase-1-matching`) depend on.

This spec deliberately stops short of any domain logic. There are no users, no resumes, no scoring, no JWTs, no domain database tables, and no production deployment. The success condition is that a solo developer can clone the repository, run a single command flow, get all services healthy, and have a green CI build on an empty pull request — leaving every subsequent feature spec free to focus only on its own logic.

Scope boundaries:

- **In scope:** monorepo workspace, `docker-compose.yml` (Postgres 16, Redis 7, MinIO), `.env.example`, FastAPI baseline (app factory, Pydantic Settings, structlog, request-id middleware, `/healthz`, async SQLAlchemy session, Alembic configured with no domain migrations), Next.js baseline (App Router, Tailwind, shadcn/ui, security-headers middleware, placeholder landing page), OpenAPI → TypeScript + Zod codegen with a CI drift check, GitHub Actions CI (lint, type-check, tests, dependency audits, CodeQL, gitleaks, drift check), pre-commit hooks (gitleaks, ruff format, prettier, file hygiene), production Dockerfiles, branch-protection runbook, root README.
- **Out of scope:** any auth flow, any domain database table, any resume upload or parsing, any scoring logic, any deployment to Vercel or Fly.io, any LLM or embedding code.

## Glossary

- **Foundation_Repo** — The MatchLayer monorepo at the workspace root, including all top-level configuration files, `apps/`, `packages/`, `infra/`, `docs/`, `ml/`, and `.github/`.
- **Local_Dev_Stack** — The set of services defined in `docker-compose.yml`: Postgres 16, Redis 7, and MinIO.
- **API_App** — The FastAPI application located at `apps/api/`, exposing the Python package `matchlayer_api`.
- **Web_App** — The Next.js application located at `apps/web/`.
- **Shared_Types_Package** — The pnpm workspace package located at `packages/shared-types/` that holds OpenAPI-generated TypeScript types and Zod schemas.
- **Codegen_Pipeline** — The script and tooling that generates TypeScript types via `openapi-typescript` and Zod schemas via `openapi-zod-client` from the API_App's OpenAPI spec into the Shared_Types_Package.
- **OpenAPI_Drift_Check** — A CI job that re-runs the Codegen_Pipeline and fails when the regenerated artifacts differ from the artifacts committed in the pull request.
- **CI_Pipeline** — The GitHub Actions workflow(s) under `.github/workflows/` that run on pull requests and pushes to `main`.
- **Pre_Commit_Hooks** — The hooks defined in `.pre-commit-config.yaml` that run locally before each commit.
- **Container_Image_Builder** — The set of production Dockerfiles for the API_App and the Web_App, plus their build configuration.
- **Healthcheck_Endpoint** — The `GET /healthz` endpoint exposed by the API_App.
- **Request_Id_Middleware** — The FastAPI middleware that assigns or propagates a request-id and binds it to logs.
- **Security_Headers_Middleware** — The Next.js middleware that sets the security headers required by the security steering doc.
- **Repo_Setup_Runbook** — The markdown document at `docs/runbooks/repo-setup.md` describing manual GitHub-side configuration (branch protection, secret scanning, etc.).
- **Root_README** — The `README.md` file at the repository root.
- **PII** — Personally identifiable information as classified in `security.md` (resume contents, names, emails, phone numbers, etc.).

## Requirements

### Requirement 1: Monorepo Scaffold

**User Story:** As a solo developer, I want a fully scaffolded monorepo with workspaces and language tooling configured, so that I can start adding feature code without rebuilding the foundation each phase.

#### Acceptance Criteria

1. THE Foundation_Repo SHALL contain a `pnpm-workspace.yaml` file that declares `apps/*` and `packages/*` as workspace package globs.
2. THE Foundation_Repo SHALL contain a root `package.json` declaring the workspace and exposing top-level scripts named `lint`, `typecheck`, `test`, `build`, and `codegen`.
3. THE Foundation_Repo SHALL contain the directories `apps/web/`, `apps/api/`, `packages/shared-types/`, `infra/docker/`, and `docs/runbooks/`.
4. THE Foundation_Repo SHALL commit `pnpm-lock.yaml` at the repo root and `uv.lock` inside `apps/api/` so dependency versions are reproducible across machines.
5. WHEN a developer runs `pnpm install` at the repo root and `uv sync` inside `apps/api/` from a fresh clone on a machine with pnpm, Node 20 or later, Python 3.11 or later, uv, and Docker Engine 24 or later already installed, THE Foundation_Repo SHALL complete dependency installation within 5 minutes on a 50 Mbps connection AND the exit status of each install command SHALL reflect the actual installation outcome.
6. IF a directory under `apps/` or `packages/` contains a `package.json` file but is not matched by a glob in `pnpm-workspace.yaml`, THEN THE Foundation_Repo SHALL fail `pnpm install` with an error identifying the unrecognized package.
7. THE Foundation_Repo SHALL NOT treat directories under `apps/` or `packages/` that lack a `package.json` (for example IDE metadata folders or scratch directories) as workspace packages.

### Requirement 2: Local Development Stack

**User Story:** As a solo developer, I want a single command to start Postgres, Redis, and MinIO locally, so that I develop against the same data services Phase 1 will use in production.

#### Acceptance Criteria

1. THE Foundation_Repo SHALL contain a `docker-compose.yml` file at the repo root defining services for Postgres 16, Redis 7, and MinIO.
2. WHEN a developer runs `docker compose up -d` from the repo root on a machine with Docker Engine 24 or later, THE Local_Dev_Stack SHALL report all three services as healthy via `docker compose ps` within 60 seconds.
3. THE Local_Dev_Stack SHALL define a healthcheck for Postgres using `pg_isready`, a healthcheck for Redis using `redis-cli ping`, and a healthcheck for MinIO using its `/minio/health/live` endpoint.
4. THE Local_Dev_Stack SHALL persist Postgres data to a named Docker volume so data is preserved across `docker compose down` invocations that omit the `--volumes` flag.
5. THE Local_Dev_Stack SHALL bind Postgres to host port 5432, Redis to host port 6379, MinIO S3 API to host port 9000, and MinIO console to host port 9001 by default.
6. IF a Local_Dev_Stack service fails its healthcheck three consecutive times during startup, THEN THE Local_Dev_Stack SHALL report that service as unhealthy via `docker compose ps` and the `docker compose up` exit code SHALL be non-zero.
7. IF a host port required by the Local_Dev_Stack is already bound by another process, THEN THE Local_Dev_Stack SHALL exit with a non-zero status and surface the conflicting port in its stderr output.
8. WHEN a developer runs `docker compose down` without `--volumes`, THE Local_Dev_Stack SHALL stop and remove containers and the data volume contents SHALL remain intact.

### Requirement 3: Environment Variable Documentation

**User Story:** As a solo developer, I want a single `.env.example` file documenting every environment variable both apps need, so that I never have to read source code to find what to set.

#### Acceptance Criteria

1. THE Foundation_Repo SHALL contain a committed `.env.example` file at the repo root.
2. THE `.env.example` file SHALL contain an entry for every environment variable read by the API_App and the Web_App at startup or runtime.
3. THE `.env.example` file SHALL include placeholder values that match the Local_Dev_Stack defaults so a developer who copies `.env.example` to `.env` can run both apps against the Local_Dev_Stack without further edits.
4. THE Foundation_Repo SHALL list `.env` and `.env.local` in its `.gitignore` so secret values stay out of version control.
5. IF the API_App or the Web_App references an environment variable that has no corresponding entry in `.env.example`, THEN THE CI_Pipeline SHALL fail the build with a message identifying the missing variable name.
6. IF `.env.example` contains an entry for an environment variable that is not referenced by either the API_App or the Web_App, THEN THE CI_Pipeline SHALL fail the build with a message identifying the stale variable name.

### Requirement 4: Backend Application Baseline

**User Story:** As a solo developer, I want a FastAPI app with logging, configuration, health, and database wiring already in place, so that auth and matching features land on a clean baseline.

#### Acceptance Criteria

1. THE API_App SHALL expose a FastAPI application instance produced by an explicit application-factory function (for example `create_app()`).
2. THE API_App SHALL load all configuration through a single `pydantic-settings` `BaseSettings` subclass that reads environment variables.
3. IF a required environment variable defined on the Settings class is absent at startup, THEN THE API_App SHALL exit with a non-zero status and log a structured error naming the missing variable.
4. THE API_App SHALL emit logs as JSON via `structlog` and SHALL include the fields `request_id`, `route`, `method`, `status`, and `latency_ms` on every request-scoped log line.
5. WHEN an HTTP request enters the API_App, THE Request_Id_Middleware SHALL assign a UUIDv7 to the request, bind it to the log context, and set it as the `X-Request-Id` response header.
6. WHEN an inbound HTTP request supplies an `X-Request-Id` header that matches the format `^[A-Za-z0-9_-]{8,128}$`, THE Request_Id_Middleware SHALL reuse that value instead of generating a new one.
7. WHEN the API_App receives a `GET /healthz` request, THE Healthcheck_Endpoint SHALL execute a lightweight Postgres connectivity probe using the async session factory.
8. WHILE the Postgres connectivity probe succeeds, THE Healthcheck_Endpoint SHALL return HTTP 200 with a JSON body containing a `status` field equal to `"ok"`.
9. IF the Postgres connectivity probe fails, THEN THE Healthcheck_Endpoint SHALL return HTTP 503 with a JSON body containing a `status` field equal to `"unhealthy"` and a machine-readable reason code that does not include connection strings, credentials, or PII.
10. THE API_App SHALL configure an async SQLAlchemy 2.x engine and async session factory and SHALL provide a FastAPI dependency that yields a session per request and closes it after the response is sent.
11. THE API_App SHALL include Alembic configured to read the database URL from the Settings class, with the Alembic baseline revision present and zero domain migrations applied.
12. IF the API_App cannot establish a connection to Postgres during startup, THEN THE API_App SHALL log a structured error and exit with a non-zero status code rather than accept HTTP traffic.
13. IF Postgres becomes unavailable after the API_App has started serving traffic, THEN THE API_App SHALL continue accepting HTTP requests, the Healthcheck_Endpoint SHALL report `"unhealthy"` per acceptance criterion 9, and individual data-dependent endpoints SHALL surface the failure as an RFC 7807 error response rather than crash the process.
14. THE API_App SHALL exclude PII from every log line emitted by the application or its middleware.

### Requirement 5: Frontend Application Baseline

**User Story:** As a solo developer, I want a Next.js App Router scaffold with the design-system foundations already wired up, so that auth and matching pages can be built without doing setup work and the look is professional from the first commit.

#### Acceptance Criteria

1. THE Web_App SHALL be a Next.js application using the App Router with TypeScript strict mode enabled, including the `noImplicitAny`, `strictNullChecks`, and `noUncheckedIndexedAccess` compiler options.
2. THE Web_App SHALL have Tailwind CSS configured and applied to a root `layout.tsx` that imports the global stylesheet.
3. THE Web_App SHALL have shadcn/ui initialized with a `components.json` configuration file at `apps/web/components.json` and at least one shadcn/ui primitive component imported into the landing page.
4. THE Web_App SHALL load **Geist Sans** and **Geist Mono** via `next/font` and apply them as the default sans and mono font families through Tailwind's font configuration.
5. THE Web_App SHALL have **Framer Motion** installed and at least one purposeful entrance animation applied on the placeholder landing page (for example, a fade-up on the hero text). The animation SHALL respect `prefers-reduced-motion` and disable when the user has that preference set.
6. THE Web_App SHALL have **`next-themes`** integrated with `system` as the default theme and SHALL expose a working theme toggle accessible from the landing page.
7. THE Web_App SHALL define the brand color tokens, surfaces, and gradient described in `design.md` as Tailwind theme colors and CSS custom properties, with both light and dark theme variants.
8. THE Web_App SHALL render a placeholder landing page at the route `/` that contains the literal text "MatchLayer", a hero section using the design-system tokens (background, typography, brand gradient on at least one element), and a theme toggle.
9. WHEN a developer runs `pnpm --filter web dev`, THE Web_App SHALL start a dev server on port 3000 and serve the landing page within 10 seconds on a machine that has already completed `pnpm install`.
10. WHEN a developer runs `pnpm --filter web build`, THE Web_App SHALL produce a production build with zero TypeScript errors and zero ESLint errors.
11. THE placeholder landing page SHALL pass WCAG AA color-contrast requirements in both light and dark themes for all text content.

### Requirement 6: Frontend Security Headers

**User Story:** As a solo developer, I want security headers applied from day one, so that browsers enforce the protections required by the security steering doc before any real user data enters the system.

#### Acceptance Criteria

1. THE Security_Headers_Middleware SHALL set a `Content-Security-Policy` header on every HTML response served by the Web_App.
2. WHILE the Web_App is served over HTTPS, THE Security_Headers_Middleware SHALL set `Strict-Transport-Security: max-age=31536000; includeSubDomains; preload` on every response.
3. THE Security_Headers_Middleware SHALL set `X-Content-Type-Options: nosniff` on every response.
4. THE Security_Headers_Middleware SHALL set `X-Frame-Options: DENY` on every response.
5. THE Security_Headers_Middleware SHALL set `Referrer-Policy: strict-origin-when-cross-origin` on every response.
6. THE Security_Headers_Middleware SHALL set `Permissions-Policy: camera=(), microphone=(), geolocation=()` on every response.
7. THE Web_App SHALL include an automated test that asserts the presence and exact value of each security header listed in acceptance criteria 1 through 6 on the landing-page response.

### Requirement 7: Shared Types and OpenAPI Codegen Pipeline

**User Story:** As a solo developer, I want TypeScript types and Zod schemas auto-generated from the FastAPI OpenAPI spec, so that frontend and backend can never drift on request and response shapes.

#### Acceptance Criteria

1. THE Shared_Types_Package SHALL be a pnpm workspace package located at `packages/shared-types/` with its own `package.json` and `tsconfig.json`.
2. WHEN the Codegen_Pipeline runs, THE Codegen_Pipeline SHALL first obtain the API_App's OpenAPI spec by invoking the application factory and reading the live spec; the pipeline SHALL fail with a non-zero exit status if the spec cannot be obtained.
3. THE Codegen_Pipeline SHALL produce TypeScript types from the obtained OpenAPI spec using `openapi-typescript` and write them into a deterministic file path under `packages/shared-types/src/`.
4. THE Codegen_Pipeline SHALL produce Zod schemas from the obtained OpenAPI spec using `openapi-zod-client` and write them into a deterministic file path under `packages/shared-types/src/`.
5. THE Codegen_Pipeline SHALL be invocable as a single root-level script `pnpm codegen` that obtains the API_App's OpenAPI spec, runs both generators, and exits with status 0 on success.
6. WHEN the Codegen_Pipeline runs, THE Codegen_Pipeline SHALL write generated artifacts only into `packages/shared-types/`.
7. THE Codegen_Pipeline SHALL NOT fall back to a cached or stale OpenAPI spec; if a fresh spec cannot be obtained, the pipeline SHALL fail rather than emit artifacts derived from prior input.
8. THE OpenAPI_Drift_Check SHALL run in the CI_Pipeline on every pull request, regenerate the shared types and Zod schemas, and fail the build when the regenerated artifacts differ from the artifacts committed in the pull request.
9. THE Shared_Types_Package SHALL re-export the generated types and schemas from a single `index.ts` entrypoint so consuming code imports from `@matchlayer/shared-types` rather than from generated file paths.

### Requirement 8: Continuous Integration Pipeline

**User Story:** As a solo developer, I want every pull request to run lint, type-check, tests, and security scans automatically, so that broken or insecure code never reaches `main`.

#### Acceptance Criteria

1. THE CI_Pipeline SHALL be defined as one or more GitHub Actions workflow files under `.github/workflows/`.
2. THE CI_Pipeline SHALL trigger on every pull request targeting the `main` branch and on every push to the `main` branch.
3. WHEN the CI_Pipeline runs against a pull request whose only diff is a one-line markdown comment change, THE CI_Pipeline SHALL complete all required checks within 10 minutes.
4. THE CI_Pipeline SHALL execute `ruff format --check`, `ruff check`, `mypy`, and `pytest` against the API_App and SHALL fail the build if any of these commands exits with a non-zero status.
5. THE CI_Pipeline SHALL execute `eslint`, `prettier --check`, `tsc --noEmit`, and `vitest run` against the Web_App and the Shared_Types_Package and SHALL fail the build if any of these commands exits with a non-zero status.
6. THE CI_Pipeline SHALL execute `pip-audit` against the API_App's `uv.lock` and SHALL fail the build on findings classified as high or critical severity.
7. THE CI_Pipeline SHALL execute `pnpm audit --prod` and SHALL fail the build on findings classified as high or critical severity.
8. THE CI_Pipeline SHALL execute CodeQL analysis configured for both Python and TypeScript sources.
9. THE CI_Pipeline SHALL execute `gitleaks` against the pull request diff and SHALL fail the build when any potential secret is detected.
10. THE CI_Pipeline SHALL execute the OpenAPI_Drift_Check defined in Requirement 7.
11. THE CI_Pipeline SHALL install JavaScript dependencies with `pnpm install --frozen-lockfile` and Python dependencies with `uv sync --frozen` so lockfile drift is rejected at install time.
12. IF any required CI_Pipeline check fails, THEN THE CI_Pipeline SHALL report a failed status to the pull request that blocks merge until the failure is resolved.

### Requirement 9: Pre-commit Hooks

**User Story:** As a solo developer, I want secret scanning and basic formatting to run before every commit, so that obvious mistakes never reach the remote.

#### Acceptance Criteria

1. THE Foundation_Repo SHALL contain a `.pre-commit-config.yaml` file at the repo root that defines the Pre_Commit_Hooks.
2. THE Pre_Commit_Hooks SHALL include `gitleaks` configured to scan staged content.
3. THE Pre_Commit_Hooks SHALL include `ruff format` configured to run on staged Python files.
4. THE Pre_Commit_Hooks SHALL include `prettier` configured to run on staged JavaScript, TypeScript, JSON, Markdown, and YAML files.
5. THE Pre_Commit_Hooks SHALL include file-hygiene checks for trailing whitespace, end-of-file newline, and merge-conflict markers.
6. WHEN a developer runs `pre-commit install` from the repo root, THE Pre_Commit_Hooks SHALL be installed into the local git hooks so subsequent commits invoke them automatically.
7. IF any Pre_Commit_Hook reports a failure on staged content, THEN THE Pre_Commit_Hooks SHALL exit with a non-zero status, block the commit, and on a best-effort basis print the offending file paths; failure to print the file paths SHALL NOT cause the commit to be allowed.

### Requirement 10: Production Container Images

**User Story:** As a solo developer, I want production-grade Dockerfiles for both apps that follow the security baseline, so that deployment in a later spec is not blocked on container hardening.

#### Acceptance Criteria

1. THE Foundation_Repo SHALL contain a production Dockerfile for the API_App located at `infra/docker/api.Dockerfile`.
2. THE Foundation_Repo SHALL contain a production Dockerfile for the Web_App located at `infra/docker/web.Dockerfile`.
3. Each Container_Image_Builder Dockerfile SHALL pin its base images by digest and SHALL use a minimal final-stage base image consisting of a distroless image, an Ubuntu Chiseled image, or an Alpine variant.
4. Each Container_Image_Builder Dockerfile SHALL configure the runtime container to run as a non-root user with UID greater than or equal to 10000.
5. Each Container_Image_Builder Dockerfile SHALL be runnable with the `--read-only` flag, declaring any required writable scratch path as a `tmpfs` mount or a named volume in its documentation.
6. Each Container_Image_Builder Dockerfile SHALL define a `HEALTHCHECK` instruction that targets the `/healthz` endpoint for the API_App image and the Web_App's index route for the Web_App image.
7. THE Container_Image_Builder Dockerfile for the API_App SHALL produce a final image that contains only the Python interpreter, the application code, and the Python runtime dependencies declared in `apps/api/uv.lock`.
8. THE Container_Image_Builder Dockerfile for the Web_App SHALL produce a final image that contains only the Next.js production server output, its Node runtime, and its production-only dependencies.
9. WHEN `docker build` is run for either Container_Image_Builder Dockerfile from a fresh clone with the Local_Dev_Stack stopped, THE Container_Image_Builder SHALL complete the build with a zero exit status.

### Requirement 11: Repository Setup Runbook

**User Story:** As a solo developer, I want documented branch-protection and repo-setup steps in the repo, so that GitHub-side configuration is reproducible even though it cannot be enforced from code.

#### Acceptance Criteria

1. THE Foundation_Repo SHALL contain a markdown document at `docs/runbooks/repo-setup.md`.
2. THE Repo_Setup_Runbook SHALL document the required branch-protection rules for `main` including required CI_Pipeline status checks, required pull-request review approvals, linear-history enforcement, and the prohibition on direct pushes.
3. THE Repo_Setup_Runbook SHALL document the steps for enabling GitHub Secret Scanning and Dependabot security updates on the repository.
4. THE Repo_Setup_Runbook SHALL document the steps for enabling repository-level CodeQL when CodeQL default setup is unavailable.
5. THE Repo_Setup_Runbook SHALL present each manual step as a numbered checklist item that can be re-run in order after a fresh fork or repository transfer.

### Requirement 12: Root README and Onboarding

**User Story:** As a solo developer returning to the repo months later, I want the Root_README to give me a single command flow to get the project running, so that ramp-up time is minutes rather than hours.

#### Acceptance Criteria

1. THE Root_README SHALL document the prerequisite tooling: pnpm, Node 20 or later, Python 3.11 or later, uv, Docker Engine 24 or later, and `pre-commit`.
2. THE Root_README SHALL document a numbered setup flow covering cloning the repo, copying `.env.example` to `.env`, running `pnpm install` at the repo root, running `uv sync` inside `apps/api/`, running `docker compose up -d`, applying the Alembic baseline, running `pre-commit install`, and starting the API_App and Web_App in development mode.
3. THE Root_README SHALL link to `docs/runbooks/repo-setup.md` for GitHub-side configuration.
4. THE Root_README SHALL include a section that names `phase-1-auth` and `phase-1-matching` as the next specs in Phase 1 and identifies which capabilities each will deliver.
