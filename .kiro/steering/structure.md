# MatchLayer — Repo Structure

Single monorepo, single git repo. Nx-style layout but using native package managers (pnpm + uv) rather than full Nx orchestration unless we outgrow that.

## Top-level layout
```
matchlayer/
├── .kiro/                  # Kiro steering, specs, hooks
├── apps/
│   ├── web/                # Next.js frontend (Phase 1)
│   └── api/                # FastAPI backend (Phase 1)
├── packages/
│   ├── shared-types/       # TS types shared across web (and generated from OpenAPI)
│   └── ui/                 # Shared React components (introduced when needed)
├── ml/                     # Python ML pipelines, training scripts, eval suites
│   ├── pipelines/
│   ├── notebooks/          # Exploratory only — never imported by apps
│   └── evals/              # DeepEval suites (Phase 5)
├── infra/
│   ├── docker/             # Dockerfiles, docker-compose for local dev
│   ├── cdk/                # AWS CDK (TypeScript) — IaC (Phase 6)
│   └── github/             # Workflow definitions referenced by .github/workflows
├── docs/                   # Architecture docs, ADRs, runbooks
├── .github/workflows/      # CI/CD
├── docker-compose.yml      # Local dev: postgres, redis, minio (S3-compatible)
├── pnpm-workspace.yaml
├── package.json
└── README.md
```

## Why this shape
- **`apps/` vs `packages/`** — apps are deployable units, packages are libraries imported by apps. Standard Nx convention.
- **`ml/` is separate from `apps/api/`** — training and evaluation code has different dependencies, different lifecycles, and shouldn't bloat the API container. The API imports trained artifacts (or calls model services), not training code.
- **`infra/` is separate from app code** — infra changes are reviewed differently and deployed differently.
- **`docs/`** — ADRs (Architecture Decision Records) live here. Steering docs in `.kiro/steering/` are the always-loaded summary; ADRs are the long-form rationale.

## Backend structure (`apps/api/`)
```
apps/api/
├── src/matchlayer_api/
│   ├── main.py             # FastAPI app factory
│   ├── config.py           # Pydantic Settings
│   ├── api/                # Routers grouped by feature
│   │   ├── auth/
│   │   ├── resumes/
│   │   └── matches/
│   ├── core/               # Cross-cutting: logging, security, deps
│   ├── db/                 # SQLAlchemy models, session, migrations
│   ├── services/           # Business logic, called by routers
│   ├── ml/                 # Thin clients to ml/ artifacts or model services
│   └── workers/            # SQS consumers (Phase 6)
├── tests/
├── alembic/
├── pyproject.toml
└── Dockerfile
```

## Frontend structure (`apps/web/`)
Standard Next.js App Router layout:
```
apps/web/
├── src/
│   ├── app/                # Routes
│   ├── components/         # Page-specific components
│   ├── lib/                # API client, auth helpers, utils
│   └── styles/
├── public/
├── package.json
└── Dockerfile
```

## Naming
- **Folders & files:** `kebab-case` for everything except Python (snake_case) and React components (PascalCase files).
- **Python packages:** `matchlayer_*` prefix to avoid clashes.
- **Database tables:** plural snake_case (`users`, `resumes`, `match_results`).
- **API routes:** plural kebab-case (`/api/v1/resumes`, `/api/v1/match-results`).
- **Branches:** `phase-N/short-description` (e.g., `phase-1/resume-upload`).

## What goes where — quick rules
- New API endpoint → `apps/api/src/matchlayer_api/api/<feature>/`
- New page → `apps/web/src/app/<route>/`
- Shared TS type → `packages/shared-types/` (or generated from OpenAPI)
- Training script → `ml/pipelines/`
- Architectural decision → `docs/adr/NNNN-title.md`
- Infra change → `infra/`
