# Requirements Document

## Introduction

`phase-2-nlp-embeddings` delivers Phase 2 of the MatchLayer roadmap: NLP & Embeddings. Phase 1 shipped a deterministic, non-LLM matching pipeline — TF-IDF cosine similarity plus keyword/skill overlap against a committed `Skill_Lexicon` — behind the `POST /api/v1/matches` surface, with the scoring core kept framework-free (`apps/api/src/matchlayer_api/scoring/`) and configured through the `ml/` adapter layer (`apps/api/src/matchlayer_api/ml/`). Phase 2 upgrades the intelligence of that pipeline without changing its shape: sentence-embedding semantic similarity replaces TF-IDF cosine as the similarity component, spaCy-based skill extraction structurally replaces the TF-IDF-plus-stopword-blocklist keyword derivation, and PostgreSQL gains the pgvector extension (ADR 0004) so resume and job-description embeddings are stored and reusable.

Phase 2 also fixes a known Phase 1 quality defect: the Phase 1 `Keyword_Analyzer` sources analyzed keywords from single-document TF-IDF filtered by a hand-curated stopword blocklist (`_JOB_POSTING_STOPWORDS` in `keyword_analyzer.py`), and generic non-skill words (for example "check", "selection") leak into the missing-keywords list. Phase 2's Skill_Extractor makes the analyzed set skill-only by construction: every analyzed, matched, and missing keyword must be a recognized skill, not a residual high-TF-IDF term. The blocklist approach is retired rather than extended.

Per ADR 0001 (phase gating), Phase 2 is deployable on its own and builds nothing for Phase 3+: no OpenAI or any paid/hosted LLM API, no LangGraph, no prompt infrastructure. Per the product cost ceiling, total monthly spend stays under $20 — the embedding model is an open-source Sentence Transformers model (`all-MiniLM-L6-v2` or `bge-small-en-v1.5`) running in-process on the Fly.io backend, so model size and memory footprint are first-class constraints. Existing architectural boundaries carry forward: the scoring core stays framework-free with configuration injected via constructors from the `ml/` adapter; ML pipeline/training code lives in top-level `ml/`; the API consumes artifacts through thin clients in `apps/api/src/matchlayer_api/ml/`.

Security baseline carries forward unchanged: resume text and job-description text are Restricted PII, never logged. Embeddings and extracted skills are derived from Restricted source text and are handled under the same access-control scoping (per-user, never cross-tenant), never logged as vector values, and never sent to any third-party service.

This document does not restate the cross-cutting baselines from `security.md`, `conventions.md`, `structure.md`, or `tech.md`; individual requirements reference the clauses they depend on.

Scope boundaries:

- **In scope:** enabling the pgvector extension on the development and production PostgreSQL databases with an Alembic migration; an `Embedding_Service` that produces sentence embeddings from resume text and job-description text using a committed open-source Sentence Transformers model; persistence of resume and match embeddings in pgvector columns/tables; a `Semantic_Scorer` component that computes semantic similarity from embeddings and replaces the TF-IDF similarity component inside the `Match_Scorer` combination; a spaCy-based `Skill_Extractor` that derives skills from job descriptions and resumes using the `Skill_Lexicon` (expanded as needed) plus spaCy linguistic features, structurally replacing the Phase 1 TF-IDF keyword derivation; an updated `Scorer_Version` scheme covering the model identifier so stored scores stay reproducible and auditable; updated rule-based suggestions driven by the skill-based missing set (still non-LLM); eyeball eval-dataset expansion (including a case that demonstrates the generic-term leak is fixed) and a lightweight eval runner comparing Phase 2 scoring against Phase 1 on the same pairs; degradation behavior when the model artifact is unavailable; deployment fit within Fly.io memory limits (with the documented Supabase/Neon Postgres fallback if Fly Postgres + pgvector is painful); configuration, `.env.example`, README runbook, and cost-log updates.
- **Out of scope:** any LLM API usage, resume coaching, or bullet rewriting (Phase 3); agent workflows (Phase 4); DeepEval golden/adversarial suites (Phase 5 — Phase 2 only expands `eyeball/`); asynchronous/queued scoring via SQS (Phase 4/6); dedicated vector databases (rejected by ADR 0004); GPU hosting or any paid inference service; frontend redesign of the results page beyond surfacing the updated breakdown labels; multi-resume or recruiter-side workflows (Phase 7); embedding-based resume search or cross-user similarity features (no cross-user vector queries in Phase 2).

## Glossary

