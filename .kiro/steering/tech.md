# MatchLayer — Tech Stack

## Frontend
- **Next.js** (App Router, TypeScript) — chosen for SSR, file-based routing, Vercel-friendly.
- **Tailwind CSS** for styling.
- **shadcn/ui** for component primitives.
- **TanStack Query** for server state.
- **Zod** for runtime validation (Zod schemas auto-generated from the FastAPI OpenAPI spec via `openapi-zod-client`).

## Backend
- **FastAPI** (Python 3.11+) — async, type-driven, OpenAPI out of the box.
- **Pydantic v2** for request/response models.
- **SQLAlchemy 2.x** (async) + **Alembic** for migrations.
- **uv** for Python dependency management (faster than pip/poetry).
- **JWT** auth via **PyJWT** (active maintenance, safer defaults than `python-jose`); passwords hashed with `argon2-cffi`.

## Database & storage
- **PostgreSQL 16** with the **pgvector** extension (added in Phase 2).
- **AWS S3** for resume file storage (PDF/DOCX).
- **Redis** added in Phase 4+ for caching, rate limiting, and agent state.

## ML / AI
- **Phase 1:** scikit-learn (TF-IDF), simple keyword matchers — no LLMs.
- **Phase 2:** `sentence-transformers` (`all-MiniLM-L6-v2` or `bge-small-en-v1.5`), spaCy for skill extraction.
- **Phase 3:** OpenAI API (GPT-4o-mini for cost) behind an abstraction layer so we can swap providers.
- **Phase 4:** LangGraph for agent orchestration.
- **Phase 5:** DeepEval for LLM evaluation, MLflow or a lightweight equivalent for prompt versioning.

## Hosting per phase
- **Phases 1–5:**
  - **Frontend:** Vercel (hobby tier, free).
  - **Backend:** Fly.io (free shared-cpu-1x machine, free 3GB Postgres). Picked for the closest free-tier parity to ECS Fargate so the Phase 6 migration is mostly an environment swap.
  - **Postgres:** Fly Postgres until pgvector is needed; consider Supabase or Neon if Fly Postgres + pgvector becomes painful.
  - **S3:** real AWS S3 (free tier covers the expected volume) — keeps the file-storage abstraction identical from day one.
- **Phase 6+:** full AWS migration. ECS Fargate, RDS, CloudFront, SQS, CloudWatch.

## Infrastructure-as-code (Phase 6+)
- **AWS CDK (TypeScript)** for IaC — type-safe, AWS-first, no separate state file to manage.
- **GitHub Actions** for CI/CD.
- **Docker** + **docker-compose** for local development from Phase 1.

## Observability
- Structured logging (JSON) with request IDs from Phase 1.
- OpenTelemetry tracing introduced in Phase 4 when async workflows appear.
- Sentry for frontend + backend errors.

## Testing
- **Backend:** pytest, pytest-asyncio, httpx for API tests.
- **Frontend:** Vitest + Testing Library; Playwright for E2E from Phase 1 deploy.
- **AI:** DeepEval suites versioned in repo (Phase 5).

## Security tooling
- **Dependency scanning:** `pip-audit` (Python, works with `uv` lockfiles), `pnpm audit --prod` (JS/TS), Trivy on container images.
- **SAST:** CodeQL (or Semgrep) on every PR.
- **Secret scanning:** `gitleaks` as a pre-commit hook + GitHub Secret Scanning enabled on the repo.
- **Dependabot** enabled for security updates only.
- **Pinned major versions** for all dependencies; lockfiles committed and CI installs with frozen lockfile.

## Decision log
- **Monorepo over polyrepo** — solo dev, AI-assisted, easier cross-cutting changes.
- **Nx-style structure** with native package managers (pnpm for JS, uv for Python) rather than full Nx tooling, to keep the toolchain lean. Revisit if multiple JS apps are added.
- **Open-source embeddings before OpenAI** — cost control and resume signal (NLP depth, not just API calls).
- **Postgres + pgvector over a dedicated vector DB** (Pinecone, Weaviate) — one fewer service, sufficient for expected scale.
- **CDK over Terraform** — solo dev already familiar with CDK, AWS-only deployment, CloudFormation-managed state avoids the Terraform state-file ops burden, higher-level constructs reduce infra LOC. **TypeScript** chosen so `infra/` shares language and tooling with `apps/web/` and `packages/`.
