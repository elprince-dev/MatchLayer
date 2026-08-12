# Implementation Plan: phase-3-llm-layer

## Overview

Build order follows the dependency chain of the shared LLM request pipeline: configuration and data foundations first, then the provider abstraction, prompt/redaction machinery, and cost controls as independently testable units, then the orchestrator and feature services that compose them, then the HTTP/SSE surface, codegen, and finally the frontend streaming experience. Backend is Python (FastAPI, Pydantic v2, SQLAlchemy 2.x async); frontend is TypeScript (Next.js). Property-based tests use Hypothesis (backend) and fast-check (frontend), minimum 100 iterations, each tagged `# Feature: phase-3-llm-layer, Property N: <title>`.

## Tasks

- [x] 1. Configuration and infrastructure foundation
  - [x] 1.1 Add the LLM settings block to `config.py` and `.env.example`
    - Add all `MATCHLAYER_LLM_*` settings to the existing `Settings` (`llm_base_url`, `llm_api_key: SecretStr | None`, `llm_model`, `llm_timeout_seconds`, `llm_max_output_tokens`, `llm_daily_quota`, `llm_monthly_spend_limit_usd`, `llm_max_bullets`, `llm_max_bullet_chars`, `llm_max_questions`, `llm_cache_ttl_seconds`, `llm_price_input_usd_per_mtok`, `llm_price_output_usd_per_mtok`) with the design's defaults
    - Add a `model_validator` that fails startup naming the offending setting for any non-positive numeric value and for `llm_max_questions < 5`
    - List every new setting in `.env.example` with placeholder values; the API key entry carries a non-functional placeholder
    - _Requirements: 1.3, 1.4, 7.8, 18.1, 18.2_

  - [x] 1.2 Write property test for config validation
    - **Property 21: Config validation rejects non-positive bounds at startup**
    - **Validates: Requirements 7.8, 18.2**

  - [x] 1.3 Create `core/redis.py` and relocate the Redis import boundary
    - New `core/redis.py` owning the redis import and the per-request async client factory
    - Refactor `core/rate_limit.py` to receive an injected client
    - Update `tests/unit/test_import_boundaries.py` to name `core/redis.py` as the single allowed redis importer
    - _Requirements: 13.1 (Rate_Limiter reuse), design decision D6_

  - [x] 1.4 Create the `llm_results` and `llm_invocation_logs` tables
    - SQLAlchemy models per the design's Data Models section (UUIDv7 PKs, JSONB payloads, nullable token/cost fields distinct from zero, `cost_basis`, `failure_category`)
    - One Alembic migration adding both tables with the documented indexes: `(match_result_id, feature, created_at DESC)` on `llm_results`; `(feature, prompt_template_version, llm_model, created_at)` and `(created_at)` on `llm_invocation_logs`
    - No soft delete on either table
    - _Requirements: 12.1, 12.3, 12.4, 16.3_

- [x] 2. Result schemas
  - [x] 2.1 Implement `services/llm/schemas.py`
    - `FailureReason` closed enum; `CoachingReport` (summary, strengths, gaps, 3..10 `ImprovementAction`s with validator enforcing descending priority order); `BulletRewriteEntry`/`BulletRewrite` (1..3 alternatives, non-empty rationale); `InterviewQuestion`/`InterviewQuestionSet` (category enum, question ≤300 chars, reason ≤500 chars, 5..`llm_max_questions` count); generic `LLMResultEnvelope[T]` with `is_fallback`, `fallback_reason`, `prompt_template_version`, `created_at`
    - These models are the OpenAPI source of truth for generated TS/Zod types; fallback content conforms to the same `result` schema
    - _Requirements: 5.2, 6.2, 7.2, 7.3, 8.4, 9.2_

  - [x] 2.2 Write unit tests for schema field bounds and validators
    - Improvement count/ordering, question count/length bounds, alternatives bounds, envelope fields
    - _Requirements: 5.2, 6.2, 7.2, 7.3_