- **API_App** — The FastAPI application at `apps/api/` exposing the Python package `matchlayer_api`, as established in Phase 1.
- **Scoring_Core** — The framework-free Python package `apps/api/src/matchlayer_api/scoring/` established by `phase-1-matching` Requirement 10.1: it imports no FastAPI, SQLAlchemy, `matchlayer_api.config`, or storage/web modules; all configuration is injected via constructors.
- **ML_Adapter** — The thin marshalling layer at `apps/api/src/matchlayer_api/ml/` (for example `scorer_adapter.py`) that reads settings, loads artifacts, and injects configuration into the Scoring_Core. The only sanctioned bridge between the framework world and the Scoring_Core.
- **ML_Workspace** — The top-level `ml/` directory holding pipelines, the lexicon artifact, and eval datasets; never imported by `apps/`.
- **Match_Scorer** — The Scoring_Core component from Phase 1 that combines a similarity component and a keyword-coverage component into a 0–100 integer score with an explainable breakdown. Phase 2 changes what feeds the two components, not the combination contract.
- **Embedding_Model** — The committed open-source Sentence Transformers model used for embedding generation: `all-MiniLM-L6-v2` or `bge-small-en-v1.5` (selection finalized in design). Identified by a pinned model name and revision.
- **Embedding_Service** — The Phase 2 component that produces a fixed-dimension embedding vector from input text using the Embedding_Model, running in-process in the API_App with no external network calls at inference time.
- **Embedding** — A fixed-dimension float vector produced by the Embedding_Service from resume text or job-description text. Derived from Restricted PII and handled under the same classification: access-scoped to the owning User_Account, never logged as values, never sent to a third-party service.
- **Semantic_Scorer** — The Scoring_Core component that computes the semantic similarity component of the match score from two Embeddings (cosine similarity), replacing the Phase 1 TF-IDF similarity component.
- **Skill_Extractor** — The Phase 2 component that extracts a set of skills from a text (job description or resume) using spaCy linguistic processing combined with the Skill_Lexicon. Its output is skill-only: every extracted term is a canonical Skill_Lexicon term.
- **Skill_Lexicon** — The committed, versioned skill vocabulary artifact (`ml/lexicon/skill_lexicon.v1.json` in Phase 1, versioned forward as it grows) defining canonical skills, aliases, and weights. The single source of truth for what counts as a skill.
- **Skill** — A canonical term from the Skill_Lexicon. In Phase 2, every analyzed, matched, and missing keyword on a Match_Result is a Skill.
- **Match_Result** — A row in the `match_results` table from Phase 1, storing `score`, `score_breakdown`, `matched_keywords`, `missing_keywords`, `suggestions`, and `scorer_version`. Phase 2 continues to populate these fields with upgraded content.
- **Scorer_Version** — The string identifier persisted on every Match_Result that makes a stored score reproducible. Phase 2 extends it to identify the scoring algorithm version, the Skill_Lexicon version, the Embedding_Model name and revision, and the spaCy pipeline name and version.
- **Vector_Store** — The pgvector-backed storage for Embeddings inside the existing PostgreSQL database (ADR 0004): the pgvector extension plus the columns/tables holding resume and match embeddings.
- **Resume** — A row in the `resumes` table from Phase 1, owned by a User_Account, holding `extracted_text` (Restricted PII).
- **Job_Description** — The plain-text job description supplied on a `POST /api/v1/matches` request, classified Restricted per `security.md`.
- **User_Account** — The authenticated principal from `phase-1-auth` that owns every Resume, Match_Result, and Embedding.
- **Scoring_Service** — The Phase 1 service module (`apps/api/src/matchlayer_api/services/matching.py`) that orchestrates match creation; the only module that reads/writes `match_results`.
- **Eyeball_Dataset** — The hand-curated resume+JD pairs in `ml/evals/datasets/eyeball/` used for sanity-checking scoring quality, per the dataset README schema.
- **Eval_Runner** — The Phase 2 script in the ML_Workspace that scores every Eyeball_Dataset pair with the current pipeline and reports scores and skill sets against each pair's expectations.
- **Degraded_Mode** — The API_App behavior when the Embedding_Model artifact cannot be loaded: scoring continues with the Phase 1 deterministic algorithm rather than failing.

## Requirements

### Requirement 1: pgvector Extension and Vector Storage

**User Story:** As an operator, I want vector storage inside the existing PostgreSQL database, so that embeddings persist without adding a new service (ADR 0004).

#### Acceptance Criteria

1. THE API_App SHALL provide an Alembic migration, reviewed like code per `conventions.md`, that enables the pgvector extension and creates the Vector_Store columns/tables for Resume Embeddings and Job_Description Embeddings (associated with `resumes` rows and `match_results` rows respectively).
2. WHEN the Alembic migration runs against a PostgreSQL instance that is at the Phase 1 migration head and where the pgvector extension is available, THE migration SHALL complete successfully without manual SQL steps outside Alembic, leaving the database at the new migration head.
3. IF the Alembic migration fails for a reason other than pgvector unavailability (for example insufficient database privileges or a schema conflict), THEN THE migration SHALL leave the database schema unchanged (no partially applied objects) and report the failure, so a failed migration is safely re-runnable after the cause is fixed.
4. THE local development docker-compose PostgreSQL image SHALL include the pgvector extension so the migration succeeds in local development.
5. IF the production PostgreSQL instance cannot enable the pgvector extension, THEN THE project documentation SHALL record the fallback decision to a managed Postgres provider with pgvector support (Supabase or Neon, per `tech.md`) before Phase 2 deployment proceeds.
6. THE Vector_Store SHALL store each Embedding with the dimension matching the Embedding_Model's output dimension, and THE Alembic migration SHALL declare that dimension explicitly; IF a write attempts to store an Embedding whose dimension differs from the declared dimension, THEN THE Vector_Store SHALL reject the write and persist nothing for that write.
7. WHEN the Embedding_Model is changed to a model with a different output dimension, THE project SHALL require a new reviewed Alembic migration authored for that change; THE API_App SHALL NOT automatically migrate, re-dimension, or re-generate stored Embeddings.
8. THE Vector_Store SHALL associate every stored Embedding with the owning User_Account's `id` and the source entity (`resumes` row or `match_results` row), so every read of an Embedding is scoped to its owner exactly as Resume and Match_Result reads are scoped in Phase 1; IF a write attempts to store an Embedding that does not reference an existing owning User_Account and source entity, THEN THE Vector_Store SHALL reject the write.
9. IF the Alembic migration runs against a PostgreSQL instance where the pgvector extension is not available, THEN THE migration SHALL fail with an error indicating pgvector unavailability and SHALL leave the database schema unchanged.

