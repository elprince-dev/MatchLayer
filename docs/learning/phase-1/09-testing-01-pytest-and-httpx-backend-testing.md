# Backend testing with pytest, pytest-asyncio, and httpx

## Introduction

This document explains how the automated tests for a Python web service are
written and run, using three tools that work together. The first is pytest, a
test runner and testing framework for Python that discovers test functions,
executes them, and reports which passed and which failed. The second is
pytest-asyncio, a plugin that teaches pytest how to run asynchronous test
functions — test functions defined with `async def` that must be driven by an
event loop rather than called directly. The third is httpx, a modern HyperText
Transfer Protocol (HTTP) client that can call a web application directly inside
the same process, with no network socket and no separately running server. Most
backend tests in a typed web project exercise the service's Application
Programming Interface (API) by sending a request and asserting on the response,
so these three tools are the everyday workhorses of the Backend and
Testing-and-quality tracks.

**Learning outcomes** — after reading this document you will be able to:

- Explain what pytest discovers and runs, and how a fixture supplies shared setup to a test.
- Describe what pytest-asyncio adds and what `asyncio_mode = "auto"` means for async test functions.
- Explain how httpx drives an Asynchronous Server Gateway Interface (ASGI) application in-process without opening a network port.
- Recognise the common mistakes in async backend tests and recover from them.

Prerequisites:

- [Async Python and the asyncio model](03-backend-03-async-python-and-asyncio.md)
- [FastAPI and the application-factory pattern](03-backend-01-fastapi-application-factory.md)

## Problem it solves

A web service is only trustworthy if you can prove, repeatedly and quickly, that
its endpoints behave as intended. The concrete problem is verification: given a
request, does the service return the right status code, the right body, and the
right side effects, and does it keep doing so after every change? Doing this by
hand — starting the server, opening a browser or a command-line client, and
eyeballing each response — is slow, easy to forget, and impossible to run on
every commit.

The common prior approach was to test a running server over a real network
socket: boot the application on a port, point an HTTP client at
`http://localhost:8000`, and tear it down afterwards. That works but has real
costs:

- Starting and stopping a real server for every test run is slow and flaky, because a port can already be in use or the server can still be warming up when the first request arrives.
- A real socket pulls in the operating system's networking stack, so a test failure can come from the network rather than from the code under test.
- Wiring a real database and real external services into every test makes runs slow and non-deterministic, so failures are hard to reproduce.

The pytest plus pytest-asyncio plus httpx combination solves this by running the
test, the test framework, and the web application together in one Python
process. pytest finds and runs the tests; pytest-asyncio runs the asynchronous
ones on an event loop; and httpx delivers each request straight into the
application object in memory, so there is no port, no socket, and no separate
server to manage.

## Mental model

Think of testing a restaurant kitchen. The slow way is to seat real diners,
place orders through the front door, and wait to see what comes out — you are
testing the whole building, including the parking lot and the front-of-house
staff, not the kitchen alone. A faster way hands the order ticket directly to
the chef through the kitchen window and inspects the plate that comes back. You
still test the real cooking, but you skip the parts that are not the kitchen.

httpx with its in-process driver is that kitchen window: it hands a request
ticket straight to the application and inspects the response plate, skipping the
network entirely. pytest is the inspector who runs through a checklist, and a
fixture is the prep station that lays out clean ingredients before each check.

Here is how the pieces fit together for one test:

1. pytest discovers a function whose name starts with `test_` and decides to run it.
2. pytest sees the test asks for a fixture by name, builds that fixture first, and passes its value in as an argument.
3. Because the test is an `async def` and asynchronous mode is enabled, pytest-asyncio runs it on a fresh event loop instead of calling it like an ordinary function.
4. Inside the test, the httpx client sends a request directly into the application object and waits for the response to come back.
5. The test asserts on the response status and body; if every assertion holds the test passes, otherwise pytest records the failure and the offending values.

The whole model rests on keeping everything in one process so each test is fast,
isolated, and reproducible.

## How it works

pytest is built on convention over configuration. It collects tests by walking
the project for files, classes, and functions whose names follow a discovery
pattern — by default, functions named `test_*` in files named `test_*`. Each
collected function becomes an independent test. When a test function declares a
parameter, pytest looks for a fixture of that name: a fixture is a function,
marked as such, whose return or yielded value is injected as that argument. A
fixture that uses `yield` runs its setup before the `yield`, hands the value to
the test, and runs its teardown afterwards, which makes fixtures the standard
place to build and then clean up shared state. Fixtures can request other
fixtures, so setup composes into small, reusable layers.

By itself pytest runs ordinary synchronous functions. An asynchronous test —
one defined with `async def` — is a coroutine, meaning calling it does not run
its body; it returns a coroutine object that has to be driven by an event loop,
the scheduler that advances asynchronous code. This is where the asyncio plugin
comes in: it intercepts collected coroutine tests, creates an event loop, and
runs each one to completion on that loop. In automatic mode the plugin treats
every coroutine test as an asynchronous test with no per-test marker required,
which keeps test files free of repetitive decorators. The loop is created fresh
per test so one test's pending work cannot leak into the next.

An in-process HTTP client closes the loop between the test and the application.
A web application that speaks the asynchronous server interface is, at its core,
a callable that accepts a request described as plain data and produces a
response. An in-process transport implements the client's send step by invoking
that callable directly in memory instead of serialising bytes onto a socket.
The client builds a normal request object, the transport feeds it to the
application, and the application's response is handed straight back as a normal
response object the test can assert on. Because nothing crosses the network, the
request path is deterministic and fast, and the test exercises the real routing,
validation, and handler code — only the transport layer is swapped out.