- [x] 3. Provider abstraction (`ml/llm/`)
  - [x] 3.1 Define the provider-neutral `LLMClient` protocol and types in `ml/llm/client.py`
    - `LLMMessage`, `LLMRequest` (with `output_schema` and mandatory `max_output_tokens`), `LLMUsage` (nullable tokens/cost + `cost_basis`), `LLMStreamChunk`, `LLMCompletion`, `LLMError` with failure category, `LLMClient` protocol (`validate_credentials`, `stream`, `result`)
    - No OpenRouter-specific identifiers anywhere in these types
    - _Requirements: 1.1_

  - [x] 3.2 Implement the OpenRouter adapter in `ml/llm/openrouter.py`
    - `httpx.AsyncClient` POST to `{base_url}/chat/completions` with `stream: true`, `response_format` json_schema mapped from `output_schema`, `max_tokens` from settings, `usage: {"include": true}`; SSE chunk parsing with `[DONE]` sentinel
    - Full call inside `asyncio.timeout(settings.llm_timeout_seconds)`; on expiry close the stream and raise `LLMError(category="timeout")`
    - Exactly one attempt, no retries; cost resolution: provider-reported → computed from configured pricing → unavailable
    - `validate_credentials()` via authenticated `GET /key` distinguishing invalid-key / unreachable / timeout
    - API key held as `SecretStr`, attached only as the `Authorization` header, never in exceptions, logs, or reprs
    - _Requirements: 1.2, 1.4, 1.5, 1.6, 1.9, 1.12, 8.1, 12.2_

  - [x] 3.3 Implement `ml/llm/availability.py` and startup wiring
    - Availability state: `key_present` recorded at startup; key absent → app starts normally with features in LLM_Unavailable
    - Key present → `validate_credentials()` in lifespan startup; failure aborts startup naming the cause category, never the key value
    - No endpoint or config path for user-supplied keys
    - _Requirements: 1.7, 1.8, 1.10, 10.2_

  - [x] 3.4 Write unit tests for the adapter against a mocked OpenRouter endpoint
    - Request payload shape (`max_tokens`, `response_format`, no `tools`), timeout abort, single-attempt behavior, the three startup-validation failure categories, key never appearing in logs or error strings
    - _Requirements: 1.2, 1.4, 1.6, 1.9, 1.10, 1.12, 4.3_

  - [x] 3.5 Write boundary/static tests
    - Only `ml/llm/openrouter.py` references OpenRouter; only `core/redis.py` imports redis; no hardcoded model identifier outside config defaults; no prompt instruction literals in services/routers
    - _Requirements: 1.1, 1.3, 2.1_

- [x] 4. Prompt templates and assembly
  - [x] 4.1 Create the prompt templates and version registry
    - `ml/prompts/resume_coach.v1.txt`, `bullet_rewrite.v1.txt`, `interview_questions.v1.txt` — each with named `{placeholder}` slots plus injection-hardening instructions (treat delimited content as data; never reveal the system prompt)
    - `ml/prompts/registry.py` with `ACTIVE_PROMPT_VERSIONS` as the single designated active-version source
    - _Requirements: 2.1, 2.2, 2.4, 4.2_

  - [x] 4.2 Implement `services/llm/prompting.py`
    - `load_template` (missing/unreadable file → `PromptTemplateError`), `render` (exact named-placeholder substitution only; missing value → `PromptRenderError` with template/version/placeholder name in the structured log, no PII, nothing transmitted)
    - Message construction: rendered template as the sole system-role message; all user-derived content in the user-role message wrapped in `<user_content kind=...>` delimiters
    - Deterministic delimiter neutralization of literal `<user_content` / `</user_content` sequences in user text, applied after redaction and before wrapping
    - _Requirements: 2.5, 2.6, 2.7, 4.1, 4.4, 4.6_

  - [x] 4.3 Write property test for prompt rendering
    - **Property 5: Prompt rendering is exact substitution**
    - **Validates: Requirements 2.6, 2.7**

  - [x] 4.4 Write property test for message construction
    - **Property 6: Message construction and delimiter neutralization**
    - **Validates: Requirements 4.1, 4.4, 4.6**

  - [x] 4.5 Write unit test asserting consecutive prompt versions differ
    - No two consecutive versions of a feature's template are byte-identical
    - _Requirements: 2.3_

