# FastAPI and the application-factory pattern

## Introduction

This document explains how a modern Python web framework builds the object
that answers web requests, and a specific construction pattern — the
application factory — that makes that object easy to build, test, and
reconfigure. The framework is FastAPI, a Python library for building web
interfaces that is asynchronous (it can work on many requests concurrently
without one slow request blocking the others) and that speaks the
Asynchronous Server Gateway Interface (ASGI) — the standard contract,
explained below, between a Python web application and the server program that
runs it. An application factory is a function whose only job is to build and
return a fully configured application object, rather than creating that object
once as a module-level global. This belongs in the Backend track because it is
the entry point every other backend topic plugs into.

**Learning outcomes** — after reading this document you will be able to:

- Explain what the Asynchronous Server Gateway Interface (ASGI) is and why an async framework needs it.
- Describe what an application factory is and why returning a freshly built application from a function is easier to test than a module-level global.
- Name the steps a factory performs to wire logging, middleware, error handlers, and routes onto one application object.
- Recognise the common mistakes around factory wiring and recover from them.

Prerequisites: this document builds on
[async Python and the asyncio model](03-backend-03-async-python-and-asyncio.md), which
explains the `async`/`await` concurrency this framework is built on, and on
[Pydantic and pydantic-settings](03-backend-02-pydantic-and-pydantic-settings.md), which
explains the typed configuration object the factory reads.

## Problem it solves

A web application needs one object that holds every route, every piece of
shared setup, and every cross-cutting rule, so the server has a single thing to
hand requests to. The straightforward approach is to create that object once as
a module-level global and decorate it directly. That works for a script, but it
creates concrete problems as the application grows.

The common prior approach — one global application built at import time — has
real costs:

- Tests cannot build a second, differently configured application without re-importing the module, because the global is created the moment the module is first imported and is then frozen for the process.
- Configuration is read at import time, so a test that wants to exercise production-mode behaviour has no clean way to inject different settings.
- Tooling that only needs to inspect the application (for example, to dump its interface description) is forced to trigger all the import-time side effects the global performs.

The application-factory pattern solves this by moving construction into a
function. Calling the function returns a new, fully wired application; calling
it again with different configuration returns a second independent one. Tests
build a fresh instance per case, tooling builds one to inspect, and the running
server builds the one it serves.

## Mental model

Think of the factory as a recipe rather than a single finished cake. The recipe
(the function) can be followed as many times as you like, each time producing a
fresh cake configured to that occasion. A pre-baked cake sitting on the counter
(a module-level global) is the only cake you get, and you cannot change its
ingredients after the fact.

When the factory runs, it assembles the application in a fixed order:

1. Read the validated configuration object so every later step sees the same settings.
2. Configure logging first, so any later step that emits a log line uses the configured format.
3. Create the empty application object and attach a startup/shutdown hook (the lifespan) to it.
4. Add the cross-cutting request handlers (the middleware) in a deliberate order.
5. Register the error handlers that turn raised exceptions into structured responses.
6. Attach the routers that hold the actual endpoints, then return the finished application.

Each step layers one concern onto the object, and because the steps live in a
function, the whole sequence reruns cleanly every time the function is called.

## How it works

An asynchronous web framework cannot talk to a web server directly through the
older synchronous calling convention, because that convention assumes one
request is fully handled before the next begins. Instead it implements the
Asynchronous Server Gateway Interface (ASGI): a small, standard contract that
defines an application as a callable accepting three arguments — a `scope`
(metadata about the connection), a `receive` channel (to read incoming
messages), and a `send` channel (to write outgoing messages). Any ASGI server
can run any ASGI application, which is what lets you swap the server program
without rewriting the application.

The framework gives you a high-level application object that implements that
ASGI callable for you. You declare endpoints by writing functions and
associating them with a path and a method; the framework builds a routing table
and, on each request, matches the incoming path to the right function, calls it,
and turns its return value into a response. Because the framework is async, it
can suspend a request that is waiting on a slow resource (a database round-trip,
say) and use the freed time to make progress on other requests.

The application-factory pattern is the discipline of building that object inside
a function. The function reads configuration, constructs the application, and
then attaches everything the application needs in a fixed order: a lifespan hook
(code that runs once at startup and once at shutdown), middleware (wrappers that
see every request and response), exception handlers (functions that convert a
raised error into a response), and routers (grouped collections of endpoints).
The order matters because middleware composes as nested layers — the framework
runs the most recently added outer layer first on the way in and last on the way
out — so the sequence in which they are attached determines the sequence in which
they execute. Returning the assembled object from the function, rather than
binding it to a global, is what makes the construction repeatable and testable.

## MatchLayer Phase 1 usage

In MatchLayer the factory is the function `create_app` in
`apps/api/src/matchlayer_api/main.py`. It takes an optional settings override,
reads the cached settings when none is given, and returns a configured
application. The signature and the first construction step look like this:

Source: `apps/api/src/matchlayer_api/main.py`

```python
def create_app(settings: Settings | None = None) -> FastAPI:
```

The same file builds the application object with a title, a version, and the
lifespan hook attached:

Source: `apps/api/src/matchlayer_api/main.py`

```python
    app = FastAPI(
        title="MatchLayer API",
        version="0.0.0",
        lifespan=lifespan,
```

After construction the factory adds the Cross-Origin Resource Sharing (CORS)
middleware, then the request-id middleware, then registers the error handlers,
then includes the routers — the exact ordered wiring the Mental model describes.
A module-level `app` is built once from this same factory so the server command
`uvicorn matchlayer_api.main:app` has an object to serve, while tests call
`create_app` directly to get an isolated instance per test and the interface-dump
tool calls it to inspect the routes without serving traffic.

## Common pitfalls

- **Mistake:** Configuring logging or other global state _after_ creating the application object and attaching routes.
  **Symptom:** Early startup log lines come out in the wrong format (or are missing) because the code that emitted them ran before logging was configured.
  **Recovery:** Configure logging as the first step inside the factory, before the application object is built, so every later line uses the configured renderer.

- **Mistake:** Adding middleware in the wrong order and assuming attachment order equals inbound execution order.
  **Symptom:** A middleware that should wrap every response (for example, one that stamps a header on errors) does not run for some responses, because it was attached too early and ended up too far inside the nested stack.
  **Recovery:** Recall that the framework runs the last-added middleware first on the inbound path; attach the outermost concern last, and verify the resulting order against a test request.

- **Mistake:** Keeping a single module-level application as the only way to build the app, then trying to test production-mode behaviour.
  **Symptom:** A test cannot exercise a different configuration because the global was already built at import time with the default settings.
  **Recovery:** Build the application inside a factory function that accepts a settings argument, and have tests call the factory with the configuration they need.

- **Mistake:** Performing expensive or connection-opening work at import time instead of inside the lifespan startup hook.
  **Symptom:** Importing the module (for tooling or tests) hangs or fails because it tries to reach a database that need not be running for that task.
  **Recovery:** Move startup work into the lifespan hook, which the framework runs only when the application is actually served, not when the module is merely imported.

## External reading

- [FastAPI: first steps and the application object](https://fastapi.tiangolo.com/tutorial/first-steps/)
- [FastAPI: bigger applications and routers](https://fastapi.tiangolo.com/tutorial/bigger-applications/)
- [FastAPI: lifespan events](https://fastapi.tiangolo.com/advanced/events/)
- [Python documentation: the asyncio library](https://docs.python.org/3/library/asyncio.html)
