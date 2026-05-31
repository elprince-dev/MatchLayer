# Requirements Document

## Introduction

`phase-1-matching` is the third and final spec that completes Phase 1 of the MatchLayer roadmap. It builds directly on `phase-1-foundation` (the monorepo scaffold, FastAPI app factory, async SQLAlchemy session, structlog with PII redaction, request-id middleware, RFC 7807 error envelope, OpenAPI → TypeScript + Zod codegen pipeline, Next.js App Router scaffold with design tokens and the security-headers proxy, and the `docker-compose.yml` Postgres / Redis / MinIO stack) and on `phase-1-auth` (the `users` table, JWT auth via PyJWT HS256, refresh-token rotation, the append-only `audit_events` table, the Redis-backed `Rate_Limiter`, the `/api/v1/auth/me` endpoints, and the `useAuth` hook). This spec delivers the user-facing payoff of Phase 1: a logged-in user uploads a resume and supplies a job description, and the system returns a transparent match score, a keyword/skill overlap analysis, and rule-based improvement suggestions.

Per `product.md`, Phase 1 is the "MVP Foundation — naive ATS scoring (TF-IDF/keyword)". All matching logic in this spec is simple, deterministic, and non-LLM: scikit-learn TF-IDF cosine similarity plus keyword/skill overlap against a committed lexicon. This honors the "infrastructure before intelligence" product principle and the $20/month cost ceiling. There are no embeddings (Phase 2), no `sentence-transformers`, no spaCy, and no LLM or third-party AI API calls (Phase 3+).

This document does not re-state the cross-cutting baselines from `security.md`, `conventions.md`, `structure.md`, `design.md`, or `tech.md`; individual requirements reference the clauses they depend on so the contract stays traceable without duplication. Where this spec extends the scaffold or the auth surface, that extension is called out explicitly rather than restated.

Scope boundaries:

- **In scope:** the `resumes` and `match_results` tables and their first matching Alembic migration `0002_resumes_and_matches.py`; resume file upload (PDF and DOCX) to S3/MinIO with server-side magic-byte MIME validation, a hard size limit, UUID object keys, and the original filename retained as a display-only column; bounded, safe server-side text extraction from PDF and DOCX; job-description input as pasted text on the match request; the deterministic TF-IDF-plus-keyword `Match_Scorer` producing an explainable 0–100 score; keyword/skill overlap analysis (matched and missing terms) against a committed `Skill_Lexicon`; rule-based `Suggestion_Generator` output derived from missing terms; the API endpoints `POST /api/v1/resumes`, `GET /api/v1/resumes`, `GET /api/v1/resumes/{id}`, `DELETE /api/v1/resumes/{id}`, `POST /api/v1/matches`, `GET /api/v1/matches`, `GET /api/v1/matches/{id}`, and `DELETE /api/v1/matches/{id}`, all authenticated with the access token from `phase-1-auth`, rate-limited, quota-bounded, and returning RFC 7807 errors; per-user daily upload and scoring quotas (cost-as-DoS defense); the Next.js `/upload` and `/matches/[id]` (results) pages plus a resume-and-match library view inside the authenticated shell; the non-indexing guarantee that these authenticated, PII-bearing pages and all `/api/v1/*` responses are never crawled or indexed (`noindex, nofollow`, robots-disallowed, excluded from any sitemap), per `seo.md` and ADR 0006; OpenAPI codegen consuming the new endpoints; the `ml/`-versus-`apps/api` code separation for the scoring algorithm and lexicon; and updates to `.env.example`, the README runbook, and the MinIO bucket-provisioning step deferred by `phase-1-foundation`.
- **Out of scope:** semantic similarity, embeddings, and pgvector (Phase 2); `sentence-transformers` and spaCy-based skill extraction (Phase 2); any LLM-driven coaching, bullet rewriting, or natural-language suggestions (Phase 3+); asynchronous/queued or sandboxed-worker parsing via SQS (Phase 4 — Phase 1 parses in the request path under hard resource bounds); virus scanning via ClamAV (deferred per `security.md` to before the Phase 7 SaaS launch); resume versioning and diffing (Phase 7); recruiter-side or multi-resume-to-one-JD batch workflows (Phase 7); export of match results to PDF or other formats; **public-page search-engine optimization (marketing-page metadata, Open Graph/Twitter cards, canonical URLs, `sitemap.xml`/`robots.ts` generation, JSON-LD structured data, Core Web Vitals tuning) — owned by the separate `seo-foundation` spec per ADR 0006; this spec covers only the non-indexing guarantee for its own authenticated surfaces**; production object-storage encryption-with-CMK and S3 Block Public Access account settings (Phase 6 AWS migration — Phase 1 keeps the MinIO bucket private and non-public); and any paid third-party service.

## Glossary