### Requirement 2: Embedding Generation

**User Story:** As a user, I want my resume and job descriptions understood semantically rather than by exact word overlap, so that the match score reflects meaning.

#### Acceptance Criteria

1. THE Embedding_Service SHALL produce a fixed-dimension float vector from an input text using the Embedding_Model, executing in-process with no network call to any external inference service.
2. THE Embedding_Model SHALL be an open-source Sentence Transformers model (`all-MiniLM-L6-v2` or `bge-small-en-v1.5`) pinned to a specific model name and revision recorded in configuration.
3. WHEN the input text exceeds the Embedding_Model's maximum sequence length, THE Embedding_Service SHALL apply a documented, deterministic chunking-and-aggregation strategy so the resulting Embedding represents the full document rather than only its truncated prefix, and the chunking process SHALL operate entirely in memory: no chunk text and no intermediate chunk vector SHALL be written to a log line, a temporary file surviving the request, or any telemetry signal.
4. THE Embedding_Service SHALL be deterministic: identical input text under an identical Embedding_Model name and revision SHALL produce an identical Embedding.
5. WHEN a Resume's text extraction succeeds, THE API_App SHALL generate and persist the Resume's Embedding in the Vector_Store so subsequent match requests against that Resume reuse the stored Embedding instead of re-embedding the resume text.
6. WHEN `POST /api/v1/matches` is invoked and the request is scored by the Phase 2 semantic pipeline (not in Degraded_Mode and not via a per-request fallback per Requirement 7), THE Scoring_Service SHALL generate the Job_Description's Embedding and persist it in the Vector_Store associated with the created Match_Result.
7. WHEN a match request references a Resume that has no stored Embedding (for example a resume uploaded before Phase 2 deployed), or whose stored Embedding records an Embedding_Model name or revision different from the currently configured Embedding_Model, THE Scoring_Service SHALL generate and persist the Resume's Embedding at match time rather than failing or reusing the stale Embedding.
8. THE Embedding_Service SHALL enforce a configurable wall-clock time bound `MATCHLAYER_EMBEDDING_TIMEOUT_SECONDS` (default 20) applied per input text end-to-end, including any chunking and aggregation per criterion 3, and IF the bound is exceeded for any input text of a match request, THEN THE Scoring_Service SHALL complete that request via the per-request fallback defined in Requirement 7 while the API_App remains in normal mode for other requests.
9. THE Embedding_Service SHALL treat input texts as Restricted PII per `security.md`: neither the input text nor the Embedding vector values SHALL appear in a log line, an error message, an Audit_Event payload, or any telemetry signal that leaves the system.
10. THE Vector_Store SHALL record, with each persisted Embedding, the Embedding_Model name and revision that produced it, so the reuse-versus-regenerate decision in criterion 7 is decidable from stored data.
11. IF embedding generation or Vector_Store persistence fails during resume upload processing, THEN THE API_App SHALL complete the resume upload and text-extraction flow successfully with the Resume stored without an Embedding, and the Resume's Embedding SHALL be generated at match time per criterion 7.
12. IF persisting a generated Embedding to the Vector_Store fails during match creation, THEN THE Scoring_Service SHALL complete the match request using the in-memory Embedding, and the persistence failure SHALL NOT cause the request to return a 5xx error.

### Requirement 3: Semantic Similarity Scoring

**User Story:** As a user, I want the similarity part of my match score to be based on semantic meaning, so that a resume saying "built REST services in Django" scores well against a JD asking for "web API development in Python" even without exact keyword overlap.

#### Acceptance Criteria

