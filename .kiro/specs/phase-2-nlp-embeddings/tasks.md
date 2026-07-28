# Implementation Plan: phase-2-nlp-embeddings

## Overview

Implementation proceeds bottom-up along the Phase 1 boundaries: pgvector storage first, then the framework-free Scoring_Core components (Embedding_Service, Semantic_Scorer, Skill_Extractor, versioning), the expanded Skill_Lexicon, the Phase 2 scorer composition, the ML_Adapter and configuration, service-layer orchestration with the full fallback ladder, and finally evaluation and deployment artifacts. The Phase 1 `Match_Scorer` is never modified as an engine — it is retained verbatim as the fallback path. Every Scoring_Core addition is Python 3.13, `mypy --strict`, constructor-injected, with Hypothesis property tests driven by a deterministic stub `Text_Encoder` (no real model in property tests).

## Tasks

- [x] 1. pgvector storage foundation
  - [x] 1.1 Switch local development PostgreSQL to a pgvector-enabled image
    - Change the postgres service image in `docker-compose.yml` to `pgvector/pgvector:pg16`, leaving the existing init scripts (`infra/docker/postgres-init/`) unchanged
    - _Requirements: 1.4_

  - [x] 1.2 Write the Alembic migration for the pgvector extension and embedding tables
    - Single revision on the Phase 1 head: `CREATE EXTENSION IF NOT EXISTS vector`, then `resume_embeddings` and `match_embeddings` per the design ERD (UUIDv7 PKs, `vector(384)` NOT NULL with the dimension as a DDL literal, NOT NULL `model_name`/`model_revision`, FKs to `users`/`resumes`/`match_results` with `UNIQUE(resume_id)` / `UNIQUE(match_result_id)`, index on `user_id`)
    - Migration docstring records the explicit 384 dimension decision, the no-ANN-index rationale (no vector search in Phase 2), and the index justifications per `conventions.md`
    - Rely on Alembic transactional DDL so any failure (privileges, conflict, pgvector unavailable) rolls back atomically with no partial objects
    - _Requirements: 1.1, 1.2, 1.3, 1.6, 1.8, 1.9_

  - [x] 1.3 Add SQLAlchemy embedding models and the pgvector dependency
    - Add the `pgvector` Python package (pinned) to `apps/api/pyproject.toml`; add `ResumeEmbedding` and `MatchEmbedding` to `apps/api/src/matchlayer_api/db/models.py` using `pgvector.sqlalchemy.Vector(384)`, mirroring the migration DDL and Phase 1 model patterns
    - _Requirements: 1.6, 1.8, 2.10_

  - [x] 1.4 Write integration tests for the migration and vector write semantics
    - Migration applies cleanly from the Phase 1 head; failure rolls back atomically and is re-runnable; clean pgvector-unavailability error on a non-pgvector image
    - Wrong-dimension vector write is rejected with nothing persisted; writes without an existing owner/source entity are rejected by FK constraints; `model_name`/`model_revision` are recorded
    - _Requirements: 1.2, 1.3, 1.6, 1.8, 1.9, 2.10_

- [x] 2. Embedding_Service and Semantic_Scorer in the Scoring_Core
  - [x] 2.1 Implement the Text_Encoder protocol and Embedding_Service
    - Create `apps/api/src/matchlayer_api/scoring/embedding.py` with the `Text_Encoder` protocol (`dimension`, `max_tokens`, `count_tokens`, `split_tokens`, `encode`) and `Embedding_Service.embed`: single encode when text fits, otherwise non-overlapping tokenizer chunks → token-count-weighted mean → L2-normalize, entirely in memory
    - Document the chunk-and-aggregate strategy in the module docstring; no framework imports, no env reads
    - Include a deterministic hash-based stub `Text_Encoder` test helper with a small `max_tokens` for property tests
    - _Requirements: 2.1, 2.3, 2.4, 12.1_

  - [x] 2.2 Write property test for embedding dimension and determinism
    - **Property 1: Embeddings have the declared dimension and are deterministic**
    - **Validates: Requirements 2.1, 2.4**

  - [x] 2.3 Write property test for chunk coverage and aggregation formula
    - **Property 2: Chunking covers the full document and aggregation follows the documented formula**
    - **Validates: Requirements 2.3**

  - [x] 2.4 Implement the Semantic_Scorer
    - Create `apps/api/src/matchlayer_api/scoring/semantic.py` with `similarity_component(a, b) = (cosine + 1) / 2` clamped to [0, 1], and `EmbeddingGeometryError` raised on mismatched dimensions or zero magnitude
    - _Requirements: 3.1, 3.4, 3.10, 12.1_

  - [x] 2.5 Write property test for the cosine mapping
    - **Property 3: Cosine mapping is bounded, monotone, and deterministic**
    - **Validates: Requirements 3.1, 3.4**

  - [x] 2.6 Write property test for undefined cosine geometry
    - **Property 12: Undefined cosine geometry signals an error**
    - **Validates: Requirements 3.10**

