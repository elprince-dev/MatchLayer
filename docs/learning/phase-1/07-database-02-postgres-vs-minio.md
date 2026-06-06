# Postgres versus MinIO: two stores, two jobs

## Introduction

This document explains why the local stack runs two different storage systems
side by side, and how to decide which one a given piece of data belongs in. The
first is PostgreSQL ("Postgres"): a relational database, meaning a server that
keeps structured data in tables of rows and columns and answers questions with a
query language. The second is MinIO: an object store that speaks the Amazon
Simple Storage Service (S3) protocol, meaning it saves whole files ("objects")
under string keys in flat containers called buckets, the same way Amazon's
cloud file storage does. Phase 1 uses both because they solve different
problems, and putting the wrong data in the wrong one is a common and costly
mistake. This belongs in the Database and storage track because choosing the
right store is the first decision in any feature that saves data.

**Learning outcomes** — after reading this document you will be able to:

- Explain what a relational database is good at and what an object store is good at.
- Describe the difference between structured records and opaque binary blobs.
- Explain why storing large files inside a relational database is usually the wrong choice.
- Decide, for a new piece of data, which of the two stores it belongs in.

Prerequisites:

- [PostgreSQL 16 fundamentals](07-database-01-postgresql-fundamentals.md) explains the relational database this document compares against the object store.

## Problem it solves

An application has two very different kinds of data to keep. One kind is
structured records you query and relate — accounts, timestamps, status flags —
where you constantly ask questions like "find the row whose email equals this".
The other kind is large opaque files — an uploaded resume in Portable Document
Format (PDF), an image — that you store whole and hand back whole, and never
query by their internal contents. The problem is that no single store is ideal
for both.

A common prior approach is to keep everything in one place: stuff the uploaded
file into a column of the database alongside the structured fields. That
approach has real costs:

- Large binary blobs bloat the database's storage and its backups, making every backup and restore slower even when you only care about the small structured rows.
- Reading a row that contains a multi-megabyte file pulls that whole blob through the database connection and its memory, competing with the fast small queries the database is tuned for.
- The database cannot do anything useful with the file's bytes — it cannot index, search, or validate them — so the blob gets none of the relational machinery's benefits while paying all of its costs.

Splitting the two solves it: structured records live in the relational
database, and the file bytes live in an object store designed for exactly that.
The database row keeps a small reference (a key string) to the object.

## Mental model

Think of an office that handles paperwork. There are two pieces of equipment,
and using the right one for each task keeps the office fast:

1. A filing cabinet of index cards (the relational database) holds one small card per case, each with labelled fields you can sort and cross-reference quickly.
2. A warehouse of numbered storage boxes (the object store) holds the bulky documents themselves; each box has a label (a key) written on it.
3. When a document arrives, the bulky pages go into a warehouse box and the box's label is written onto the matching index card.
4. To look something up, you flip through the fast index cards; when you actually need the pages, the card tells you which warehouse box to fetch.
5. You never try to cram a thick folder of pages into the thin index-card drawer, and you never try to sort and cross-reference by riffling through warehouse boxes.

The filing cabinet answers questions; the warehouse holds weight. Each does the
job it is shaped for.

## How it works

A relational database stores data as rows in tables with typed columns, enforces
relationships and constraints between them, and exposes a declarative query
language so you can filter, join, sort, and aggregate. It is optimised for many
small, structured records and for answering arbitrary questions about them
quickly. Its strengths — indexing, transactions, joins — all assume the data is
structured and individually meaningful.

An object store works at a completely different granularity. Its unit is the
object: a whole file plus a little metadata, addressed by a unique key string
inside a flat container called a bucket. You put an object under a key, you get
it back by that key, and you delete it by that key. There are no rows, no
columns, no joins, and no querying by the file's internal bytes. In exchange it
scales to enormous numbers of large files cheaply, streams them efficiently, and
keeps that load away from the database. Object stores following the S3 protocol
expose this through a small, well-known set of operations (put, get, delete,
list) so application code written against that protocol works the same whether
the store is a local container or a cloud service.

