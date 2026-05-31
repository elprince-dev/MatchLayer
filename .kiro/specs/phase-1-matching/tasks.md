# Implementation Plan: phase-1-matching

## Overview

This plan implements the third and final Phase 1 spec: resume upload (PDF/DOCX) to S3/MinIO, bounded server-side text extraction, a deterministic non-LLM TF-IDF-plus-keyword `Match_Scorer`, keyword/skill overlap analysis, rule-based suggestions, the resume and match HTTP surfaces, the Next.js upload/results/library pages, and the non-indexing controls for the authenticated/PII surface.

The implementation language is **Python** for the backend (`apps/api`, package `matchlayer_api`) and **TypeScript** for the frontend (`apps/web`, Next.js App Router), matching the existing repo and the design document. It builds on the existing `create_app` factory, `Settings`, async SQLAlchemy session, `MatchLayerError` RFC 7807 envelope, `get_current_user`, the Redis-backed `RateLimiter`, `Audit_Service`, and the `@matchlayer/shared-types` codegen pipeline — all treated as fixed.

The build order is: configuration and persistence first, then the framework-free scoring core (`scoring/`) and its `ml/` adapter, then the upload infrastructure (storage, MIME, extraction), then the service and router layers, then app wiring and cross-cutting non-indexing controls, then the OpenAPI codegen and the frontend, and finally the README runbook. Each step builds on the previous and ends by wiring new code into the running app.

Property-based tests use **Hypothesis** (already a dev dependency), live under `apps/api/tests/property/`, and each property test references the property number and text from the design. All tasks in this plan are required.

## Tasks

- [x] 1. Configuration, dependencies, environment contract, and error types
  - [x] 1.1 Add matching Settings fields and the weight-sum validator
    - Add the new `MATCHLAYER_*` fields to `apps/api/src/matchlayer_api/config.py` `Settings` (`resume_max_bytes`, `resume_max_decompressed_bytes`, `resume_max_archive_entries`, `resume_extraction_timeout_seconds`, `resume_max_extracted_chars`, `jd_min_chars`, `jd_max_chars`, `match_max_keywords`, `match_max_suggestions`, `score_weight_similarity`, `score_weight_keyword`, `resume_rate_limit_per_min`, `match_rate_limit_per_min`, `resume_daily_quota`, `match_daily_quota`) with the documented defaults
    - Reuse the existing S3 fields verbatim; do not introduce a second credential set
    - Add a `model_validator` that asserts `score_weight_similarity + score_weight_keyword == 1.0`, failing fast at startup like the existing JWT-secret validator
    - _Requirements: 5.3, 14.6, 14.7_

  - [x] 1.2 Add the Phase 1 runtime dependencies
    - Add `scikit-learn>=1.5,<2.0`, `boto3>=1.34,<2.0`, `pypdf>=4.2,<6.0`, `python-docx>=1.1,<2.0`, and `filetype>=1.2,<2.0` to `apps/api/pyproject.toml` with pinned major versions; refresh `uv.lock`
    - Do NOT add `sentence-transformers`, spaCy, any embedding model, or any LLM dependency
    - _Requirements: 5.8, 10.5_

  - [x] 1.3 Update the environment-variable contract
    - Add one `.env.example` entry per new variable from task 1.1, each with the documented default as its placeholder value
    - Confirm the object-storage entries reuse the foundation S3 variables and add no divergent resume-storage credentials
    - _Requirements: 14.6, 14.7, 14.9_

  - [x] 1.4 Add the new RFC 7807 error subclasses
    - Add `PayloadTooLargeError` (413 `payload_too_large`), `UnsupportedMediaTypeError` (415 `unsupported_media_type`), `MalformedUploadError` (422 `malformed_upload`), `ResumeNotExtractableError` (422 `resume_not_extractable`), `QuotaExceededError` (429 `quota_exceeded`), and `NotFoundError` (404 `not_found`) to `apps/api/src/matchlayer_api/core/errors.py`, each setting `status_code`/`error_type`/`title`
    - Reuse the existing `unauthenticated`, `rate_limited`, and `rate_limiter_unavailable` handlers unchanged
    - _Requirements: 1.5, 1.6, 2.2, 2.4, 8.5, 11.6_

  - [x] 1.5 Write unit tests for settings, error envelopes, and env drift
    - Assert the weight validator rejects weights that do not sum to 1.0 and accepts the defaults
    - Assert each new error subclass serializes to the correct RFC 7807 `type`/`status`
    - Run the foundation `.env` drift check against the updated `.env.example`
    - _Requirements: 5.3, 14.9_