- [x] 3. Scorer_Version v2 scheme
  - [x] 3.1 Implement Scorer_Version composition and parsing
    - Create `apps/api/src/matchlayer_api/scoring/versioning.py` with `SEMANTIC_ALGORITHM_VERSION = "2.0.0"`, `semantic_scorer_version(...)` producing `2.0.0+lex.{lex}+emb.{name}@{rev}+spacy.{name}@{ver}` (rejecting any component containing `+`), and `parse_scorer_version(...)` recovering all components from the string alone (Phase 1 strings parse as algorithm+lexicon only)
    - _Requirements: 6.1, 6.2, 6.3, 5.3_

  - [x] 3.2 Write property test for Scorer_Version round-trip and injectivity
    - **Property 13: Scorer_Version round-trips and is injective**
    - **Validates: Requirements 6.1, 6.3, 5.3**

  - [x] 3.3 Write property test for fallback version distinctness
    - **Property 14: Fallback Scorer_Version values are distinct from every semantic value**
    - **Validates: Requirements 6.2**

- [x] 4. Skill_Extractor
  - [x] 4.1 Implement the spaCy-based Skill_Extractor
    - Create `apps/api/src/matchlayer_api/scoring/skills.py`: constructor takes injected spaCy `Language`, `Skill_Lexicon`, and `max_keywords`; builds a `PhraseMatcher` over case-folded canonical terms and aliases (token-boundary matching)
    - `extract(text)`: candidate spans resolved longest-match-wins (ties by earliest start), alias resolution to canonical terms, POS/noun-chunk gating with the lexicon as final authority (output can never contain a non-lexicon term), deduplicated and ordered by descending weight with ascending lexicographic tie-break
    - `analyze(resume_text, job_description)`: analyzed = extract(jd) capped at `max_keywords` (highest-weight retained); matched = analyzed ∩ extract(resume); missing = analyzed \ matched; reuse the Phase 1 `KeywordAnalysis` dataclass
    - Deterministic; no framework imports, no env reads
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.7, 4.8, 4.9, 4.11, 12.1_

  - [x] 4.2 Write property test for skill-only extraction output
    - **Property 7: Extraction output is skill-only**
    - **Validates: Requirements 4.1, 4.2, 4.9**

  - [x] 4.3 Write property test for alias resolution symmetry
    - **Property 8: Alias surface forms resolve to canonical terms identically for both text roles**
    - **Validates: Requirements 4.3**

  - [x] 4.4 Write property test for token boundaries and longest-match preference
    - **Property 9: Matching respects token boundaries and prefers the longest surface form**
    - **Validates: Requirements 4.11**

  - [x] 4.5 Write property test for matched/missing partition
    - **Property 10: matched/missing partition the analyzed set**
    - **Validates: Requirements 4.4**

  - [x] 4.6 Write property test for ordering, tie-break, and cap
    - **Property 11: Skill sets are weight-ordered, tie-broken lexicographically, and capped**
    - **Validates: Requirements 4.5**