- **Foundation_Repo** — The MatchLayer monorepo as defined in `phase-1-foundation`, including `apps/`, `packages/`, `ml/`, `infra/`, `docs/`, and the root configuration files.
- **API_App** — The FastAPI application at `apps/api/` exposing the Python package `matchlayer_api`, as established in `phase-1-foundation`.
- **Web_App** — The Next.js application at `apps/web/`, as established in `phase-1-foundation`.
- **User_Account** — A row in the `users` table as defined in `phase-1-auth`; the authenticated principal that owns every Resume and Match_Result created by this spec.
- **Access_Token** — The short-lived JWT with `type="access"` issued by the `phase-1-auth` JWT_Service and presented via the `Authorization: Bearer <token>` request header.
- **Resumes_Router** — The FastAPI router(s) under `apps/api/src/matchlayer_api/api/resumes/` that expose the resume HTTP surface and delegate business logic to the Resume_Service.
- **Matches_Router** — The FastAPI router(s) under `apps/api/src/matchlayer_api/api/matches/` that expose the match HTTP surface and delegate business logic to the Scoring_Service.
- **Resume_Service** — The Python module at `apps/api/src/matchlayer_api/services/resumes.py` that owns resume upload, validation orchestration, extraction orchestration, retrieval, and soft-deletion. The Resume_Service is the only module permitted to read or write the `resumes` table.
- **Scoring_Service** — The Python module at `apps/api/src/matchlayer_api/services/matching.py` that orchestrates match creation: loading a Resume, invoking the Match_Scorer, and persisting a Match_Result. The Scoring_Service is the only module permitted to read or write the `match_results` table.
- **Resume** — A row in the `resumes` table representing one uploaded resume file owned by a User_Account. Stores `id` (UUIDv7), `user_id`, `original_filename` (display-only), `storage_key`, `content_type`, `byte_size`, `extracted_text`, `extraction_status`, `extraction_char_count`, `created_at`, `updated_at`, and `deleted_at`.
- **Match_Result** — A row in the `match_results` table representing one scoring of one Resume against one Job_Description, owned by a User_Account. Stores `id` (UUIDv7), `user_id`, `resume_id`, `job_description_text`, `score`, `score_breakdown`, `matched_keywords`, `missing_keywords`, `suggestions`, `scorer_version`, `created_at`, `updated_at`, and `deleted_at`.
- **Job_Description** — The plain-text job description supplied by the user in the body of a `POST /api/v1/matches` request. Stored on the Match_Result as `job_description_text` and classified as Restricted per `security.md` (it may contain identifying or sensitive content).
- **Resume_Storage** — The object-storage abstraction at `apps/api/src/matchlayer_api/core/storage.py` that wraps the S3-compatible client (MinIO in local development, AWS S3 in production) and is the only module permitted to read or write resume file bytes.
- **Mime_Validator** — The component that determines a file's true media type from its leading bytes (magic-byte sniffing via `filetype` or `python-magic`), independent of the client-supplied `Content-Type` header.
- **Resume_Extractor** — The component that converts an uploaded PDF or DOCX file into plain UTF-8 text under hard resource bounds. Text extraction is a one-way transformation and is not a reversible parser; no pretty-printer or round-trip property applies.
- **Match_Scorer** — The framework-free, deterministic scoring library that, given resume text and Job_Description text, produces a 0–100 integer score, a score breakdown, the matched and missing keyword sets, and the inputs for the Suggestion_Generator. The Match_Scorer imports only scikit-learn and the Python standard library; it imports no FastAPI, SQLAlchemy, or other web/database modules.
- **Keyword_Analyzer** — The part of the Match_Scorer that derives the candidate keyword/skill set for a Job_Description from the Skill_Lexicon and TF-IDF term weighting, then partitions it into terms present in and terms absent from the resume text.
- **Suggestion_Generator** — The part of the Match_Scorer that produces rule-based, deterministic improvement suggestions from the missing keyword set. No LLM, embedding, or external service participates.
- **Skill_Lexicon** — The committed, versioned data artifact that enumerates the canonical skills and keywords (with normalization/alias rules) used by the Keyword_Analyzer. Distributed with the API package and derived only from non-LLM sources.
- **Scorer_Version** — A string identifier combining the Match_Scorer algorithm version and the Skill_Lexicon version, persisted on every Match_Result so a stored score is reproducible and auditable.
- **Upload_Quota** — The per-User_Account, per-calendar-day limit on successful resume uploads, enforced as a cost-as-DoS control per `security.md`.
- **Scoring_Quota** — The per-User_Account, per-calendar-day limit on Match_Result creations, enforced as a cost-as-DoS control per `security.md`.
- **Rate_Limiter** — The Redis-backed sliding-window rate-limit primitive at `apps/api/src/matchlayer_api/core/rate_limit.py` introduced in `phase-1-auth`, reused here for the resume and match endpoints.
- **Audit_Event** — A row in the append-only `audit_events` table introduced in `phase-1-auth`. This spec adds the event types `resume_uploaded`, `resume_deleted`, `match_created`, `match_deleted`, and `quota_rejected`.
- **Upload_Page** — The Next.js App Router page at `apps/web/src/app/(app)/upload/` for selecting a resume file and entering a Job_Description.
- **Results_Page** — The Next.js App Router page at `apps/web/src/app/(app)/matches/[id]/` that renders a Match_Result as the Phase 1 "demo moment" per `design.md`.
- **Library_View** — The authenticated Next.js view listing a User_Account's resumes and recent Match_Results.
- **Authenticated_Shell** — The Next.js `(app)` route-group layout from `phase-1-auth` that gates authenticated routes and redirects unauthenticated visitors to `/login`.
- **Indexing_Policy** — The non-indexing rule from `seo.md` and ADR 0006 that classifies every route Public or Authenticated and requires that all Authenticated routes and `/api/v1/*` responses are excluded from search-engine crawling and indexing. For this spec the Upload_Page, the Results_Page, and the Library_View are Authenticated, and every endpoint defined here is API-classified.
- **PII** — Personally identifiable information as classified in `security.md`. For this spec the directly-handled PII includes the uploaded file bytes, the extracted resume text, the original filename, and the Job_Description text, all classified Restricted.

