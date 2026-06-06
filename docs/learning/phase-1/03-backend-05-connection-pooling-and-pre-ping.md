# Connection pooling and pre-ping

## Introduction

This document explains how a database client keeps a small set of open
connections ready to reuse instead of opening a new one for every query, and a
specific safety check that detects connections that have silently gone dead. The
reusable set is a connection pool: a managed group of open database connections
that the application borrows from and returns to. The safety check is pre-ping: a
tiny test query run at the moment a connection is borrowed, to confirm the
connection is still alive before handing it to the code that needs it. This
belongs in the Backend track because it is the reliability layer underneath every
database query the application makes.

**Learning outcomes** — after reading this document you will be able to:

- Explain what a connection pool is and why reusing connections is faster than opening one per query.
- Describe what the pool size and overflow limits control and how they bound concurrency.
- Explain what pre-ping does and which failure it prevents.
- Recognise the common mistakes around pooling and recover from them.

Prerequisites: this document builds on
[SQLAlchemy async engine and the per-request session](03-backend-04-sqlalchemy-async-and-session-dependency.md),
which introduces the database engine that owns the pool described here.

## Problem it solves

Opening a connection to a database is expensive: it involves a network handshake
and authentication before any query can run. If an application opened a fresh
connection for every query and closed it afterward, that setup cost would dominate
and the database would be churning through connections constantly. The naive
approach of one-connection-per-query is simple but does not hold up under real
traffic.

The common prior approach — open, use, and close a connection per query — has real
costs:

- The repeated handshake-and-authenticate cost is paid on every single query, adding latency that has nothing to do with the actual work.
- A burst of traffic opens a burst of connections, and the database has a hard limit on how many it will accept, so the application can exhaust that limit and start failing.
- There is no shared place to bound how many connections the application uses at once, so concurrency is uncontrolled.

A connection pool solves the cost problem by keeping a handful of connections open
and lending them out: borrowing and returning a connection is cheap, so the
handshake cost is paid rarely. Pool limits solve the concurrency problem by
capping how many connections the application will hold. Pre-ping solves a separate
reliability problem described below.

## Mental model

Think of a connection pool as a small fleet of company cars. Rather than buying a
new car every time someone needs to drive somewhere (and scrapping it on return),
the company keeps a few cars in a lot. An employee checks one out, drives it,
and returns it for the next person. Buying a car is slow and costly; checking one
out of the lot is quick.

Pre-ping is the quick walk-around inspection before each trip:

1. An employee requests a car from the lot (the application asks the pool for a connection).
2. Before handing over the keys, the attendant turns the ignition to confirm the car still starts (pre-ping runs a tiny test query).
3. If it starts, the employee takes it (the live connection is handed to the caller).
4. If it does not start — the battery died while parked (the database closed the connection while it sat idle) — the attendant quietly swaps in a working car (the pool discards the dead connection and provides a fresh one).
5. On return, the car goes back to the lot for reuse rather than to the scrapyard (the connection returns to the pool).

The pool makes reuse cheap; the pre-ping inspection makes sure a long-parked
connection has not silently died before someone relies on it.

## How it works

A connection pool is a managed cache of open database connections held by the
client. When code needs the database, it checks out a connection from the pool;
when it is done, it returns the connection to the pool rather than closing it. The
expensive setup — network connect and authenticate — happens only when the pool
first creates a connection, not on every checkout, so the steady-state cost of
running a query drops to nearly the cost of the query itself.

The pool is bounded by two numbers. The pool size is the number of connections
kept open and reused under normal load. An overflow allowance permits a bounded
number of extra connections to be opened temporarily when demand briefly exceeds
the pool size; once demand falls, those extras are closed again. Past the sum of
the two, further requests wait for a connection to be returned instead of opening
unbounded sockets. Together these limits cap how much load the application can
place on the database and prevent a traffic spike from exhausting the database's
own connection limit.

Pre-ping addresses a failure that pooling alone introduces. A pooled connection
can sit idle and, in the meantime, be closed by the database server, a firewall,
or a network device — without the client noticing. The next time the application
borrows that connection and runs a query, the query fails with a confusing
connection error. Pre-ping prevents this: at checkout, the pool issues a trivial
test query first. If it succeeds, the connection is healthy and is handed over. If
it fails, the pool transparently discards the dead connection and supplies a fresh
one, so the caller always receives a working connection. The cost is one tiny
extra round-trip per checkout, which is negligible compared with the cost of an
intermittent, hard-to-reproduce failure after the database restarts or a network
blip drops idle connections.

## MatchLayer Phase 1 usage

In MatchLayer the pool is configured where the engine is created, in
`apps/api/src/matchlayer_api/core/db.py`. The engine turns on pre-ping and sets
the pool size and overflow allowance in one place:

Source: `apps/api/src/matchlayer_api/core/db.py`

```python
engine: AsyncEngine = create_async_engine(
    str(_settings.database_url),
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=5,
)
```

The `pool_pre_ping=True` setting is what makes a stale or broken connection
detected at checkout and replaced rather than handed to a request handler, so a
database restart or an idle-connection timeout becomes recoverable instead of
surfacing as an intermittent error. The `pool_size=5` and `max_overflow=5`
settings cap the connections one process holds — five long-lived connections plus
up to five burst connections under load — so a traffic spike queues rather than
opening unbounded sockets at the database. The same module also runs a one-time
liveness query at startup so a misconfigured database fails the deploy fast, and
that startup probe and the per-request session that draws from this pool are
covered in
[SQLAlchemy async engine and the per-request session](03-backend-04-sqlalchemy-async-and-session-dependency.md).

## Common pitfalls

- **Mistake:** Leaving pre-ping off and assuming a pooled connection is always alive.
  **Symptom:** Requests fail intermittently with connection errors right after the database restarts or after a period of low traffic, then recover on retry.
  **Recovery:** Enable pre-ping on the engine so each checkout verifies the connection and transparently replaces a dead one.

- **Mistake:** Setting the pool size and overflow far higher than the database can accept.
  **Symptom:** Under load the database rejects new connections because the application's many processes together exceed the server's connection limit.
  **Recovery:** Size the pool with the number of application processes in mind so the total stays within the database's limit, and let requests queue rather than opening unbounded connections.

- **Mistake:** Borrowing a connection (or session) and never returning it.
  **Symptom:** The pool drains and new requests hang waiting for a connection that is never released back.
  **Recovery:** Always return the connection — use a context-managed block or a request-scoped provider that releases it whether the work succeeds or fails.

- **Mistake:** Treating pre-ping as a substitute for handling errors that occur mid-query.
  **Symptom:** A connection that dies _during_ a long query still raises an error, because pre-ping only checks at checkout, not continuously.
  **Recovery:** Keep pre-ping for the checkout-time check and still handle query-time failures with a retry or a clean error path, since the two cover different moments.

## External reading

- [SQLAlchemy: connection pooling](https://docs.sqlalchemy.org/en/20/core/pooling.html)
- [SQLAlchemy: dealing with disconnects and pre-ping](https://docs.sqlalchemy.org/en/20/core/pooling.html#disconnect-handling-pessimistic)
- [SQLAlchemy: engine creation API](https://docs.sqlalchemy.org/en/20/core/engines.html)
- [PostgreSQL: connection limits and max_connections](https://www.postgresql.org/docs/current/runtime-config-connection.html)
