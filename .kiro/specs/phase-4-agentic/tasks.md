# Implementation Plan: Phase 4 — Agentic AI

## Overview

Build the LangGraph multi-agent workflow bottom-up: configuration and database schema first, then the typed state and pure rule functions, then the agent class hierarchy and the five concrete agents, then graph wiring, persistence services, queue and tracing infrastructure, the SQS worker, the async API endpoints, and finally the frontend Progress UI. Property-based tests (Hypothesis backend, fast-check frontend) land next to the code they verify. All Phase 3 LLM plumbing (orchestrator, redaction, quota, breaker, logging, prompt registry) is reused, never duplicated. Implementation language: Python 3.13 (backend, `mypy --strict` on new packages) and TypeScript (frontend).

## Tasks

- [x] 1. Set up Phase 4 dependencies and configuration
  - [x] 1.1 Add Python dependencies and Phase 4 Settings
    - Add `langgraph`, `langgraph-checkpoint-postgres`, `aioboto3`, and OpenTelemetry packages (`opentelemetry-sdk`, `opentelemetry-exporter-otlp`) to `apps/api/pyproject.toml` with pinned major versions; run `uv sync` to update `uv.lock`
    - Extend `apps/api/src/matchlayer_api/config.py` `Settings` with the Phase 4 fields: `MATCHLAYER_SQS_QUEUE_URL`, `MATCHLAYER_SQS_REGION`, `MATCHLAYER_SQS_ENDPOINT_URL`, `MATCHLAYER_AGENT_NODE_TIMEOUT_SECONDS` (default 20), `MATCHLAYER_AGENT_MAX_ATTEMPTS` (default 2), `MATCHLAYER_AGENT_ANALYZE_RATE_LIMIT_PER_MINUTE` (default 10), `MATCHLAYER_AGENT_JOB_POLL_RATE_LIMIT_PER_MINUTE` (default 120), `MATCHLAYER_AGENT_CACHE_TTL_SECONDS` (default 86400), `MATCHLAYER_OTEL_EXPORTER_OTLP_ENDPOINT` (empty default), `MATCHLAYER_OTEL_SERVICE_NAME`
    - No direct environment reads anywhere in Phase 4 business logic
    - _Requirements: 8.3, 11.1, 13.5, 16.2_

  - [x] 1.2 Add LocalStack to docker-compose and document config in .env.example
    - Add a `localstack` service (SQS only) to `docker-compose.yml` with a queue-creation init hook so the queue exists on stack start
    - List every Phase 4 setting in `.env.example` with a LocalStack-wired placeholder and a comment stating the setting's unit and effect
    - _Requirements: 11.3, 16.7_

- [x] 2. Create database schema
  - [x] 2.1 Create agent_jobs and agent_runs migration and SQLAlchemy models
    - Alembic migration creating `agent_jobs` (UUIDv7 id, user_id FK, match_id FK, status CHECK in queued/running/completed/failed, attempts int default 0, created_at, nullable started_at/completed_at as UTC timestamptz, result_json nullable, error_json nullable) and `agent_runs` (UUIDv7 id, job_id FK, agent_name, input_state_json, output_state_json, latency_ms, status CHECK in completed/degraded/failed, failure_reason_json nullable, created_at)
    - Indexes with rationale documented in the migration: `agent_runs(job_id)`, `agent_jobs(user_id)`, `agent_jobs(match_id, user_id, status)`, and the partial unique index `agent_jobs(match_id, user_id) WHERE status IN ('queued','running')` for in-flight idempotency
    - Add matching SQLAlchemy 2.x models in `apps/api/src/matchlayer_api/db/`
    - _Requirements: 12.1, 10.5_

  - [x] 2.2 Create checkpointer schema migration
    - Alembic migration invoking `AsyncPostgresSaver.setup()` to create LangGraph's checkpoint tables; neither API nor worker creates or alters checkpointer schema at runtime
    - _Requirements: 2.5_