1. THE Semantic_Scorer SHALL compute the similarity component as the cosine similarity between the Resume's Embedding and the Job_Description's Embedding, mapped to the inclusive range 0 to 1 by a deterministic transformation that is monotonically non-decreasing over the full cosine range -1 to 1 and is documented in the repository, so any two testers applying the documented transformation to the same cosine value obtain the same component value.
2. THE Match_Scorer SHALL combine the semantic similarity component and the skill-coverage component (per Requirement 4) into a final integer score in the inclusive range 0 to 100 using the weights `MATCHLAYER_SCORE_WEIGHT_SIMILARITY` (default 0.6, preserving the Phase 1 default) and `MATCHLAYER_SCORE_WEIGHT_KEYWORD` (default 0.4, preserving the Phase 1 default), applying a documented deterministic rounding rule to produce the integer, preserving the Phase 1 combination contract.
3. THE Match_Scorer SHALL produce a `score_breakdown` object that reports at minimum the semantic similarity component value and the skill-coverage component value as pre-weighting values in the inclusive range 0 to 1, the two weights applied, the final score, and an identifier distinguishing the semantic similarity method from the Phase 1 TF-IDF method, so a stored breakdown is unambiguous about which algorithm produced it and the weighted combination is reproducible from the breakdown alone.
4. THE Semantic_Scorer SHALL be deterministic: identical Embeddings SHALL produce an identical similarity component value.
5. WHEN the resume text, the Job_Description text, or both are empty after normalization — where "empty after normalization" means the text contains only whitespace after the Phase 1 text-normalization step is applied — THE Match_Scorer SHALL return a final score of 0 with both breakdown components recorded as 0, treating a partially empty input pair the same as a fully empty one and preserving the Phase 1 empty-input contract.
6. WHEN both input texts are non-empty and the semantic similarity component evaluates to 0, THE Match_Scorer SHALL still compute the final score from the weighted combination, so a zero similarity component does not suppress a non-zero skill-coverage component.
7. THE Semantic_Scorer SHALL reside in the Scoring_Core and SHALL import no FastAPI, SQLAlchemy, `matchlayer_api.config`, or storage/web module; the Embedding vectors and configuration SHALL be injected by the ML_Adapter via constructor or method arguments.
8. THE Match_Scorer SHALL NOT call any LLM or paid third-party AI API; the only model executed for scoring SHALL be the committed open-source Embedding_Model and the spaCy pipeline.
9. IF `MATCHLAYER_SCORE_WEIGHT_SIMILARITY` and `MATCHLAYER_SCORE_WEIGHT_KEYWORD` do not sum to 1.0 within a tolerance of ±0.001, or either weight lies outside the inclusive range 0 to 1, THEN THE API_App SHALL fail startup with an error indicating the weight misconfiguration, so an invalid weight configuration is never used to score a request.
10. IF the two Embeddings have mismatched dimensions or either Embedding has zero magnitude, making cosine similarity undefined, THEN THE Semantic_Scorer SHALL signal an error to its caller, and THE Scoring_Service SHALL complete that request via the per-request fallback of Requirement 7.3, stamped per Requirement 6.2, rather than returning an undefined or NaN similarity component.

### Requirement 4: Skill Extraction

**User Story:** As a user, I want the analyzed, matched, and missing keywords to be actual skills, so that the gap analysis tells me what abilities to develop rather than echoing generic job-posting words.

#### Acceptance Criteria

1. THE Skill_Extractor SHALL derive the analyzed skill set of a Job_Description using spaCy linguistic processing (tokenization, part-of-speech tagging, noun-phrase or entity candidates) combined with Skill_Lexicon matching, and every term in the analyzed skill set SHALL be a canonical Skill_Lexicon term.
2. THE Skill_Extractor SHALL replace the Phase 1 TF-IDF-plus-stopword-blocklist keyword derivation as the source of `matched_keywords` and `missing_keywords` on every new Match_Result, and THE Scoring_Core SHALL no longer include non-lexicon TF-IDF terms in those sets.
3. THE Skill_Extractor SHALL normalize skill mentions by case-folding and Skill_Lexicon alias resolution, applied identically to Job_Description text and resume text, so every surface form of a skill (for example "py", "node js") resolves to its canonical term, preserving the Phase 1 alias contract.
4. THE Skill_Extractor SHALL partition the analyzed skill set into `matched_keywords` (skills present in the Resume, as determined by running the Skill_Extractor against the resume text) and `missing_keywords` (skills absent from the Resume); the two sets SHALL be disjoint and their union SHALL equal the analyzed skill set; and IF the Resume's extracted text is empty after normalization, THEN `matched_keywords` SHALL be empty and `missing_keywords` SHALL equal the analyzed skill set.
5. THE Skill_Extractor SHALL order the analyzed, matched, and missing sets by descending skill weight from the Skill_Lexicon, breaking equal-weight ties by ascending lexicographic order of the canonical term, and THE analyzed set SHALL be capped at `MATCHLAYER_MATCH_MAX_KEYWORDS` (default 50) terms by retaining the highest-weighted terms, preserving the Phase 1 ordering and cap contracts.
6. THE Match_Scorer SHALL compute the skill-coverage component as the fraction of the analyzed skill set present in the Resume, defined as 0 when the analyzed skill set is empty.
7. THE Skill_Extractor SHALL be deterministic: identical input text under an identical Scorer_Version SHALL produce an identical ordered skill set.
8. THE Skill_Extractor SHALL reside in the Scoring_Core with the spaCy pipeline and Skill_Lexicon injected by the ML_Adapter, and SHALL import no FastAPI, SQLAlchemy, `matchlayer_api.config`, or storage/web module.
9. WHEN the Job_Description contains generic job-posting terms that are not Skill_Lexicon skills (for example "check", "selection", "experience", "team"), THE Skill_Extractor SHALL exclude those terms from the analyzed, matched, and missing sets.
10. IF the Skill_Extractor derives an empty analyzed skill set from a non-empty Job_Description, THEN THE Scoring_Service SHALL fall back to the Phase 1 keyword derivation for that request and stamp the Scorer_Version per Requirement 6.2; and IF the Phase 1 fallback also derives an empty analyzed set, THEN THE Match_Scorer SHALL record a skill-coverage component of 0 per criterion 4.6 rather than rejecting the request.
11. THE Skill_Extractor SHALL match skill surface forms only at token boundaries, preferring the longest matching surface form at any position, so a skill term is never recognized as a substring of a longer token (for example "java" SHALL NOT match inside "javascript", and "node.js" SHALL resolve to its own canonical term rather than to "js"), preserving the Phase 1 boundary-matching contract.
12. IF the spaCy pipeline fails to load at API_App startup, or skill extraction raises an error for an individual request, THEN THE Scoring_Service SHALL complete the affected request(s) using the Phase 1 keyword derivation stamped per Requirement 6.2, SHALL NOT return a 5xx error caused solely by the extraction failure, and THE API_App SHALL log a structured event naming the failure category containing no Restricted PII.

