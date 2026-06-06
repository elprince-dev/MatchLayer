# SQLAlchemy async engine and the per-request session

## Introduction

This document explains how a Python application talks to a relational database
through a library that maps database rows to Python objects, and how it gives
each web request its own short-lived database conversation. The library is
SQLAlchemy, in its version 2.x asynchronous form: it provides an engine (the
object that owns connections to the database), a session (a single unit-of-work
conversation that batches reads and writes), and a way to produce a fresh session
per request. The terms are defined as they appear below. This belongs in the
Backend track because every endpoint that reads or writes data does so through
one of these per-request sessions.

**Learning outcomes** — after reading this document you will be able to:

- Explain what a database engine is and why it is created once and shared for the whole process.
- Describe what a session factory is and what a per-request session represents as a unit of work.
- Explain how a web framework injects a fresh session into each request and guarantees it is closed afterward.
- Recognise the common mistakes around async sessions and recover from them.

Prerequisites: this document builds on
[async Python and the asyncio model](03-backend-03-async-python-and-asyncio.md), which
explains the `async`/`await` style the engine and session use, and on
[Pydantic and typed settings](03-backend-02-pydantic-and-pydantic-settings.md), which explains
the validated configuration object the engine reads its connection string from.

## Problem it solves

An application that uses a database needs two things at once: a cheap, reusable
supply of database connections, and a clean boundary around each request's reads
and writes so they commit or roll back as a unit. The naive approach — open a raw
connection wherever you need one, run a query, close it — works for a one-off
script but breaks down in a server.

The common prior approach — ad-hoc connections and hand-written Structured Query Language (SQL) strings
scattered through the code — has real costs:

- Opening a brand-new connection for every query is slow, because establishing a database connection is far more expensive than running a query on an existing one.
- Without a unit-of-work boundary, a request that does several writes can leave the database half-updated when one of them fails.
- Hand-built query strings are easy to get wrong and easy to make unsafe, and they scatter the database vocabulary across the codebase instead of centralising it.

SQLAlchemy's async Application Programming Interface (API) solves this with a long-lived engine that hands out
reusable connections, a session that scopes a request's work into one
unit-of-work, and a typed query interface that builds statements safely. A
framework-level provider gives each request a fresh session and closes it when the
response is sent.

## Mental model

Think of the engine as a library's front desk that owns a small set of reading
rooms (connections). A session is one patron's research visit: the patron checks
out a reading room from the desk, does all their reading and note-taking for that
visit, and at the end either files their notes (commit) or discards them (roll
back), then returns the room to the desk for the next patron.

When a request arrives, the per-request session works like this:

1. The framework asks the session factory for a new session for this request.
2. The session borrows a connection from the engine's pool of reading rooms.
3. The request's handler runs its reads and writes through that one session.
4. When the handler returns (or raises), the surrounding block closes the session and returns its connection to the pool.
5. The next request repeats the cycle with its own fresh session, never sharing one with another request.

The engine is created once and lives for the whole process; sessions are created
and destroyed constantly, one per request.

## How it works

A database engine is the object that manages connectivity to the database. It is
created once, near startup, from a connection string (the address and credentials
of the database). Internally it keeps a pool of open connections so that work
does not pay the cost of establishing a connection every time; borrowing one from
the pool and returning it is cheap. Because the engine is expensive to build and
safe to share, an application creates exactly one and reuses it everywhere.

A session is a single unit-of-work: a conversation with the database that tracks
the objects you have loaded and the changes you intend to make, and flushes them
to the database as one coherent batch. It borrows a connection from the engine for
the duration of its work and returns it when closed. Sessions are deliberately
short-lived and are not shared between concurrent units of work, because the
changes tracked inside one are meant to commit or roll back together. To avoid
constructing sessions by hand each time, applications use a session factory: a
configured callable that produces a new session bound to the engine on demand.

In a web framework the natural unit of work is one request, so the framework is
given a provider that yields a fresh session at the start of each request and
closes it at the end. The provider is written as a generator that opens a session
inside a context-managed block, yields it to the handler, and — because the block
guarantees cleanup even when the handler raises — closes the session and returns
its connection to the pool no matter how the request ends. Tests swap this
provider for one that points at a test database, which is why isolating session
creation behind a single provider matters. In the async form, the engine, the
session, and the provider are all asynchronous, so a query that waits on the
database pauses the request's coroutine and frees the event loop to serve others.

## MatchLayer Phase 1 usage

In MatchLayer all three pieces live in
`apps/api/src/matchlayer_api/core/db.py`. The engine is created once at module
import from the validated connection string:

Source: `apps/api/src/matchlayer_api/core/db.py`

```python
engine: AsyncEngine = create_async_engine(
    str(_settings.database_url),
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=5,
)
```

The session factory is bound to that engine, and the per-request provider opens a
session inside an `async with` block so it is always closed, even on error:

Source: `apps/api/src/matchlayer_api/core/db.py`

```python
async def get_session() -> AsyncIterator[AsyncSession]:
```

Source: `apps/api/src/matchlayer_api/core/db.py`

```python
    async with SessionLocal() as session:
        yield session
```

Every endpoint that needs the database declares this provider as a dependency, so
each request receives its own session and the framework closes it once the
response is sent. Tests override this one provider through the framework's
dependency-override mechanism rather than reaching into module globals, which
keeps each test pointed at its own database. The engine's connection-pool
settings — including the liveness check on checkout — are covered in
[Connection pooling and pre-ping](03-backend-05-connection-pooling-and-pre-ping.md).

## Common pitfalls

- **Mistake:** Creating a new engine per request instead of one shared engine for the process.
  **Symptom:** Performance is poor and the database complains about too many connections, because each request spins up its own pool instead of reusing one.
  **Recovery:** Create the engine once at module or startup scope and share it; create only sessions per request, drawn from that one engine.

- **Mistake:** Sharing a single session across concurrent requests or holding one open for the whole process.
  **Symptom:** Requests interfere with each other's pending changes, or you see errors about a session being used concurrently.
  **Recovery:** Give every request its own fresh session from the factory, scoped to that request and closed at the end.

- **Mistake:** Forgetting to close the session (or closing it only on the success path) so connections are never returned to the pool.
  **Symptom:** The pool is exhausted under load and new requests hang waiting for a connection that is never released.
  **Recovery:** Open the session inside a context-managed block (or a generator dependency that does) so it is closed whether the handler returns or raises.

- **Mistake:** Accessing attributes of loaded objects after the session that loaded them has already closed.
  **Symptom:** An error about a detached object or a lazy load that cannot run, because the session that owned the object is gone.
  **Recovery:** Configure the session so loaded objects stay usable after commit for the serialization step, and keep object access within the request's session lifetime.

## External reading

- [SQLAlchemy: asyncio extension](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html)
- [SQLAlchemy: engine and connection basics](https://docs.sqlalchemy.org/en/20/core/engines.html)
- [SQLAlchemy: session basics and the unit of work](https://docs.sqlalchemy.org/en/20/orm/session_basics.html)
- [FastAPI: dependencies with yield](https://fastapi.tiangolo.com/tutorial/dependencies/dependencies-with-yield/)