- [x] 3. Implement agent state schema and deterministic rules
  - [x] 3.1 Implement AgentState and output schemas in `ml/agents/state.py`
    - `AgentState` (identifiers, `redacted_resume_text`, `job_description_skills`, `MatchSnapshot`, per-agent output fields, `agent_status` dict), `AgentCompletion`, `AgentStatusFlag`, `FailureDetail` (closed trigger enum, operator-safe detail)
    - Output schemas: `CandidateProfile`, `ATSOutput`, `SkillGapEntry`/`SkillGapReport`, `ImprovementAction`/`RewriteSuggestion`/`ImprovementReport`, `AnalysisResult`, `AgentTraceSummary` — each output with `degraded: bool = False` marker and `derived_from_degraded_input` where specified
    - State carries identifiers plus redacted/derived content only — no field for raw `extracted_text`; package passes `mypy --strict`
    - _Requirements: 1.2, 1.3, 8.2_

  - [x] 3.2 Implement the Confidence_Level rule in `ml/agents/confidence.py`
    - Pure function over `(semantic: bool, resume_len: int, jd_len: int)` returning exactly one of `high`/`medium`/`low`: `high` iff semantic AND 200 ≤ resume_len ≤ 50_000 AND 100 ≤ jd_len ≤ 20_000; `medium` iff exactly one of {semantic, both-lengths-in-bounds}; else `low`
    - _Requirements: 4.2, 4.5_

  - [x] 3.3 Write property test for the confidence rule
    - **Property 6: Confidence rule is total, deterministic, and caps fallback scores**
    - **Validates: Requirements 4.2, 4.5**

  - [x] 3.4 Implement skill-gap classification and prioritization rules in `ml/agents/gap_rules.py`
    - Pure free functions: classification (JD skill absent from profile skills ∪ matched skills → `missing`; present in profile but not matched → `weak`; covered → no entry) and prioritization (missing before weak, descending JD occurrence count, case-insensitive alphabetical tie-break; ranks 1..n sequential and unique)
    - _Requirements: 5.1, 5.2, 5.3_

  - [x] 3.5 Write property test for Skill_Gap_Report well-formedness
    - **Property 7: Skill_Gap_Report well-formedness**
    - **Validates: Requirements 5.2, 5.4**

  - [x] 3.6 Write property test for full skill coverage
    - **Property 8: Full skill coverage yields an empty, non-degraded report**
    - **Validates: Requirements 5.7**

- [x] 4. Implement the agent class hierarchy
  - [x] 4.1 Implement `BaseAgent` in `ml/agents/base.py`
    - Generic `BaseAgent[TOut: BaseModel]` with `name`/`output_field` ClassVars, abstract `run` and `build_degraded`, tracing hooks `on_span_start`/`on_span_end`, and the final `__call__` template method: span emission, `asyncio.wait_for` per-node timeout, exception → `classify_failure` → `_build_degraded_safely` (degraded-constructor failure yields minimal schema-valid output with `degraded_construction_error` reason), latency measured node-invocation-start → output-return, `persist_agent_run` callback, partial-state return `{output_field: output, "agent_status": {...}}`
    - Dependency injection via `AgentDeps` (node_timeout_s, persist_agent_run, tracer, clock) — no global state, no direct config reads
    - _Requirements: 1.2, 8.1, 8.2, 8.3, 8.4, 8.6, 12.2, 13.1_

  - [x] 4.2 Implement `LLMAgent` in `ml/agents/llm_agent.py`
    - Final `run` delegating to the Phase 3 `LLMOrchestrator.invoke` with abstract `feature_spec()` and `build_prompt_input()` (may raise `EmptyInputError` → degraded, zero calls, zero quota); span hooks attach prompt version, model id, and input hash only when a provider call occurred
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.6, 9.7, 9.9, 13.2_

  - [x] 4.3 Implement `DeterministicAgent` in `ml/agents/deterministic_agent.py`
    - Thin abstract marker class: module imports nothing from `ml/llm/`, constructor accepts no orchestrator or client — Requirement 1.6 holds structurally
    - _Requirements: 1.6_