- [x] 2. Persistence: SQLAlchemy models and the Alembic migration
  - [x] 2.1 Add the Resume and MatchResult ORM models
    - Add `Resume` and `MatchResult` mapped classes to `apps/api/src/matchlayer_api/db/models.py`, mirroring existing conventions (`PG_UUID` PK defaulting to `_uuid7`, `timestamptz` with `server_default=now()`, nullable `deleted_at`, `JSONB` for `score_breakdown`/`matched_keywords`/`missing_keywords`/`suggestions`)
    - `Resume`: `user_id` FK to `users.id` (ON DELETE CASCADE), `original_filename`, `storage_key`, `content_type`, `byte_size`, `extracted_text` (nullable), `extraction_status`, `extraction_char_count` (nullable)
    - `MatchResult`: `user_id` FK, `resume_id` FK to `resumes.id`, `job_description_text`, `score`, `score_breakdown`, `matched_keywords`, `missing_keywords`, `suggestions`, `scorer_version`
    - _Requirements: 14.2, 14.5_

  - [x] 2.2 Create the `0002_resumes_and_matches` migration
    - Create `apps/api/alembic/versions/0002_resumes_and_matches.py` with `revision = "0002_resumes_and_matches"` and `down_revision = "0001_users_and_auth"`
    - `upgrade()` creates both tables plus indexes `resumes_user_id_idx`, `match_results_user_id_idx`, `match_results_resume_id_idx`, and the composite cursor indexes `resumes_user_created_idx` on `resumes(user_id, created_at DESC, id DESC)` and `match_results_user_created_idx` on `match_results(user_id, created_at DESC, id DESC)`; document why each index exists in a comment
    - `downgrade()` drops every index then table in reverse order
    - _Requirements: 14.1, 14.2, 14.3, 14.4_

  - [x] 2.3 Write an integration test for migration apply/rollback and schema
    - Apply `0002` against the docker-compose Postgres, assert both tables and all indexes exist, then run `downgrade()` and assert they are removed
    - _Requirements: 14.1, 14.2, 14.3, 14.4, 14.5_

- [x] 3. Skill Lexicon source of truth and runtime artifact
  - [x] 3.1 Create the lexicon source, build pipeline, and committed artifact
    - Create the canonical source `ml/lexicon/skill_lexicon.v1.json` (canonical skills, alias rules, optional per-term weights/metadata, and a `lexicon_version`) and the regeneration script `ml/pipelines/build_skill_lexicon.py` (never imported by the API at runtime)
    - Commit the package-data copy `apps/api/src/matchlayer_api/scoring/data/skill_lexicon.v1.json`
    - Add a CI drift check (mirroring the existing `.env`/OpenAPI checks) that fails the build if the committed copy diverges from the `ml/` source
    - _Requirements: 10.3, 10.4_

  - [x] 3.2 Implement the Skill_Lexicon loader and Scorer_Version
    - Implement `apps/api/src/matchlayer_api/scoring/lexicon.py` loading the committed artifact via `importlib.resources` (stdlib only), exposing canonical terms, alias normalization, per-term weight/metadata lookup, and `lexicon_version`
    - Compute `Scorer_Version = f"{ALGORITHM_VERSION}+lex.{lexicon_version}"`
    - _Requirements: 10.3, 10.4_

  - [x] 3.3 Write unit tests for the lexicon loader and version
    - Assert alias rules normalize terms and that `Scorer_Version` changes when `lexicon_version` changes
    - _Requirements: 10.4_