## Requirements

### Requirement 1: Authentication, Authorization, and Per-User Scoping

**User Story:** As a registered user, I want every resume and match operation to require my session and to expose only my own data, so that no one else can read, score against, or delete my resumes.

#### Acceptance Criteria

1. THE Resumes_Router and THE Matches_Router SHALL require a valid Access_Token presented via the `Authorization: Bearer <token>` request header on every endpoint defined by this specification.
2. WHEN any endpoint defined by this specification is invoked without an `Authorization` header, with a scheme other than `Bearer`, or with a token whose JWT signature does not validate or whose `type` claim is not `access`, THE invoked router SHALL return HTTP 401 with the RFC 7807 envelope whose `type` is `unauthenticated`.
3. WHEN an endpoint defined by this specification is invoked with a valid Access_Token whose `sub` claim resolves to a User_Account whose `deleted_at` is non-null, THE invoked router SHALL return HTTP 401 with the RFC 7807 envelope whose `type` is `unauthenticated`.
4. THE Resume_Service and THE Scoring_Service SHALL scope every read and write to the `user_id` derived from the Access_Token's `sub` claim, so query results never include rows owned by a different User_Account.
5. WHEN a `GET` or `DELETE` request targets a `resumes/{id}` or `matches/{id}` resource whose row exists but whose `user_id` does not equal the requesting User_Account's `id`, THE invoked router SHALL return HTTP 404 with the RFC 7807 envelope whose `type` is `not_found`, so the existence of another User_Account's resource is not disclosed.
6. WHEN a `GET` or `DELETE` request targets a `resumes/{id}` or `matches/{id}` resource whose row does not exist or whose `deleted_at` is non-null, THE invoked router SHALL return HTTP 404 with the RFC 7807 envelope whose `type` is `not_found`.

### Requirement 2: Resume Upload and Validation

**User Story:** As a user, I want to upload my resume as a PDF or DOCX file and have it stored safely, so that I can score it against job descriptions.

#### Acceptance Criteria

1. THE Resumes_Router SHALL expose `POST /api/v1/resumes` accepting a `multipart/form-data` request body containing a single file part named `file`.
2. WHEN `POST /api/v1/resumes` is invoked with a request body whose declared length exceeds `MATCHLAYER_RESUME_MAX_BYTES` (default 5242880, i.e. 5 MiB), THE Resumes_Router SHALL reject the request with HTTP 413 and the RFC 7807 envelope whose `type` is `payload_too_large` and SHALL NOT persist a Resume or write any object to Resume_Storage.
3. WHEN `POST /api/v1/resumes` receives a file whose true media type, determined by the Mime_Validator from the file's leading bytes, is neither `application/pdf` nor `application/vnd.openxmlformats-officedocument.wordprocessingml.document`, THE Resume_Service SHALL reject the request with HTTP 415 and the RFC 7807 envelope whose `type` is `unsupported_media_type` and SHALL NOT persist a Resume or write any object to Resume_Storage, regardless of the client-supplied `Content-Type` header or file extension.
4. WHEN `POST /api/v1/resumes` receives a file whose true media type is `application/vnd.openxmlformats-officedocument.wordprocessingml.document` (a ZIP container) whose total uncompressed size would exceed `MATCHLAYER_RESUME_MAX_DECOMPRESSED_BYTES` (default 52428800, i.e. 50 MiB) or whose entry count exceeds `MATCHLAYER_RESUME_MAX_ARCHIVE_ENTRIES` (default 256), THE Resume_Service SHALL reject the request with HTTP 422 and the RFC 7807 envelope whose `type` is `malformed_upload` and SHALL NOT persist a Resume, so a decompression-bomb upload is refused per the `security.md` file-upload threat model.
5. WHEN `POST /api/v1/resumes` receives a file that passes the Mime_Validator and size checks, THE Resume_Service SHALL generate a UUIDv7, derive the object key `<uuidv7>.<ext>` where `<ext>` is `pdf` or `docx` according to the validated media type, and write the file bytes to Resume_Storage under that key, and SHALL NEVER incorporate any part of the client-supplied filename into the object key or any filesystem path.
6. THE Resume_Service SHALL persist the client-supplied filename verbatim in the `resumes.original_filename` column for display purposes only, and SHALL treat `original_filename` as Restricted PII that is never written to a log line, an error message, or an Audit_Event payload.
7. WHEN a resume upload completes object storage and validation, THE Resume_Service SHALL insert a Resume row recording `user_id`, `original_filename`, `storage_key`, the validated `content_type`, `byte_size`, and an initial `extraction_status`, and SHALL emit an Audit_Event of type `resume_uploaded` referencing the User_Account's `id` and the new Resume's `id` and containing no PII.
8. WHEN `POST /api/v1/resumes` carries an `Idempotency-Key` request header that matches a key persisted within the preceding 24 hours for the same User_Account, THE Resumes_Router SHALL return the original response for that key without writing a second object to Resume_Storage or inserting a second Resume row, per the `security.md` idempotency rule for upload endpoints.
9. WHEN a resume upload succeeds, THE Resumes_Router SHALL return HTTP 201 with a body containing the Resume fields `{id, original_filename, content_type, byte_size, extraction_status, created_at, updated_at}` and SHALL NOT include `extracted_text`, `storage_key`, or the raw file bytes in the response body.
10. THE Resume_Storage SHALL write resume objects with no public-read access so an uploaded resume is retrievable only through the authenticated API surface.

