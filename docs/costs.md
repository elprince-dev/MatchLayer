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
| AWS SQS (Phase 4)         | $0                          | Agent Job_Queue; free tier is 1M requests/month, expected volume is orders of magnitude below                |
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

### Phase 4 line-item detail (agentic AI)

- **LLM calls per agent run: at most 2.** The Agent_Graph has exactly two
  LLM_Agents (Resume_Analysis and Improvement); the other three agents are
  deterministic by construction and can never call the provider. Both LLM
  calls flow through the Phase 3 orchestrator, so every existing cost
  control applies unchanged: the analyze endpoint pre-checks that the user
  has ≥ 2 `MATCHLAYER_LLM_DAILY_QUOTA` units before accepting a job, each
  call consumes one unit via the atomic reserve, and the $10/month
  Spend_Circuit_Breaker counts agent calls in the same monthly spend
  accounting as the Phase 3 features. Cache hits, degraded paths, and
  breaker-open runs make **zero** provider calls. Worst-case marginal cost
  per run ≈ 2 × $0.03 = **$0.06**, inside the existing $10/mo LLM cap —
  Phase 4 adds no new LLM spend headroom, it spends from the same capped
  budget.
- **SQS: free tier.** The agent Job_Queue is the only new AWS service. The
  SQS free tier is **1M requests/month**; one analyze run costs a handful
  of requests (1 send + long-poll receives + 1 delete), so expected volume
  (hundreds of runs/month) sits orders of magnitude below the cap.
  LocalStack emulates SQS in local dev at $0.
- **Redis and Postgres:** the Agent_Cache, rate limits, job rows, run rows,
  and checkpointer tables all land on the existing Redis and Fly Postgres
  footprint — no new instance, no size change, $0 marginal.
- **Projection vs. the $20 ceiling: unchanged.** Phase 4 adds $0 of new
  recurring line items; its LLM usage is bounded by the pre-existing $10
  breaker. The Phases 1–5 worst case stays **≤ ~$17.25/mo** with the same
  ~$2.75 headroom, typical months ~$7.25–$9.

## Approaching the ceiling

Action thresholds:

- **$15/mo:** review usage, identify the biggest line item, decide whether to optimize.
- **$20/mo:** stop adding services. Cut something or accept the cost in writing.

## History

- 2026-08-18 — Phase 4 (agentic AI): at most 2 LLM calls per agent run,
  bounded by the existing per-user daily quota and the $10/month spend
  circuit breaker (no new LLM budget); SQS job queue inside the AWS free
  tier (1M requests/month). No new recurring line items — Phases 1–5
  worst case unchanged at ≤ ~$17.25/mo.
- 2026-08-10 — Phase 3 (LLM layer): OpenRouter with `anthropic/claude-haiku-4.5`
  ($1/$5 per 1M input/output tokens). Per-user daily quota of 25 calls plus a
  $10/month spend circuit breaker as the hard worst case. Projected total
  ≤ ~$17.25/mo worst case, ~$7.25–$9/mo typical.
- 2026-07-27 — Phase 2 (NLP & embeddings): Fly API machine planned at
  shared-cpu-1x / 1GB (~$6/mo est.) for the baked embedding model; Fly
  Postgres retained with pgvector (Supabase/Neon fallback documented, not
  needed). Projected total ~$7.25/mo.
- 2026-05-23 — Initialized. Domain purchased.
