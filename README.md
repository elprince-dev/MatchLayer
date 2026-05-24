# MatchLayer

An AI-native ATS simulator and career intelligence platform. Upload a resume + job description, get a transparent match score, semantic skill-gap analysis, and AI-driven improvement suggestions.

**Domain:** [matchlayer.net](https://matchlayer.net) (not yet live)
**Status:** Phase 0 — planning and scaffolding.

## Why this exists

Real ATS systems are opaque. Candidates optimize blindly. MatchLayer makes the matching process transparent and gives actionable feedback grounded in semantic understanding rather than keyword tricks.

It's also a portfolio project, deliberately built as a 7-phase progression from a small MVP to a full SaaS — each phase deployable and resume-worthy on its own.

## Roadmap

| Phase | Focus | Status |
|---|---|---|
| 1 | MVP foundation — Next.js + FastAPI + Postgres + S3, naive ATS scoring | Not started |
| 2 | NLP & embeddings — sentence-transformers + pgvector | Not started |
| 3 | LLM layer — resume coach, interview question generator | Not started |
| 4 | Agentic AI — LangGraph multi-agent workflows | Not started |
| 5 | AI testing & evaluation — DeepEval, prompt versioning | Not started |
| 6 | AWS production architecture — ECS, CDK, CI/CD | Not started |
| 7 | SaaS — Stripe, multi-tenancy, admin, MFA | Not started |

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

## Running locally

Not yet runnable. Phase 1 will deliver:

```bash
docker compose up -d        # Postgres + Redis + MinIO
pnpm install
uv sync
pnpm dev                    # frontend on :3000
uv run uvicorn matchlayer_api.main:app --reload  # backend on :8000
```

## Documentation

- [`.kiro/steering/`](./.kiro/steering/) — always-loaded project context (product, tech, structure, conventions, security, per-phase docs).
- [`docs/adr/`](./docs/adr/) — Architecture Decision Records.
- [`docs/runbooks/`](./docs/runbooks/) — operational runbooks (Phase 6+).

## License

MIT. See [`LICENSE`](./LICENSE).

## Author

Built by [hhumoham](https://github.com/hhumoham) as a portfolio + learning project.