### Requirement 3: Resume Text Extraction

**User Story:** As a user, I want the system to read the text out of my uploaded PDF or DOCX, so that it can be analyzed against a job description.

#### Acceptance Criteria

1. WHEN a Resume is created per Requirement 2, THE Resume_Extractor SHALL extract plain UTF-8 text from the stored file synchronously within the request path during Phase 1, under the resource bounds defined by acceptance criteria 2 through 5.
2. THE Resume_Extractor SHALL abort extraction and treat the result as a failure WHEN extraction exceeds `MATCHLAYER_RESUME_EXTRACTION_TIMEOUT_SECONDS` (default 15) of wall-clock time.
3. THE Resume_Extractor SHALL truncate extracted text to at most `MATCHLAYER_RESUME_MAX_EXTRACTED_CHARS` (default 200000) characters and SHALL record the retained character count in `resumes.extraction_char_count`.
4. WHEN extraction completes successfully and yields at least one non-whitespace character, THE Resume_Service SHALL store the extracted text in `resumes.extracted_text` and set `resumes.extraction_status` to `succeeded`.
5. IF extraction fails, times out, raises a parser error, or yields only whitespace, THEN THE Resume_Service SHALL set `resumes.extraction_status` to `failed`, leave `resumes.extracted_text` null, and SHALL NOT cause the `POST /api/v1/resumes` request to return a 5xx status solely because extraction failed.
6. THE Resume_Service SHALL treat `resumes.extracted_text` as Restricted PII and SHALL NEVER write its contents, or any substring of its contents, to a log line, an error message, an Audit_Event payload, or any telemetry signal that leaves the system.
7. WHEN extraction fails or times out, THE Resume_Service SHALL log a structured event that names the failure category (for example `extraction_timeout`, `corrupt_document`, or `empty_text`) and references the Resume's `id`, and SHALL NOT include the file bytes or any extracted text in that log event.

### Requirement 4: Resume Retrieval, Listing, and Deletion

**User Story:** As a user, I want to list, view, and delete my uploaded resumes, so that I control which resumes are available to score.

#### Acceptance Criteria

1. THE Resumes_Router SHALL expose `GET /api/v1/resumes` returning the requesting User_Account's resumes whose `deleted_at` is null, ordered by `created_at` descending, using cursor-based pagination with the query parameters `limit` and `cursor` per `conventions.md`.
2. THE `GET /api/v1/resumes` response items SHALL contain the fields `{id, original_filename, content_type, byte_size, extraction_status, created_at, updated_at}` and SHALL NOT contain `extracted_text` or `storage_key`.
3. WHEN `GET /api/v1/resumes` is invoked with a `limit` value that is non-numeric, less than 1, or greater than 100, THE Resumes_Router SHALL return HTTP 422 with the RFC 7807 envelope whose `type` is `validation_error`.
4. THE Resumes_Router SHALL expose `GET /api/v1/resumes/{id}` returning the single Resume identified by `{id}` and owned by the requesting User_Account, with the same field set defined in acceptance criterion 2.
5. THE Resumes_Router SHALL expose `DELETE /api/v1/resumes/{id}` that, for a Resume owned by the requesting User_Account whose `deleted_at` is null, sets `deleted_at` to the current time (soft delete per `conventions.md`), returns HTTP 204, and emits an Audit_Event of type `resume_deleted` referencing the User_Account's `id` and the Resume's `id`.
6. WHEN `DELETE /api/v1/resumes/{id}` targets a Resume whose `deleted_at` is already non-null, THE Resumes_Router SHALL return HTTP 204 without inserting a second `resume_deleted` Audit_Event, so deletion is idempotent.
7. THE Resume_Service SHALL retain the stored object in Resume_Storage and the `extracted_text` column after a soft delete during Phase 1; hard deletion of bytes and a purge job are deferred to Phase 7 per `security.md`.

### Requirement 5: Match Scoring Algorithm

**User Story:** As a user, I want a transparent 0–100 score that reflects how well my resume matches a job description, so that I can trust and act on the result.

#### Acceptance Criteria

1. THE Match_Scorer SHALL compute a similarity component using scikit-learn TF-IDF vectorization of the resume text and the Job_Description text followed by cosine similarity between the two resulting vectors.
2. THE Match_Scorer SHALL compute a keyword-coverage component equal to the fraction of the Job_Description's analyzed keyword set (per Requirement 6) that is present in the resume text.
3. THE Match_Scorer SHALL combine the similarity component and the keyword-coverage component into a final integer score in the inclusive range 0 to 100 using the fixed, documented weights `MATCHLAYER_SCORE_WEIGHT_SIMILARITY` (default 0.6) and `MATCHLAYER_SCORE_WEIGHT_KEYWORD` (default 0.4), which SHALL sum to 1.0.
4. THE Match_Scorer SHALL be deterministic: for identical resume text, identical Job_Description text, and an identical Scorer_Version, repeated invocations SHALL produce an identical score, an identical score breakdown, and identical matched and missing keyword sets.
5. THE Match_Scorer SHALL produce a `score_breakdown` object that reports at minimum the similarity component value, the keyword-coverage component value, the two weights applied, and the final score, so the score is explainable to the user without re-running the algorithm.
6. WHEN the resume text is empty or the Job_Description text is empty after normalization, THE Match_Scorer SHALL return a final score of 0 with a `score_breakdown` that records both component values as 0, rather than raising an error.
7. THE Match_Scorer SHALL stamp every result it produces with the current Scorer_Version, and THE Scoring_Service SHALL persist that Scorer_Version on the Match_Result.
8. THE Match_Scorer SHALL NOT call any LLM, embedding model, `sentence-transformers` model, spaCy pipeline, or external network service; its only third-party dependency for scoring SHALL be scikit-learn.

