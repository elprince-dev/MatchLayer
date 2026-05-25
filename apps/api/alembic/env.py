"""Alembic environment for the MatchLayer API.

Alembic stays synchronous: migrations open a single short-lived
connection, run DDL, and exit. The runtime app uses asyncpg, but the
``+asyncpg`` driver is async-only — it cannot back the sync
``EngineFacade`` Alembic builds in ``run_migrations_online``. We
therefore swap the driver from ``+asyncpg`` to the sync ``+psycopg``
(psycopg3) driver here, leaving every other piece of the URL —
credentials, host, port, database — untouched.

``psycopg[binary]`` is pinned in ``pyproject.toml`` precisely for this
purpose; it is not imported by application code.

This module reads its database URL from
:class:`matchlayer_api.config.Settings` rather than from the inert
``sqlalchemy.url`` key in ``alembic.ini``. ``conventions.md`` forbids
ad-hoc ``os.environ.get`` reads ("all config via ``pydantic-settings``
reading env vars. No ``os.environ.get`` scattered in code"), and
Requirement 4.2 names a single ``BaseSettings`` subclass as the only
configuration entry point. Sourcing the URL through Settings keeps
Alembic on the same validation path as the running app: a malformed
DSN fails Pydantic validation here, before any DDL touches the
database.

``target_metadata`` is set to :data:`None` for now. Phase 1 foundation
ships zero domain tables (Design §6.7); the first real migration in
``phase-1-auth`` will replace this with the project's ORM metadata so
``--autogenerate`` can diff models against the live schema.

Design reference: §6.7. Requirements covered: 4.11.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from matchlayer_api.config import get_settings

# Alembic's ``Config`` object — exposes the values from ``alembic.ini``.
# We don't read ``sqlalchemy.url`` from it (the file deliberately omits
# that key); instead we mutate the in-memory config below to inject the
# Settings-derived URL before either migration mode runs.
config = context.config

# Honour the ``[loggers]`` / ``[handlers]`` / ``[formatters]`` blocks
# from ``alembic.ini`` so ``alembic upgrade head`` produces readable
# console output. Skip when invoked without a config file (e.g. some
# Alembic API tests) — ``config_file_name`` is ``None`` in that case.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Phase 1 foundation has no ORM metadata yet. ``target_metadata`` is
# read by ``--autogenerate`` to diff declared models against the live
# schema; with no models, autogenerate has nothing to do and explicit
# revisions remain the only way to add migrations — which is exactly
# what Design §6.7 prescribes for this phase.
target_metadata = None


def _alembic_database_url() -> str:
    """Return the sync database URL Alembic should connect through.

    Reads :attr:`Settings.database_url` (already validated as a
    :class:`pydantic.PostgresDsn`) and substitutes the async ``+asyncpg``
    driver for the sync ``+psycopg`` (psycopg3) driver. Other URL
    components — userinfo, host, port, database, query string — are
    preserved verbatim.

    ``str.replace`` with ``count=1`` is deliberate: the substring is
    only meaningful in the scheme position, so a single replacement at
    the start of the URL is the correct, minimal operation. A fallback
    branch leaves a non-asyncpg URL alone (already-sync ``+psycopg``
    URLs work as-is, and the bare ``postgresql://`` form Pydantic also
    accepts is sync-by-default).
    """
    settings = get_settings()
    url = str(settings.database_url)
    if "+asyncpg" in url:
        return url.replace("+asyncpg", "+psycopg", 1)
    if "+psycopg" in url:
        return url
    # ``postgresql://`` (no driver suffix) defaults to a sync DBAPI in
    # SQLAlchemy. Make the sync driver explicit so Alembic doesn't pick
    # whatever DBAPI happens to be importable first.
    return url.replace("postgresql://", "postgresql+psycopg://", 1)


def run_migrations_offline() -> None:
    """Render migrations as raw SQL without opening a DB connection.

    "Offline mode" emits the DDL that *would* run against the database
    to stdout, useful for code review and for environments where a
    human runs the SQL manually. The empty Phase 1 baseline produces
    no DDL output, but we wire the mode up correctly so future
    migrations work without revisiting this file.
    """
    url = _alembic_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # Compare types and server defaults when --autogenerate lands.
        # Harmless on an empty baseline; matches the standard config
        # the first real migration will rely on.
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Open a sync connection and apply pending migrations.

    Alembic builds its :class:`Engine` from the ``[alembic]`` section
    of the loaded config. We override ``sqlalchemy.url`` in-memory
    with the Settings-derived URL right before the engine is
    constructed so the operator never has to keep two URLs in sync
    between ``.env`` and ``alembic.ini``.

    ``poolclass=pool.NullPool`` is the canonical choice for short-lived
    Alembic runs: each invocation opens exactly one connection, runs
    its DDL, and exits. Long-lived pooling would be wasteful here and
    actively harmful when Alembic runs as part of a deploy hook that
    competes with the application for connection slots.
    """
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _alembic_database_url()

    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
