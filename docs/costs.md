# Cost log

Running list of monthly recurring costs. Update when anything changes. Goal: stay under **$20/month** for Phases 1–5; revisit ceiling for Phase 6.

## Current monthly spend

| Item                      | Cost                        | Notes                                                                                                        |
| ------------------------- | --------------------------- | ------------------------------------------------------------------------------------------------------------ |
| Domain (`matchlayer.net`) | ~$1.25/mo                   | $15/year amortized                                                                                           |
| Vercel (frontend)         | $0                          | Hobby tier                                                                                                   |
| Fly.io API machine        | ~$6/mo (est.)               | Phase 2: shared-cpu-1x upsized to **1GB RAM** for the baked embedding model                                  |
| Fly Postgres (pgvector)   | $0                          | Free 3GB volume; `pgvector/pgvector:pg16`-equivalent extension enabled                                       |
| AWS S3 (resume storage)   | $0                          | Free tier covers expected volume                                                                             |
| OpenRouter LLM (Phase 3)  | ≤ $10/mo hard cap           | Spend circuit breaker at `MATCHLAYER_LLM_MONTHLY_SPEND_LIMIT_USD=10`; usage-priced, likely far below the cap |
| **Total**                 | **≤ ~$17.25/mo worst case** | Under the $20 ceiling even with the LLM cap fully consumed (~$2.75 headroom); typical months ~$7.25–$9       |

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

### Phase 3 line-item detail (LLM layer)

- **Provider & model:** OpenRouter, default model `anthropic/claude-haiku-4.5`
  (`MATCHLAYER_LLM_MODEL`). Per-token pricing at the time of writing:
  **$1.00 per 1M input tokens, $5.00 per 1M output tokens** (source: the
  [OpenRouter model page](https://openrouter.ai/anthropic/claude-haiku-4.5),
  checked when this entry was written). The same figures are configured as
  the computed-cost fallback (`MATCHLAYER_LLM_PRICE_INPUT_USD_PER_MTOK=1.00`,
  `MATCHLAYER_LLM_PRICE_OUTPUT_USD_PER_MTOK=5.00`) used when the provider's
  usage payload carries no reported cost.
- **Per-call worst case:** output is hard-capped at
  `MATCHLAYER_LLM_MAX_OUTPUT_TOKENS=4096` → output ≤ 4,096 × $5/1M ≈
  **$0.0205**. Input (system prompt + redacted resume + JD + match context)
  is realistically ≤ ~10,000 tokens → input ≤ 10,000 × $1/1M = **$0.01**.
  Worst-case per call ≈ **$0.03**.
- **Quota math from the daily-quota default:**
  `MATCHLAYER_LLM_DAILY_QUOTA=25` calls per user per UTC day. One user at
  full quota every day: 25 × 30 = 750 calls/month × ~$0.03 ≈ **~$22.50/month
  uncapped** — which is exactly why the per-user quota alone is not the
  spend control and the circuit breaker exists. (Caching, persisted-result
  reuse, and fallbacks mean real usage sits far below quota; only initiated
  provider calls count.)
- **The spend limit is the worst-case monthly LLM spend:**
  `MATCHLAYER_LLM_MONTHLY_SPEND_LIMIT_USD=10`. The breaker recomputes the
  current UTC month's spend from `llm_invocation_logs` before every provider
  call and opens at ≥ $10 (503 for LLM features, fallbacks still served, and
  it fails safe open on tracking failures). Regardless of user count or
  quota settings, **monthly LLM spend cannot exceed ~$10** plus at most a
  handful of in-flight calls at the boundary (~$0.03 each).
- **Projection vs. the $20 ceiling:** pre-existing recurring costs ~$7.25/mo
  (domain + Fly machine) + LLM worst case $10 = **~$17.25/mo worst case**,
  under the $20/month ceiling from `product.md` with ~$2.75 headroom.
  Expected typical months: LLM spend in the cents-to-low-dollars range,
  total ~$7.25–$9/mo.

## Approaching the ceiling

Action thresholds:

- **$15/mo:** review usage, identify the biggest line item, decide whether to optimize.
- **$20/mo:** stop adding services. Cut something or accept the cost in writing.

## History

- 2026-08-10 — Phase 3 (LLM layer): OpenRouter with `anthropic/claude-haiku-4.5`
  ($1/$5 per 1M input/output tokens). Per-user daily quota of 25 calls plus a
  $10/month spend circuit breaker as the hard worst case. Projected total
  ≤ ~$17.25/mo worst case, ~$7.25–$9/mo typical.
- 2026-07-27 — Phase 2 (NLP & embeddings): Fly API machine planned at
  shared-cpu-1x / 1GB (~$6/mo est.) for the baked embedding model; Fly
  Postgres retained with pgvector (Supabase/Neon fallback documented, not
  needed). Projected total ~$7.25/mo.
- 2026-05-23 — Initialized. Domain purchased.