### Requirement 6: Keyword and Skill Overlap Analysis

**User Story:** As a user, I want to see which keywords and skills from the job description my resume already covers and which it is missing, so that I understand the gap concretely.

#### Acceptance Criteria

1. THE Keyword_Analyzer SHALL derive the Job_Description's analyzed keyword set from the union of (a) canonical skills and keywords from the Skill_Lexicon that appear in the Job_Description text and (b) the highest-weighted TF-IDF terms of the Job_Description, capped at `MATCHLAYER_MATCH_MAX_KEYWORDS` (default 50) terms.
2. THE Keyword_Analyzer SHALL normalize terms for comparison by case-folding and by applying the Skill_Lexicon's alias rules, so that a term and its lexicon-defined alias are treated as the same keyword.
3. THE Keyword_Analyzer SHALL partition the analyzed keyword set into a `matched_keywords` set containing every term present in the normalized resume text and a `missing_keywords` set containing every term absent from the normalized resume text.
4. THE `matched_keywords` set and the `missing_keywords` set SHALL be disjoint, and their union SHALL equal the analyzed keyword set.
5. THE Keyword_Analyzer SHALL ensure every term reported in `matched_keywords` is verifiably present in the normalized resume text.
6. THE Scoring_Service SHALL persist `matched_keywords` and `missing_keywords` on the Match_Result and return both in the match response body as ordered lists, ordered by descending keyword weight so the most important terms appear first.

### Requirement 7: Improvement Suggestions

**User Story:** As a user, I want concrete, rule-based suggestions for improving my resume against the job, so that I know what to change without waiting for an AI feature that does not exist yet in Phase 1.

#### Acceptance Criteria

1. THE Suggestion_Generator SHALL produce suggestions derived solely from the `missing_keywords` set and the Skill_Lexicon's metadata for those terms, using fixed rules and templates with no LLM, embedding, or external service involvement.
2. THE Suggestion_Generator SHALL produce at most `MATCHLAYER_MATCH_MAX_SUGGESTIONS` (default 10) suggestions, ordered by descending weight of the missing keyword each suggestion addresses.
3. WHEN the `missing_keywords` set is empty, THE Suggestion_Generator SHALL produce a single affirmative suggestion indicating that the resume already covers the analyzed keywords, rather than an empty list with no explanation.
4. THE Suggestion_Generator SHALL be deterministic: identical `missing_keywords` input and an identical Scorer_Version SHALL produce an identical ordered suggestion list.
5. THE Suggestion_Generator SHALL NOT fabricate experience, employers, dates, or credentials in any suggestion; each suggestion SHALL reference a missing keyword and SHALL phrase guidance as an action for the user to take.
6. THE Scoring_Service SHALL persist the suggestions on the Match_Result and return them in the match response body.

### Requirement 8: Match Creation Endpoint

**User Story:** As a user, I want to submit one of my resumes together with a job description and receive a scored result, so that I get the match score, the keyword overlap, and the suggestions in one call.

#### Acceptance Criteria

1. THE Matches_Router SHALL expose `POST /api/v1/matches` accepting a JSON body with the fields `resume_id` (string) and `job_description` (string).
2. WHEN `POST /api/v1/matches` is invoked with a body that fails Pydantic validation, THE Matches_Router SHALL return HTTP 422 with the RFC 7807 envelope whose `type` is `validation_error` and SHALL NOT create a Match_Result.
3. WHEN `POST /api/v1/matches` is invoked with a `job_description` whose length after trimming is shorter than `MATCHLAYER_JD_MIN_CHARS` (default 30) or longer than `MATCHLAYER_JD_MAX_CHARS` (default 50000) characters, THE Matches_Router SHALL return HTTP 422 with the RFC 7807 envelope whose `type` is `validation_error` and SHALL NOT create a Match_Result.
4. WHEN `POST /api/v1/matches` is invoked with a `resume_id` that does not resolve to a Resume owned by the requesting User_Account with `deleted_at` null, THE Matches_Router SHALL return HTTP 404 with the RFC 7807 envelope whose `type` is `not_found`.
5. WHEN `POST /api/v1/matches` references a Resume whose `extraction_status` is not `succeeded`, THE Matches_Router SHALL return HTTP 422 with the RFC 7807 envelope whose `type` is `resume_not_extractable` and SHALL NOT create a Match_Result.
6. WHEN `POST /api/v1/matches` is invoked with a valid `resume_id` and a valid `job_description`, THE Scoring_Service SHALL load the Resume's `extracted_text`, invoke the Match_Scorer, insert a Match_Result row recording `user_id`, `resume_id`, `job_description_text`, `score`, `score_breakdown`, `matched_keywords`, `missing_keywords`, `suggestions`, and `scorer_version`, and emit an Audit_Event of type `match_created` referencing the User_Account's `id`, the Resume's `id`, and the Match_Result's `id` and containing no PII.
7. WHEN match creation succeeds, THE Matches_Router SHALL return HTTP 201 with a body containing the Match_Result fields `{id, resume_id, score, score_breakdown, matched_keywords, missing_keywords, suggestions, scorer_version, created_at, updated_at}`.
8. THE Matches_Router SHALL classify `job_description_text` as Restricted PII and SHALL NEVER include it, or any substring of it, in a log line, an error message, an Audit_Event payload, or any telemetry signal that leaves the system.
9. WHEN `POST /api/v1/matches` carries an `Idempotency-Key` request header that matches a key persisted within the preceding 24 hours for the same User_Account, THE Matches_Router SHALL return the original response for that key without inserting a second Match_Result, per the `security.md` idempotency rule.