- [x] 4. Framework-free scoring core (`scoring/`, sklearn + stdlib only)
  - [x] 4.1 Implement the Keyword_Analyzer
    - Implement `apps/api/src/matchlayer_api/scoring/keyword_analyzer.py` deriving the analyzed set from the union of (a) `Skill_Lexicon` canonical terms present in the JD and (b) the highest-weighted TF-IDF terms of the JD, capped at `MATCHLAYER_MATCH_MAX_KEYWORDS`
    - Normalize by case-fold plus lexicon alias rules; partition into `matched` (present in normalized resume text) and `missing` (absent); order each list by descending weight
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6_

  - [x] 4.2 Write property test for analyzed-set boundedness
    - **Property 5: The analyzed keyword set is bounded and well-formed**
    - **Validates: Requirements 6.1**

  - [x] 4.3 Write property test for the matched/missing partition
    - **Property 6: Matched and missing partition the analyzed set**
    - **Validates: Requirements 6.3, 6.4**

  - [x] 4.4 Write property test for matched-keyword soundness
    - **Property 7: Every matched keyword is present in the resume**
    - **Validates: Requirements 6.5**

  - [x] 4.5 Write property test for lexicon-alias interchangeability
    - **Property 8: Lexicon aliases are treated as the same keyword**
    - **Validates: Requirements 6.2**

  - [x] 4.6 Implement the Suggestion_Generator
    - Implement `apps/api/src/matchlayer_api/scoring/suggestions.py` producing fixed-rule/template suggestions keyed off each missing term and its lexicon metadata, at most `MATCHLAYER_MATCH_MAX_SUGGESTIONS`, ordered by descending missing-keyword weight
    - Each suggestion references exactly one missing keyword and phrases guidance as a user action; never fabricates experience/employers/dates; empty `missing` yields exactly one affirmative suggestion
    - _Requirements: 7.1, 7.2, 7.3, 7.5_

  - [x] 4.7 Write property test for suggestion boundedness and provenance
    - **Property 10: Suggestions are bounded and derived only from missing keywords**
    - **Validates: Requirements 7.1, 7.2, 7.5**

  - [x] 4.8 Write property test for the empty-missing affirmative case
    - **Property 11: Empty missing set yields exactly one affirmative suggestion**
    - **Validates: Requirements 7.3**

  - [x] 4.9 Write property test for suggestion determinism
    - **Property 12: Suggestion generation is deterministic**
    - **Validates: Requirements 7.4**

  - [x] 4.10 Implement the Match_Scorer
    - Implement `apps/api/src/matchlayer_api/scoring/scorer.py` (imports limited to scikit-learn and stdlib): normalize texts, compute the TF-IDF cosine similarity component, delegate to `Keyword_Analyzer` for the coverage component (`|matched| / |analyzed|`, 0 when analyzed is empty), combine with the configured weights into a clamped integer `0..100`, build the `ScoreResult` dataclass (`score`, `ScoreBreakdown`, `matched_keywords`, `missing_keywords`, `suggestions`, `scorer_version`), and stamp `Scorer_Version`
    - Empty resume or empty JD after normalization returns `score == 0` with both components `0` without raising
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8_

  - [x] 4.11 Write property test for score boundedness
    - **Property 1: Score is always a bounded integer**
    - **Validates: Requirements 5.1, 5.3**

  - [x] 4.12 Write property test for scoring determinism
    - **Property 2: Scoring is deterministic**
    - **Validates: Requirements 5.4, 5.7**

  - [x] 4.13 Write property test for breakdown consistency
    - **Property 3: Keyword-coverage equals the matched fraction and the breakdown is consistent**
    - **Validates: Requirements 5.2, 5.5**

  - [x] 4.14 Write property test for the empty-input zero score
    - **Property 4: Empty resume or empty job description scores zero without error**
    - **Validates: Requirements 5.6**

  - [x] 4.15 Write property test for descending-weight ordering
    - **Property 9: Keyword and suggestion lists are ordered by descending weight**
    - **Validates: Requirements 6.6, 7.2**

  - [x] 4.16 Write unit tests with eyeball example pairs
    - Cover a strong-match pair, a clear mismatch, and a keyword-stuffed adversarial pair (reuse `ml/evals/datasets/eyeball/`)
    - _Requirements: 5.1, 5.2, 5.3_