### Requirement 5: Skill Lexicon Expansion

**User Story:** As a user, I want the skill vocabulary to be broad enough that real skills in my resume and target jobs are recognized, so that skill-only extraction does not silently drop skills the lexicon never knew about.

#### Acceptance Criteria

1. THE ML_Workspace SHALL provide a pipeline script, extending the Phase 1 `build_skill_lexicon.py` approach, that builds an expanded Skill_Lexicon version from non-LLM open sources, where each source is documented in the pipeline with its name, version or retrieval date, and license, and where re-running the pipeline against the same source inputs produces a byte-identical artifact.
2. THE expanded Skill_Lexicon SHALL preserve the Phase 1 artifact contract — canonical terms, aliases, per-term weights in the range 0 < weight ≤ 1, and distinct schema-version and lexicon-version fields — in a committed, versioned JSON artifact whose lexicon version participates in the Scorer_Version, with a canonical-term count strictly greater than the Phase 1 lexicon's.
3. WHEN the Skill_Lexicon artifact version changes, THE Scorer_Version SHALL change, so Match_Results produced under different lexicon versions are distinguishable.
4. THE expanded Skill_Lexicon artifact SHALL load through the Scoring_Core `Skill_Lexicon` loader without error, with the loader either preserved unchanged from Phase 1 or versioned alongside the artifact within this specification.
5. WHEN the lexicon pipeline completes a build, THE lexicon pipeline SHALL emit a summary listing the canonical terms added, the canonical terms removed, and the count of each relative to the previously committed Skill_Lexicon version, so lexicon growth is reviewable in the pull request that commits the new artifact.
6. IF the assembled lexicon data violates a Phase 1 artifact invariant — a duplicate canonical term, an alias mapped to more than one canonical term, an alias colliding with a canonical term, or a weight outside the range 0 < weight ≤ 1 — THEN THE lexicon pipeline SHALL exit with a non-zero status and SHALL NOT write any artifact.
7. THE lexicon pipeline SHALL provide a check mode, preserving the Phase 1 `--check` behavior, that exits with a non-zero status when any committed Skill_Lexicon artifact copy differs from the output the pipeline would produce.
8. IF the Scoring_Core loader encounters a Skill_Lexicon artifact whose schema version it does not support, THEN THE loader SHALL fail with an error indicating the unsupported schema version rather than loading the artifact partially.

### Requirement 6: Scorer Versioning and Reproducibility

**User Story:** As an operator, I want every stored score to identify exactly which algorithm, lexicon, and model produced it, so that historical scores remain auditable after Phase 2 changes the pipeline.

#### Acceptance Criteria

