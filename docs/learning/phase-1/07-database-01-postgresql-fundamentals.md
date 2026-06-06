# PostgreSQL 16 fundamentals

## Introduction

This document explains the database that stores almost everything the
application remembers between requests. That database is PostgreSQL (often
shortened to "Postgres"): an open-source Relational Database Management
System (RDBMS), which means a long-running program that keeps your data in
tables and answers questions about it using a query language. The version
pinned here is PostgreSQL 16. This document teaches the four ideas a junior
developer needs before reading any database code: the relational model (data
organised as tables of rows and columns), schemas (named folders that group
tables inside one database), transactions (all-or-nothing bundles of changes),
and indexes (lookup structures that make searches fast). This belongs in the
Database and storage track because it is the foundation every later database
topic builds on.

**Learning outcomes** — after reading this document you will be able to:

- Explain what the relational model is and how tables, rows, and columns relate to each other.
- Describe what a schema is and why grouping tables under a namespace helps.
- Explain what a transaction guarantees and why all-or-nothing behaviour matters.
- Explain what an index does and the trade-off it introduces.

Prerequisites: No prerequisites. This is a foundational Database and storage
topic written for a reader who has never run a database before.

## Problem it solves

Applications need to remember facts — who registered, what they uploaded, when
something happened — in a way that survives a restart, stays correct when many
requests touch it at once, and can be queried flexibly later. The concrete
problem is durable, consistent, queryable storage for structured data.

A common prior approach is to store data in flat files (plain text or
spreadsheet-style files the program reads and rewrites). That approach has real
costs:

- There is no safe way for two processes to write the same file at once, so concurrent updates corrupt each other or silently overwrite.
- There is no all-or-nothing guarantee: a crash halfway through writing several related records leaves the data half-updated and inconsistent.
- Answering a new question ("every account created last week") means scanning and parsing the whole file by hand each time, which is slow and error-prone.

A relational database solves all three. It serialises concurrent writes safely,
wraps related changes in transactions that either all succeed or all roll back,
and lets you ask new questions with a declarative query language instead of
hand-written file-scanning code.

## Mental model

Think of a relational database as a well-run reference library, and a query as
a request you hand to the front desk:

1. The library is divided into named rooms (schemas), and each room holds shelves of identically structured record cards (tables).
2. Every card in one shelf has the same labelled fields (columns) — for example a "users" shelf where every card has an id, an email, and a created-at date — and one filled-in card is a single row.
3. You hand the desk a written request in a fixed language ("give me every card in the users shelf whose created-at is after Monday"); you describe what you want, not how to find it.
4. To answer quickly the library keeps separate alphabetised catalogues (indexes) that point at the cards, so the desk does not walk every shelf.
5. When you ask to change several cards together, the desk does it as one sealed operation (a transaction): either every change is filed, or — if anything fails — none of them is, and the shelves look untouched.

The database is the library building; it stays open and running while many
visitors (requests) come and go, each getting consistent answers.

## How it works

The relational model organises data as tables. A table is a named collection of
rows, and every row has the same set of typed columns. One column (or a
combination) is the primary key — a value guaranteed unique within the table
that identifies each row. Tables relate to each other through foreign keys: a
column in one table that holds the primary-key value of a row in another,
which is how an "orders" table points at the "customers" table without copying
the customer's details into every order.

A schema is a namespace inside a single database: a named container that groups
tables (and other objects) so their names do not collide and so access can be
granted per group. A fresh database starts with a default schema named
`public`. One running database server can host many databases, each database
can hold many schemas, and each schema can hold many tables.

A transaction is a bundle of one or more statements that the database treats as
a single unit. Transactions provide four guarantees, abbreviated as Atomicity,
Consistency, Isolation, Durability (ACID): atomicity means all statements commit
or none do; consistency means the database moves from one valid state to
another; isolation means concurrent transactions do not see each other's
half-finished work; and durability means that once committed, the change
survives a crash. To deliver isolation without forcing every reader to wait for
every writer, Postgres uses Multi-Version Concurrency Control (MVCC): instead of
locking a row on read, it keeps multiple versions of a row and shows each
transaction the version that was current when it started. Readers never block
writers and writers never block readers.