- [x] 5. PII redaction
  - [x] 5.1 Implement `services/llm/redaction.py` and the redaction policy doc
    - Region segmentation (contact/header region, employment-history sections via the committed heading lexicon, other text); email/phone regexes over the whole text; name heuristic over the header region with every-occurrence redaction
    - Indexed typed placeholders `[EMAIL_n]`, `[PHONE_n]`, `[NAME_n]` — one index per distinct value per type, first-occurrence order, consistent across occurrences; employment-history spans exempt
    - Deterministic and pure; `REDACTOR_VERSION` constant; 5-second wall-clock bound; `RedactionError` carrying no input fragment; neither input nor output ever logged
    - Write `docs/redaction-policy.md` documenting the Redaction_Exception, its rationale, and the committed boundary rule
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7_

  - [x] 5.2 Create committed synthetic redaction fixtures and the exact-equality test
    - `tests/fixtures/redaction/*.json` pairing inputs with expected outputs: repeated values, multiple distinct values per type, exception-region cases; no real personal data
    - Test asserting redactor output matches each fixture exactly
    - _Requirements: 3.9_

  - [x] 5.3 Write property test for placeholder correctness
    - **Property 1: Redaction placeholder correctness**
    - **Validates: Requirements 3.1, 3.2, 3.5**

  - [x] 5.4 Write property test for the employment-history exception
    - **Property 2: Redaction exception preserves employment-history spans**
    - **Validates: Requirements 3.3**

  - [x] 5.5 Write property test for redaction determinism
    - **Property 3: Redaction determinism**
    - **Validates: Requirements 3.4**

- [x] 6. Checkpoint — foundations
  - Ensure all tests pass, ask the user if questions arise.

- [x] 7. Cost controls, caching, and invocation logging
  - [x] 7.1 Implement `services/llm/quota.py` (DailyQuota)
    - Key `llm:quota:{user_id}:{YYYYMMDD}` (UTC), 48h expiry; read-only `gate()`; atomic Lua INCR-if-below-limit `reserve()` called only at provider-call initiation; both return `remaining` for the `X-LLM-Quota-Remaining` header
    - Any Redis error → `QuotaAccountingError` (fallback path, no provider call)
    - _Requirements: 13.1, 13.2, 13.3, 13.4, 13.6, 13.7, 13.8_

  - [x] 7.2 Write property test for quota accounting
    - **Property 14: Quota accounting counts exactly the initiated provider calls**
    - **Validates: Requirements 13.1, 13.2, 13.3, 13.4, 13.6, 13.7**

  - [x] 7.3 Implement `services/llm/invocation_log.py`
    - Persist exactly one row per provider call (success or failure): feature, registry-active prompt version, model, redactor version, input hash, validated output XOR failure category, latency, token usage, cost + basis, user id, match id, UTC timestamp
    - Best-effort write: failure never fails the request, emits `invocation_log_write_failed` structured event; monthly spend SUM query via SQLAlchemy Core
    - _Requirements: 12.1, 12.2, 12.3, 12.4, 12.5, 14.1_

  - [x] 7.4 Write property test for invocation logging
    - **Property 20: Invocation log records every call exactly once**
    - **Validates: Requirements 2.4, 12.1, 12.2, 12.3**

  - [x] 7.5 Implement `services/llm/spend.py` (SpendCircuitBreaker)
    - `evaluate()` sums current-UTC-month `cost_usd` from invocation logs; open iff sum ≥ limit; memoized process-local state; evaluated before each provider call and after each log persist
    - Read failure or log-persist failure forces state open (`tracking_failure`) until a successful below-limit evaluation; month rollover and raised limit close it at next evaluation
    - Exactly one structured transition event per open↔closed change (direction, tracked spend, limit, cause — no PII, no key)
    - _Requirements: 14.1, 14.2, 14.4, 14.5, 14.6, 14.7, 14.8_

  - [x] 7.6 Write property test for the circuit breaker
    - **Property 15: Spend circuit breaker opens exactly at the limit and fails safe**
    - **Validates: Requirements 14.1, 14.2, 14.4, 14.5, 14.6, 14.7, 14.8**

  - [x] 7.7 Implement `services/llm/cache.py` (LLMCache)
    - Key `llm:cache:{user_id}:{feature}:{input_hash}:v{ver}:{model}` — user id always in key and lookup; value = serialized validated envelope; TTL from settings
    - Lookup failure → miss; write failure → result still returned, logged; fallbacks and invalid outputs never written
    - _Requirements: 15.1, 15.2, 15.3, 15.4, 15.6, 15.7, 15.8_

  - [x] 7.8 Write property test for cache isolation
    - **Property 13: Cache isolation across users**
    - **Validates: Requirements 15.3**

  - [x] 7.9 Write unit tests for cache and logging edges
    - TTL expiry treated as miss, lookup failure → normal path, write failure → result returned, invocation-log write failure → request completes and breaker opens
    - _Requirements: 12.5, 14.8, 15.6, 15.7, 15.8_

