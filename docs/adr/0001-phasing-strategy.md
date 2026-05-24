# 0001 — Phasing strategy

**Status:** Accepted
**Date:** 2026-05-23

## Context

MatchLayer's surface area (frontend, backend, ML, LLMs, agents, evaluation, AWS, SaaS) is large enough that "build everything at once" is the most likely failure mode. As a solo dev project that doubles as a portfolio piece, every phase needs to be deployable and resume-worthy on its own.

## Decision

Build in **7 progressive phases**:

1. MVP foundation — Next.js + FastAPI + Postgres + S3, naive ATS scoring.
2. NLP & embeddings — sentence-transformers + pgvector.
3. LLM layer — resume coach, interview questions.
4. Agentic AI — LangGraph multi-agent workflows.
5. AI testing & evaluation — DeepEval, prompt versioning.
6. AWS production architecture — ECS, CDK, CI/CD.
7. SaaS — Stripe, multi-tenancy, admin, MFA.

Each phase must:
- produce a deployed, working application
- stand alone on a resume
- prepare infrastructure for the next phase but **not** build that phase's features early

A phase is "shipped" when (a) it's deployed and reachable, (b) it could stand alone on a resume, (c) the next phase's infrastructure is unblocked. Detailed scope, deliverables, success criteria, risks, and work breakdowns live per-phase in `.kiro/steering/phase-N-*.md`.

## Consequences

**Positive**
- Avoids the over-engineering trap. No SQS, agents, or evaluation in Phase 1.
- Every phase produces a tangible artifact and demo.
- Reduces cognitive load: one phase at a time.

**Negative**
- Some refactoring overhead between phases (e.g., Phase 1's TF-IDF gets replaced in Phase 2).
- Risk of premature design choices that make later phases harder. Mitigated by the steering docs documenting future-phase needs upfront, so Phase 1 doesn't paint itself into a corner.

## Anti-patterns to refuse
- Building Phase N+1 features inside Phase N.
- Skipping Phase 1's "boring" plumbing because the AI parts seem more interesting.
- Over-engineering Phase 1 because Phase 6 will eventually need it (e.g., adding SQS in Phase 1 because Phase 4 needs it).
