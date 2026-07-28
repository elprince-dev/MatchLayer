# Cost log

Running list of monthly recurring costs. Update when anything changes. Goal: stay under **$20/month** for Phases 1–5; revisit ceiling for Phase 6.

## Current monthly spend

| Item                      | Cost          | Notes                                                                       |
| ------------------------- | ------------- | --------------------------------------------------------------------------- |
| Domain (`matchlayer.net`) | ~$1.25/mo     | $15/year amortized                                                          |
| Vercel (frontend)         | $0            | Hobby tier                                                                  |
| Fly.io API machine        | ~$6/mo (est.) | Phase 2: shared-cpu-1x upsized to **1GB RAM** for the baked embedding model |
| Fly Postgres (pgvector)   | $0            | Free 3GB volume; `pgvector/pgvector:pg16`-equivalent extension enabled      |
| AWS S3 (resume storage)   | $0            | Free tier covers expected volume                                            |
| OpenAI (Phase 3+)         | TBD           | Token quotas enforced server-side                                           |
| **Total**                 | **~$7.25/mo** | Under the $20 ceiling with ~$12 headroom                                    |

### Phase 2 line-item detail

- **Fly machine size:** `shared-cpu-1x` with **1GB RAM** (~$6/mo estimated at
  Fly's on-demand pricing; confirm the exact invoice line after first full
  month). The SentenceTransformer (`all-MiniLM-L6-v2`) + spaCy
  (`en_core_web_sm`) footprint measures roughly 500–800MB peak RSS at
  ready-to-serve, so the free-tier 256MB machine cannot host Phase 2. The
  peak-RSS measurement procedure lives in the README's "Phase 2 semantic
  scoring — runbook" section; record the measured number here when the
  deployment lands.
- **Postgres provider:** **Fly Postgres** (free 3GB) remains the provider —
  pgvector is available on Fly Postgres images, so the Supabase/Neon fallback
  (kept as the documented alternative) was **not** exercised. If Fly Postgres
  - pgvector proves painful in operation, the fallback decision is: migrate
    to **Neon** free tier (native pgvector, serverless, 0.5GB free) and record
    the change here plus an ADR.

## Approaching the ceiling

Action thresholds:

- **$15/mo:** review usage, identify the biggest line item, decide whether to optimize.
- **$20/mo:** stop adding services. Cut something or accept the cost in writing.

## History

- 2026-07-27 — Phase 2 (NLP & embeddings): Fly API machine planned at
  shared-cpu-1x / 1GB (~$6/mo est.) for the baked embedding model; Fly
  Postgres retained with pgvector (Supabase/Neon fallback documented, not
  needed). Projected total ~$7.25/mo.
- 2026-05-23 — Initialized. Domain purchased.