The division of labour follows from these shapes. Anything you need to query,
relate, or update field-by-field — identities, ownership, status, timestamps —
belongs in the relational database. Anything that is a whole opaque file you
store and retrieve as a unit — an uploaded document, a generated artifact —
belongs in the object store, with only its key and a few descriptive fields
recorded as a row in the database. The row is the small, queryable handle; the
object is the heavy payload. This keeps the database lean and fast while the
object store absorbs the bulk.

## MatchLayer Phase 1 usage

The local stack in `docker-compose.yml` runs both stores as separate services.
The relational database is the `postgres` service, the primary datastore for
structured records:

Source: `docker-compose.yml`

```yaml
postgres:
  image: postgres:16-alpine@sha256:16bc17c64a573ef34162af9298258d1aec548232985b33ed7b1eac33ba35c229
  environment:
    POSTGRES_USER: matchlayer
    POSTGRES_PASSWORD: dev_only_password
    POSTGRES_DB: matchlayer
  ports:
    - "5432:5432"
```

The object store is the `minio` service, which provides S3-compatible storage
for uploaded resume files. It runs the MinIO server, exposes an Application
Programming Interface (API) port and a console port, and keeps its objects on
its own named volume:

Source: `docker-compose.yml`

```yaml
minio:
  image: minio/minio:RELEASE.2025-09-07T16-13-09Z@sha256:14cea493d9a34af32f524e538b8346cf79f3321eff8e708c1e2960462bd8936e
  command: server /data --console-address :9001
  environment:
    MINIO_ROOT_USER: matchlayer
    MINIO_ROOT_PASSWORD: dev_only_password
  ports:
    - "9000:9000"
    - "9001:9001"
  volumes:
    - matchlayer-minio-data:/data
```

Phase 1 uses both because the application has both kinds of data: account and
matching records that must be queried and related (Postgres), and uploaded
resume files that are stored and fetched as whole objects (MinIO). Running MinIO
locally also means the file-storage code talks to the same S3 protocol it will
use against a real cloud object store in production, so the storage code does not
change when the deployment target does. The comment block at the top of
`docker-compose.yml` records exactly this split between the two services.

## Common pitfalls

- **Mistake:** Storing uploaded file bytes in a database column instead of in the object store.
  **Symptom:** The database and its backups grow quickly, and queries that touch those rows get slow as the blobs are dragged through memory.
  **Recovery:** Store the file in the object store under a key and keep only that key plus descriptive fields as a row in the database.

- **Mistake:** Trying to query objects by their contents, as if the object store were a database.
  **Symptom:** There is no operation to filter by what is inside a file; code resorts to downloading and scanning every object, which is slow and does not scale.
  **Recovery:** Record the queryable facts as columns in the database row that references the object, and query those instead of the bytes.

- **Mistake:** Treating the two stores as one transaction, expecting a file write and a row write to roll back together.
  **Symptom:** A failure leaves an orphaned object with no row, or a row pointing at an object that was never written.
  **Recovery:** Order the operations deliberately and reconcile — write the object first then the row, and add cleanup for orphans — because the object store is not part of the database transaction.

- **Mistake:** Hard-coding direct file paths instead of going through the storage abstraction (the thin layer that hides whether the store is local or cloud).
  **Symptom:** Code works locally but breaks in production because the local filesystem path does not exist there.
  **Recovery:** Always read and write objects through the S3-protocol client so the same code targets the local container and the cloud store identically.

## External reading

- [PostgreSQL 16 documentation](https://www.postgresql.org/docs/16/index.html)
- [MinIO documentation](https://min.io/docs/minio/container/index.html)
- [MinIO: core administration concepts](https://min.io/docs/minio/linux/administration/concepts.html)
- [Docker Compose: multi-service applications](https://docs.docker.com/compose/)