1. WHEN a Match_Result is produced by the Phase 2 semantic pipeline, THE Scoring_Service SHALL persist a `scorer_version` string that follows a documented format from which each of the four component identifiers — the scoring algorithm version, the Skill_Lexicon version, the Embedding_Model name and revision, and the spaCy pipeline name and version — is individually recoverable from the stored string alone, without consulting external records.
2. WHEN a Match_Result is produced via any fallback path — startup Degraded_Mode (Requirements 7.1–7.2), per-request embedding fallback (Requirement 7.3), or empty-analyzed-skill-set fallback (Requirement 4.10) — THE Scoring_Service SHALL persist a `scorer_version` value that identifies the Phase 1 deterministic algorithm, is distinct from every value the Phase 2 semantic pipeline can produce, and still carries the Skill_Lexicon version in the documented format.
3. THE Scorer_Version scheme SHALL be injective over pipeline compositions: no two distinct combinations of scoring algorithm version, Skill_Lexicon version, Embedding_Model name and revision, and spaCy pipeline name and version SHALL yield the same Scorer_Version value, so a change to any one component always produces a changed Scorer_Version.
4. THE Scoring_Service and every Alembic migration introduced by this specification SHALL NOT modify the stored `score`, `score_breakdown`, `matched_keywords`, `missing_keywords`, `suggestions`, or `scorer_version` of any Match_Result created before Phase 2 deployed; Phase 2 SHALL NOT re-score or migrate those rows.
5. WHEN `GET /api/v1/matches` or `GET /api/v1/matches/{id}` is invoked for a Match_Result created before Phase 2 deployed, THE API_App SHALL return the `score`, `score_breakdown`, `matched_keywords`, `missing_keywords`, `suggestions`, and `scorer_version` values identical to the values persisted at that Match_Result's creation.
6. WHEN identical resume text and identical Job_Description text are scored under an identical Scorer_Version — including in separate API_App processes — THE Match_Scorer SHALL produce identical `score`, `score_breakdown`, `matched_keywords`, `missing_keywords`, and `suggestions` values.

### Requirement 7: Degraded Mode

**User Story:** As a user, I want match scoring to keep working even if the embedding model fails to load, so that an ML artifact problem never takes down the core product flow.

#### Acceptance Criteria

1. IF the Embedding_Model artifact fails to load at API_App startup for any reason (missing artifact, corrupt or incompatible artifact, or an exception raised during model loading), THEN THE API_App SHALL start successfully and serve all endpoints, with scoring operating in Degraded_Mode.
2. WHILE in Degraded_Mode, THE Scoring_Service SHALL score every match request using the Phase 1 deterministic algorithm (TF-IDF similarity plus lexicon keyword coverage, including the Phase 1 keyword derivation and rule-based suggestions), SHALL stamp each result per Requirement 6.2, and SHALL preserve the `POST /api/v1/matches` response contract per Requirement 9.1 so a client cannot receive a contract-breaking response due to Degraded_Mode.
3. IF embedding generation fails or exceeds the `MATCHLAYER_EMBEDDING_TIMEOUT_SECONDS` bound (Requirement 2.8) for an individual request, THEN THE Scoring_Service SHALL complete that single request with the Phase 1 deterministic algorithm (stamped per Requirement 6.2) rather than returning a 5xx error caused solely by the embedding failure, and THE API_App SHALL remain in normal mode for other requests with no automatic transition to Degraded_Mode regardless of how many individual requests fall back.
4. WHEN the API_App enters Degraded_Mode, exits Degraded_Mode, or falls back for an individual request, THE API_App SHALL log exactly one structured JSON event per occurrence naming the failure or transition category from a documented set that covers at minimum artifact load failure, embedding timeout, and embedding runtime error; the event SHALL include the `request_id` for per-request fallbacks and SHALL contain no Restricted PII, no resume or job-description text, and no Embedding values.
5. THE API_App health surface (the Phase 1 `/healthz` endpoint or a field added to it) SHALL report semantic scoring availability as a machine-readable field with exactly two distinguishable values (available, unavailable), and WHILE in Degraded_Mode, the health surface SHALL continue to report the API_App itself as serving, so an operator can detect Degraded_Mode without reading logs and orchestration does not restart-loop a degraded instance.
6. WHILE in Degraded_Mode, or when a per-request fallback occurs, THE Scoring_Service SHALL persist the Match_Result without a stored Embedding for the affected request, and the absence of an Embedding SHALL NOT cause match creation to fail; the Embedding persistence obligations of Requirements 2.5 and 2.6 are suspended for that request only.
7. THE API_App SHALL exit Degraded_Mode only via a process restart in which the Embedding_Model loads successfully; WHEN such a restart occurs, subsequent match requests SHALL be scored with the Phase 2 pipeline, and Match_Results produced while in Degraded_Mode SHALL be retained unchanged and SHALL NOT be automatically re-scored, consistent with Requirement 6.4.

### Requirement 8: Suggestions Continuity

**User Story:** As a user, I want improvement suggestions to be driven by the missing skills, so that the advice gets sharper as the skill analysis gets sharper.

#### Acceptance Criteria

1. THE Scoring_Core suggestion component SHALL derive suggestions solely from the Phase 2 `missing_keywords` skill set and the Skill_Lexicon metadata for those skills, using fixed rules and templates with no LLM, embedding-model text generation, or external service involvement; each suggestion SHALL reference exactly one skill from that request's `missing_keywords` set, SHALL phrase its guidance as an action for the user to take, and SHALL NOT fabricate experience, employers, dates, or credentials.
2. THE suggestion component SHALL produce at most `MATCHLAYER_MATCH_MAX_SUGGESTIONS` (default 10) suggestions per Match_Result, ordered by descending Skill_Lexicon weight of the missing skill each suggestion addresses, preserving the Phase 1 cap and ordering contract.
3. WHEN the `missing_keywords` set is empty, THE suggestion component SHALL produce exactly one affirmative suggestion indicating that the resume already covers the analyzed skills, rather than an empty list with no explanation.
4. THE suggestion component SHALL be deterministic: identical `missing_keywords` input and an identical Scorer_Version SHALL produce an identical ordered suggestion list.
5. WHEN a Match_Result is produced in Degraded_Mode or via a per-request fallback (Requirements 7.2 and 7.3), THE suggestion component SHALL apply the same cap, ordering, determinism, and non-fabrication rules to that request's `missing_keywords` set (which may contain Phase 1-derived non-lexicon terms), so a degraded request still returns a populated `suggestions` field.