- [x] 5. Implement the five concrete agents
  - [x] 5.1 Implement `ResumeAnalysisAgent` and its prompt template
    - `ml/agents/resume_analysis_agent.py` extending `LLMAgent[CandidateProfile]`: `build_prompt_input` raises `EmptyInputError` on empty/None redacted text; `feature_spec` uses new versioned prompt `agent_resume_analysis.v1` (structured roles, delimited user-content region) registered in the Phase 3 prompt registry; `build_degraded` from Phase 2 Skill_Extractor results and persisted Match_Result fields
    - Add the `agent_resume_analysis.v1` prompt file under `apps/api/src/matchlayer_api/ml/prompts/`
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_

  - [x] 5.2 Implement `ATSAgent`
    - `ml/agents/ats_agent.py` extending `DeterministicAgent[ATSOutput]` with injected Phase 2 scorer adapter: reuse persisted score when `match_snapshot.scorer_version` equals the active Scorer_Version, else invoke `Semantic_Match_Scorer` (with Degraded_Mode ladder); attach confidence via the `confidence.py` rule; `build_degraded` = persisted score fields with `confidence="low"`
    - Output carries composite score, breakdown, Confidence_Level, and Scorer_Version
    - _Requirements: 4.1, 4.3, 4.4, 4.6_

  - [x] 5.3 Implement `SkillGapAgent`
    - `ml/agents/skill_gap_agent.py` extending `DeterministicAgent[SkillGapReport]`: pure function of candidate_profile, match_snapshot skills, and job_description_skills using `gap_rules.py`; degraded profile input → derive from Match_Result skills alone with `derived_from_degraded_input=True`; empty gap list is valid, never a degradation trigger; `build_degraded` = persisted missing skills classified `missing`, ranked sequentially in persisted order
    - _Requirements: 5.1, 5.4, 5.5, 5.6, 5.7_

  - [x] 5.4 Implement `ImprovementAgent`
    - `ml/agents/improvement_agent.py` extending `LLMAgent[ImprovementReport]`: `feature_spec` resolves the Phase 3 resume-coach Prompt_Template lineage through the registry (never string literals); `build_prompt_input` = serialized Candidate_Profile + matched/missing skills in the delimited region, no dependency on Skill_Gap_Report; consumes a degraded profile without shape-branching, marking `derived_from_degraded_input=True`; `build_degraded` from stored rule-based suggestions and missing skills
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6_

  - [x] 5.5 Implement `SynthesizerAgent`
    - `ml/agents/synthesizer.py` extending `DeterministicAgent[AnalysisResult]`: pure function assembling the four upstream outputs plus one `AgentTraceSummary` per agent (name, status from state flags, latency, failure reason when degraded); overrides `_build_degraded_safely` to re-raise so Synthesizer failure propagates and fails the job
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6_

  - [x] 5.6 Write unit tests for the hierarchy contract
    - Every concrete agent subclasses exactly one of `LLMAgent`/`DeterministicAgent`; no concrete agent overrides `__call__`; `DeterministicAgent` subclasses hold no LLM reference; Synthesizer re-raises instead of degrading
    - _Requirements: 1.4, 1.6, 7.6_

  - [x] 5.7 Write property test for deterministic agents making no LLM call
    - **Property 3: Deterministic agents make no LLM call**
    - **Validates: Requirements 1.6, 5.1, 7.1**

  - [x] 5.8 Write property test for deterministic re-execution
    - **Property 9: Deterministic agents re-execute to identical output**
    - **Validates: Requirements 5.3, 7.3, 12.5**

  - [x] 5.9 Write property test for Analysis_Result completeness
    - **Property 10: Analysis_Result completeness and status fidelity**
    - **Validates: Requirements 7.1, 7.2, 7.4, 7.5**

  - [x] 5.10 Write property test for provider-bound text redaction
    - **Property 2: Provider-bound text is always redacted**
    - **Validates: Requirements 9.2, 3.1**

- [x] 6. Checkpoint - Ensure all tests pass
  - Ensure all tests pass (`mypy --strict` on `ml/agents/`, ruff, pytest), ask the user if questions arise.

- [x] 7. Wire the agent graph
  - [x] 7.1 Implement graph construction and the best-effort checkpointer in `ml/agents/graph.py`
    - `build_agents` (constructs the five agents with dependencies), `build_graph` (StateGraph over AgentState; edges START→resume_analysis, START→ats, resume_analysis→skill_gap, resume_analysis→improvement, [ats, skill_gap, improvement]→synthesizer→END), `compile_graph` accepting an optional checkpointer
    - `BestEffortSaver` wrapping `AsyncPostgresSaver`: catches checkpoint write failures, logs one structured warning (job id + reason, no PII), run continues in memory; checkpoints keyed by `thread_id = job_id`
    - _Requirements: 1.1, 1.5, 2.1, 2.2, 2.3, 2.4, 2.6_

  - [x] 7.2 Write property test for graceful degradation
    - **Property 4: Graceful degradation under any non-Synthesizer failure combination**
    - **Validates: Requirements 1.7, 3.4, 4.4, 5.5, 6.4, 8.1, 8.2, 8.4, 8.5, 8.6, 12.2**

  - [x] 7.3 Write unit tests for graph structure and timeout mechanics
    - Five nodes with expected edges; slow node with tiny configured timeout degrades and discards late results
    - _Requirements: 1.1, 1.5, 8.3_

