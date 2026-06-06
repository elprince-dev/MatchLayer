# Async Python and the asyncio model

## Introduction

This document explains how Python lets a single program make progress on many
slow tasks at once without using multiple threads, through a style of code
called asynchronous programming. The two building blocks are the `async`/`await`
keywords — syntax that marks a function as pausable and marks the points where it
may pause — and asyncio, the standard Python library that runs such functions. A
function defined with `async def` is a coroutine: a function that can suspend
itself partway through while it waits for something slow (a network reply, a
database answer) and hand control back so other work can run in the meantime.
This belongs in the Backend track because the web framework, the database access
layer, and the middleware are all written in this style.

**Learning outcomes** — after reading this document you will be able to:

- Explain what a coroutine is and what the `async` and `await` keywords do.
- Describe the event loop and how it interleaves many coroutines on one thread.
- Distinguish work that benefits from this model (waiting on input/output) from work that does not (heavy computation).
- Recognise the common mistakes in async code and recover from them.

Prerequisites: No prerequisites.

## Problem it solves

A server spends most of its time waiting — for a database to answer, for a remote
service to reply, for bytes to arrive over the network. The simplest model gives
each request its own thread (an operating-system-managed strand of execution) and
lets that thread block while it waits. That works, but it has concrete costs when
many requests are in flight at once.

The common prior approach — one blocking thread per concurrent request — has real
costs:

- Each thread consumes memory and operating-system bookkeeping, so thousands of mostly-waiting requests tie up thousands of mostly-idle threads.
- Switching between many threads has overhead the operating system pays even though almost every thread is doing nothing but waiting.
- Code that shares data across threads needs careful locking to stay correct, which is a frequent source of subtle bugs.

The asynchronous model solves this by noticing that a waiting task does not need
a whole thread — it needs a way to step aside and be resumed later. One thread
runs many coroutines, and whenever a coroutine pauses to wait, the thread picks
up another coroutine that is ready to run. The waiting costs almost nothing
because no thread is parked on it.

## Mental model

Think of one chef (a single thread) cooking many dishes. A blocking cook would
start one dish, stand and watch the pot boil, and refuse to touch another dish
until the first is plated. An async chef puts the pot on, sets a timer, and turns
to chop vegetables for a second dish while the first simmers; when a timer dings,
the chef returns to whichever dish is ready. The chef never does two knife
strokes at the literal same instant, but no time is wasted standing idle.

Here is how the pieces map to that picture:

1. Each `async def` function is a recipe the chef can start and pause — a coroutine.
2. The `await` keyword is the chef saying "this step needs to simmer; I'll step away and come back when it's ready."
3. The event loop is the chef: a scheduler that holds all the in-progress recipes and decides which ready one to advance next.
4. While one coroutine is paused at an `await`, the loop runs another coroutine that is ready, so the single thread stays busy.
5. When the awaited thing finishes, the loop resumes the paused coroutine right where it left off.

The whole model rests on coroutines voluntarily pausing at `await` points so the
one event loop can keep the thread productive.

## How it works

A coroutine is a function declared with `async def`. Calling it does not run it
immediately; it produces a coroutine object that does nothing until it is handed
to a scheduler. That scheduler is the event loop: a single loop, running on one
thread, that holds a set of coroutines and advances them one at a time. The loop
runs a coroutine until it reaches an `await` on something not yet finished, at
which point the coroutine suspends and returns control to the loop. The loop then
advances another coroutine that is ready. When the awaited operation completes,
the loop resumes the suspended coroutine from exactly the line it paused on, with
all its local variables intact.

The `await` keyword may only appear inside an `async` function, and it may only
be applied to an awaitable — typically another coroutine or a library object that
knows how to cooperate with the loop. This is why async tends to spread through a
codebase: to `await` a database call, the calling function must itself be
`async`, and so must its caller. The payoff is concurrency without threads: many
requests share one loop on one thread, and the only moments the loop can switch
between them are the `await` points, which makes the interleaving predictable.

The model only helps when the waiting is for input/output — network, disk,
database — because that is what can be handed off cheaply. It does not help with
work that is pure computation, because a long calculation has no natural pause
point: it never reaches an `await`, so it holds the single thread and stalls every
other coroutine. The discipline, therefore, is to keep coroutines free of any
long-running blocking call and to let them `await` cooperative library operations
instead. Code that genuinely must do heavy computation or call a blocking library
is moved off the loop (for example, onto a separate worker) so the loop stays
responsive.

## MatchLayer Phase 1 usage

In MatchLayer the database access layer is written as coroutines. The file
`apps/api/src/matchlayer_api/core/db.py` defines a startup probe as an `async`
function that awaits a database round-trip:

Source: `apps/api/src/matchlayer_api/core/db.py`

```python
async def verify_database_connection() -> None:
```

Inside that function the actual waiting happens at `await` points, where the
coroutine pauses and lets the event loop run other work until the database
answers:

Source: `apps/api/src/matchlayer_api/core/db.py`

```python
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
```

The per-request session provider in the same file is also a coroutine, declared
`async def get_session(...)`, so the web framework can `await` it while opening a
database session and resume it to close the session once the response is sent.
Because the framework, the database layer, and the request-handling middleware
are all written this way, one event loop on one thread serves many requests
concurrently — each request's coroutine pauses at its `await` points (mostly
database round-trips) and the loop fills that time with other requests.

## Common pitfalls

- **Mistake:** Calling a blocking, synchronous function (a slow computation or a non-cooperative library call) directly inside a coroutine.
  **Symptom:** The whole service becomes unresponsive while that one call runs, because the single event loop thread is stuck and cannot advance any other coroutine.
  **Recovery:** Replace the blocking call with an awaitable equivalent, or run the blocking work off the loop (for example, in a separate worker or thread pool) and `await` the result.

- **Mistake:** Calling a coroutine like an ordinary function and not awaiting it.
  **Symptom:** The work never happens; you get a coroutine object and often a warning that a coroutine was never awaited.
  **Recovery:** `await` the coroutine (from inside another `async` function) or schedule it on the loop, so the scheduler actually runs it.

- **Mistake:** Using `await` inside a plain, non-`async` function.
  **Symptom:** The code fails to even parse, because `await` is only valid inside an `async def`.
  **Recovery:** Mark the enclosing function `async def`, and update its callers to await it, following the chain up until it reaches code the framework already runs on the loop.

- **Mistake:** Assuming that because nothing runs in parallel on the thread, no interleaving can surprise you, and mutating shared state across an `await`.
  **Symptom:** A value read before an `await` is stale after it, because another coroutine ran during the pause and changed the shared state.
  **Recovery:** Treat every `await` as a point where other coroutines may run; re-read shared state after awaiting, or avoid sharing mutable state across pause points.

## External reading

- [Python documentation: coroutines and tasks](https://docs.python.org/3/library/asyncio-task.html)
- [Python documentation: the asyncio event loop](https://docs.python.org/3/library/asyncio-eventloop.html)
- [Python documentation: developing with asyncio (common mistakes)](https://docs.python.org/3/library/asyncio-dev.html)
- [FastAPI: concurrency and async/await](https://fastapi.tiangolo.com/async/)