### Requirement 9: API and Contract Continuity

**User Story:** As a user, I want the upload-and-match flow I already use to keep working identically, so that Phase 2 improves results without breaking my workflow or the frontend.

#### Acceptance Criteria

1. THE API_App SHALL preserve the Phase 1 request and response contracts of `POST /api/v1/resumes`, `GET /api/v1/resumes`, `GET /api/v1/resumes/{id}`, `DELETE /api/v1/resumes/{id}`, `POST /api/v1/matches`, `GET /api/v1/matches`, `GET /api/v1/matches/{id}`, and `DELETE /api/v1/matches/{id}` without breaking changes, as observable in the FastAPI-generated OpenAPI schema: every Phase 1 field SHALL keep its name, type, and required/optional status; every Phase 1 status code and RFC 7807 error `type` SHALL be returned under the same conditions as in Phase 1; and any Phase 2 addition SHALL appear only as a new optional field, so no `v2` version bump is required per `conventions.md`.
2. THE API_App SHALL NOT include Embedding vector values, vector distances, or any other embedding-derived field in any API response body — including list responses and RFC 7807 error responses — beyond the semantic similarity component value already reported inside `score_breakdown` per Requirement 3.3, which is part of the preserved Phase 1 response contract.
3. WHEN a Resume is soft-deleted, THE API_App SHALL treat the Resume's stored Embedding under the same retention rule as `extracted_text` in Phase 1: retained after soft delete, with hard deletion deferred to the Phase 7 purge job per `security.md`.
4. THE API_App SHALL apply the Phase 1 authentication, per-user scoping, rate-limiting, daily quota, and Idempotency-Key behaviors unchanged to every endpoint listed in criterion 1, including that a `POST /api/v1/matches` or `POST /api/v1/resumes` request replaying an Idempotency-Key persisted within the preceding 24 hours for the same User_Account SHALL return the original stored response without re-invoking the Phase 2 embedding or scoring pipeline.
5. WHEN `POST /api/v1/matches` succeeds under the Phase 2 pipeline, THE response `score_breakdown` SHALL contain every field of the Phase 1 breakdown contract — the similarity component value, the coverage component value, the two weights applied, and the final score — under their Phase 1 names and types, with the Requirement 3.3 method identifier and any semantic labels present only as new fields, so the unmodified Phase 1 Results_Page renders the response without error or missing data.
6. THE API_App SHALL NOT introduce a new endpoint, query parameter, or response field that exposes Embedding values, vector distances, or cross-user similarity queries.
7. IF Embedding generation fails or exceeds its `MATCHLAYER_EMBEDDING_TIMEOUT_SECONDS` bound during `POST /api/v1/resumes` processing, THEN THE API_App SHALL complete the upload per the Phase 1 contract (HTTP 201 with the Phase 1 response body, `extraction_status` reflecting text extraction alone), leaving the Resume eligible for match-time Embedding generation per Requirement 2.7 rather than failing the upload.
8. WHEN a Match_Result is soft-deleted via `DELETE /api/v1/matches/{id}`, THE API_App SHALL retain the Match_Result's stored Job_Description Embedding under the same retention rule as criterion 3: retained after soft delete, with hard deletion deferred to the Phase 7 purge job per `security.md`.

### Requirement 10: Deployment Fit and Cost

**User Story:** As an operator, I want Phase 2 to run inside the existing free-tier hosting and the $20/month ceiling, so that semantic scoring does not force a paid infrastructure change.

#### Acceptance Criteria

1. WHEN the API_App container starts on the Fly.io machine size the project deploys to, THE API_App SHALL load the Embedding_Model and the spaCy pipeline and reach its ready-to-serve state without out-of-memory termination.
2. THE Embedding_Model and spaCy pipeline artifacts SHALL be baked into the API container image or fetched at container build time, sourced by the pinned Embedding_Model name and revision defined in Requirement 2.2 and the pinned spaCy pipeline name and version recorded in configuration.
3. WHEN Phase 2 deploys, THE project cost log (`docs/costs.md`) SHALL record the Phase 2 hosting configuration as itemized monthly costs naming the Fly.io machine size and the Postgres provider in use (including any provider change made under Requirement 1.5), and SHALL confirm the itemized total monthly spend is less than $20.00.
4. WHILE serving requests, THE API_App SHALL make no network call to any third-party AI or inference service, so the Phase 2 pipeline introduces no per-request inference cost.
5. THE deployment documentation SHALL record the measured peak resident memory of the API_App in MB at the ready-to-serve state, together with the Fly.io machine size and memory limit the measurement was taken against.
6. WHILE serving requests, THE API_App SHALL make no network call to a model hub to download the Embedding_Model or spaCy artifacts.
7. IF the measured peak resident memory exceeds the memory limit of the deployed Fly.io machine size, THEN THE project documentation SHALL record a remediation decision (a smaller Embedding_Model, a different machine size within the cost ceiling, or another documented change) before Phase 2 deployment proceeds.