- [x] 8. Orchestrator and feature services
  - [x] 8.1 Implement `services/llm/orchestrator.py` and `services/llm/results.py`
    - `LLMFeatureSpec` dataclass; `run()` executing the normative pipeline order: quota gate → persisted-result reuse (coach) → redact → assemble prompt → cache lookup → breaker evaluation → atomic quota reserve → single provider call → terminal validation → persist → cache write → invocation log
    - Canonical prompt-input hash: `sha256` over `feature | template_version | model | redacted_system_values` — shared by invocation log and cache key
    - Failure taxonomy: every exception maps to exactly one `FailureReason`, exactly one structured failure log event (reason, request id, user id, feature, prompt version — no PII, no key), then the fallback builder; fallbacks never persisted, never cached; never a 5xx from an LLM failure
    - `results.py`: LLM_Result persistence + newest-first cursor pagination queries
    - _Requirements: 8.2, 8.3, 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 1.11, 13.2, 13.3, 13.8_

  - [x] 8.2 Implement `services/llm/coach.py` (Resume_Coach)
    - Feature spec: inputs = redacted resume + JD text + stored `matched_keywords`/`missing_keywords` read verbatim; persisted-result reuse before the pipeline (same active version + model → no call, no quota); version/model change → fresh call, old rows retained
    - Fallback built from stored `suggestions` + `missing_keywords`, empty lists carried as empty
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.7_

  - [x] 8.3 Implement `services/llm/bullets.py` (Bullet_Rewriter)
    - Pydantic request validation: 1..`llm_max_bullets` bullets, none empty/whitespace, each ≤ `llm_max_bullet_chars` → 422 before any LLM work
    - Prompt includes JD context + missing skills; post-validation alignment check (one entry per bullet, submission order, `original` exact match — any deviation is a schema-validation failure)
    - Fallback: each bullet unchanged + guidance from stored missing skills/suggestions only
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.6, 6.7_

  - [x] 8.4 Implement `services/llm/questions.py` (Interview_Question_Generator)
    - Prompt includes matched + missing skills; schema enforces 5..`llm_max_questions`, category enum, length bounds; out-of-bounds counts fail validation, never truncated/padded
    - Fallback: template-based questions from matched/missing skills, generic templates when both lists empty, always ≥ 5, same schema
    - _Requirements: 7.1, 7.2, 7.3, 7.5, 7.6, 7.7_

  - [x] 8.5 Write property test for prompt grounding
    - **Property 7: Prompt grounding includes stored match context**
    - **Validates: Requirements 5.3, 6.4, 7.6**

  - [x] 8.6 Write property test for the validation gate
    - **Property 8: Validation gate — accept if and only if schema holds**
    - Uses the scripted `FakeLLMClient` replaying valid payloads, malformed JSON, truncations, bound violations, bullet misalignments under arbitrary stream chunking
    - **Validates: Requirements 4.5, 5.2, 6.2, 6.7, 7.2, 7.3, 7.7, 8.1, 8.2, 8.3, 8.5**

  - [x] 8.7 Write property test for universal fallback behavior
    - **Property 9: Universal fallback outcome for any LLM failure**
    - Injects each failure mode; asserts one attempt max, one structured failure event, PII/key sentinels absent, no persisted row or cache entry
    - **Validates: Requirements 1.11, 1.12, 2.5, 3.6, 9.1, 9.2, 9.4, 9.5, 9.6, 13.8**

  - [x] 8.8 Write property test for fallback content
    - **Property 10: Fallback content is schema-conformant and locally derived**
    - **Validates: Requirements 5.5, 6.6, 7.5, 9.2, 9.3**

  - [x] 8.9 Write property test for repeat-request suppression
    - **Property 12: Repeat requests never re-call the provider**
    - **Validates: Requirements 5.4, 15.2**

  - [x] 8.10 Write property test for hash and cache-key consistency
    - **Property 4: Hash and cache-key consistency over redacted text**
    - **Validates: Requirements 3.8, 12.6, 15.1**

- [x] 9. Checkpoint — LLM pipeline
  - Ensure all tests pass, ask the user if questions arise.