- [x] 8. Implement job persistence services and the agent cache
  - [x] 8.1 Implement the job lifecycle service in `services/agent_jobs/service.py`
    - Create-with-idempotency (insert; on partial-unique-index violation fetch and return the existing non-terminal job), guarded status transitions (`queued→running→completed|failed`; only status/timestamps/error mutate after creation), owner-scoped reads joining `agent_runs` for step statuses; no code path deletes jobs or runs
    - _Requirements: 10.5, 12.1, 12.4, 12.6_

  - [x] 8.2 Implement agent run persistence in `services/agent_jobs/runs.py`
    - `persist_agent_run(...)`: exactly one immutable row per node invocation with JSON-serialized input/output state (redacted/derived by construction), latency_ms, status, structured failure reason (null iff completed)
    - _Requirements: 12.2, 12.3, 12.7_

  - [x] 8.3 Implement the Agent_Cache in `services/agent_jobs/cache.py`
    - Redis cache keyed `agent-cache:{user_id}:{agent_name}:{prompt_version}:{input_hash}` (hash over redacted input), TTL from settings; only non-degraded outputs written; read failure → miss, write failure → proceed, each with one structured warning; LLM agents fulfil this via the Phase 3 orchestrator cache with agent-specific namespaces
    - _Requirements: 9.7, 9.10_

  - [x] 8.4 Write property test for the agent cache
    - **Property 12: Agent_Cache round-trip, degraded exclusion, and user isolation**
    - **Validates: Requirements 9.7**

- [x] 9. Implement queue client and tracing infrastructure
  - [x] 9.1 Implement the JobQueue client in `services/agent_jobs/queue.py`
    - Async `aioboto3` SQS wrapper: `enqueue` (identifiers-only `JobMessage` body, trace context as message attributes), `receive` (long poll), `delete`, `healthcheck` (GetQueueAttributes, short timeout); queue URL/region/endpoint/credentials exclusively from Settings
    - _Requirements: 11.1, 11.3, 13.4, 16.1_

  - [x] 9.2 Implement OpenTelemetry setup in `core/tracing.py`
    - `configure_tracing(settings)`: no-op tracer when `MATCHLAYER_OTEL_EXPORTER_OTLP_ENDPOINT` unset; else TracerProvider + OTLP exporter + BatchSpanProcessor; `TraceContextTextMapPropagator` inject/extract helpers for SQS message attributes; export failures never alter outcomes, at most one structured warning per failure; spans carry identifiers and hashes only, never Restricted PII
    - _Requirements: 13.3, 13.4, 13.5, 13.7_