### Requirement 11: Evaluation

**User Story:** As the developer, I want evidence that Phase 2 scoring beats Phase 1 on the same resume+JD pairs, so that the upgrade is measurable rather than assumed.

#### Acceptance Criteria

1. THE Eyeball_Dataset SHALL be expanded to at least 10 pairs following the dataset README schema, covering at minimum: strong match, clear mismatch, partial match, keyword-stuffed adversarial, a semantic-paraphrase pair (same skills expressed in different words) whose Phase 1 TF-IDF score fails to reach the pair's expected score band, and a generic-term-leak pair whose Job_Description contains generic non-skill words (for example "check", "selection") with `must_miss_skills` expectations asserting those words never appear in the analyzed, matched, or missing sets.
2. THE Eval_Runner SHALL score every Eyeball_Dataset pair with the Phase 2 pipeline and report in its output, per pair, the 0–100 score, the score band met or missed (where bands map to the score as: low = 0–39, medium = 40–69, high = 70–100), the matched and missing skill sets, and each `must_match_skills`/`must_miss_skills` expectation marked as met or violated.
3. THE Eval_Runner SHALL also score every pair with the Phase 1 deterministic algorithm and report both the Phase 1 and Phase 2 scores side by side within the same per-pair entry of its output.
4. THE Eval_Runner SHALL live in the ML_Workspace, SHALL NOT be imported by the API_App, and SHALL use only committed dataset files containing no real personal data per the dataset README.
5. WHEN the Eval_Runner runs against the committed Eyeball_Dataset, THE generic-term-leak pair's expectations SHALL pass under the Phase 2 pipeline, demonstrating the Phase 1 defect is fixed.
6. WHEN the Eval_Runner runs against the committed Eyeball_Dataset, THE semantic-paraphrase pair's Phase 2 score SHALL exceed its Phase 1 score and SHALL fall within the pair's expected score band, demonstrating the semantic improvement is measurable.
7. IF an Eyeball_Dataset file does not conform to the dataset README schema, THEN THE Eval_Runner SHALL report an error identifying the offending file by name and terminate with a non-zero exit status without emitting pass/fail results for any pair.
8. WHEN scoring completes for all pairs, THE Eval_Runner SHALL report a summary containing the count of pairs whose expectations passed and failed under the Phase 2 pipeline, and SHALL exit with a non-zero exit status if any Phase 2 expectation (score band, `must_match_skills`, or `must_miss_skills`) is violated.

### Requirement 12: Architecture Boundaries

**User Story:** As the developer, I want Phase 2 code to land in the same clean boundaries Phase 1 established, so that the codebase stays testable and the Phase 3+ layers have a stable foundation.

#### Acceptance Criteria

1. THE Scoring_Core SHALL contain the Semantic_Scorer, the Skill_Extractor, and the updated Match_Scorer composition, and SHALL import no FastAPI, SQLAlchemy, `matchlayer_api.config`, or storage/web module; the Embedding_Model artifacts, the spaCy pipeline, the Skill_Lexicon, and every configuration value consumed by the Scoring_Core SHALL be injected by the ML_Adapter via constructor or method arguments.
2. THE ML_Adapter SHALL be the only API_App layer that reads the Phase 2 scoring configuration values, loads the Embedding_Model and spaCy pipeline artifacts, and constructs the Phase 2 scorer; THE ML_Adapter SHALL compute no similarity value, skill-coverage value, or score value, so every scoring computation resides in the Scoring_Core.
3. THE ML_Workspace SHALL hold the lexicon pipeline, the Eval_Runner, and the eval datasets; THE API_App SHALL NOT import any Python module from the ML_Workspace, and reading the committed Skill_Lexicon JSON artifact as a data file SHALL be the only sanctioned dependency of the API_App on ML_Workspace contents.
4. THE Vector_Store data access SHALL live in the API_App's database layer (`db/` and services) and SHALL NOT live in the Scoring_Core; THE Scoring_Core SHALL operate only on in-memory values passed to it as arguments (input texts, Embedding vectors, injected artifacts, and configuration values), performing no database or network access at scoring time.
5. THE API_App SHALL define every new configuration value introduced by this specification in its Pydantic settings, SHALL document each such value in `.env.example` with a placeholder value, and SHALL pass each such value into the Scoring_Core only via the ML_Adapter; THE Scoring_Core SHALL NOT read any environment variable.
6. THE API_App test suite SHALL include an automated boundary check that fails IF the Scoring_Core imports FastAPI, SQLAlchemy, `matchlayer_api.config`, or a storage/web module, or IF any API_App module imports a Python module from the ML_Workspace, so criteria 1 and 3 are verified on every test run.
