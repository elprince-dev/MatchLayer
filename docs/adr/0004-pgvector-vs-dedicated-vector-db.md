# 0004 — Vector storage: pgvector over a dedicated vector DB

**Status:** Accepted
**Date:** 2026-05-23
**Applies to:** Phase 2+

## Context

Phase 2 introduces semantic embeddings for resume/JD matching. Embeddings need fast similarity search. The two main paths:

- **Dedicated vector DB** — Pinecone, Weaviate, Qdrant, Milvus.
- **Postgres with the pgvector extension** — adds vector type + index types (IVFFlat, HNSW) to a standard Postgres.

## Decision

**Postgres 16 + pgvector**, single database, both relational and vector data.

## Rationale

- **One fewer service to operate.** Adding a second database doubles ops surface (backups, monitoring, networking, IaC).
- **Sufficient for expected scale.** Tens of thousands of resumes, not millions. pgvector with HNSW indexing easily handles this with sub-50ms p95 latency.
- **Transactional consistency.** Insert a resume and its embedding in the same transaction. No two-system consistency dance.
- **Free tier compatibility.** Fly Postgres, Supabase, and Neon all support pgvector on free or near-free tiers. Pinecone's free tier is aggressive on limits.
- **Migration path stays open.** If MatchLayer ever needs millions of vectors with sub-10ms latency, swapping in a dedicated vector DB is a contained change behind the embedding service abstraction.

## Consequences

**Positive**
- Single database, single backup, single connection pool, single set of credentials.
- Joins between vector results and relational data are native SQL, not application-side stitching.
- pgvector is open-source and well-maintained.

**Negative**
- Slower than top-tier dedicated vector DBs at very high scale (>1M vectors with sub-10ms requirements).
- HNSW index parameters require some tuning.
- Adds one extension to the Postgres dependency, which complicates self-hosting if we ever leave managed Postgres.

## Alternatives considered

- **Pinecone:** rejected. Adds a vendor + a service for a problem we don't have at our scale.
- **Weaviate / Qdrant / Milvus self-hosted:** rejected. More ops than we want.
- **Plain Postgres without pgvector** (compute similarity at query time): rejected. Performance falls off a cliff at any non-trivial volume.
