# Integration testing against a real Postgres

## Introduction

This document explains why some automated tests run against a genuine database server started in a container rather than against a fake stand-in, and how a test suite wires that up without tests interfering with one another. An integration test is a test that exercises several real components joined together — here the application code, a database driver, and an actual running database — to confirm they agree at their shared boundaries. It is the counterpart to a unit test, which isolates one small piece of code and replaces everything that piece depends on with stand-ins. This topic belongs in the Testing and quality track because the database is the one dependency whose behaviour is too rich to imitate convincingly, so the backend test suite talks to a real Postgres (an open-source relational database server) instead of a stub.

**Learning outcomes** — after reading this document you will be able to:

- Explain the difference between an integration test and a unit test, and when each one is the right tool.
- Describe why running tests against a real database catches defects that a faked database hides.
- Explain how a test suite keeps tests isolated from one another when they all share one real database.
- Recognise the common mistakes in real-database integration testing and recover from them.

Prerequisites — read these first:

- [PostgreSQL fundamentals](07-database-01-postgresql-fundamentals.md) — what the relational database under test actually does.
- [Docker Compose and healthchecks](10-containers-04-docker-compose-and-healthchecks.md) — how the database container is started and confirmed ready locally.
- [SQLAlchemy async engine and the per-request session](03-backend-04-sqlalchemy-async-and-session-dependency.md) — the engine and session the tests open against that database.

## Problem it solves

The concrete problem is confidence: you want a passing test suite to mean the code really works against the database it will use in production, not against a convenient imitation of it. Databases enforce a great deal of behaviour that lives outside the application — uniqueness constraints, foreign-key relationships, default values, transaction semantics, the exact dialect of Structured Query Language (SQL) the server accepts, and the way queries actually return rows. A test that never touches a real database cannot observe any of that.

The common prior approach is to replace the database with a test double (a stand-in object substituted for a real dependency during a test). The most aggressive form is a mock (a test double that returns canned, pre-programmed answers instead of doing real work). Mocking the database has real costs:

- A mocked query returns whatever the test author told it to return, so a query with a typo, a wrong join, or a constraint violation still "passes" — the mock never runs the SQL, so it cannot disagree with it.
- Some teams substitute a different, lighter database (for example an in-memory engine) for the real one. The two databases differ in dialect and features, so tests pass against the substitute and then fail against the production server, which defeats the purpose.
- Mocks encode the author's assumptions about how the database behaves. When those assumptions are wrong, the test and the code are wrong together and agree with each other, hiding the defect.

Running the integration layer against the same database engine used in production removes that whole class of false confidence. The trade-off is that the suite now needs a real database available and needs a strategy to stop one test's writes from leaking into the next.

## Mental model

Think of the difference between a flight simulator and a real test flight. A unit test is the simulator: cheap, instant, fully controlled, and perfect for rehearsing one manoeuvre in isolation — but the physics are modelled, not real. An integration test against a real database is the test flight: slower and needing an actual aircraft and runway, but it is the only way to learn how the real machine behaves under real air. You want both, and you want most of your rehearsal in the simulator and a smaller number of real flights to confirm the model held.

Here is what one real-database integration test does, step by step:

1. Before the test runs, the suite checks that a database server is reachable; if it is not, the test is skipped rather than failed, so a developer without the database running is not blocked.
2. The suite resets the relevant tables to a known clean state so leftovers from an earlier test cannot influence this one.
3. The test opens a session (a single unit-of-work conversation with the database) and inserts whatever rows it needs as its starting point.
4. The test drives the application code, which runs real queries against those rows in the real server.
5. The test asserts on what the application returned and, where relevant, on what the database now contains.
6. On teardown the session's open transaction is rolled back and the connection is returned, leaving nothing behind for the next test.

The first and last steps are what make a shared real database safe to test against repeatedly.

## How it works

Integration testing against a real database rests on three ideas: fidelity, disposability, and isolation.

Fidelity means the test talks to the same kind of server the application will use in production. Because the server is real, it enforces every rule the application relies on — a duplicate insert that violates a uniqueness constraint raises the same error in the test that it would in production, and a query that returns rows in a particular order does so for real. A test double can only ever return what its author imagined; a real server returns what is true. That is the entire reason to pay the extra cost.

Disposability means the database is cheap to create and throw away, which is what a container makes possible. A container is an isolated, lightweight package that runs a piece of software with its own filesystem and dependencies, started from a fixed image so every developer and every continuous integration (CI) run gets byte-for-byte the same database version and configuration. Before the suite runs, the schema is brought up to date by applying the project's migrations (a migration is a versioned, ordered change to the database schema), so the tables the tests expect exist exactly as production has them.

Isolation means one test's writes never bleed into another's, even though all the tests share one database. There are two complementary techniques. The first is the transaction-rollback pattern: each test runs inside a database transaction (a group of statements that either all take effect together or none do) and that transaction is rolled back at the end, discarding every change the test made. The second is truncation: between tests, the suite empties the relevant tables outright, which is the bluntest reset and the one to reach for when the code under test commits its own work through a connection the test cannot roll back. Truncation must delete in an order that respects foreign-key relationships — child tables before the parent tables they point at — or the database refuses the operation.