- [x] 5. ml/ adapter and import-boundary enforcement
  - [x] 5.1 Implement the scorer adapter
    - Implement `apps/api/src/matchlayer_api/ml/scorer_adapter.py` with a cached `get_scorer()` that constructs one `Match_Scorer` from the loaded `Skill_Lexicon` and the configured weights, and a `score(resume_text, job_description) -> ScoreResult` that only marshals inputs/outputs (no scoring arithmetic)
    - The adapter imports from `matchlayer_api.scoring`, never the reverse
    - _Requirements: 10.2_

  - [x] 5.2 Write import-boundary and adapter-delegation tests
    - Extend `apps/api/tests/unit/test_import_boundaries.py` to assert `matchlayer_api.scoring.*` imports only scikit-learn and stdlib and that the API never imports `ml/pipelines`
    - Assert the adapter delegates to the scorer without computing
    - _Requirements: 5.8, 10.1, 10.2, 10.3_

- [x] 6. Checkpoint - scoring core complete
  - Ensure all tests pass, ask the user if questions arise.

- [x] 7. Upload infrastructure: storage, MIME validation, and extraction
  - [x] 7.1 Implement Resume_Storage
    - Implement `apps/api/src/matchlayer_api/core/storage.py` wrapping a `boto3` S3 client built from the existing `Settings` S3 fields, with `put` (private ACL / no public-read) and `get` running via `run_in_threadpool`
    - The object key is `<uuidv7>.<ext>` (`ext ∈ {pdf, docx}`) and never incorporates any part of the client filename
    - _Requirements: 2.5, 2.10_

  - [x] 7.2 Implement the Mime_Validator
    - Implement `apps/api/src/matchlayer_api/core/mime.py` `detect(data: bytes) -> Literal["pdf","docx"] | None` using `filetype` magic-byte sniffing, ignoring the client `Content-Type` and extension; return `None` for anything other than PDF/DOCX
    - _Requirements: 2.3_

  - [x] 7.3 Write property test for magic-byte rejection
    - **Property 13: Non-PDF/DOCX bytes are rejected by magic-byte detection**
    - **Validates: Requirements 2.3**

  - [x] 7.4 Implement the Resume_Extractor
    - Implement `apps/api/src/matchlayer_api/services/extraction.py` `extract(data, kind) -> ExtractionOutcome` running `pypdf`/`python-docx` in a worker thread under `asyncio.wait_for(timeout=MATCHLAYER_RESUME_EXTRACTION_TIMEOUT_SECONDS)`, with a cooperative wall-clock check during page/paragraph iteration and truncation to `MATCHLAYER_RESUME_MAX_EXTRACTED_CHARS`
    - Add the DOCX zip-bomb guard (uncompressed size and entry-count limits via stdlib `zipfile`, before extraction)
    - Whitespace-only/empty/timeout/parser-error result → `status="failed"`, text null, `failure_category` set; never raises into the request path solely due to extraction failure
    - _Requirements: 2.4, 3.1, 3.2, 3.3, 3.4, 3.5, 3.7_

  - [x] 7.5 Write property test for extraction truncation and counting
    - **Property 15: Extracted text is truncated and counted consistently**
    - **Validates: Requirements 3.3**

  - [x] 7.6 Write unit tests for extraction failure categories
    - Cover `extraction_timeout`, `corrupt_document`, and `empty_text`; assert failure logs name the category and the resume id only, never bytes or text
    - _Requirements: 3.5, 3.7_

- [x] 8. Audit_Service event types
  - [x] 8.1 Add the five new audit event types
    - Add `resume_uploaded {resume_id}`, `resume_deleted {resume_id}`, `match_created {resume_id, match_id}`, `match_deleted {match_id}`, and `quota_rejected {quota}` to `apps/api/src/matchlayer_api/services/audit.py` as `TypedDict` payloads with matching `emit` overloads; the `user_id` principal is passed via the existing `user_id=` argument, not the payload
    - _Requirements: 2.7, 4.5, 8.6, 9.4, 11.6_

  - [x] 8.2 Write unit tests asserting audit payloads carry no PII
    - Assert every new payload contains internal IDs only (no filename, resume text, or JD text)
    - _Requirements: 2.7, 4.5, 8.6, 9.4, 11.6_

