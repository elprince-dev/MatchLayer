# pgvector and why Phase 1 stops short of it

## Introduction

This document explains a Postgres capability the project deliberately does not
use yet, and why drawing that line matters. The capability is pgvector: an
extension to PostgreSQL ("Postgres", the relational database the stack runs)
that adds a vector data type and similarity search, where a vector is an ordered
list of numbers and similarity search means finding the stored vectors closest
to a query vector. Extensions are optional add-on modules that bolt extra types
and functions onto Postgres without changing the core. pgvector becomes relevant
when an application needs to search by meaning rather than by exact value, which
this project plans for Phase 2. This document explains what pgvector is, the
problem it would solve, and why Phase 1 runs a plain Postgres image and stops
short of installing it. This belongs in the Database and storage track because
it marks the edge of what the Phase 1 database does.

**Learning outcomes** — after reading this document you will be able to:

- Explain what a vector embedding is and what similarity search does at a high level.
- Describe what a Postgres extension is and how pgvector extends the database.
- Explain why an unused capability is left out of an early build rather than installed in advance.
- Recognise the boundary between what the current database does and what a later one will.

Prerequisites:

- [PostgreSQL 16 fundamentals](07-database-01-postgresql-fundamentals.md) explains the relational database that pgvector extends.

## Problem it solves

Some searches cannot be expressed as exact matches. "Find resumes similar in
meaning to this job description" is not a keyword lookup — two documents can
share almost no words yet mean nearly the same thing. The problem pgvector
addresses is searching by semantic closeness instead of by literal equality,
directly inside the database that already holds the records.

A common prior approach is to bolt on a separate, dedicated vector database — a
second specialised service whose only job is to store vectors and answer
nearest-neighbour queries. That approach has real costs:

- It adds another service to run, secure, back up, and keep in sync with the relational database, which is a meaningful operational burden for a small project.
- The vectors live apart from the rows they describe, so every query that needs both the record and its similarity result has to coordinate two systems.
- It is one more moving part to pay for and reason about before the application has even proven it needs semantic search.

pgvector solves the same need with one fewer service: it keeps the vectors in
the same Postgres database as the rows, so a single query can filter on ordinary
columns and rank by vector similarity together. That consolidation is the
recorded rationale for preferring it over a dedicated vector store.

## Mental model

Think of a library that, until now, has only let you find books by their exact
title. Semantic search is asking instead for "books that feel like this one",
and a vector is the trick that makes that possible:

1. Each book is summarised as a long list of numbers that captures its themes — books about similar things end up with similar lists (an embedding).
2. To find books like a given one, you turn that book into its own list of numbers and look for the stored lists that sit closest to it (nearest-neighbour search).
3. "Closest" is measured as a distance between the two lists of numbers, so ranking by similarity becomes ranking by smallest distance.
4. The library's existing catalogue system learns one new trick — storing these number-lists and sorting by distance — rather than the town building a second, separate library only for this.
5. Until anyone actually requests "books like this one", the new trick stays switched off, and the catalogue works exactly as it did before.

The number-lists are the vectors; teaching the existing catalogue to handle them
is the extension. Leaving the trick switched off until it is needed keeps the
catalogue simple.

## How it works

A vector embedding is a fixed-length list of numbers produced by a model that
reads a piece of content — a sentence, a document, an image — and outputs
coordinates in a high-dimensional space, arranged so that content with similar
meaning lands near other similar content. Comparing two pieces of content then
reduces to measuring how close their vectors are, using a distance such as the
cosine of the angle between them or the straight-line distance.

A relational database does not understand these lists out of the box. An
extension closes that gap: it is an installable module that registers new column
types, operators, and index methods with the database engine. The vector
extension adds a column type that stores an embedding, distance operators that
compare two vectors, and specialised index types that make nearest-neighbour
search fast by organising vectors so the engine can find close ones without
comparing against every row.

Crucially, an extension is opt-in and additive. Until it is installed and a
column actually uses its type, the database behaves exactly as a plain
installation does — same tables, same queries, same performance. That property
is what lets a team defer the capability cleanly: the decision to add semantic
search is a future, isolated change (install the extension, add a column, add an
index, populate the vectors), not something that has to be wired in from the
start. Adding it before any feature needs it would mean carrying an unused
column type, an embedding model, and the discipline of keeping vectors current —
all cost, no benefit, until the search that uses them exists. The disciplined
default is to run the standard database and turn the capability on at the moment
the feature that needs it arrives.

## MatchLayer Phase 1 usage

The Postgres service in `docker-compose.yml` runs the stock official image,
which does not bundle the vector extension:

Source: `docker-compose.yml`

```yaml
postgres:
  image: postgres:16-alpine@sha256:16bc17c64a573ef34162af9298258d1aec548232985b33ed7b1eac33ba35c229
```

That image is plain PostgreSQL 16 with no extension preinstalled, so the
database in this phase has no vector column type and runs no similarity search —
it is the deliberate stopping point. Semantic search arrives in Phase 2, at which
point the image (or an initialisation step) gains the extension and a migration
adds the vector column and index; the decision to take that route rather than
adopt a separate vector database is recorded in the architecture decision record
at `docs/adr/0004-pgvector-vs-dedicated-vector-db.md`. Until then, keeping the
unmodified image means nothing in the current stack carries the weight of a
capability it does not yet use.

See the Architecture Decision Record (ADR)
[0004 — pgvector over a dedicated vector database](../../adr/0004-pgvector-vs-dedicated-vector-db.md)
for the full rationale behind this boundary.

## Common pitfalls

- **Mistake:** Assuming the stock Postgres image can already store vectors and run similarity search.
  **Symptom:** A query that uses the vector column type or distance operators fails with an error about an unknown type or operator.
  **Recovery:** Recognise that the extension is not installed in this phase; semantic search is added later when the feature that needs it lands.

- **Mistake:** Installing and wiring up the extension early "to be ready", before any feature uses it.
  **Symptom:** The stack carries an unused column type and embedding machinery that must be maintained and kept current for no current benefit.
  **Recovery:** Keep the standard image now and add the extension as one isolated change at the moment the search feature is built.

- **Mistake:** Confusing keyword matching with semantic similarity and expecting the current database to rank by meaning.
  **Symptom:** Results match shared words but miss documents that mean the same thing in different words, and there is no way to rank by closeness.
  **Recovery:** Use the keyword and exact-match tools the current database offers for now, and treat meaning-based ranking as the capability the later extension provides.

- **Mistake:** Reaching for a separate dedicated vector database without checking the recorded decision.
  **Symptom:** A second storage service is proposed that duplicates data and adds operational load the project chose to avoid.
  **Recovery:** Consult the architecture decision record on vector storage and follow the one-database approach it settled on.

## External reading

- [PostgreSQL 16 documentation](https://www.postgresql.org/docs/16/index.html)
- [PostgreSQL: extensions and the create-extension command](https://www.postgresql.org/docs/16/sql-createextension.html)
- [PostgreSQL: the data definition language](https://www.postgresql.org/docs/16/ddl.html)
- [Docker official image: postgres on Docker Hub](https://docs.docker.com/docker-hub/)