An index is an auxiliary data structure that lets the database find rows
matching a condition without scanning the whole table. The default index type
is a B-tree (a balanced tree that keeps keys sorted), which makes equality and
range lookups fast. The trade-off is real: an index speeds up reads that use it
but adds work to every insert, update, and delete (because the index must be
kept current) and consumes storage. The guideline is to add an index for a
column you frequently filter or sort on, and not for columns you rarely query.

## MatchLayer Phase 1 usage

In Phase 1 the database runs as a container described in `docker-compose.yml`.
The Postgres service pins the exact image, sets the initial database name and
credentials through environment variables, publishes the standard port, and
mounts a named volume so the data outlives the container:

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
  volumes:
    - matchlayer-postgres-data:/var/lib/postgresql/data
```

The `POSTGRES_DB` value creates a database named `matchlayer` on first boot, and
the `POSTGRES_USER` becomes that database's owner. Schema-level access is set up
by an initialisation script that the container runs once, on the first boot of
the data volume, from `infra/docker/postgres-init/01-create-app-role.sql`. The
script grants the application role usage of the `public` schema and the
table-level privileges it needs:

Source: `infra/docker/postgres-init/01-create-app-role.sql`

```sql
GRANT USAGE ON SCHEMA public TO matchlayer_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO matchlayer_app;
```

This is the relational model, schemas, and access control in practice: one
database (`matchlayer`), one default schema (`public`), and a role whose
privileges on the tables in that schema are declared explicitly. Transactions
and indexes are exercised by the application's migrations and queries rather
than by the Compose file, but they all run inside the server this service
starts.

## Common pitfalls

- **Mistake:** Assuming a write is saved the moment a statement returns, without committing the surrounding transaction.
  **Symptom:** Changes vanish after a disconnect or appear in your session but not in anyone else's, because an open transaction was never committed.
  **Recovery:** Commit the transaction explicitly (or use a tool that auto-commits), and treat a group of related writes as one transaction that ends with a single commit.

- **Mistake:** Filtering or sorting on a column that has no index on a table that keeps growing.
  **Symptom:** Queries that were fast on test data become slow in proportion to table size, because the database scans every row.
  **Recovery:** Add an index on the column used in the `WHERE` or `ORDER BY` clause, and confirm the query plan now uses it.

- **Mistake:** Connecting as the database owner for ordinary application work instead of a least-privilege role.
  **Symptom:** A bug or compromised query can read or rewrite anything, and intended access restrictions have no effect.
  **Recovery:** Create a dedicated application role with only the privileges it needs and connect as that role, as the initialisation script does for `matchlayer_app`.

- **Mistake:** Treating one running server, one database, and one schema as interchangeable words.
  **Symptom:** Objects are created in the wrong place or "relation does not exist" errors appear, because the search path points at a different schema than expected.
  **Recovery:** Keep the hierarchy clear — a server hosts databases, a database holds schemas, a schema holds tables — and qualify names when in doubt.

## External reading

- [PostgreSQL 16 documentation](https://www.postgresql.org/docs/16/index.html)
- [PostgreSQL: the data definition language](https://www.postgresql.org/docs/16/ddl.html)
- [PostgreSQL: transactions](https://www.postgresql.org/docs/16/tutorial-transactions.html)
- [PostgreSQL: indexes](https://www.postgresql.org/docs/16/indexes.html)
- [PostgreSQL: schemas](https://www.postgresql.org/docs/16/ddl-schemas.html)
- [PostgreSQL: Multi-Version Concurrency Control](https://www.postgresql.org/docs/16/mvcc.html)