- [x] 5. Skill_Lexicon v2
  - [x] 5.1 Extend the lexicon build pipeline
    - Extend `ml/pipelines/build_skill_lexicon.py` with documented open sources (ESCO plus the curated v1 seed), each recorded with name, version/retrieval date, and license; deterministic assembly (sorted keys, canonical JSON) so reruns are byte-identical
    - Emit an added/removed diff summary against the previously committed artifact; validate the Phase 1 invariants (unique canonicals, alias uniqueness, alias/canonical non-collision, 0 < weight ≤ 1) and exit non-zero without writing on violation; preserve `--check` drift mode
    - _Requirements: 5.1, 5.5, 5.6, 5.7_

  - [x] 5.2 Build and commit the v2 lexicon artifact
    - Run the pipeline to produce `ml/lexicon/skill_lexicon.v2.json` with `lexicon_version: "v2"`, same schema version, strictly more canonical terms than v1; copy to `apps/api/src/matchlayer_api/scoring/data/`; verify it loads through the unchanged Scoring_Core loader and that `tools/check_lexicon_drift.py` still gates both copies
    - _Requirements: 5.2, 5.4_

  - [x] 5.3 Write property test for lexicon invariant rejection
    - **Property 16: Lexicon invariant violations are rejected without output**
    - **Validates: Requirements 5.6**

  - [x] 5.4 Write unit tests for the lexicon pipeline and loader
    - Byte-identical rerun, diff summary content, `--check` drift exit code, v2 artifact loads and exceeds the v1 canonical-term count, loader rejects unsupported schema versions without partial load
    - _Requirements: 5.1, 5.2, 5.4, 5.5, 5.7, 5.8_

- [x] 6. Checkpoint — core components
  - Ensure all tests pass, ask the user if questions arise.

- [x] 7. Semantic_Match_Scorer composition
  - [x] 7.1 Implement the Semantic_Match_Scorer
    - Extend `apps/api/src/matchlayer_api/scoring/scorer.py` with `Semantic_Match_Scorer` composing the injected lexicon, Skill_Extractor, Semantic_Scorer, weights, caps, and the composed v2 `scorer_version` string
    - Empty-after-normalization check first (Phase 1 `_normalize`): either side empty → score 0, both components 0; `final = max(0, min(100, round(100 * (w_sim*similarity + w_kw*coverage))))`; coverage = |matched|/|analyzed| (0 when analyzed empty); raise `EmptyAnalyzedSetError` when JD is non-empty but analyzed is empty; propagate `EmbeddingGeometryError`
    - Reuse the Phase 1 `Suggestion_Generator` over `missing`; add optional `similarity_method` (`"semantic-embedding"` / `"tfidf"`) to `ScoreBreakdown`, populated by both scorers, leaving every Phase 1 breakdown field name/type unchanged; keep the Phase 1 `Match_Scorer` untouched as the fallback engine
    - _Requirements: 3.2, 3.3, 3.5, 3.6, 3.7, 3.8, 4.6, 4.10, 8.1, 9.5, 12.1_

  - [x] 7.2 Write property test for breakdown reproducibility and shape
    - **Property 4: The final score is reproducible from the breakdown alone, with the Phase 1 shape preserved**
    - **Validates: Requirements 3.2, 3.3, 3.6, 4.6, 9.5**

  - [x] 7.3 Write property test for empty-input scoring
    - **Property 5: Empty-after-normalization inputs score zero**
    - **Validates: Requirements 3.5**

  - [x] 7.4 Write property test for suggestion generation over missing skills
    - **Property 15: Suggestions reference exactly one missing skill each, capped, ordered, deterministic**
    - **Validates: Requirements 8.1, 8.2, 8.4**

  - [x] 7.5 Write property test for full-pipeline determinism
    - **Property 17: The full Phase 2 pipeline is deterministic** (independently constructed scorer instances with the stub encoder)
    - **Validates: Requirements 6.6, 4.7**

  - [x] 7.6 Write unit tests for scorer edge behavior
    - `EmptyAnalyzedSetError` raised for non-empty JD with empty analyzed set; empty `missing_keywords` produces exactly one affirmative suggestion; zero similarity with non-zero coverage yields the weighted combination
    - _Requirements: 4.10, 8.3, 3.6_

