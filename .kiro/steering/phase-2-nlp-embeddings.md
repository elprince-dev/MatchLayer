---
inclusion: manual
---

# Phase 2 — NLP & Embeddings

**Status:** not started
**Depends on:** Phase 1 shipped and deployed.
**Goal:** replace the naive TF-IDF scoring with semantic understanding using open-source embeddings + pgvector.

## Why this phase exists

TF-IDF only matches exact words. A resume saying "led ML projects" won't match a JD asking for "machine learning experience". Semantic embeddings fix this. Pgvector keeps it in Postgres — one fewer service to operate.

## In scope

- **Embeddings**
  - Use `sentence-transformers` with `BAAI/bge-small-en-v1.5` (384 dims, MIT license, strong leaderboard performance per dim) or `all-MiniLM-L6-v2` (384 dims, classic, fastest). Default to `bge-small-en-v1.5`.
  - Run the model in-process in the API container for now. Move to a dedicated inference service only if latency becomes a problem.
- **pgvector**
  - Enable extension via Alembic migration.
  - Add `embedding vector(384)` column to a new `resume_embeddings` table (separate table to keep `resumes` slim and allow re-embedding without rewriting the parent row).
  - Same for job descriptions: `match_results.jd_embedding vector(384)`.
  - HNSW index on both for fast cosine similarity.
- **Skill extraction**
  - Use spaCy + a curated skills lexicon (start with the Phase 1 lexicon, expand to ~1000 entries from public datasets like O\*NET).
  - Optional: small NER model fine-tuned on a public skills dataset (deferred unless the lexicon approach falls short).
- **Scoring v2**
  - Composite score: `0.6 * semantic_similarity + 0.3 * skill_overlap + 0.1 * keyword_overlap`. Tune weights with eyeballed examples; document as a hyperparameter.
  - Return per-component breakdown to the frontend so users can see _why_ their score is what it is.
- **Re-scoring path**
  - Add `POST /api/v1/resumes/{id}:reembed` to re-run embedding when models or weights change. Synchronous for now.
- **Frontend**
  - New "Skill gap" panel on the results page: matched skills / missing skills / nice-to-have skills, with severity coloring.
  - Tooltip explaining the score components.

## Explicitly out of scope

- LLM calls (Phase 3).
- Multi-document retrieval / RAG (not needed yet).
- Async processing (Phase 6).
- Fine-tuning embeddings on resume data.
- Cross-encoder reranking (consider in Phase 5 if eval shows it helps).

## Deliverables

1. Resume + JD embedding pipeline running on every match.
2. Composite score replacing TF-IDF in the API and UI.
3. Migration adding pgvector and the new tables/columns.
4. README updated with Phase 2 architecture and benchmarks (latency, score-quality examples).

## Success criteria

- p95 embedding latency < 500ms per document on a 2-vCPU container.
- Phase 2 scores beat Phase 1 scores on a hand-curated set of 20 resume/JD pairs (track in `ml/evals/phase2_eyeball_set.json`).
- pgvector queries return in < 50ms with 10k stored embeddings.

## Skills demonstrated

NLP · sentence transformers · vector embeddings · pgvector · HNSW · skill extraction · composite scoring · spaCy

## Risks & gotchas

- **Model size in container.** `bge-small` is ~130MB. Acceptable. Larger models bloat cold starts — don't switch to `bge-large` without measuring.
- **CPU-only inference.** Fine for `bge-small`, ~50ms per doc. If we ever want larger models, plan for GPU or a dedicated inference service.
- **Embedding versioning.** When you upgrade the model, all stored embeddings need re-running. Add an `embedding_model_version` column from day one.
- **pgvector index choice.** HNSW is generally better than IVFFlat for our scale. Document the choice.
- **Resume length variance.** Long resumes embed differently than short ones. Consider chunking + averaging for resumes > 2000 tokens.

## Folder additions

```
apps/api/src/matchlayer_api/ml/
  embeddings.py                     # model loading, encode functions
  skill_extraction.py
  scoring.py                        # composite scorer
apps/api/alembic/versions/
  ...add_pgvector_and_embeddings.py
ml/evals/
  phase2_eyeball_set.json           # hand-curated test pairs
```

## Work breakdown

1. Add pgvector to docker-compose Postgres image.
2. Alembic migration: enable extension, add `resume_embeddings`, add `jd_embedding` to `match_results`, create HNSW indexes.
3. ML module: lazy-loaded sentence-transformer wrapper, batched encode, model version constant.
4. Service: `MatchService.score()` rewritten to compute embeddings + skill extraction + composite score.
5. Backfill script: re-score existing Phase 1 matches with the new pipeline (optional but good resume signal — shows you thought about migration).
6. API: extend `match_results` response schema with the per-component breakdown.
7. Frontend: skill-gap panel + tooltip.
8. Eval: build the 20-pair eyeball set, document scores before/after.
9. Update README with the new architecture diagram.

## Definition of done

Composite scoring is live, pgvector is healthy, the eyeball set shows clear improvement over Phase 1, and the new score breakdown is visible to users.
