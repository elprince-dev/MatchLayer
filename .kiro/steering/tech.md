# MatchLayer — Tech Stack

## Frontend
- **Next.js** (App Router, TypeScript) — chosen for SSR, file-based routing, Vercel-friendly.
- **Tailwind CSS** for styling.
- **shadcn/ui** for component primitives.
- **TanStack Query** for server state.
- **Zod** for runtime validation (shared schema patterns with backend where possible).

## Backend
- **FastAPI** (Python 3.11+) — async, type-driven, OpenAPI out of the box.
- **Pydantic v2** for request/response models.
- **SQLAlchemy 2.x** (async) + **Alembic** for migrations.
- **uv** for Python dependency management (faster than pip/poetry).
- **JWT** auth via `python-jose`; passwords hashed with `argon2-cffi`.

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

## Infrastructure (Phase 6+)
- **AWS ECS Fargate** — backend containers.
- **CloudFront + S3** — frontend (Next.js static export) or Vercel for the frontend if simpler.
- **RDS Postgres** with pgvector.
- **SQS** for async AI jobs (resume parsing, agent runs, embeddings).
- **CloudWatch** for logs and metrics; structured JSON logging from day one.
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

## Decision log
- **Monorepo over polyrepo** — solo dev, AI-assisted, easier cross-cutting changes.
- **Nx-style structure** with native package managers (pnpm for JS, uv for Python) rather than full Nx tooling, to keep the toolchain lean. Revisit if multiple JS apps are added.
- **Open-source embeddings before OpenAI** — cost control and resume signal (NLP depth, not just API calls).
- **Postgres + pgvector over a dedicated vector DB** (Pinecone, Weaviate) — one fewer service, sufficient for expected scale.
- **CDK over Terraform** — solo dev already familiar with CDK, AWS-only deployment, CloudFormation-managed state avoids the Terraform state-file ops burden, higher-level constructs reduce infra LOC. **TypeScript** chosen so `infra/` shares language and tooling with `apps/web/` and `packages/`.
