# Runbook — Phase 4 agents, local async path (`phase-4-agentic`)

How to run the full asynchronous multi-agent analysis locally: LocalStack
(SQS) up, the Agent_Worker consuming, an analysis triggered over the API,
the job polled to completion, and the persisted rows and traces inspected.
Architecture rationale lives in [ADR 0008](../adr/0008-agent-architecture.md);
the deterministic agent rules in [`docs/agent-rules.md`](../agent-rules.md);
cost posture in [`docs/costs.md`](../costs.md).

Prerequisites: the Phase 1 setup from the [README](../../README.md) is done
(`.env` exists, migrations applied, resume bucket created) and you have at
least one Match_Result to analyze — see the README's "Phase 1 matching —
upload and match a resume" walkthrough for creating one.

## 1. Start LocalStack (and the rest of the backing stack)

LocalStack is part of the default docker-compose stack (SQS only). Its init
hook creates the `matchlayer-agent-jobs` queue before the service reports
healthy, so `--wait` guarantees the queue exists:

```bash
docker compose up -d --wait
```

Sanity-check the queue:

```bash
docker compose exec localstack awslocal sqs get-queue-url --queue-name matchlayer-agent-jobs
```

With the queue reachable, `curl http://localhost:8000/healthz` reports
`"agents": "available"` (the API must be running — step 3).

## 2. Start the Agent_Worker

The worker is profile-gated so the plain backing-services workflow stays
unchanged. Build and start it explicitly:

```bash
docker compose --profile worker up -d --build worker
```

Follow its structured JSON logs:

```bash
docker compose logs -f worker
```

> **Keyless by default.** The compose worker service does not carry
> `MATCHLAYER_LLM_API_KEY`, so the two LLM agents (Resume_Analysis,
> Improvement) take their degraded paths and the job still completes —
> useful for exercising the pipeline without spend. To run with the real
> provider instead, run the worker on the host with your full `.env`
> (which has `localhost` endpoints and your key):
>
> ```bash
> uv run --project apps/api --env-file .env python -m matchlayer_api.workers.agent_worker
> ```

## 3. Trigger an analysis

With the API up (`uv run --project apps/api uvicorn matchlayer_api.main:app --reload`),
log in, then POST to the analyze endpoint for a match you own (reuse
`$MATCH_ID` from the README walkthrough):

```bash
TOKEN=$(curl -s http://localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"you@example.com","password":"your-password-12+chars"}' \
  | jq -r .access_token)

JOB_ID=$(curl -s -X POST "http://localhost:8000/api/v1/matches/$MATCH_ID/analyze" \
  -H "Authorization: Bearer $TOKEN" \
  | jq -r .id)
echo "$JOB_ID"
```

The response is a `202 Accepted` with `{id, status, job_url}`. `status` is
`queued` for a fresh job, or `running` if an in-flight job for the same
match was reused (in-flight idempotency). A 429 means fewer than 2 daily
LLM quota units remain or the analyze rate limit tripped; the body carries
the UTC reset time.

## 4. Poll the job

```bash
curl -s "http://localhost:8000/api/v1/jobs/$JOB_ID" -H "Authorization: Bearer $TOKEN" | jq .
```

Or watch it transition (`queued → running → completed|failed`):

```bash
watch -n 2 "curl -s http://localhost:8000/api/v1/jobs/$JOB_ID -H 'Authorization: Bearer $TOKEN' | jq '{status, steps}'"
```

The body carries the five per-agent step statuses (`pending`, `completed`,
`degraded`, `failed`), `result` (the AnalysisResult) once `completed`, and
a structured `error` if `failed`.

## 5. Inspect the persisted rows

Agent_Job rows (one per analysis, with the lifecycle timestamps):

```bash
docker compose exec postgres psql -U matchlayer -d matchlayer -c \
  "SELECT id, status, attempts, created_at, started_at, completed_at
   FROM agent_jobs ORDER BY created_at DESC LIMIT 5;"
```