### Requirement 9: Match Retrieval, Listing, and Deletion

**User Story:** As a user, I want to revisit my previous match results and delete ones I no longer need, so that I can compare attempts and keep my history tidy.

#### Acceptance Criteria

1. THE Matches_Router SHALL expose `GET /api/v1/matches` returning the requesting User_Account's Match_Results whose `deleted_at` is null, ordered by `created_at` descending, using cursor-based pagination with the query parameters `limit` and `cursor` per `conventions.md`.
2. THE `GET /api/v1/matches` response items SHALL contain at minimum `{id, resume_id, score, created_at}` and SHALL NOT contain `job_description_text`.
3. THE Matches_Router SHALL expose `GET /api/v1/matches/{id}` returning the single Match_Result identified by `{id}` and owned by the requesting User_Account, with the field set defined in Requirement 8 acceptance criterion 7.
4. THE Matches_Router SHALL expose `DELETE /api/v1/matches/{id}` that, for a Match_Result owned by the requesting User_Account whose `deleted_at` is null, sets `deleted_at` to the current time, returns HTTP 204, and emits an Audit_Event of type `match_deleted` referencing the User_Account's `id` and the Match_Result's `id`.
5. WHEN `DELETE /api/v1/matches/{id}` targets a Match_Result whose `deleted_at` is already non-null, THE Matches_Router SHALL return HTTP 204 without inserting a second `match_deleted` Audit_Event, so deletion is idempotent.
6. WHEN a `GET /api/v1/matches/{id}` request resolves to a Match_Result whose referenced Resume has since been soft-deleted, THE Matches_Router SHALL still return the Match_Result, because the score and analysis are retained independently of the Resume's lifecycle.

### Requirement 10: ML Code Organization and the Skill Lexicon

**User Story:** As the platform owner, I want the scoring algorithm and its data artifacts organized so that training and exploration code never bloats or couples to the API, so that the Phase 2 ML work lands cleanly.

#### Acceptance Criteria

1. THE Match_Scorer SHALL be implemented as a self-contained Python module whose imports are limited to scikit-learn and the Python standard library, with no imports of FastAPI, SQLAlchemy, the API configuration, or any other web or database module, so the scoring logic is unit-testable in isolation per `structure.md`.
2. THE API_App SHALL invoke the Match_Scorer through a thin adapter under `apps/api/src/matchlayer_api/ml/` that performs no scoring arithmetic of its own beyond marshalling inputs and outputs.
3. THE Skill_Lexicon SHALL be a committed, versioned data artifact whose source of truth lives under `ml/` per `structure.md`, and any script that derives or regenerates the Skill_Lexicon SHALL reside under `ml/pipelines/` and SHALL NOT be imported by the API_App at runtime.
4. THE Skill_Lexicon SHALL carry an explicit version identifier that contributes to the Scorer_Version, so a change to the lexicon is reflected in the `scorer_version` persisted on subsequently created Match_Results.
5. THE Match_Scorer and the Skill_Lexicon SHALL NOT introduce `sentence-transformers`, spaCy, an embedding model, or any LLM dependency into the API_App's dependency set during Phase 1, in keeping with `tech.md` and the `product.md` cost ceiling.

### Requirement 11: Rate Limiting and Cost-as-DoS Quotas

**User Story:** As the platform owner, I want upload and scoring traffic bounded per user, so that a single account cannot exhaust storage or compute and breach the cost ceiling.

#### Acceptance Criteria

