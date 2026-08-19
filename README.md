# MatchLayer

An AI-native ATS simulator and career intelligence platform. Upload a resume + job description, get a transparent match score built from semantic similarity (sentence embeddings) and keyword coverage, a skill-gap breakdown, rule-based improvement suggestions, and AI-powered coaching — resume feedback, bullet rewrites, and interview questions, streamed as they generate.

**Domain:** [matchlayer.net](https://matchlayer.net) (not yet live)
**Status:** Phase 3 complete — the LLM layer (resume coach, bullet rewriting, interview questions) shipped. Phase 4 (agentic AI) is next.

## Why this exists

Real ATS systems are opaque. Candidates optimize blindly. MatchLayer makes the matching process transparent and gives actionable feedback grounded in semantic understanding rather than keyword tricks.

It's also a portfolio project, deliberately built as a 7-phase progression from a small MVP to a full SaaS — each phase deployable and resume-worthy on its own.

## Roadmap

| Phase | Focus                                                                 | Status      |
| ----- | --------------------------------------------------------------------- | ----------- |
| 1     | MVP foundation — Next.js + FastAPI + Postgres + S3, naive ATS scoring | ✅ Complete |
| 2     | NLP & embeddings — sentence-transformers + pgvector, skill extraction | ✅ Complete |
| 3     | LLM layer — resume coach, bullet rewrites, interview questions        | ✅ Complete |
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
- **ML/AI:** scikit-learn → sentence-transformers → OpenRouter (Claude Haiku 4.5) → LangGraph → DeepEval
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

6. **Create the resume bucket in MinIO.**

   The resume-upload surface writes objects to the S3 bucket named by `MATCHLAYER_S3_BUCKET` (default `matchlayer-dev`). `phase-1-foundation` deferred provisioning it, so create it once per fresh MinIO data volume. There is no auto-bootstrap — this runbook step is how the bucket gets created for local dev.

   Use the [MinIO Client (`mc`)](https://min.io/docs/minio/linux/reference/minio-mc.html) installed on your host (the `minio/minio` server image doesn't bundle `mc`). Point it at the local MinIO using the dev-only root credentials from `docker-compose.yml`, then make the bucket:

   ```bash
   # Alias the local MinIO (S3 API on :9000) using the dev-only root credentials
   mc alias set matchlayer-local http://localhost:9000 matchlayer dev_only_password

   # Create the bucket named by MATCHLAYER_S3_BUCKET — idempotent, safe to re-run
   mc mb --ignore-existing matchlayer-local/matchlayer-dev
   ```

   `--ignore-existing` makes this a no-op if the bucket is already there, so it's safe to skip when re-running the setup. If you renamed `MATCHLAYER_S3_BUCKET` in `.env`, use that name instead of `matchlayer-dev`. The bucket stays private (no public-read) — matching the Phase 1 security posture. Prefer a GUI? The MinIO console at [http://localhost:9001](http://localhost:9001) (same credentials) can create the bucket too.

7. **Apply the Alembic baseline.**

   ```bash
   uv run --project apps/api alembic -c apps/api/alembic.ini upgrade head
   ```

   `alembic.ini` lives inside `apps/api/`, so the explicit `-c` flag is required when running from the repo root — without it Alembic can't find `script_location`. Phase 1 ships migration `0001_users_and_auth` which creates `users`, `refresh_tokens`, `password_reset_tokens`, and the append-only `audit_events` table (with role-scoped grants — see [Audit log notes](#audit-log) below), and `0002_resumes_and_matches`, which creates the `resumes` and `match_results` tables. Phase 2 adds `0003_pgvector_embeddings`, which enables the `vector` extension and creates the `resume_embeddings` and `match_embeddings` tables (the docker-compose Postgres runs the `pgvector/pgvector:pg16` image, so the extension is available out of the box). `upgrade head` applies all three.

8. **Download the embedding model (Phase 2 semantic scoring).**

   The production image bakes the model at build time, but a local `uvicorn` process loads it from `MATCHLAYER_EMBEDDING_MODEL_PATH`. Without it the API still runs — in Degraded_Mode, scoring every match with the Phase 1 TF-IDF engine and reporting `"semantic_scoring": "unavailable"` on `/healthz`. To enable the Phase 2 pipeline locally, download the pinned snapshot once (~91 MB) and point `.env` at it:

   ```bash
   uv run --project apps/api python -c "
   from huggingface_hub import snapshot_download
   snapshot_download(
       repo_id='sentence-transformers/all-MiniLM-L6-v2',
       revision='c9745ed1d9f207416be6d2e6f8de32d1f16199bf',
       local_dir='$HOME/.cache/matchlayer/models/all-MiniLM-L6-v2',
       allow_patterns=['config.json', 'config_sentence_transformers.json',
                       'sentence_bert_config.json', 'modules.json',
                       'model.safetensors', 'tokenizer.json', 'tokenizer_config.json',
                       'special_tokens_map.json', 'vocab.txt',
                       '1_Pooling/config.json', '2_Normalize/*'])
   "
   echo "MATCHLAYER_EMBEDDING_MODEL_PATH=$HOME/.cache/matchlayer/models/all-MiniLM-L6-v2" >> .env
   ```

   The `allow_patterns` list mirrors the production image's filtered bake (see `infra/docker/api.Dockerfile`) — only the files sentence-transformers actually loads. Verify after starting the API: `curl http://localhost:8000/healthz` should report `"semantic_scoring": "available"`.

9. **Install pre-commit hooks.**

   ```bash
   pre-commit install
   ```

10. **Start the apps** (in two terminals):

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

## Phase 1 matching — upload and match a resume

The `phase-1-matching` surface adds resume upload (PDF/DOCX → MinIO), bounded server-side text extraction, deterministic scoring against a pasted job description, and the results UI. The endpoints are authenticated with the access token from `phase-1-auth`, rate-limited, and quota-bounded. Since Phase 2, the score's similarity component comes from sentence embeddings rather than TF-IDF whenever the semantic pipeline is available (see the [Phase 2 runbook](#phase-2-semantic-scoring--runbook) below) — the endpoints, request/response shapes, and this walkthrough are unchanged.

### End-to-end upload-and-match walkthrough

Prerequisite: the stack is up (`docker compose up -d --wait`), the resume bucket exists (setup step 6), migrations are applied (step 7), and both apps are running (step 10).

**Via the web UI:**

1. Register or log in at [http://localhost:3000/register](http://localhost:3000/register) (or `/login`) — see the auth runbook above.
2. Go to the Upload_Page at [http://localhost:3000/upload](http://localhost:3000/upload), choose a `.pdf` or `.docx` resume, and paste a job description into the textarea.
3. Submit. The page uploads the resume, creates the match, and navigates you to the Results_Page at `/matches/{id}` (the score reveal with the matched/missing keyword breakdown).

**Via the API** (replace `$TOKEN` with the `access_token` returned by login/register):

```bash
# 1. Log in and capture the access token (jq optional — the body is {access_token, user})
TOKEN=$(curl -s http://localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"you@example.com","password":"your-password-12+chars"}' \
  | jq -r .access_token)

# 2. Upload a resume (multipart field name is `file`); capture the returned resume id
RESUME_ID=$(curl -s http://localhost:8000/api/v1/resumes \
  -H "Authorization: Bearer $TOKEN" \
  -F 'file=@/path/to/resume.pdf' \
  | jq -r .id)

# 3. Create a match against a pasted job description
MATCH_ID=$(curl -s http://localhost:8000/api/v1/matches \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d "{\"resume_id\":\"$RESUME_ID\",\"job_description\":\"Senior Python engineer with FastAPI and Postgres experience...\"}" \
  | jq -r .id)

# 4. View the result (score, breakdown, matched/missing keywords, suggestions)
curl -s http://localhost:8000/api/v1/matches/$MATCH_ID -H "Authorization: Bearer $TOKEN"
```

The same match is viewable in the browser at `http://localhost:3000/matches/$MATCH_ID`, and your resumes and recent matches are listed at [http://localhost:3000/library](http://localhost:3000/library).

Since Phase 3, the results page also carries the **AI tools** tabs — Coach, Bullet Rewrites, and Interview Prep — anchored to that match. They render whatever result is already stored and only call the provider when you explicitly generate or regenerate. Without an LLM key configured they still work, serving deterministic fallbacks built from the stored match data. See the [Phase 3 runbook](#phase-3-llm-layer--runbook) below.

### Adjust the per-user daily quotas

Uploads and matches are bounded per user per UTC calendar day as a cost-as-DoS control. The counts are kept in Postgres, so the quotas hold even during a Redis outage. Exceeding either cap returns HTTP 429 with the RFC 7807 `quota_exceeded` envelope (the `detail` states the daily limit and the UTC reset time); the upload or scoring is not performed.

Tune them by editing `.env` (defaults shown) and restarting the API:

| Variable                        | Default | Caps                                            |
| ------------------------------- | ------- | ----------------------------------------------- |
| `MATCHLAYER_RESUME_DAILY_QUOTA` | `20`    | Successful resume uploads per user per UTC day  |
| `MATCHLAYER_MATCH_DAILY_QUOTA`  | `50`    | Successful match creations per user per UTC day |

The tighter per-minute sliding-window rate limits (Redis-backed) are tunable in the same way:

| Variable                               | Default | Caps                                |
| -------------------------------------- | ------- | ----------------------------------- |
| `MATCHLAYER_RESUME_RATE_LIMIT_PER_MIN` | `10`    | Resume uploads per user per minute  |
| `MATCHLAYER_MATCH_RATE_LIMIT_PER_MIN`  | `20`    | Match creations per user per minute |

Raise them when developing locally so test loops don't trip the 429; keep production values conservative.

## Phase 2 semantic scoring — runbook

Phase 2 (`phase-2-nlp-embeddings`) replaces the naive TF-IDF similarity with
sentence-embedding similarity plus spaCy-based skill extraction, with the
Phase 1 engine retained verbatim as the fallback path.

### How a document becomes an embedding (chunking strategy)

The `Embedding_Service` embeds each text with the pinned SentenceTransformer
(`all-MiniLM-L6-v2`, 384 dimensions). Documents that fit within the model's
token window are encoded in one pass. Longer documents are split into
**non-overlapping, consecutive chunks** using the model's own tokenizer (so
chunk boundaries are exactly the model's token boundaries), each chunk is
encoded separately, and the chunk vectors are combined as a
**token-count-weighted mean** which is then **L2-normalized**. The full
document is always covered — no token is dropped — and the whole procedure is
deterministic, entirely in memory, and bounded by
`MATCHLAYER_EMBEDDING_TIMEOUT_SECONDS` (default 20s) end to end.

### How the score is computed (cosine → component transformation)

Cosine similarity between the two L2-normalized vectors lands in `[-1, 1]`.
The similarity component the score uses is:

```
similarity_component = clamp((cosine + 1) / 2, 0, 1)
```

The final score is the same weighted combine as Phase 1:
`round(100 * (w_similarity * similarity_component + w_keyword * coverage))`,
clamped to `[0, 100]`, with `coverage = |matched| / |analyzed|` computed over
the spaCy `Skill_Extractor`'s lexicon-gated skill sets. The
`score_breakdown.similarity_method` field records which engine produced the
similarity: `"semantic-embedding"` (Phase 2) or `"tfidf"` (Phase 1 /
fallback); its absence on older stored results implies TF-IDF.

### Degraded_Mode

The semantic pipeline (SentenceTransformer + spaCy + v2 lexicon) is loaded
**once at startup** by the FastAPI lifespan. If any artifact fails to load,
the instance starts anyway in **Degraded_Mode**: every match request is
served by the Phase 1 engine with the Phase 1 `scorer_version` stamp, no
embeddings are generated or stored, and exactly one `model_load_failure`
structured event is logged at startup (no per-request noise). `GET /healthz`
reports it as `"semantic_scoring": "unavailable"` while still returning 200 —
a degraded instance is serving, so orchestration must not restart-loop it.
The only way out of Degraded_Mode is a process restart with a working model;
rows created during the degraded window are retained unchanged.

One deliberate exception: if the model loads but its output dimension does
not equal `MATCHLAYER_EMBEDDING_DIMENSION` (the pgvector DDL literal, 384),
startup **fails fast** — every generated vector would be rejected by the
database, which is a deployment bug to surface, not degrade around.

### Per-request fallback events (the documented category set)

Any failure on the Phase 2 path falls back **per-request** to the Phase 1
engine — never a 5xx, never a flip into Degraded_Mode — and emits exactly one
structured event per occurrence, carrying `request_id` and internal ids only
(never text or vector content):

| Event                       | Trigger                                                     |
| --------------------------- | ----------------------------------------------------------- |
| `embedding_timeout`         | An embed call exceeded the configured wall-clock bound      |
| `embedding_runtime_error`   | Any other failure while producing the two vectors           |
| `embedding_geometry_error`  | Undefined cosine (zero magnitude / dimension mismatch)      |
| `skill_extraction_empty`    | Non-empty JD but an empty analyzed skill set                |
| `skill_extraction_error`    | Any other failure inside Phase 2 scoring                    |
| `embedding_persist_failure` | Best-effort vector persistence rejected (request succeeds)  |
| `model_load_failure`        | Startup artifact load failed (once, entering Degraded_Mode) |

### Deployment notes (model bake + memory)

The API image bakes the model at **build** time
(`huggingface_hub.snapshot_download` of the pinned name + revision into
`MATCHLAYER_EMBEDDING_MODEL_PATH`) and sets `HF_HUB_OFFLINE=1` at runtime —
production never contacts a model hub. The spaCy pipeline
(`en_core_web_sm`) is a pinned wheel dependency installed by
`uv sync --frozen`.

**Peak-RSS measurement procedure (before sizing the Fly machine):** run the
production image locally with `docker run --read-only --tmpfs /tmp -m 1g`,
wait for `/healthz` to report `semantic_scoring: available`, drive one warm-up
match request, then read peak RSS via
`docker stats --no-stream` (or `cat /sys/fs/cgroup/memory.peak` inside the
container's cgroup). Record the number and the chosen machine size in
`docs/costs.md`. Expected footprint for the 384-dim MiniLM model plus
`en_core_web_sm` is roughly 500–800MB at ready-to-serve, which is why the
target Fly machine is **shared-cpu-1x with 1GB RAM** (512MB is borderline; if
measured peak RSS exceeds the 1GB machine, the documented remediation is to
move scoring to a worker or downsize the model — decision recorded in
`docs/costs.md` when taken).

## Phase 3 LLM layer — runbook

Phase 3 (`phase-3-llm-layer`) adds three LLM features to the results page —
resume coach, bullet rewriting, and interview question generation — behind a
provider-neutral abstraction, with PII redaction, per-user quotas, and a
global spend circuit breaker. The provider is **OpenRouter** (OpenAI-compatible
API); the adapter lives in `apps/api/src/matchlayer_api/ml/llm/openrouter.py`
and is the only module that knows the provider exists.

### Obtaining and configuring the OpenRouter key

1. Create an account at [openrouter.ai](https://openrouter.ai/) and generate
   an API key at [openrouter.ai/keys](https://openrouter.ai/keys). Add a few
   dollars of prepaid credit (usage-priced, no subscription).
2. Set the key in your local `.env` (never in `.env.example`, never
   committed — `.env` is gitignored):

   ```bash
   MATCHLAYER_LLM_API_KEY=sk-or-v1-...your-key...
   ```

3. Restart the API. With a key present, the adapter validates it against the
   provider (`GET /key`) during startup:
   - **Valid key** → `/healthz` reports `"llm": "available"` and the LLM
     features go live.
   - **Invalid key / provider unreachable / timeout** → startup **aborts**
     with an error naming the failure category (`invalid_key` /
     `unreachable` / `timeout`) — never the key value.

**Running without a key is fully supported.** Leave
`MATCHLAYER_LLM_API_KEY` blank and the API starts normally with the LLM
features in the LLM-unavailable state: every request is served by a
deterministic fallback built from stored match data, and `/healthz` reports
`"llm": "unavailable"` (still HTTP 200 — a keyless instance is healthy by
design). The only way to enable LLM features is a restart with the key
configured. There is deliberately **no** endpoint or per-user configuration
path for user-supplied provider keys — the app-owned key is the only one.

### Default model and `MATCHLAYER_LLM_MODEL`

The default model is **`anthropic/claude-haiku-4.5`**. The
`MATCHLAYER_LLM_MODEL` setting is the single designated source for the model
identifier — no other source file hardcodes one — so swapping models is a
config change plus restart, no code change:

```bash
MATCHLAYER_LLM_MODEL=anthropic/claude-haiku-4.5
```

Every provider call sends `max_tokens` from
`MATCHLAYER_LLM_MAX_OUTPUT_TOKENS` (default 4096) and runs under a
wall-clock timeout of `MATCHLAYER_LLM_TIMEOUT_SECONDS` (default 60s),
streaming included. Exactly one attempt per request — no retries; any
failure serves the fallback, never a 5xx. Per-call cost is taken from the
provider's reported usage when present, otherwise computed from
`MATCHLAYER_LLM_PRICE_INPUT_USD_PER_MTOK` / `MATCHLAYER_LLM_PRICE_OUTPUT_USD_PER_MTOK`
(defaults $1.00 / $5.00 per million tokens, matching Claude Haiku 4.5's
OpenRouter pricing — keep these in sync when changing the model).

### Daily_Quota — per-user daily call cap

- **Default:** 25 LLM calls per user per **UTC calendar day**
  (`MATCHLAYER_LLM_DAILY_QUOTA`).
- **Mechanics:** fixed-window Redis counter keyed
  `llm:quota:{user_id}:{YYYYMMDD}`. Only _initiated provider calls_ count —
  cache hits, reused persisted results, 429 rejections, and fallbacks that
  never reach the provider consume nothing. The reserve step is an atomic
  Lua check-and-increment, so concurrent requests can't overshoot the limit.
  A reserved call stays counted even if the provider call then fails.
- **Exceeding the quota:** the endpoint returns **429** with the limit and
  the UTC reset time in the error detail. Every LLM feature response —
  429s included — carries the `X-LLM-Quota-Remaining` header.
- **Reset:** automatic at **UTC midnight** — a new day means a new Redis key
  with a fresh count. Keys expire after 48 hours, so they self-clean without
  a sweeper. No manual reset exists or is needed.
- **Redis failure:** quota accounting errors serve the request via the
  fallback path with **no provider call** — an accounting outage can never
  cause unbounded spend.

### Spend_Circuit_Breaker — global monthly spend cap

- **Default:** **$10/month** (`MATCHLAYER_LLM_MONTHLY_SPEND_LIMIT_USD`),
  app-wide across all users.
- **Mechanics:** the tracked spend is derived, not stored — every evaluation
  recomputes `SUM(cost_usd)` over the current UTC month's
  `llm_invocation_logs` rows. The breaker is **open** iff that sum reaches
  or exceeds the limit; while open, LLM endpoints return **503** (the error
  names the spend-limit cause, no figures) and `/healthz` reports
  `"llm": "unavailable"`. In-flight calls complete and are logged.
- **Fail-safe open:** if the spend sum cannot be read, or an invocation-log
  write fails (a completed call's cost is unaccounted), the breaker forces
  open with cause `tracking_failure` — unaccounted cost never allows
  unbounded spend.
- **Reset conditions** (all automatic at the next evaluation, no restart, no
  manual step): **UTC month rollover** (the new month's sum starts from
  zero), **raising the configured limit** above the tracked spend, or a
  successful below-limit read after a transient tracking failure. Each
  open↔closed flip emits exactly one structured
  `llm_spend_breaker_transition` event.

### PII redaction and the Redaction_Exception

Before any text reaches the provider, the `PII_Redactor` replaces emails,
phone numbers, and header-detected names with indexed typed placeholders
(`[EMAIL_n]`, `[PHONE_n]`, `[NAME_n]`). **Employment-history sections of a
resume are deliberately transmitted unredacted** — employer names, role
titles, and entry content are the raw material of useful coaching, while
direct contact identifiers carry no coaching signal. The full policy — the
exception's rationale, the committed section-boundary rule that makes the
exempt/redactable decision decidable, and the failure behavior — is
documented in [`docs/redaction-policy.md`](./docs/redaction-policy.md).

### Cost tracking

Every provider call (success or failure) writes one row to
`llm_invocation_logs` with token usage, cost, and cost basis. The monthly
cost story — pricing, quota math, and the projection under the $20/month
ceiling — lives in [`docs/costs.md`](./docs/costs.md).

## Branch & PR conventions

The Phase 1 foundation lands on the branch **`phase-1/foundation`**. All subsequent feature work follows the `phase-N/short-description` pattern, with PRs merged into `main` (never pushed directly).

Full conventions — commit style, PR expectations, naming, security defaults — live in [`.kiro/steering/conventions.md`](./.kiro/steering/conventions.md).

## GitHub-side configuration

Some setup can't be done from code: branch protection on `main`, secret scanning, push protection, Dependabot, and CodeQL default setup. The numbered, re-runnable checklist for those manual steps lives in [`docs/runbooks/repo-setup.md`](./docs/runbooks/repo-setup.md). Apply it once per fork or repository transfer.

## What's next

Phases 1 through 3 are complete: auth, resume upload and matching, the results UI, semantic scoring with embeddings + pgvector, and the LLM layer — resume coach, bullet rewriting, and interview question generation behind a provider abstraction, with versioned prompts, PII redaction, structured outputs, SSE streaming, per-user daily quotas, and a monthly spend circuit breaker (specs in [`.kiro/specs/`](./.kiro/specs/)). Next up:

- **Phase 4 — Agentic AI**: LangGraph multi-agent workflows (analysis, ATS, skill-gap, and improvement agents) coordinating the existing scoring and LLM services, with SQS-backed async execution and OpenTelemetry tracing.

## Documentation

- [`.kiro/steering/`](./.kiro/steering/) — always-loaded project context (product, tech, structure, conventions, security, per-phase docs).
- [`docs/adr/`](./docs/adr/) — Architecture Decision Records.
- [`docs/runbooks/`](./docs/runbooks/) — operational runbooks (repo setup, contributing flow, SEO release, Phase 4 agents).
- [`docs/runbooks/agents-local.md`](./docs/runbooks/agents-local.md) — the Phase 4 local async path: LocalStack up, Agent_Worker up, triggering and polling an analysis, inspecting Agent_Job/Agent_Run rows and OpenTelemetry traces, the worst-case job-duration formula, and the real-provider latency measurement procedure.
- [`docs/redaction-policy.md`](./docs/redaction-policy.md) — the PII redaction policy and the employment-history Redaction_Exception.
- [`docs/costs.md`](./docs/costs.md) — running cost log and the projection against the $20/month ceiling.
- [`docs/learning/`](./docs/learning/) — long-form learning library covering the concepts behind each phase.

## License

MIT. See [`LICENSE`](./LICENSE).

## Author

Built by [Mohammad El Prince](https://github.com/elprince-dev/) as a portfolio + learning project.