- [x] 8. Configuration and ML_Adapter
  - [x] 8.1 Add Phase 2 settings and .env.example entries
    - Add to `apps/api/src/matchlayer_api/config.py`: `MATCHLAYER_EMBEDDING_MODEL_NAME` (default `sentence-transformers/all-MiniLM-L6-v2`), `MATCHLAYER_EMBEDDING_MODEL_REVISION` (pinned HF commit SHA), `MATCHLAYER_EMBEDDING_MODEL_PATH`, `MATCHLAYER_EMBEDDING_DIMENSION` (384), `MATCHLAYER_EMBEDDING_TIMEOUT_SECONDS` (20), `MATCHLAYER_SPACY_PIPELINE` (`en_core_web_sm`)
    - Extend the Phase 1 weight validator to reject weights outside [0, 1] and use the ±0.001 sum tolerance; document every new var in `.env.example` with placeholders so `tools/check_env_drift.py` passes
    - _Requirements: 2.2, 2.8, 3.9, 12.5_

  - [x] 8.2 Write property test for weight validation
    - **Property 6: Invalid score weights are rejected at settings construction**
    - **Validates: Requirements 3.9**

  - [x] 8.3 Implement the semantic adapter
    - Create `apps/api/src/matchlayer_api/ml/semantic_adapter.py`: `load_semantic_pipeline()` called once from the FastAPI lifespan — loads the SentenceTransformer from the local path and the spaCy pipeline (version from installed package metadata), wraps the model in a concrete `Text_Encoder`, composes the v2 `scorer_version`, returns `None` on any load failure logging one `model_load_failure` structured event (no PII); `semantic_available()`; `embed_with_timeout()` via worker thread + `asyncio.wait_for` raising `EmbeddingTimeoutError`
    - Startup dimension check: configured `MATCHLAYER_EMBEDDING_DIMENSION` vs loaded encoder dimension mismatch fails startup fast (only when the model loads)
    - Add pinned dependencies: `sentence-transformers`, spaCy, and `en_core_web_sm` as a direct wheel URL in `apps/api/pyproject.toml` so `uv sync --frozen` reproduces them
    - _Requirements: 7.1, 7.4, 2.8, 1.7, 10.2, 12.2_

  - [x] 8.4 Wire the scorer adapter
    - Extend `apps/api/src/matchlayer_api/ml/scorer_adapter.py`: keep Phase 1 `get_scorer()` unchanged; add `get_semantic_scorer()` returning the composed Phase 2 scorer from the loaded pipeline; the adapter computes no similarity, coverage, or score value
    - _Requirements: 12.1, 12.2_

  - [x] 8.5 Write unit tests for the adapter
    - Broken model path → `load_semantic_pipeline()` returns `None` with exactly one `model_load_failure` event containing no PII; dimension mismatch fails startup; timeout wrapper raises on a fake slow encoder
    - _Requirements: 7.1, 7.4, 1.7, 2.8_

- [x] 9. Vector_Store access layer
  - [x] 9.1 Implement Vector_Store service functions
    - Create `apps/api/src/matchlayer_api/services/vector_store.py` with async `get_resume_embedding` (user-scoped, returns `StoredEmbedding` with vector + model name/revision), `upsert_resume_embedding`, `insert_match_embedding` — SQLAlchemy 2.x only, no raw SQL
    - _Requirements: 1.8, 2.10, 12.4_

  - [x] 9.2 Write integration tests for the Vector_Store
    - Owner-scoped reads (cross-user read returns nothing); upsert replaces the existing row for a resume; model name/revision round-trip drives the reuse-vs-regenerate decision
    - _Requirements: 1.8, 2.7, 2.10_