1. THE Resumes_Router SHALL apply the Rate_Limiter to `POST /api/v1/resumes` keyed on the requesting User_Account's `id` with a default limit of `MATCHLAYER_RESUME_RATE_LIMIT_PER_MIN` (default 10) requests per 1-minute window.
2. THE Matches_Router SHALL apply the Rate_Limiter to `POST /api/v1/matches` keyed on the requesting User_Account's `id` with a default limit of `MATCHLAYER_MATCH_RATE_LIMIT_PER_MIN` (default 20) requests per 1-minute window.
3. WHEN the Rate_Limiter rejects a request, THE invoked router SHALL return HTTP 429 with the RFC 7807 envelope whose `type` is `rate_limited` and SHALL set the `Retry-After` response header to the integer number of seconds returned by the Rate_Limiter.
4. THE Resume_Service SHALL enforce the Upload_Quota of `MATCHLAYER_RESUME_DAILY_QUOTA` (default 20) successful uploads per User_Account per calendar day in UTC.
5. THE Scoring_Service SHALL enforce the Scoring_Quota of `MATCHLAYER_MATCH_DAILY_QUOTA` (default 50) successful Match_Result creations per User_Account per calendar day in UTC.
6. WHEN a request would exceed the Upload_Quota or the Scoring_Quota, THE invoked router SHALL return HTTP 429 with the RFC 7807 envelope whose `type` is `quota_exceeded` and a `detail` field that states the daily limit and the UTC time the quota resets, SHALL NOT perform the underlying upload or scoring, and SHALL emit an Audit_Event of type `quota_rejected` whose payload names the quota category (`upload` or `scoring`) and references the User_Account's `id`.
7. IF Redis is unreachable when the Rate_Limiter executes a check, THEN THE Rate_Limiter SHALL return a rejected decision (fail-closed) and THE invoked router SHALL return HTTP 503 with the RFC 7807 envelope whose `type` is `rate_limiter_unavailable`, consistent with the `phase-1-auth` Rate_Limiter contract.

### Requirement 12: Frontend Upload Surface

**User Story:** As a user, I want a calm, accessible page to upload my resume and paste a job description, so that starting a match is obvious and fast.

#### Acceptance Criteria

1. THE Web_App SHALL expose the Upload_Page within the Authenticated_Shell so an unauthenticated visitor is redirected to `/login` per the `phase-1-auth` Authenticated_Shell contract.
2. THE Upload_Page SHALL provide a file input that accepts only `.pdf` and `.docx` files and a multi-line text input for the Job_Description, each associated with a visible label via `id` and `for` attributes.
3. THE Upload_Page form SHALL validate inputs using the Zod schemas for `POST /api/v1/resumes` and `POST /api/v1/matches` imported from `@matchlayer/shared-types`, and SHALL announce validation and server errors via an `aria-live="polite"` region per `design.md` accessibility rules.
4. WHEN the user selects a file larger than `MATCHLAYER_RESUME_MAX_BYTES` or of an unaccepted type, THE Upload_Page SHALL display an inline error before submission and SHALL NOT issue the upload request.
5. WHEN an upload request returns HTTP 413, 415, 422, or 429, THE Upload_Page SHALL render the RFC 7807 `detail` in the error region without exposing any stack trace or internal identifier.
6. THE Upload_Page SHALL follow the `design.md` "app shell: calm" guidance, using design-system tokens rather than hard-coded colors, and SHALL pass WCAG AA color-contrast in both light and dark themes.
7. THE Upload_Page SHALL use the generated API client and types from `@matchlayer/shared-types` and SHALL NOT hand-write request or response types for the resume or match endpoints, per `conventions.md`.

### Requirement 13: Frontend Results Surface

**User Story:** As a user, I want the results page to reveal my score and skill overlap in a way that feels like the payoff, so that the Phase 1 demo lands.

#### Acceptance Criteria

1. THE Web_App SHALL expose the Results_Page at `/matches/[id]` within the Authenticated_Shell, fetching the Match_Result via `GET /api/v1/matches/{id}` using the generated client from `@matchlayer/shared-types`.
2. THE Results_Page SHALL render the final score as an animated count-up to the score value with the `design.md` signature violet→cyan gradient applied to the score number, per the `design.md` "results page: the demo moment" section.
3. THE Results_Page SHALL render the matched and missing keywords as two visually distinct groups, using the `success` token family for matched terms and the `warning` token family for missing terms, and SHALL render the improvement suggestions as a readable list.
4. THE Results_Page SHALL render the `score_breakdown` so the user can see the similarity component, the keyword-coverage component, and the applied weights that produced the final score.
5. WHILE the user has `prefers-reduced-motion` set, THE Results_Page SHALL present the final score and all content in their resolved state with animation disabled, per `design.md` motion rules.
6. WHEN `GET /api/v1/matches/{id}` returns HTTP 404, THE Results_Page SHALL render a friendly not-found state rather than a stack trace or a raw error object.
7. THE Results_Page SHALL render every suggestion and keyword as plain text, and SHALL NOT use `dangerouslySetInnerHTML` for any Match_Result-derived content.
8. THE Library_View SHALL list the User_Account's resumes and recent Match_Results within the Authenticated_Shell, link each Match_Result to its Results_Page, and pass WCAG AA color-contrast in both light and dark themes.

### Requirement 14: Persistence, Migrations, Storage Provisioning, and Environment Variable Contract

**User Story:** As a developer onboarding to the repo or returning after a break, I want one Alembic migration that creates the matching tables, a documented MinIO bucket step, and one set of `.env.example` entries for every new variable, so that the foundation contract continues to hold.

#### Acceptance Criteria