- [x] 10. API surface: routers, SSE, health
  - [x] 10.1 Implement the three sub-resource routers in `api/matches/llm/`
    - `router.py` + `schemas.py`: POST/GET-list/GET-one for `coaching-reports`, `bullet-rewrites`, `interview-question-sets` under `/api/v1/matches/{matchId}/`
    - 401 before any existence check; identical 404 RFC 7807 for not-yours and not-found; cursor pagination (opaque base64 `(created_at, id)`, limit 1–100 default 20, descending order, 422 on malformed cursor/out-of-bounds limit); `X-LLM-Quota-Remaining` on every response including 429; 429 detail states the limit and UTC reset; 503 breaker response with spend-limit `type`, no figures; RFC 7807 everywhere with user-safe `detail`
    - _Requirements: 5.6, 6.3, 13.5, 16.1, 16.2, 16.4, 16.5, 16.8, 16.9, 16.10, 10.3_

  - [x] 10.2 Implement SSE streaming in `api/matches/llm/sse.py`
    - `stream=true` query parameter negotiation; all gates evaluated before the stream opens (gate rejections return plain RFC 7807, no stream); `delta`/`complete`/`degraded`/`error` events with exactly one terminal event, stream closed immediately after; timeout enforced over the open stream; client disconnect (`asyncio.CancelledError`) aborts the adapter's provider call; deltas carry display content only, never system prompt or key; cache hits emit `complete` directly
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5, 11.6, 11.7, 15.5_

  - [x] 10.3 Add the `llm` field to `/healthz`
    - `llm: available|unavailable` additive field following the Phase 2 `semantic_scoring` pattern; `unavailable` iff key absent or breaker open; never changes the 200 status; no key/spend details; recovery flips it back without code change
    - _Requirements: 10.1, 10.2, 10.4, 10.5, 10.6_

  - [x] 10.4 Write property test for bullet request validation
    - **Property 11: Bullet request validation rejects invalid input before any call**
    - **Validates: Requirements 6.3**

  - [x] 10.5 Write property test for ownership indistinguishability
    - **Property 16: Ownership indistinguishability**
    - **Validates: Requirements 5.6, 16.2**

  - [x] 10.6 Write property test for persistence round trip
    - **Property 17: Persistence round trip**
    - **Validates: Requirements 6.5, 7.4, 16.3, 17.9**

  - [x] 10.7 Write property test for cursor pagination
    - **Property 18: Cursor pagination is complete, ordered, and bounded**
    - **Validates: Requirements 16.4, 16.10**

  - [x] 10.8 Write property test for SSE terminal events
    - **Property 19: SSE streams terminate with exactly one correct terminal event**
    - **Validates: Requirements 8.5, 11.2, 11.3, 11.5, 11.6, 15.5**

  - [x] 10.9 Write router, SSE, and availability integration tests
    - Happy path per feature; coach version-change regeneration (5.7); non-streaming vs `stream=true`; gate rejections return JSON without opening a stream; client-disconnect aborts the adapter; key-absent startup; the three 10.3 cases; breaker-open blocks new calls while in-flight calls complete and log (14.3); healthz in both states; `X-Robots-Tag` on new endpoints (16.7)
    - _Requirements: 1.8, 5.1, 5.7, 6.1, 7.1, 10.3, 11.1, 11.4, 11.7, 14.3, 16.7_

  - [x] 10.10 Regenerate OpenAPI → TypeScript/Zod types
    - Run the existing codegen pipeline so `packages/shared-types/` covers every new endpoint, envelope, fallback marker, failure-reason enum, and the two-value `llm` health enum; verify the CI drift check passes
    - _Requirements: 8.4, 9.2, 16.6_