- [x] 10. Implement the agent worker
  - [x] 10.1 Implement the SQS consumer in `workers/agent_worker.py`
    - Long-running async consumer: parse/validate `JobMessage` (malformed or job-not-found → structured warning + delete, poison-message safety); extract trace context (invalid/missing → new trace) and open the job span; redelivery policy from the persisted `attempts` counter (terminal → ack; below `MATCHLAYER_AGENT_MAX_ATTEMPTS` → increment, set running + started_at, execute; at max → failed + ack); build initial AgentState in the worker (load Resume/Match_Result/JD, run PII_Redactor on extracted_text, run Skill_Extractor on the JD, project MatchSnapshot — the API analyze path never reads extracted_text); pre-invocation validation failure → job failed, no node executes; invoke compiled graph with `thread_id = job_id`; persist AnalysisResult + completed/failed transition; delete message only after the terminal transition is committed; same structlog JSON logging and OTel setup as the API
    - _Requirements: 1.8, 11.2, 11.4, 11.5, 11.7, 11.8, 12.7, 13.1, 13.6_

  - [x] 10.2 Create the worker Dockerfile and docker-compose service
    - `infra/docker/worker.Dockerfile` reusing the API codebase with entrypoint `python -m matchlayer_api.workers.agent_worker` (no HTTP server, non-root user); add a `worker` service to `docker-compose.yml`
    - _Requirements: 11.2, 11.3_

  - [x] 10.3 Write property test for invalid invocation
    - **Property 5: Invalid invocation fails before any node executes**
    - **Validates: Requirements 1.8**

  - [x] 10.4 Write property test for redelivery dispatch
    - **Property 16: Redelivery dispatch decision**
    - **Validates: Requirements 11.5**

  - [x] 10.5 Write property test for poison messages
    - **Property 17: Poison messages are absorbed**
    - **Validates: Requirements 11.7**

  - [x] 10.6 Write property test for span accounting
    - **Property 18: Span accounting mirrors run accounting**
    - **Validates: Requirements 13.1, 13.2**

  - [x] 10.7 Write property test for PII-free persistence and telemetry
    - **Property 1: No raw resume PII on any persistence or telemetry surface**
    - **Validates: Requirements 1.3, 2.3, 12.3, 13.3**

  - [x] 10.8 Write property test for prompt reconstruction
    - **Property 19: LLM prompt reconstruction round-trip**
    - **Validates: Requirements 12.5**

  - [x] 10.9 Write the reproducibility demonstration test
    - Automated test required by Requirement 12.5: for at least one completed Agent_Job, re-executing each Deterministic_Agent against persisted input state reproduces the persisted output state field-for-field, and each LLM prompt reconstructed from persisted input state + recorded prompt version + PII_Redactor version recomputes to the input hash recorded in the LLM_Invocation_Log
    - _Requirements: 12.5_

- [x] 11. Implement the async API endpoints
  - [x] 11.1 Implement `POST /api/v1/matches/{id}/analyze`
    - In `api/matches/`: authn + ownership (indistinguishable 404), rate limit (analyze limit from settings), read-only quota precheck (≥2 Daily_Quota units, else 429 RFC 7807 with UTC reset time, no job row, no message), in-flight idempotency via the partial unique index, persist job (`queued`) → commit → enqueue with injected trace headers, enqueue failure → job `failed` + 503 RFC 7807 (no orphaned queued row), respond 202 with `{id, status: "queued", job_url}`; Pydantic-validated input, `X-Robots-Tag: noindex, nofollow`
    - _Requirements: 9.4, 10.1, 10.4, 10.5, 10.6, 10.7, 11.1, 11.6, 11.8_

  - [x] 11.2 Implement `GET /api/v1/jobs/{id}` in a new `api/jobs/` router
    - Owner-scoped job read returning status, ISO 8601 UTC `Z` timestamps (started_at/completed_at null when unset), per-agent steps derived solely from agent_runs rows (`pending` when no row), `result` present iff completed, structured PII-free display-safe `error` present iff failed; indistinguishable 404; rate limit 120/min; `X-Robots-Tag`; RFC 7807 errors
    - _Requirements: 10.2, 10.3, 10.4, 10.6, 10.7, 12.4_

  - [x] 11.3 Add the `agents` field to `/healthz`
    - `"agents": "available" | "unavailable"` from `JobQueue.healthcheck()` (result cached ~10 s), following the `semantic_scoring`/`llm` pattern; always HTTP 200 `status: "ok"`; never leaks queue URLs, credentials, or endpoint addresses
    - _Requirements: 16.1, 16.6_

  - [x] 11.4 Write property test for LLM call and quota accounting
    - **Property 11: LLM call, quota, and invocation-log accounting agree**
    - **Validates: Requirements 3.1, 3.6, 6.1, 9.3, 9.4, 9.5, 9.9**

  - [x] 11.5 Write property test for job status response derivation
    - **Property 13: Job status response derivation**
    - **Validates: Requirements 10.2, 10.3**

  - [x] 11.6 Write property test for ownership indistinguishability
    - **Property 14: Ownership indistinguishability**
    - **Validates: Requirements 10.4, 12.4**

  - [x] 11.7 Write property test for in-flight idempotency
    - **Property 15: In-flight job idempotency under concurrency**
    - **Validates: Requirements 10.5**

  - [x] 11.8 Write unit tests for endpoint flows and health
    - Analyze 202 flow, enqueue-failure 503 compensation, headers, RFC 7807 envelopes, rate limiting; `/healthz` agents field both branches
    - _Requirements: 10.1, 10.6, 10.7, 11.6, 16.1, 16.6_