1. THE Foundation_Repo SHALL contain an Alembic revision file at `apps/api/alembic/versions/0002_resumes_and_matches.py` whose `down_revision` is the `phase-1-auth` revision `0001_users_and_auth`.
2. THE migration `0002_resumes_and_matches` SHALL create the `resumes` table with the columns required by Requirements 2 and 3 (including `id` UUIDv7 primary key, `user_id` foreign key to `users.id`, `original_filename`, `storage_key`, `content_type`, `byte_size`, `extracted_text`, `extraction_status`, `extraction_char_count`, `created_at`, `updated_at`, and `deleted_at`) and the `match_results` table with the columns required by Requirements 5 through 8 (including `id` UUIDv7 primary key, `user_id` foreign key to `users.id`, `resume_id` foreign key to `resumes.id`, `job_description_text`, `score`, `score_breakdown` JSONB, `matched_keywords` JSONB, `missing_keywords` JSONB, `suggestions` JSONB, `scorer_version`, `created_at`, `updated_at`, and `deleted_at`).
3. THE migration `0002_resumes_and_matches` SHALL create indexes on `resumes.user_id`, `match_results.user_id`, and `match_results.resume_id`, and SHALL document in the migration why each index exists per `conventions.md`.
4. THE migration `0002_resumes_and_matches` SHALL provide a working `downgrade()` that drops every table and index it created in reverse order.
5. THE `resumes` and `match_results` tables SHALL use plural snake_case names, UUIDv7 primary keys, and a nullable `deleted_at` soft-delete column, per `conventions.md`.
6. THE `.env.example` file SHALL gain entries for every new environment variable introduced by this spec, including at minimum `MATCHLAYER_RESUME_MAX_BYTES`, `MATCHLAYER_RESUME_MAX_DECOMPRESSED_BYTES`, `MATCHLAYER_RESUME_MAX_ARCHIVE_ENTRIES`, `MATCHLAYER_RESUME_EXTRACTION_TIMEOUT_SECONDS`, `MATCHLAYER_RESUME_MAX_EXTRACTED_CHARS`, `MATCHLAYER_JD_MIN_CHARS`, `MATCHLAYER_JD_MAX_CHARS`, `MATCHLAYER_MATCH_MAX_KEYWORDS`, `MATCHLAYER_MATCH_MAX_SUGGESTIONS`, `MATCHLAYER_SCORE_WEIGHT_SIMILARITY`, `MATCHLAYER_SCORE_WEIGHT_KEYWORD`, `MATCHLAYER_RESUME_RATE_LIMIT_PER_MIN`, `MATCHLAYER_MATCH_RATE_LIMIT_PER_MIN`, `MATCHLAYER_RESUME_DAILY_QUOTA`, and `MATCHLAYER_MATCH_DAILY_QUOTA`, each with a placeholder matching its documented default.
7. THE `.env.example` entries for object storage SHALL reuse the foundation S3 settings (`MATCHLAYER_S3_ENDPOINT_URL`, `MATCHLAYER_S3_REGION`, `MATCHLAYER_S3_ACCESS_KEY_ID`, `MATCHLAYER_S3_SECRET_ACCESS_KEY`, `MATCHLAYER_S3_BUCKET`) and SHALL NOT introduce a second, divergent set of credentials for resume storage.
8. THE root README SHALL gain a runbook step documenting how to create the resume MinIO bucket named by `MATCHLAYER_S3_BUCKET` for local development (the bucket-provisioning step deferred by `phase-1-foundation`), how to upload a resume and run a match end-to-end, and how the per-user daily quotas can be adjusted via environment variables.
9. THE CI `.env` drift check defined in `phase-1-foundation` SHALL pass against the updated `.env.example`, so every new variable referenced by the API_App or the Web_App has a corresponding documented entry and no documented entry is stale.

### Requirement 15: Non-Indexing of Authenticated, PII-Bearing Surfaces

**User Story:** As a user whose resume and job descriptions are private, I want the pages and API responses that contain my data to be impossible for search engines to crawl or index, so that my resume content can never surface in a public search result.

#### Acceptance Criteria

1. THE Web_App SHALL classify the Upload_Page, the Results_Page, and the Library_View as Authenticated routes per the Indexing_Policy, and SHALL NOT add any sitemap entry, canonical tag, Open Graph tag, or other discoverability metadata to any of them.
2. THE Authenticated_Shell layout SHALL export Next.js route metadata that sets `robots` to `{ index: false, follow: false }`, so every nested authenticated route — including the Upload_Page, the Results_Page, and the Library_View — inherits a `noindex, nofollow` directive.
3. THE Matches_Router, THE Resumes_Router, and every other endpoint defined by this specification SHALL set the response header `X-Robots-Tag: noindex, nofollow` on every response, regardless of status code.
4. WHERE the Foundation_Repo provides a `robots.txt` or generated `app/robots.ts`, the Web_App SHALL ensure it disallows `/api/` and the authenticated application paths (`/upload`, `/matches`, and the Library_View path); IF no robots resource yet exists in the Foundation_Repo, THEN this spec SHALL introduce one that encodes those disallow rules.
5. WHERE the Foundation_Repo provides a generated `sitemap.xml` or `app/sitemap.ts`, the Web_App SHALL ensure the Upload_Page, the Results_Page, and the Library_View are excluded from it; the absence of these routes from any sitemap SHALL hold even after future public-SEO work lands.
6. THE Results_Page SHALL NOT be made publicly shareable or indexable by this spec; any future public-shareable-result capability is out of scope and requires a separate ADR per `seo.md`, because it deliberately crosses the PII/indexing boundary and must strip PII first.
7. THE Web_App SHALL rely on the Indexing_Policy controls (`noindex` metadata, `X-Robots-Tag`, and `robots.txt` disallow) as defense in depth and SHALL NOT treat authentication gating alone as sufficient to keep PII surfaces out of search-engine indexes.