- [x] 10. Service orchestration and fallback ladder
  - [x] 10.1 Update the Scoring_Service match flow
    - Extend `apps/api/src/matchlayer_api/services/matching.py` `create_match`: load stored resume embedding, reuse when model name+revision match, otherwise generate under `embed_with_timeout` and upsert best-effort; embed the JD; score via the Phase 2 scorer; persist `match_results` plus the JD embedding (best-effort — persistence failure never fails the request)
    - Implement the full fallback ladder per the design decision tree: Degraded_Mode, `embedding_timeout`, `embedding_runtime_error`, `embedding_geometry_error`, `skill_extraction_error`, `skill_extraction_empty` — each completing the request with the Phase 1 engine, Phase 1 `scorer_version` stamp, no stored embedding, and exactly one structured event with `request_id` and no PII; per-request fallbacks never flip the process into Degraded_Mode
    - Leave the Idempotency-Key replay short-circuit, list/get/delete paths, and pre-Phase-2 stored rows untouched
    - _Requirements: 2.6, 2.7, 2.8, 2.9, 2.12, 3.10, 4.10, 4.12, 6.1, 6.2, 6.4, 7.2, 7.3, 7.4, 7.6, 9.4_

  - [x] 10.2 Write unit tests for the fallback ladder and event discipline
    - Each fallback path completes with a Phase 1-stamped result and no 5xx; log-capture asserts exactly one event per occurrence with documented category, `request_id`, and zero PII or vector content; repeated per-request fallbacks leave normal mode intact
    - _Requirements: 7.3, 7.4, 2.9, 4.10, 4.12, 2.12_

  - [x] 10.3 Embed resumes at upload
    - Update `apps/api/src/matchlayer_api/services/resumes.py`: after successful extraction, generate and upsert the resume embedding best-effort under the timeout; any embedding or persistence failure leaves the upload response identical to Phase 1 (HTTP 201, `extraction_status` reflecting extraction alone) and the resume is embedded lazily at match time
    - _Requirements: 2.5, 2.11, 9.7_

  - [x] 10.4 Write unit tests for upload embedding behavior
    - Upload succeeds with embedding persisted on the happy path; upload response unchanged when embedding generation or persistence fails
    - _Requirements: 2.5, 2.11, 9.7_

- [x] 11. Health surface and Degraded_Mode wiring
  - [x] 11.1 Add semantic availability to /healthz
    - Extend the health endpoint with `"semantic_scoring": "available" | "unavailable"` backed by `semantic_available()`; status-code semantics unchanged so a degraded instance still reports serving
    - _Requirements: 7.5_

  - [x] 11.2 Write integration tests for Degraded_Mode
    - App instance with a broken model path starts and serves; all match requests use the Phase 1 engine with Phase 1 stamps and valid response schema; `/healthz` reports `unavailable`; no embedding rows written; restart with a working model recovers to the Phase 2 pipeline with prior degraded rows retained unchanged
    - _Requirements: 7.1, 7.2, 7.5, 7.6, 7.7_

- [x] 12. Architecture boundaries and contract continuity
  - [x] 12.1 Extend the automated boundary check
    - Extend the Phase 1 boundary test: `matchlayer_api.scoring` imports no FastAPI, SQLAlchemy, pgvector, `matchlayer_api.config`, or storage/web modules and reads no environment variables; no `matchlayer_api` module imports from the `ml/` workspace
    - _Requirements: 12.1, 12.3, 12.6, 3.7, 4.8, 11.4_

  - [x] 12.2 Write contract-continuity integration tests
    - OpenAPI snapshot diff: every Phase 1 field keeps name/type/required status, Phase 2 additions optional-only, no embedding-exposing field/endpoint/query parameter; pre-Phase-2 Match_Results returned verbatim; Idempotency-Key replay returns the stored response without invoking the Phase 2 pipeline; soft-delete retains stored embeddings for resumes and matches; one cross-process determinism example
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.6, 9.8, 6.4, 6.5, 6.6_

- [x] 13. Checkpoint — full pipeline wired
  - Ensure all tests pass, ask the user if questions arise.