- [x] 12. Write the required latency-budget tests
  - [x] 12.1 Write the branch-overlap parallelism test
    - Inject a fixed delay into the Skill_Gap and Improvement nodes and assert the combined branch phase completes in less than the sum of the two injected delays
    - _Requirements: 14.2_

  - [x] 12.2 Write the 30-second latency-budget CI test
    - Full Agent_Graph execution for one Agent_Job with a mocked LLM_Client at 10 s injected per-call latency, asserting `started_at`→`completed_at` elapsed time < 30 seconds
    - _Requirements: 14.1, 14.4_

- [x] 13. Regenerate shared types
  - [x] 13.1 Run OpenAPI→TS/Zod codegen for the new endpoints
    - Regenerate `packages/shared-types/` so the analyze and job endpoints' request/response types and Zod schemas are exposed with curated re-exports; ensure the CI drift check covers them
    - _Requirements: 10.8_

- [x] 14. Checkpoint - Ensure all tests pass
  - Ensure all tests pass (backend suite including the unchanged Phase 3 suite, mypy --strict, codegen drift check), ask the user if questions arise.

- [x] 15. Implement the frontend Progress UI
  - [x] 15.1 Implement the typed agent-jobs API client in `apps/web/src/lib/api/agent-jobs.ts`
    - Analyze + job polling calls using generated types and Zod schemas from `packages/shared-types/`; every polled response Zod-parsed at runtime
    - _Requirements: 15.5_

  - [x] 15.2 Implement the polling hook in `apps/web/src/hooks/use-agent-job.ts`
    - TanStack Query with `refetchInterval` default 2000 ms clamped to [1000, 5000]; stops within one interval on: terminal status, HTTP 404/429/5xx, Zod parse failure, or 120 s since first poll — each mapping to a distinct UI state with a recovery action
    - _Requirements: 15.1, 15.4, 15.5, 15.7_

  - [x] 15.3 Implement the progress component in `apps/web/src/components/analysis/analysis-progress.tsx`
    - Exactly five labeled steps ("Analyzing resume…", "ATS scoring…", "Finding skill gaps…", "Generating improvements…", "Combining results…") rendering polled step statuses; changes announced via `aria-live="polite"`; skeleton pending states, calm app-shell styling per `design.md`
    - _Requirements: 15.1_

  - [x] 15.4 Implement the result component with the "Show reasoning" toggle in `apps/web/src/components/analysis/analysis-result.tsx`
    - Renders the AnalysisResult with a visible degraded badge on exactly the sections whose contributing agent degraded; "Show reasoning" toggle defaulting off on every load (no persistence), revealing per-agent trace summaries as plain text / safe markdown with embedded HTML stripped — never `dangerouslySetInnerHTML`
    - _Requirements: 15.2, 15.3_

  - [x] 15.5 Wire the analysis flow into the match results page
    - Trigger button calling analyze, progress display while polling, result render on completion, per-case error states (failed / 404 / 429 with reset time / 5xx / Zod failure / timeout) each with a recovery action; `(app)` route group so noindex is inherited
    - _Requirements: 15.1, 15.2, 15.4, 15.7_

  - [x] 15.6 Write property test for polling termination
    - **Property 20: Polling terminates correctly on any response sequence (frontend)**
    - **Validates: Requirements 15.4, 15.5, 15.7**

  - [x] 15.7 Write property test for degraded indicators
    - **Property 21: Degraded indicators track degraded outputs exactly (frontend)**
    - **Validates: Requirements 15.2**

  - [x] 15.8 Write component tests for the Progress UI
    - Five labeled steps with polled statuses, `aria-live` announcements, show-reasoning default-off toggle and safe rendering (HTML-bearing trace content rendered inert), per-case error states
    - _Requirements: 15.1, 15.3, 15.4_