- [x] 11. Frontend streaming experience (`apps/web`)
  - [x] 11.1 Implement the SSE client layer in `lib/llm/`
    - `sse.ts`: fetch + ReadableStream SSE parser (native EventSource cannot send auth headers/POST bodies)
    - `use-llm-stream.ts`: start request, accumulate deltas, resolve exactly one terminal event; connection close without a terminal → interrupted state
    - `progressive-text.ts`: display-only tolerant text extraction from partial JSON
    - _Requirements: 11.1, 17.2, 17.10, 17.11_

  - [x] 11.2 Write property test for stream state derivation
    - **Property 22: Frontend stream state derives only from the terminal event** (fast-check + Vitest)
    - **Validates: Requirements 17.2, 17.10, 17.11**

  - [x] 11.3 Implement the shared LLM display components
    - `StreamingText.tsx` (progressive plain-text rendering), `FallbackBadge.tsx` (shown exactly when `is_fallback === true`), `LlmErrorStates.tsx` (429 with daily limit + UTC reset, 503 "AI features temporarily disabled", interrupted-with-retry), skeleton loading state before the first delta
    - All LLM content rendered as plain text or `react-markdown` with `skipHtml`; no `dangerouslySetInnerHTML`
    - _Requirements: 17.3, 17.4, 17.5, 17.6_

  - [x] 11.4 Write property test for HTML injection safety
    - **Property 23: LLM content rendering injects no HTML** (fast-check + Vitest)
    - **Validates: Requirements 17.3**

  - [x] 11.5 Implement the feature panels and results-page integration
    - `LlmTabs.tsx` (Coach / Bullet Rewrites / Interview Prep) added to the existing `(app)/matches/[id]` results page (inherits `noindex, nofollow`)
    - `CoachPanel.tsx`, `BulletRewritePanel.tsx` (bullet select/paste with generated-Zod client validation, inline errors, no request on violation), `InterviewPanel.tsx`
    - On load, GETs (TanStack Query) show the newest persisted result without triggering generation; each result shows `prompt_template_version` + `created_at` with a Regenerate action re-running the streaming flow
    - _Requirements: 17.1, 17.7, 17.8, 17.9_

  - [x] 11.6 Write property test for client-side bullet validation
    - **Property 24: Client-side bullet validation mirrors the server bounds** (fast-check + Vitest)
    - **Validates: Requirements 17.7**

  - [x] 11.7 Write frontend component tests
    - Tab composition, fallback badge both branches, 429/503 messages, skeleton state, load-persisted-without-POST, regenerate interaction; static check: no `dangerouslySetInnerHTML` in `components/llm/`
    - _Requirements: 17.1, 17.3, 17.4, 17.5, 17.6, 17.8, 17.9_

- [x] 12. Documentation and cost tracking
  - [x] 12.1 Update the runbook and cost log
    - README/runbook Phase 3 section: obtaining/configuring the OpenRouter key, default model and `MATCHLAYER_LLM_MODEL`, Daily_Quota and Spend_Circuit_Breaker behavior with defaults and reset conditions, Redaction_Exception policy reference
    - `docs/costs.md` Phase 3 entry: model per-token pricing, quota math from the daily-quota default, the spend limit as worst-case monthly LLM spend, projected total under the $20/month ceiling
    - Verify `.env` remains gitignored and no committed file contains a real key; no new scanner exclusions
    - _Requirements: 18.3, 18.4, 18.5, 18.6_

- [x] 13. Final checkpoint
  - Ensure all tests pass (backend and frontend), ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional (property tests, unit tests, integration tests) and can be skipped for a faster MVP
- Each property test implements exactly one design property, tagged `# Feature: phase-3-llm-layer, Property N: <title>`, Hypothesis `max_examples=100` / fast-check `numRuns: 100`
- Test doubles per the design's Testing Strategy: scripted `FakeLLMClient`, `fakeredis` (honoring the Lua semantics), injected clock for UTC-day/month-rollover cases
- Backend checks: `ruff format`, `ruff check`, `mypy --strict` on `services/`, `api/`, `ml/`; frontend: ESLint + Vitest
- Frontend tasks (11.x) depend on the regenerated types from task 10.10

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.3", "1.4"] },
    { "id": 1, "tasks": ["1.2", "2.1", "3.1", "4.1", "5.1"] },
    { "id": 2, "tasks": ["2.2", "3.2", "4.2", "5.2", "7.1", "7.3"] },
    {
      "id": 3,
      "tasks": [
        "3.3",
        "3.4",
        "4.3",
        "4.4",
        "4.5",
        "5.3",
        "5.4",
        "5.5",
        "7.2",
        "7.4",
        "7.5",
        "7.7"
      ]
    },
    { "id": 4, "tasks": ["3.5", "7.6", "7.8", "7.9", "8.1"] },
    { "id": 5, "tasks": ["8.2", "8.3", "8.4", "8.10"] },
    { "id": 6, "tasks": ["8.5", "8.6", "8.7", "8.8", "8.9", "10.1", "10.3"] },
    { "id": 7, "tasks": ["10.2", "10.4", "10.5"] },
    { "id": 8, "tasks": ["10.6", "10.7", "10.8", "10.9", "10.10"] },
    { "id": 9, "tasks": ["11.1", "12.1"] },
    { "id": 10, "tasks": ["11.2", "11.3"] },
    { "id": 11, "tasks": ["11.4", "11.5"] },
    { "id": 12, "tasks": ["11.6", "11.7"] }
  ]
}
```