- [x] 14. Evaluation
  - [x] 14.1 Expand the eyeball dataset
    - Grow `ml/evals/datasets/eyeball/` to at least 10 pairs following the dataset README schema (no real personal data): strong match, clear mismatch, partial match, keyword-stuffed adversarial, a semantic-paraphrase pair expected to beat its Phase 1 TF-IDF score, and a generic-term-leak pair with `must_miss_skills` asserting words like "check" and "selection" never appear in any skill set
    - _Requirements: 11.1_

  - [x] 14.2 Implement the Eval_Runner
    - Create `ml/evals/run_eyeball.py` importing only `matchlayer_api.scoring`: validate every dataset file against the README schema first (malformed file → error naming the file, non-zero exit, no pair results); score each pair with both the Phase 2 pipeline (real model + spaCy) and the Phase 1 `Match_Scorer`; per-pair report with both scores side by side, band met/missed (low 0–39 / medium 40–69 / high 70–100), matched/missing sets, and each `must_match_skills`/`must_miss_skills` expectation met or violated; summary with pass/fail counts and non-zero exit on any Phase 2 expectation violation
    - _Requirements: 11.2, 11.3, 11.4, 11.7, 11.8_

  - [x] 14.3 Write unit tests for the Eval_Runner
    - Report shape, side-by-side scores, malformed-file rejection with filename and non-zero exit, summary counts and exit codes on fixture datasets; dataset content validation covering ≥10 pairs and the required categories
    - _Requirements: 11.1, 11.2, 11.3, 11.7, 11.8_

  - [x] 14.4 Run the evaluation gate and fix failures
    - Run `run_eyeball.py` against the committed dataset; adjust the pipeline, lexicon, or dataset expectations (within schema) until all Phase 2 expectations pass — the generic-term-leak pair proves the Phase 1 defect fixed and the semantic-paraphrase pair's Phase 2 score exceeds its Phase 1 score within the expected band
    - _Requirements: 11.5, 11.6, 11.8_

- [x] 15. Deployment artifacts and documentation
  - [x] 15.1 Update the API image build
    - Extend `infra/docker/api.Dockerfile`: build stage runs `huggingface_hub.snapshot_download` for the pinned model name + revision into `MATCHLAYER_EMBEDDING_MODEL_PATH`; spaCy model installed via the pinned wheel through `uv sync --frozen`; runtime sets `HF_HUB_OFFLINE=1`; non-root/no-shell conventions preserved
    - _Requirements: 10.2, 10.6_

  - [x] 15.2 Update project documentation
    - README runbook section: chunking strategy, Degraded_Mode behavior, the documented fallback event category set, and the cosine→component transformation; `docs/costs.md` itemized Phase 2 update naming the Fly machine size and Postgres provider with total under $20, including the Supabase/Neon fallback decision if applicable; deployment docs note the peak-RSS measurement procedure and machine size
    - _Requirements: 2.3, 3.1, 7.4, 10.3, 10.5, 10.7, 1.5_

- [x] 16. Final checkpoint
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for a faster MVP, but the 17 property tests map one-to-one to the design's Correctness Properties and are the primary evidence of correctness
- Property tests use the deterministic stub `Text_Encoder` and small synthetic lexicons; the real embedding model is exercised only by the Eval_Runner and integration tests
- The Phase 1 `Match_Scorer`, keyword derivation, and suggestion engine are reused verbatim as the fallback path — no task modifies their scoring behavior
- Deployment verification steps that cannot run in code (Fly.io peak-RSS measurement, production pgvector enablement) are documented procedures in 15.2, not automated tasks
- Each task references the granular requirement clauses it implements for traceability

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "2.1", "3.1", "5.1", "14.1"] },
    { "id": 1, "tasks": ["1.2", "2.2", "2.4", "3.2", "4.1", "5.2"] },
    { "id": 2, "tasks": ["1.3", "2.3", "2.5", "3.3", "4.2", "5.3"] },
    { "id": 3, "tasks": ["1.4", "2.6", "4.3", "5.4", "7.1", "8.1"] },
    { "id": 4, "tasks": ["4.4", "7.2", "8.2", "8.3", "9.1"] },
    { "id": 5, "tasks": ["4.5", "7.3", "8.4", "9.2", "10.3"] },
    { "id": 6, "tasks": ["4.6", "7.4", "10.1", "11.1"] },
    { "id": 7, "tasks": ["7.5", "7.6", "8.5", "10.2", "10.4", "12.1"] },
    { "id": 8, "tasks": ["11.2", "12.2", "14.2"] },
    { "id": 9, "tasks": ["14.3", "15.1", "15.2"] },
    { "id": 10, "tasks": ["14.4"] }
  ]
}
```