One subtlety follows from this design. The asynchronous server interface defines
a separate startup-and-shutdown channel (often called the lifespan) that runs
once when a real server boots and once when it stops. An in-process transport
that only drives the request-and-response channel does not fire those
startup-and-shutdown events. That is usually the behaviour a fast test wants,
because startup work such as opening a database connection should be replaced by
a test substitute rather than performed for real. When a test genuinely needs
the startup path to run, a different client that drives the lifespan channel is
used instead.

## MatchLayer Phase 1 usage

In MatchLayer the three libraries are declared as development-only dependencies
of the backend in `apps/api/pyproject.toml`, pinned by major version so an
upgrade is a deliberate change rather than an accident:

Source: `apps/api/pyproject.toml`

```text
    "pytest>=8.3,<9.0",
    "pytest-asyncio>=0.24,<1.0",
    "httpx>=0.27,<1.0",
```

pytest is configured in the same file. Two settings carry most of the weight:
`asyncio_mode = "auto"` makes the asyncio plugin treat every coroutine test as
an asynchronous test, so no test needs a per-function marker; and the
function-scoped fixture loop setting gives each test its own event loop. The
`filterwarnings = ["error"]` setting (shown elsewhere in the same table)
promotes warnings to failures, which is why the test wiring is careful about
deprecations and resource cleanup:

Source: `apps/api/pyproject.toml`

```text
[tool.pytest.ini_options]
minversion = "8.0"
testpaths = ["tests"]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"
```

The shared fixtures live in `apps/api/tests/conftest.py` — a `conftest.py` is a
file pytest loads automatically so the fixtures it defines are available to
every test in that directory without being imported. The `client` fixture builds
an httpx client whose transport drives the application in-process. It uses the
explicit `ASGITransport` shape rather than the deprecated `app=` shortcut (which
would raise a deprecation warning, and warnings are errors here), and it
deliberately does not trigger the startup channel, so a test that stubs the
database out never needs a real one:

Source: `apps/api/tests/conftest.py`

```python
@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac
```

A representative test under `apps/api/tests/test_health.py` puts the pieces
together. It is an `async def` function (so pytest-asyncio runs it on a loop), it
asks for both the `client` fixture and a dependency-override fixture by name, and
it asserts on the response the in-process client returns:

Source: `apps/api/tests/test_health.py`

```python
async def test_healthz_returns_200_ok_when_db_probe_succeeds(
    client: AsyncClient,
    override_get_session: OverrideGetSession,
) -> None:
    override_get_session(None)

    response = await client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

Integration tests, which need a real database, reuse the same httpx-in-process
pattern but wire the application's per-request session to a test-controlled one.
The fixture in `apps/api/tests/integration/conftest.py` overrides the session
dependency so the routes run against the test's transaction, then drives them
through the same `ASGITransport`:

Source: `apps/api/tests/integration/conftest.py`

```python
@pytest_asyncio.fixture
async def client_with_session(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    app = create_app()

    async def _override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _override

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac

    app.dependency_overrides.pop(get_session, None)
```

Because the whole suite runs in one process with no network and the application's
external dependencies are replaced by overrides or a local database, the backend
Continuous Integration (CI) check can run the unit-style tests fast and
deterministically on every change. The split between an in-process `client` that
skips startup and a `client_with_session` that talks to a real database is the
boundary between fast unit-style coverage and slower integration coverage. The
session-override pattern itself is described in
[SQLAlchemy async engine and the per-request session dependency](03-backend-04-sqlalchemy-async-and-session-dependency.md).

## Common pitfalls

- **Mistake:** Writing an `async def` test while the asyncio plugin is not active (no automatic mode and no per-test marker).
  **Symptom:** pytest reports the test as passed without running its body, or emits a "coroutine was never awaited" warning; assertions inside the test never execute, so a broken endpoint looks green.
  **Recovery:** Enable the plugin's automatic mode (or mark the test) so the coroutine is actually driven on an event loop; confirm by making an assertion fail on purpose and checking the run turns red.

- **Mistake:** Constructing the httpx client with the old `AsyncClient(app=...)` shortcut instead of an explicit transport.
  **Symptom:** A deprecation warning is raised; in a suite that promotes warnings to errors, the test fails before it ever sends a request.
  **Recovery:** Build the client with `ASGITransport(app=app)` and pass that transport to `AsyncClient(transport=...)`, which is the supported in-process shape.

- **Mistake:** Expecting the application's startup work (such as a database connection probe) to run under the in-process request transport.
  **Symptom:** Tests behave as if startup never happened — a connection that startup was supposed to verify is missing, or, conversely, a test unexpectedly tries to reach a real database and fails when none is available.
  **Recovery:** Treat the in-process transport as request-only; stub the startup dependency with an override for fast tests, or use a client that drives the lifespan channel when the startup path is the thing under test.

- **Mistake:** Registering a dependency override on the application and not removing it after the test.
  **Symptom:** A later, unrelated test passes or fails depending on run order, because it inherits the leftover override; the failure moves around and is hard to reproduce.
  **Recovery:** Build a fresh application per test or pop the override on fixture teardown, so each test starts from a clean dependency map.

## External reading

- [pytest documentation](https://docs.pytest.org/en/stable/)
- [pytest: how to use fixtures](https://docs.pytest.org/en/stable/how-to/fixtures.html)
- [FastAPI: testing](https://fastapi.tiangolo.com/tutorial/testing/)
- [FastAPI: concurrency and async/await](https://fastapi.tiangolo.com/async/)
- [Python documentation: asyncio](https://docs.python.org/3/library/asyncio.html)