One more practical concern is availability. A real-database test needs the server to be up, but a developer may run the suite before starting it. The standard answer is a reachability probe that opens a network connection to the database's host and port; if the connection fails, the affected tests are marked skipped rather than failed. That keeps the fast unit tests usable everywhere while the integration layer activates only where the database is present.

## MatchLayer Phase 1 usage

The Phase 1 integration fixtures live in `apps/api/tests/integration/conftest.py`. A `conftest.py` file is where the pytest testing framework looks for shared fixtures (a fixture is a named piece of setup that pytest builds once and hands to any test that asks for it by name).

The reachability probe opens a socket to the database's port and reports whether it succeeded:

Source: `apps/api/tests/integration/conftest.py`

```python
def postgres_available() -> bool:
    return _service_available("127.0.0.1", 5432)
```

Each integration test module uses that probe to skip the whole module when no database is running, so the unit suite still passes on a machine that never started the container:

Source: `apps/api/tests/integration/test_register.py`

```python
pytestmark = pytest.mark.skipif(
    not postgres_available(),
    reason="Postgres not available (docker-compose not running)",
)
```

The core fixture opens a real async engine against the Compose-managed Postgres, hands the test a session, and rolls that session back on teardown so the test leaves no trace:

Source: `apps/api/tests/integration/conftest.py`

```python
@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    settings = get_settings()
    engine = create_async_engine(
        str(settings.database_url),
        echo=False,
        poolclass=NullPool,
    )
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        try:
            yield session
        finally:
            await session.rollback()
    await engine.dispose()
```

The engine here reads the same connection string the running application uses, so the tests exercise the production database engine rather than a substitute. The session factory (the configured callable that produces a new session bound to the engine) is described in
[SQLAlchemy async engine and the per-request session](03-backend-04-sqlalchemy-async-and-session-dependency.md), and the Compose service that starts this Postgres is covered in
[Docker Compose and healthchecks](10-containers-04-docker-compose-and-healthchecks.md).

Rollback alone is not enough for the auth flows, because the application commits through its own session inside the route handlers, and a commit cannot be rolled back from a different session. To wipe that committed state, an autouse fixture truncates the four auth tables before every test, deleting child tables before the parent `users` table they reference:

Source: `apps/api/tests/integration/conftest.py`

```python
            await conn.exec_driver_sql(
                "TRUNCATE TABLE refresh_tokens, password_reset_tokens, "
                "audit_events, users RESTART IDENTITY CASCADE"
            )
```

A second isolation detail is test data hygiene: fixtures generate a fresh, unique email address per user so two tests can never collide on the database's case-insensitive uniqueness index, and they use a documentation-reserved domain so the email validator accepts it. That keeps every test independent without depending on a particular teardown order.

## Common pitfalls

- **Mistake:** Mocking the database in tests that are supposed to verify queries, constraints, or migrations.
  **Symptom:** The test suite is green, but the code throws a database error in a real environment — a malformed query, a violated constraint, or a column that does not exist.
  **Recovery:** Move tests that depend on real database behaviour into the integration layer and run them against the real server; keep mocks for code paths that have nothing to do with the database.

- **Mistake:** Relying on transaction rollback for isolation when the code under test commits through its own session.
  **Symptom:** A test fails only when run after another test — a leftover row trips a uniqueness index, or a "select all rows" assertion sees rows a previous test wrote.
  **Recovery:** Add a truncation step (or an equivalent committed-state reset) that runs before each test, deleting tables in foreign-key order, so committed rows from a prior test cannot survive into the next.

- **Mistake:** Letting the suite hard-fail when the database server is not running locally.
  **Symptom:** A developer who only wants the fast unit tests gets a wall of connection-refused errors before any test body runs.
  **Recovery:** Add a reachability probe and skip the integration tests when the server is unreachable, so the integration layer activates only where the database is present.

- **Mistake:** Pointing the tests at a different, lighter database than production "to keep tests fast".
  **Symptom:** Tests pass against the substitute and then fail against production over dialect or feature differences the substitute did not model.
  **Recovery:** Run the integration tests against the same database engine and major version used in production, started from a pinned container image so every run matches.

## External reading

- [pytest: how to use fixtures](https://docs.pytest.org/en/stable/how-to/fixtures.html)
- [pytest: skipping and xfail](https://docs.pytest.org/en/stable/how-to/skipping.html)
- [PostgreSQL: emptying a table of all rows](https://www.postgresql.org/docs/current/sql-truncate.html)
- [PostgreSQL: transactions tutorial](https://www.postgresql.org/docs/current/tutorial-transactions.html)
- [Docker: containerize an application](https://docs.docker.com/get-started/workshop/02_our_app/)
