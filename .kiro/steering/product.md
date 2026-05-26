# MatchLayer — Product Context

## What it is

MatchLayer is an AI-native ATS (Applicant Tracking System) simulator and career intelligence platform. Users upload a resume and a job description, and the system returns a match score, semantic skill-gap analysis, and AI-driven improvement suggestions. Long-term it grows into a full SaaS recruiting/career product.

**Domain:** `matchlayer.net`

## Target users

- **Primary (MVP):** job seekers who want to know how their resume scores against a specific job and how to improve it.
- **Secondary (later phases):** recruiters and small teams running candidate pipelines.

## Why it exists

Real ATS systems are opaque. Candidates optimize blindly. MatchLayer makes the matching process transparent and gives actionable, AI-generated feedback grounded in semantic understanding rather than keyword tricks.

## Product principles

- **Ship vertically, not horizontally.** Every phase produces a working, deployable product on its own.
- **Infrastructure before intelligence.** Get the end-to-end flow working with simple logic, then layer in ML/LLMs.
- **Open-source first.** Use OpenAI/paid APIs only when open-source can't deliver. Cost control is a real requirement.
- **Evaluate everything AI.** Once LLMs enter the system, every prompt and output is versioned and measured.

## 7-phase roadmap

1. **MVP Foundation** — Next.js + FastAPI + Postgres + S3, JWT auth, naive ATS scoring (TF-IDF/keyword).
2. **NLP & Embeddings** — Sentence Transformers + pgvector, semantic similarity, skill extraction.
3. **LLM Layer** — Resume coach, bullet rewriting, interview question generator (OpenAI initially).
4. **Agentic AI** — LangGraph multi-agent workflows (analysis, ATS, skill-gap, improvement agents).
5. **AI Testing & Evaluation** — DeepEval, prompt versioning, evaluation dashboard, hallucination tracking.
6. **AWS Production Architecture** — ECS Fargate, CloudFront, SQS, CloudWatch, GitHub Actions CI/CD.
7. **SaaS & Advanced Features** — Stripe subscriptions, resume versioning, admin dashboard, team mode.

## Current phase

**Phase 0 — planning and scaffolding.** No phase work has started.

## Out of scope (for now)

- Mobile apps
- LinkedIn/job-board scraping
- Recruiter-side workflows (deferred to Phase 7)
- Real-time collaboration

## Success signals per phase

Each phase ships when: (a) it is deployed somewhere reachable, (b) it could stand alone on a resume, (c) the next phase's infrastructure is unblocked.

## Cost ceiling

- **Phases 1–5:** total monthly spend must stay under **$20**. Use free tiers (Vercel hobby, Fly.io free, Supabase/Neon free Postgres, OpenAI usage-priced low). Track in a running cost log in `docs/costs.md`.
- **Phase 6:** budget revisited explicitly when AWS lands. AWS infra has different cost dynamics; $20 is unrealistic with managed services running 24/7.
- **Domain + miscellaneous (~$1.25/mo amortized for `matchlayer.net`)** counts toward the ceiling.