- [x] 9. Per-user rate limiting and idempotency primitives
  - [x] 9.1 Implement the rate-limit dependency and idempotency helpers
    - Add `user_rate_limit(endpoint: Literal["resume","match"])` to `apps/api/src/matchlayer_api/core/dependencies.py` that depends on `get_current_user`, calls the existing `RateLimiter` with key `rl:{endpoint}:user:{user_id}` (60s window, limit from settings), and raises the existing `RateLimited` (→ 429 + `Retry-After`) or `RateLimiterUnavailableError` (→ 503, fail-closed)
    - Add Redis-backed idempotency helpers storing `idem:{user_id}:{route}:{key}` with a 24h TTL holding the created resource id and serialized 201 response, returning the stored response on replay
    - _Requirements: 11.1, 11.2, 11.3, 11.7, 2.8, 8.9_

  - [x] 9.2 Write tests for the rate-limit decision and idempotency replay
    - Assert limit-exceeded → 429 `rate_limited` with `Retry-After`, Redis-down → 503 `rate_limiter_unavailable`, and that a stored idempotency key returns the original response
    - _Requirements: 11.1, 11.2, 11.3, 11.7, 2.8, 8.9_

- [x] 10. Resume API surface
  - [x] 10.1 Implement the resume Pydantic schemas
    - Add `ResumeResponse` (exactly `{id, original_filename, content_type, byte_size, extraction_status, created_at, updated_at}`, excluding `extracted_text`/`storage_key`) and `ResumeListResponse` (`items`, `next_cursor`) to `apps/api/src/matchlayer_api/api/resumes/schemas.py`
    - _Requirements: 2.9, 4.2_

  - [x] 10.2 Write property test for safe resume response shape
    - **Property 17: Resume response never exposes sensitive fields**
    - **Validates: Requirements 2.9, 4.2**

  - [x] 10.3 Implement the Resume_Service
    - Implement `apps/api/src/matchlayer_api/services/resumes.py` as the only module reading/writing `resumes`, with every query scoped `WHERE user_id = :current_user`
    - `create_resume`: enforce `Upload_Quota` (Postgres count for the UTC day → 429 `quota_exceeded` + audit `quota_rejected`) → `Mime_Validator.detect` (415 on `None`) → DOCX zip-bomb guard (422 `malformed_upload`) → `Resume_Storage.put` under a UUIDv7 key → INSERT row (`extraction_status='pending'`) → `Resume_Extractor.extract` → UPDATE extraction columns → emit `resume_uploaded`
    - Add `list_resumes` (cursor pagination, `created_at` desc), `get_resume` (raises `NotFoundError` for missing/deleted/other-owner), and idempotent `soft_delete_resume` (sets `deleted_at`, emits `resume_deleted` only on first delete); retain stored bytes and `extracted_text` after soft delete
    - Never log `original_filename`, file bytes, or `extracted_text`, and never place them in audit payloads
    - _Requirements: 1.4, 2.5, 2.6, 2.7, 3.4, 3.5, 3.6, 4.1, 4.5, 4.6, 4.7, 11.4, 11.6_

  - [x] 10.4 Write property test for filename-free storage keys
    - **Property 14: Storage keys never incorporate the client filename**
    - **Validates: Requirements 2.5**

  - [x] 10.5 Write property test for fail-soft extraction
    - **Property 16: Extraction fails soft**
    - **Validates: Requirements 3.5**

  - [x] 10.6 Implement the Resumes_Router
    - Implement `apps/api/src/matchlayer_api/api/resumes/router.py` with `POST /api/v1/resumes` (multipart `file`; 413 `payload_too_large` on declared length over `MATCHLAYER_RESUME_MAX_BYTES`, checked pre-service; honors `Idempotency-Key`; 201 with the safe field set), `GET /api/v1/resumes` (cursor list; `limit` 1–100 else 422 `validation_error`), `GET /api/v1/resumes/{id}` (404 `not_found` if missing/deleted/other-owner), and `DELETE /api/v1/resumes/{id}` (204, idempotent)
    - Every route depends on `get_current_user` and the `user_rate_limit("resume")` dependency
    - _Requirements: 1.5, 1.6, 2.1, 2.2, 2.8, 2.9, 4.1, 4.3, 4.4, 4.5, 11.1, 11.3_

  - [x] 10.7 Write integration tests for the resume endpoints
    - Cover auth gating (1.1–1.3, 1.5, 1.6), upload happy path plus 413/415/422 (2.1–2.4, 2.7), list/get/delete (4.4, 4.5, 4.7), the MinIO no-public-read guarantee (2.10), and the "never logged" PII negatives using the log-capture pattern (2.6, 3.6, 3.7)
    - _Requirements: 1.1, 1.2, 1.3, 2.1, 2.2, 2.3, 2.4, 2.6, 2.7, 2.10, 3.6, 3.7, 4.4, 4.5, 4.7_

