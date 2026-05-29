# MatchLayer

An AI-native ATS simulator and career intelligence platform. Upload a resume + job description, get a transparent match score, semantic skill-gap analysis, and AI-driven improvement suggestions.

**Domain:** [matchlayer.net](https://matchlayer.net) (not yet live)
**Status:** Phase 0 — planning and scaffolding.

## Why this exists

Real ATS systems are opaque. Candidates optimize blindly. MatchLayer makes the matching process transparent and gives actionable feedback grounded in semantic understanding rather than keyword tricks.

It's also a portfolio project, deliberately built as a 7-phase progression from a small MVP to a full SaaS — each phase deployable and resume-worthy on its own.

## Roadmap

| Phase | Focus                                                                 | Status      |
| ----- | --------------------------------------------------------------------- | ----------- |
| 1     | MVP foundation — Next.js + FastAPI + Postgres + S3, naive ATS scoring | Not started |
| 2     | NLP & embeddings — sentence-transformers + pgvector                   | Not started |
| 3     | LLM layer — resume coach, interview question generator                | Not started |
| 4     | Agentic AI — LangGraph multi-agent workflows                          | Not started |
| 5     | AI testing & evaluation — DeepEval, prompt versioning                 | Not started |
| 6     | AWS production architecture — ECS, CDK, CI/CD                         | Not started |
| 7     | SaaS — Stripe, multi-tenancy, admin, MFA                              | Not started |

Detailed phase docs in [`.kiro/steering/`](./.kiro/steering/).

## Repo layout

```
matchlayer/
├── .kiro/                  # Kiro steering, specs, hooks
├── apps/
│   ├── web/                # Next.js frontend
│   └── api/                # FastAPI backend
├── packages/               # Shared TS libraries
├── ml/                     # Python ML pipelines and eval suites
├── infra/                  # Dockerfiles, CDK, CI configs
├── docs/                   # ADRs, runbooks
└── docker-compose.yml      # Local dev
```

## Tech stack (high level)

- **Frontend:** Next.js (App Router, TypeScript) · Tailwind · shadcn/ui · Zod
- **Backend:** FastAPI · Pydantic · SQLAlchemy · Alembic · PyJWT · uv
- **Data:** PostgreSQL 16 + pgvector · S3 · Redis
- **ML/AI:** scikit-learn → sentence-transformers → OpenAI → LangGraph → DeepEval
- **Infra:** Vercel + Fly.io (Phases 1–5) → AWS ECS + CDK (TypeScript) (Phase 6)
- **Dev:** Docker · pnpm · uv · pytest · vitest · playwright

Full stack rationale in [`.kiro/steering/tech.md`](./.kiro/steering/tech.md).

## Prerequisites

Install these before running the setup flow below. On Windows, do everything inside **WSL2** — `pre-commit` and `gitleaks` are not supported on native Windows in this repo.

- **pnpm** — JS/TS package manager. The exact version is pinned via `packageManager` in the root `package.json`; `corepack enable` will install it for you.
- **Node.js 24+** — runtime for the web app and the codegen orchestrator. See `.nvmrc`.
- **Python 3.13+** — runtime for the API. See `.python-version`.
- **uv** — Python package manager. Install via `curl -LsSf https://astral.sh/uv/install.sh | sh` (or `pipx install uv`).
- **Docker Engine 24+** — local Postgres, Redis, and MinIO. Docker Desktop on macOS/Windows or `docker.io` in WSL2 both work.
- **`pre-commit`** — runs lint/format/secret-scan hooks. Install with `pipx install pre-commit` (or `brew install pre-commit`).
- **`gitleaks` v8.x on PATH** — install a prebuilt binary from the [gitleaks releases page](https://github.com/gitleaks/gitleaks/releases). `.pre-commit-config.yaml` uses the `gitleaks-system` hook variant, which calls the system-installed binary rather than compiling from Go source — so you need the binary, not just `go install`.

## Running locally

After the prerequisites are installed:

1. **Clone the repo.**

   ```bash
   git clone https://github.com/elprince-dev/matchlayer.git
   cd matchlayer
   ```

2. **Create a local `.env`.**

   ```bash
   cp .env.example .env
   ```

   The defaults match the docker-compose services, so no edits are needed for local dev.

3. **Install JS/TS dependencies (at the repo root).**

   ```bash
   pnpm install
   ```

4. **Install Python dependencies (in `apps/api/`).**

   ```bash
   uv sync --project apps/api
   ```

5. **Start the local infrastructure.**

   ```bash
   docker compose up -d --wait
   ```

   Brings up Postgres, Redis, and MinIO and blocks until each service's healthcheck passes.

6. **Apply the Alembic baseline.**

   ```bash
   uv run --project apps/api alembic -c apps/api/alembic.ini upgrade head
   ```

   `alembic.ini` lives inside `apps/api/`, so the explicit `-c` flag is required when running from the repo root — without it Alembic can't find `script_location`. Phase 1 ships migration `0001_users_and_auth` which creates `users`, `refresh_tokens`, `password_reset_tokens`, and the append-only `audit_events` table (with role-scoped grants — see [Audit log notes](#audit-log) below).

7. **Install pre-commit hooks.**

   ```bash
   pre-commit install
   ```

8. **Start the apps** (in two terminals):

   ```bash
   # API — http://localhost:8000
   uv run --project apps/api uvicorn matchlayer_api.main:app --reload
   ```

   ```bash
   # Web — http://localhost:3000
   pnpm --filter @matchlayer/web dev
   ```

## Phase 1 auth — local development helpers

### Environment variables

`cp .env.example .env` covers every variable the auth surface needs, including the `MATCHLAYER_JWT_SECRET` placeholder (33 bytes, satisfies the 32-byte floor) and `MATCHLAYER_ENVIRONMENT=development` (required for the cookie `Secure`-flag carve-out on `http://localhost`). No further edits are needed for local dev.

### Retrieve a dev-mode reset link

Phase 1 has no email provider. The password-reset request flow logs the link via the dev-mode store. Retrieve the most recent link with:

```bash
curl http://localhost:8000/api/v1/dev/last-reset-link
```

Returns `{ "link": "http://localhost:3000/reset-password?token=...", "created_at": "..." }` or both fields `null` when no reset has been requested since the API process started.

This endpoint is **only** mounted when `MATCHLAYER_ENVIRONMENT=development`. In any other environment the path returns the standard 404 envelope.

### Inspect recent audit events

Every security-relevant action (register, login success/failure, refresh rotation, password reset, etc.) writes an append-only row to `audit_events`. Inspect them with:

```bash
psql "$MATCHLAYER_DATABASE_URL" -c "SELECT created_at, event_type, user_id, payload FROM audit_events ORDER BY created_at DESC LIMIT 20;"
```

The audit log is retained for at least 1 year. Archiving to S3 is deferred to Phase 6.

### Audit log

The `audit_events` table is append-only by construction. The migration grants `INSERT` and `SELECT` to `MATCHLAYER_DATABASE_APP_ROLE` (the app's runtime role) and explicitly revokes `UPDATE`, `DELETE`, and `TRUNCATE`. A successful auth path produces exactly one row per documented event type per request.

The docker-compose `POSTGRES_USER` is the role the migration grants `INSERT, SELECT` on `audit_events` to — keep them in sync if you change either value.

### Run the local timing test (INV-5)

The login-timing-equality invariant (Requirement 2.4) and the Argon2id p95 hash-latency budget (Requirement 15.2) are both verified by local-only timing tests that are excluded from CI by the `not timing` pytest marker (CI runners are too noisy for sub-30ms timing assertions):

```bash
cd apps/api && uv run pytest -m timing
```

Run it on a quiet developer laptop. Expects ≤ 25ms median delta between the unknown-email and known-but-wrong-password code paths and Argon2id p95 hash latency under the §15.2 / Requirement 15.2 budget. The `cd apps/api &&` prefix matches CI's `working-directory: apps/api` so `asyncio_mode = "auto"` from `apps/api/pyproject.toml` is honored (see task 16.8).

### Skip-if-no-infra tests

Integration tests under `apps/api/tests/integration/` and infra-dependent property tests under `apps/api/tests/property/` skip when Postgres or Redis isn't reachable. Bring up `docker compose up -d --wait` to run them locally; CI runs them automatically.

## Branch & PR conventions

The Phase 1 foundation lands on the branch **`phase-1/foundation`**. All subsequent feature work follows the `phase-N/short-description` pattern, with PRs merged into `main` (never pushed directly).

Full conventions — commit style, PR expectations, naming, security defaults — live in [`.kiro/steering/conventions.md`](./.kiro/steering/conventions.md).

## GitHub-side configuration

Some setup can't be done from code: branch protection on `main`, secret scanning, push protection, Dependabot, and CodeQL default setup. The numbered, re-runnable checklist for those manual steps lives in [`docs/runbooks/repo-setup.md`](./docs/runbooks/repo-setup.md). Apply it once per fork or repository transfer.

## What's next

This spec is the scaffold. Two sibling specs build the actual Phase 1 product on top:

- **`phase-1-auth`** — JWT auth (PyJWT, Argon2id passwords), register / login / refresh / logout / password-reset endpoints, rate limiting on auth routes, and the audit log baseline.
- **`phase-1-matching`** — resume upload (PDF/DOCX), server-side parsing, TF-IDF scoring against a job description, and the results UI that renders the score and skill breakdown.

## Documentation

- [`.kiro/steering/`](./.kiro/steering/) — always-loaded project context (product, tech, structure, conventions, security, per-phase docs).
- [`docs/adr/`](./docs/adr/) — Architecture Decision Records.
- [`docs/runbooks/`](./docs/runbooks/) — operational runbooks (Phase 6+).

## License

MIT. See [`LICENSE`](./LICENSE).

## Author

Built by [Mohammad El Prince](https://github.com/elprince-dev/) as a portfolio + learning project.