Agent_Run rows (one immutable row per node invocation — name, latency,
status, failure reason):

```bash
docker compose exec postgres psql -U matchlayer -d matchlayer -c \
  "SELECT agent_name, status, latency_ms, failure_reason_json
   FROM agent_runs WHERE job_id = '$JOB_ID' ORDER BY created_at;"
```

Both surfaces carry redacted/derived content only — no raw resume text, by
construction.

## 6. Inspect the OpenTelemetry traces

Tracing is a no-op until `MATCHLAYER_OTEL_EXPORTER_OTLP_ENDPOINT` is set
(no collector runs in the default stack). To see traces locally, run a
Jaeger all-in-one with OTLP/HTTP ingest and point both processes at it:

```bash
# Jaeger UI on :16686, OTLP/HTTP ingest on :4318
docker run -d --name jaeger -p 16686:16686 -p 4318:4318 \
  jaegertracing/all-in-one:1.62.0
echo "MATCHLAYER_OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318" >> .env
```

Restart the API and run the worker on the host (step 2's `uv run` variant,
so it picks up the endpoint from `.env`), trigger an analysis, then open
[http://localhost:16686](http://localhost:16686) and search for the
`matchlayer-worker` service. A completed job shows one job span with one
child span per agent node (five for a full run), carrying identifiers and
hashes only — never resume content. Trace context is propagated from the
API's analyze request through the SQS message to the worker, so the whole
async path shares one trace id.

## Worst-case Agent_Job duration

Each agent node is bounded by the per-node timeout **and** (for LLM nodes)
the Phase 3 per-request LLM timeout — whichever elapses first:

```text
per_node_bound   = min(MATCHLAYER_AGENT_NODE_TIMEOUT_SECONDS, MATCHLAYER_LLM_TIMEOUT_SECONDS)
per_attempt_bound = longest_path_nodes × per_node_bound
worst_case        = MATCHLAYER_AGENT_MAX_ATTEMPTS × per_attempt_bound
```

- `longest_path_nodes = 3` — the graph's longest sequential path is
  Resume_Analysis → {Skill_Gap | Improvement} → Synthesizer (the ATS branch
  and the two middle branches run in parallel, so they never add to the
  sequential bound).
- At the defaults (`MATCHLAYER_AGENT_NODE_TIMEOUT_SECONDS=20`,
  `MATCHLAYER_LLM_TIMEOUT_SECONDS=60`, `MATCHLAYER_AGENT_MAX_ATTEMPTS=2`):

```text
worst_case = 2 × 3 × min(20, 60) = 2 × 3 × 20 = 120 seconds
```

A timed-out node degrades and the run continues, so this bound is only
approached when every longest-path node exhausts its full timeout on both
delivery attempts. The typical-path budget (no cache hits, provider calls
under 10 s) is under 30 seconds, enforced by the CI latency-budget test.

## Measuring real-provider latency

The CI latency test mocks the LLM client at 10 s per call and asserts the
`started_at → completed_at` elapsed time stays under 30 seconds. To repeat
that measurement against the real provider:

1. Ensure `.env` carries a real `MATCHLAYER_LLM_API_KEY` (and the daily
   quota has ≥ 2 units left for your user).
2. Run the worker on the host so it uses that key:

   ```bash
   uv run --project apps/api --env-file .env python -m matchlayer_api.workers.agent_worker
   ```

3. Trigger an analysis (step 3) and poll until `status` is `completed`
   (step 4).
4. Read the measured duration from the persisted timestamps:

   ```bash
   docker compose exec postgres psql -U matchlayer -d matchlayer -c \
     "SELECT id, started_at, completed_at,
             EXTRACT(EPOCH FROM (completed_at - started_at)) AS elapsed_seconds
      FROM agent_jobs WHERE id = '$JOB_ID';"
   ```

5. Compare `elapsed_seconds` against the 30-second typical-path budget.
   Repeat a few times (with distinct inputs, so the Agent_Cache doesn't
   short-circuit the LLM calls) to see the spread under real provider
   latency.