- [x] 11. Match API surface
  - [x] 11.1 Implement the match Pydantic schemas
    - Add `CreateMatchRequest` (`resume_id`, `job_description` with a field validator enforcing trimmed length in `MATCHLAYER_JD_MIN_CHARS`..`MATCHLAYER_JD_MAX_CHARS` → 422 `validation_error`), `KeywordOut`, `SuggestionOut`, `ScoreBreakdownOut`, `MatchResponse` (full field set), `MatchListItem` (`{id, resume_id, score, created_at}`, omitting `job_description_text`), and `MatchListResponse` to `apps/api/src/matchlayer_api/api/matches/schemas.py`
    - _Requirements: 8.1, 8.2, 8.3, 8.7, 9.2_

  - [x] 11.2 Write property test for the match response shape
    - **Property 18: Match creation response carries the full field set**
    - **Validates: Requirements 8.7, 9.2**

  - [x] 11.3 Implement the Scoring_Service
    - Implement `apps/api/src/matchlayer_api/services/matching.py` as the only module reading/writing `match_results`, scoped by `user_id`
    - `create_match`: enforce `Scoring_Quota` (Postgres count, UTC day → 429 `quota_exceeded` + audit `quota_rejected`) → load owned, non-deleted `Resume` (404 `not_found` else) → require `extraction_status == 'succeeded'` (422 `resume_not_extractable` else) → call `ml.scorer_adapter.score(...)` → INSERT `match_results` (storing `job_description_text` Restricted) → emit `match_created`
    - Add `list_matches` (cursor, `created_at` desc), `get_match` (returns even if the resume was later soft-deleted), and idempotent `soft_delete_match` (emits `match_deleted` only on first delete); never log `job_description_text`
    - _Requirements: 1.4, 8.4, 8.5, 8.6, 8.8, 9.1, 9.3, 9.4, 9.5, 9.6, 11.5, 11.6_

  - [x] 11.4 Implement the Matches_Router
    - Implement `apps/api/src/matchlayer_api/api/matches/router.py` with `POST /api/v1/matches` (JSON `{resume_id, job_description}`; Pydantic + JD length → 422; honors `Idempotency-Key`; 201 full field set), `GET /api/v1/matches` (cursor list, items omit `job_description_text`), `GET /api/v1/matches/{id}` (404 on missing/deleted/other-owner), and `DELETE /api/v1/matches/{id}` (204, idempotent)
    - Every route depends on `get_current_user` and `user_rate_limit("match")`
    - _Requirements: 8.1, 8.2, 8.3, 8.7, 8.9, 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 11.2, 11.3_

  - [x] 11.5 Write integration tests for the match endpoints
    - Cover match creation plus 404/422 paths (8.1–8.6), list/get/delete including the retained-after-resume-deletion case (9.3, 9.4, 9.6), and the JD "never logged" negative (8.8)
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.8, 9.3, 9.4, 9.6_