- [x] 16. Write documentation deliverables
  - [x] 16.1 Write `docs/agent-rules.md`
    - The Confidence_Level rule with its numeric bounds, and the skill-gap classification + prioritization rules with the deterministic tie-breaking order
    - _Requirements: 4.2, 5.2_

  - [x] 16.2 Write ADR 0008 and update the ADR index
    - `docs/adr/0008-agent-architecture.md` with Status/Date/Context/Decision/Rationale/Consequences/Alternatives covering the agent class hierarchy, nodes, edges, parallel branches, AgentState, checkpointer usage, and degradation policy; add the 0008 entry to `.kiro/steering/adrs.md`; do not create `docs/adr/0004-agent-architecture.md`
    - _Requirements: 16.3, 16.8_

  - [x] 16.3 Update `docs/costs.md` and write the local runbook
    - `docs/costs.md`: ≤2 LLM calls per run under quota + breaker, SQS in the AWS free tier, Phases 1–5 ceiling intact
    - `docs/runbooks/agents-local.md` (linked from the README): executable commands for LocalStack up, worker up, trigger analyze, poll job, inspect agent_jobs/agent_runs/traces; the worst-case duration formula over node timeout, LLM timeout, longest-path node count, and max attempts with the computed default value; the real-provider latency measurement procedure
    - _Requirements: 9.8, 14.3, 14.4, 16.4_

- [x] 17. Write integration tests
  - [x] 17.1 Write checkpointer integration tests
    - Alembic-created schema; snapshots written per transition keyed by job id and readable back for in-flight and completed runs; failing saver degrades to warnings without affecting Job_Status
    - _Requirements: 2.1, 2.2, 2.4, 2.5, 2.6_

  - [x] 17.2 Write worker lifecycle integration tests against LocalStack
    - Enqueue → consume → running → completed with timestamps; delete-after-persist ordering; Synthesizer-failure persistence ordering; trace id continuity across the queue; no-exporter no-op equivalence
    - _Requirements: 11.2, 11.3, 11.4, 12.7, 13.4, 13.5_

  - [x] 17.3 Write the config hygiene check
    - Automated test asserting every Phase 4 Settings field appears in `.env.example`
    - _Requirements: 16.7_

- [x] 18. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, including the pre-existing Phase 3 suite unmodified, `mypy --strict` on `ml/agents/`, `services/agent_jobs/`, `workers/`, and the new API modules, and the OpenAPI codegen drift check. Ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- The reproducibility test (10.9), latency-budget tests (12.1, 12.2), and config hygiene check (17.3) are not marked optional because Requirements 12.5, 14.2, 14.4, and 16.7 explicitly mandate those automated tests
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate the 21 universal correctness properties from the design (Hypothesis ≥100 iterations backend, fast-check frontend), tagged `# Feature: phase-4-agentic, Property {N}: {title}`
- Phase 3 endpoints must remain contract-unchanged throughout (Requirement 16.5)
- Completed so far: 1.1, 1.2 (deps, settings, LocalStack, `.env.example`), 3.1 (`state.py`), 3.2 (`confidence.py`). In progress: 2.1 (migration `0005_agent_tables.py` exists, SQLAlchemy models pending), 3.3 (`test_confidence_rule.py` exists, unverified), 3.4 (`gap_rules.py`), 4.1 (`base.py`)

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["2.1", "2.2", "3.3", "3.4", "4.1", "9.1", "9.2"] },
    { "id": 1, "tasks": ["3.5", "3.6", "4.2", "4.3", "8.1", "8.2", "8.3"] },
    { "id": 2, "tasks": ["5.1", "5.2", "5.3", "5.4", "5.5", "8.4", "16.1"] },
    { "id": 3, "tasks": ["5.6", "5.7", "5.8", "5.9", "5.10", "7.1"] },
    {
      "id": 4,
      "tasks": ["7.2", "7.3", "10.1", "11.1", "11.2", "11.3", "16.2"]
    },
    {
      "id": 5,
      "tasks": [
        "10.2",
        "10.3",
        "10.4",
        "10.5",
        "10.6",
        "10.7",
        "10.8",
        "10.9",
        "11.4",
        "11.5",
        "11.6",
        "11.7",
        "11.8",
        "12.1",
        "12.2",
        "13.1",
        "16.3"
      ]
    },
    { "id": 6, "tasks": ["15.1", "17.1", "17.2", "17.3"] },
    { "id": 7, "tasks": ["15.2"] },
    { "id": 8, "tasks": ["15.3", "15.4", "15.6"] },
    { "id": 9, "tasks": ["15.5", "15.7", "15.8"] }
  ]
}
```
