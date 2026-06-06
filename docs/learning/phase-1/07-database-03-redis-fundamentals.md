# Redis fundamentals and the Phase 1 standby

## Introduction

This document explains an in-memory data store the local stack runs from day
one, and why it is present before any application code talks to it. That store
is Redis: a server that keeps its data in main memory (Random Access Memory)
rather than on disk, organised as keys mapped to values, which makes reads and
writes extremely fast. Because it lives in memory and speaks a tiny, fast
protocol, Redis is the usual choice for caching (keeping a ready-made copy of a
result so you do not recompute it), for rate limiting (counting how often
something happens in a time window), and for other short-lived shared state.
Phase 1 starts the Redis service now even though the application does not
connect to it until Phase 4, so the local stack already matches the service
shape it will eventually need. This belongs in the Database and storage track
because Redis is the third store in the stack, alongside the relational
database and the object store.

**Learning outcomes** — after reading this document you will be able to:

- Explain what an in-memory key-value store is and why it is fast.
- Name the common jobs Redis is used for and why each suits an in-memory store.
- Explain the trade-off between speed and durability that an in-memory store makes.
- Explain why a dependency can be declared and run in the stack before any code uses it.

Prerequisites:

- [Postgres versus MinIO: two stores, two jobs](07-database-02-postgres-vs-minio.md) introduces the other two stores in the stack that Redis sits beside.

## Problem it solves

Some data is needed fast and often, lives for a short time, and is shared across
all the processes handling requests: a cached computation, a per-user request
counter, a short-lived token. Asking the main relational database for this on
every request is wasteful, because a durable on-disk database is tuned for
correctness and permanence, not for thousands of tiny ephemeral reads per
second. The problem is a fast, shared place for short-lived data.

A common prior approach is to keep this state inside each application process's
own memory. That approach has real costs:

- Each process has its own copy, so a counter or cache in one process is invisible to the others, and the application behaves inconsistently depending on which process served the request.
- The state vanishes when a process restarts, so caches go cold and counters reset at the worst times.
- There is no shared, atomic way to coordinate across processes — for example, to enforce one global rate limit — because nothing is actually shared.

Redis solves this by being a single, fast, shared store that every process talks
to over the network. One cache, one set of counters, one source of short-lived
truth, all kept in memory for speed.

## Mental model

Think of Redis as a whiteboard on the wall of a busy kitchen, shared by every
cook (process):

1. Anyone can write a labelled note on the board (set a key to a value) or read one (get a key) in an instant, because the board is right there in the room rather than in a filing cabinet down the hall.
2. Every cook sees the same board, so a count chalked up by one cook is immediately visible to all the others (shared state across processes).
3. Some notes are written with an expiry — "discard after 10 minutes" — and wipe themselves automatically (keys with a time-to-live), which suits short-lived data.
4. The board is fast precisely because it is in the room and not durable storage; if the kitchen loses power, an unsaved board is wiped (in-memory data is volatile).
5. So the board holds working notes you can afford to lose or recompute, never the permanent records — those stay in the filing cabinet (the durable database).

The whiteboard is for speed and sharing; the filing cabinet is for permanence.
Knowing which is which keeps you from chalking irreplaceable data onto a surface
that can be wiped.

## How it works

An in-memory key-value store keeps all of its data in main memory and exposes it
as a dictionary: every value is stored under a unique key, and you read, write,
or delete a value by naming its key. Keeping the data in memory is what makes it
fast — there is no disk seek on the hot path — and it also explains the central
trade-off: memory is volatile, so data held only in memory is lost if the server
stops, unless the store also writes a copy to disk.

Such a store typically supports more than plain strings: counters that can be
incremented atomically, lists, sets, and hashes, plus the ability to attach a
time-to-live (an expiry, after which the key is removed automatically). These
features map directly onto its common jobs. Caching stores a computed result
under a key with a short expiry, so repeated requests read the cached copy
instead of recomputing. Rate limiting uses an atomic counter per user and time
window, incremented on each action and compared against a limit. Short-lived
coordination state uses keys that expire on their own so nothing has to be
cleaned up by hand.

Durability is configurable but secondary. The store can periodically snapshot
its data to disk or append a log of writes, which lets it recover most data
after a restart, but it is still fundamentally a fast, memory-first store and
not a system of record. The right mental rule is that anything in it should be
either reconstructable (a cache you can recompute) or acceptable to lose (a
transient counter). Data you cannot afford to lose belongs in a durable
database, not here.

A service like this can also be present in a deployment before any code uses it.
A container stack is a declaration of which services exist; declaring and
starting a service makes it available, but nothing forces other code to connect
to it. Running an unused-but-declared dependency early is a deliberate way to
keep local and production environments identical in shape and to make the
eventual first use a configuration step rather than an infrastructure change.

## MatchLayer Phase 1 usage

The Redis service is declared in `docker-compose.yml` alongside the database and
the object store. Its definition pins the image by digest, publishes the
standard Redis port, and gives it a healthcheck so the stack can wait for it to
be ready:

Source: `docker-compose.yml`

```yaml
redis:
  image: redis:7-alpine@sha256:6ab0b6e7381779332f97b8ca76193e45b0756f38d4c0dcda72dbb3c32061ab99
  ports:
    - "6379:6379"
  healthcheck:
    test: ["CMD", "redis-cli", "ping"]
    interval: 2s
    timeout: 3s
    retries: 30
```

No application code in this phase opens a connection to it; the service only
runs and reports healthy. Standing it up now keeps a future option open at no
real cost: when the caching and rate-limiting work lands in Phase 4, the
dependency is already declared, pinned, and running locally, so adopting it is a
matter of pointing code at the existing service rather than reshaping the stack.
The healthcheck — a `redis-cli ping` that expects a `PONG` reply — also exercises
the same readiness machinery the other services use, so the standby service
behaves like a first-class member of the stack from the start.

## Common pitfalls

- **Mistake:** Storing data you cannot afford to lose only in an in-memory store.
  **Symptom:** After a restart or eviction the data is gone, and there is no durable copy to recover it from.
  **Recovery:** Keep the system-of-record data in the durable database and use the in-memory store only for caches and transient state you can recompute or lose.

- **Mistake:** Treating cached values as always fresh and never setting an expiry.
  **Symptom:** Users see stale results long after the underlying data changed, because nothing ever invalidates the cached copy.
  **Recovery:** Attach a sensible time-to-live to cached keys and invalidate them when the source data changes.

- **Mistake:** Assuming a service that is declared in the stack is actually being used by the application.
  **Symptom:** Time is wasted debugging a "Redis problem" that cannot exist yet because no code connects to it in this phase.
  **Recovery:** Confirm whether any code path opens a connection before attributing behaviour to the service; a healthy idle service is expected here.

- **Mistake:** Leaving the in-memory store reachable on its published port with no access control in a shared environment.
  **Symptom:** Anything on the network can read or overwrite the store's contents, since it answers any client that connects.
  **Recovery:** Bind it to the local development host only and require authentication or network isolation anywhere it is not strictly local.

## External reading

- [Redis documentation](https://redis.io/docs/latest/)
- [Redis: an introduction to data types](https://redis.io/docs/latest/develop/data-types/)
- [Redis: key expiration and time-to-live](https://redis.io/docs/latest/commands/expire/)
- [Redis: persistence and durability options](https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/)
- [Docker Compose: services and dependencies](https://docs.docker.com/compose/)