- [x] 12. Application wiring and cross-cutting non-indexing controls
  - [x] 12.1 Implement the ApiNoIndexMiddleware
    - Add `ApiNoIndexMiddleware` to `apps/api/src/matchlayer_api/core/middleware.py` setting `X-Robots-Tag: noindex, nofollow` on every response whose path starts with `/api/v1/`, for all status codes including RFC 7807 error responses
    - _Requirements: 15.3_

  - [x] 12.2 Wire the routers and middleware into the app factory
    - In `apps/api/src/matchlayer_api/main.py`, `include_router` for the resumes and matches routers and register `ApiNoIndexMiddleware` so it wraps the exception-handling layer (header survives 4xx/5xx)
    - _Requirements: 2.1, 8.1, 15.3_

  - [x] 12.3 Write property test for the non-indexing header
    - **Property 23: Every API response is marked non-indexable**
    - **Validates: Requirements 15.3**

  - [x] 12.4 Write property test for per-user scoping
    - **Property 19: Per-user scoping never leaks another user's rows**
    - **Validates: Requirements 1.4**

  - [x] 12.5 Write property test for keyed-mutation idempotency
    - **Property 20: Keyed mutations are idempotent**
    - **Validates: Requirements 2.8, 8.9**

  - [x] 12.6 Write property test for soft-delete idempotency
    - **Property 21: Soft deletion is idempotent**
    - **Validates: Requirements 4.6, 9.5**

  - [x] 12.7 Write property test for ordered, complete pagination
    - **Property 22: Listing is correctly ordered and paginates completely**
    - **Validates: Requirements 4.1, 9.1**

  - [x] 12.8 Write integration tests for rate-limit and quota envelopes
    - Cover per-minute rate limiting (429 `rate_limited` + `Retry-After`), daily quotas (429 `quota_exceeded` with limit + UTC reset + audit `quota_rejected`), and the Redis fail-closed 503
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5, 11.6, 11.7_

- [x] 13. OpenAPI codegen for the new endpoints
  - [x] 13.1 Regenerate shared types and curated re-exports
    - Run `pnpm codegen` so FastAPI's OpenAPI regenerates `packages/shared-types/src/api-types.ts` and `api-schemas.ts`, then add curated re-exports to `index.ts` (`ResumeResponse`, `ResumeListResponse`, `CreateMatchRequest`, `MatchResponse`, `MatchListResponse`, and their `*Schema` Zod objects)
    - Confirm the CI codegen drift check passes
    - _Requirements: 12.7, 13.1_

- [x] 14. Frontend upload surface
  - [x] 14.1 Implement the Upload_Page
    - Implement `apps/web/src/app/(app)/upload/page.tsx` (`'use client'`) inside the Authenticated_Shell: a `.pdf`/`.docx`-restricted file input and a labeled `<textarea>` for the JD (label associated via `id`/`for`), client-side pre-validation against `MATCHLAYER_RESUME_MAX_BYTES` and accepted types before issuing the request, form validation via the generated Zod schemas, RFC 7807 `detail` errors announced through an `aria-live="polite"` region, design-token styling passing WCAG AA in both themes, upload via `FormData` then `POST /api/v1/matches` and navigation to the Results_Page
    - Use the generated client/types from `@matchlayer/shared-types`; do not hand-write request/response types
    - _Requirements: 12.1, 12.2, 12.3, 12.4, 12.5, 12.6, 12.7_

  - [x] 14.2 Write Vitest/Testing Library tests for the Upload_Page
    - Assert the accept attribute, label association, client pre-validation blocking oversized/wrong-type files, and `aria-live` rendering of RFC 7807 `detail` for 413/415/422/429
    - _Requirements: 12.2, 12.3, 12.4, 12.5_

- [x] 15. Frontend results and library surfaces
  - [x] 15.1 Implement the Results_Page
    - Implement `apps/web/src/app/(app)/matches/[id]/page.tsx` fetching via `GET /api/v1/matches/{id}` with the generated client: animated count-up to the score with the violet→cyan gradient on the score number, matched terms in the `success` token family and missing terms in `warning`, suggestions as a readable list, a visible `score_breakdown` (similarity, coverage, weights), `prefers-reduced-motion` showing resolved values with animation disabled, a friendly not-found state on 404, and all match-derived content rendered as plain text (never `dangerouslySetInnerHTML`)
    - _Requirements: 13.1, 13.2, 13.3, 13.4, 13.5, 13.6, 13.7_

  - [x] 15.2 Write Vitest/Testing Library tests for the Results_Page
    - Assert score render, matched/missing token families, breakdown display, reduced-motion resolved state via a `matchMedia` mock, the friendly 404, and that no match content uses `dangerouslySetInnerHTML`
    - _Requirements: 13.2, 13.3, 13.4, 13.5, 13.6, 13.7_

  - [x] 15.3 Implement the Library_View
    - Implement `apps/web/src/app/(app)/library/page.tsx` listing the user's resumes and recent matches within the Authenticated_Shell, each match linking to its Results_Page, passing WCAG AA contrast in both themes, using the generated client/types
    - _Requirements: 13.8_

  - [x] 15.4 Write Vitest/Testing Library tests for the Library_View
    - Assert the resume/match lists render and each match links to its Results_Page
    - _Requirements: 13.8_

- [x] 16. Frontend non-indexing controls
  - [x] 16.1 Add robots metadata to the Authenticated_Shell layout
    - In `apps/web/src/app/(app)/layout.tsx`, export `metadata` with `robots: { index: false, follow: false }` so all nested authenticated routes inherit `noindex, nofollow`; add no sitemap/canonical/OG metadata to any `(app)` route
    - _Requirements: 15.1, 15.2_

  - [x] 16.2 Create the robots route
    - Create `apps/web/src/app/robots.ts` (`MetadataRoute.Robots`) disallowing `/api/`, `/upload`, `/matches`, `/library`, and `/dashboard`
    - _Requirements: 15.4_

  - [x] 16.3 Write Vitest tests for the non-indexing controls
    - Assert the `(app)` layout exports `robots: {index:false, follow:false}`, that `app/robots.ts` disallows the required paths, and add a guard asserting any future `app/sitemap.ts` excludes `(app)` routes
    - _Requirements: 15.2, 15.4, 15.5_

- [x] 17. README runbook
  - [x] 17.1 Update the root README runbook
    - Add a runbook step to `README.md` documenting how to create the resume MinIO bucket named by `MATCHLAYER_S3_BUCKET` for local dev (the bucket-provisioning step deferred by `phase-1-foundation`), an end-to-end upload-and-match walkthrough, and how to adjust the per-user daily quotas via environment variables
    - _Requirements: 14.8_

- [x] 18. Final checkpoint - full suite green
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- All tasks in this plan are required (the previously optional `*` test sub-tasks have been promoted to required).
- Each task references specific granular requirement clauses for traceability.
- Property tests use Hypothesis (≥100 examples each), live under `apps/api/tests/property/`, and are each tagged with the feature name and the property number/text from the design.
- Requirements 1.1–1.3 are satisfied with no code change by the existing `get_current_user` dependency (per the design); they are exercised by the integration tests in task 10.7.
- Per the design's Testing Strategy, UI rendering/contrast, migration/drift/import-boundary, the "never logged" PII negatives, and infrastructure config (no-public-read, Redis fail-closed) are covered by component/integration/structural tests rather than property tests.
- Checkpoints (tasks 6, 18) ensure incremental validation.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2", "1.3", "1.4", "2.1", "3.1", "8.1"] },
    {
      "id": 1,
      "tasks": ["1.5", "2.2", "3.2", "7.1", "7.2", "8.2", "9.1", "12.1"]
    },
    { "id": 2, "tasks": ["2.3", "3.3", "4.1", "4.6", "7.3", "7.4", "9.2"] },
    {
      "id": 3,
      "tasks": [
        "4.2",
        "4.3",
        "4.4",
        "4.5",
        "4.7",
        "4.8",
        "4.9",
        "4.10",
        "7.5",
        "7.6"
      ]
    },
    {
      "id": 4,
      "tasks": ["4.11", "4.12", "4.13", "4.14", "4.15", "4.16", "5.1"]
    },
    { "id": 5, "tasks": ["5.2", "10.1", "11.1", "10.3", "11.3"] },
    { "id": 6, "tasks": ["10.2", "11.2", "10.4", "10.5", "10.6", "11.4"] },
    { "id": 7, "tasks": ["10.7", "11.5", "12.2"] },
    {
      "id": 8,
      "tasks": ["12.3", "12.4", "12.5", "12.6", "12.7", "12.8", "13.1"]
    },
    { "id": 9, "tasks": ["14.1", "15.1", "15.3", "16.1", "16.2", "17.1"] },
    { "id": 10, "tasks": ["14.2", "15.2", "15.4", "16.3"] }
  ]
}
```
