"""FastAPI application factory for the MatchLayer API.

Exposes:

* :func:`create_app` — the explicit application-factory function
  required by Requirement 4.1. Tests, the OpenAPI dump tool, and
  uvicorn all build (or import) the application through this single
  entry point.
* :data:`app` — a module-level :class:`fastapi.FastAPI` instance built
  by :func:`create_app` so ``uvicorn matchlayer_api.main:app`` works
  without further orchestration (Design §6.1).

Wiring sequence (in the exact order :func:`create_app` performs it,
each step links to its sibling task):

1. **:func:`configure_logging` from §3.3 / Design §6.3.** Called
   *before* any other startup code so the lifespan probe and the
   eventual access-log lines all emit through the configured renderer.
2. **Lifespan** that ``await``s
   :func:`~matchlayer_api.core.db.verify_database_connection` once at
   startup (Design §6.6, Requirement 4.12). Failure re-raises a
   :class:`sqlalchemy.exc.SQLAlchemyError` so uvicorn exits non-zero
   before binding a port — fail-fast at startup.
3. **CORS middleware** with the allowlist from
   :attr:`Settings.cors_allowed_origins`. Added *first* so it ends up
   inside the ASGI stack relative to RequestIdMiddleware: Starlette
   composes ``add_middleware`` calls outermost-last, so the LAST call
   runs FIRST on the inbound path. We add CORS first and
   RequestIdMiddleware second; the resulting stack is
   ``RequestIdMiddleware → CORSMiddleware → routes`` on inbound, which
   binds the structlog ``request_id`` contextvar *before* CORS or any
   route handler runs, while still letting CORS wrap every real route
   response (and OPTIONS preflights).

   ``allow_origins`` is materialised from
   :attr:`Settings.cors_allowed_origins` (typed
   ``list[AnyHttpUrl]``). We render each origin via ``str(origin)``
   and strip the trailing slash that ``AnyHttpUrl`` appends — browsers
   compare ``Origin`` against the configured list literally, and a
   mismatched trailing slash is the most common silent CORS bug. The
   "never ``*`` for origins" rule from ``security.md`` is satisfied
   structurally: there is no code path here that reaches a wildcard,
   and an empty allowlist produces an empty list rather than a
   wildcard fallback.

   ``allow_credentials=True`` so the (Phase 1-auth) cookie-based
   refresh-token flow works once it lands.
   ``allow_methods=["*"]`` / ``allow_headers=["*"]`` for now —
   Phase 1-auth narrows the authenticated-endpoint headers explicitly;
   the foundation app exposes only ``GET /healthz`` so the wider
   surface here cannot be abused.
   ``expose_headers=["X-Request-Id"]`` so browsers can read the
   request id back through the ``fetch`` API. The frontend uses it
   when building support tickets and when correlating client-side
   Sentry errors with backend log lines.
4. **:class:`~matchlayer_api.core.middleware.RequestIdMiddleware`** —
   reuses inbound ``X-Request-Id`` (when it matches the format
   contract from §3.4) or generates a UUIDv7, binds the structlog
   contextvar, sets the outbound header, and emits the per-request
   access-log line. Added *after* CORS per (3) above.
5. **:func:`register_exception_handlers` from §3.5 / Design §6.8.**
   Registers the RFC 7807 envelope for :class:`MatchLayerError`,
   :class:`fastapi.exceptions.RequestValidationError`, and the
   catch-all :class:`Exception`. The handlers source ``request_id``
   from the structlog contextvar that step (4) bound, so the envelope
   includes the same id the access-log line carries.
6. **:obj:`health.router` from §3.7.** ``GET /healthz`` is the only
   endpoint Phase 1 foundation exposes. Mounted at the root because
   container healthchecks (Design §11.1) probe ``/healthz`` directly,
   not under ``/api/v1``.

Design reference: §6.1, §6.6, §6.8.
Requirements covered: 4.1, 4.12.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from matchlayer_api.api.health import router as health_router
from matchlayer_api.config import Settings, get_settings
from matchlayer_api.core.db import verify_database_connection
from matchlayer_api.core.errors import register_exception_handlers
from matchlayer_api.core.logging import configure_logging
from matchlayer_api.core.middleware import RequestIdMiddleware

# Module-level logger. The startup probe runs *before* any HTTP
# request, so the contextvar fields the request-id middleware binds
# (request_id, route, method) are deliberately absent on these lines —
# operators correlate via the ``event`` name instead.
_log = structlog.get_logger(__name__)


def _format_cors_origins(settings: Settings) -> list[str]:
    """Return the CORS allowlist as plain strings, stripped of trailing ``/``.

    Pydantic's :class:`pydantic.AnyHttpUrl` always renders with a
    trailing slash (``http://localhost:3000/``), but browsers compare
    the inbound ``Origin`` header literally — and the ``Origin``
    header never carries a trailing slash. The mismatch is the single
    most common silent CORS-failure cause; handling it here once
    spares every future caller from re-discovering it.

    Returns the list as-is when empty: an empty allowlist must NOT
    fall back to ``"*"`` (``security.md`` "Anti-patterns to refuse").
    """
    return [str(origin).rstrip("/") for origin in settings.cors_allowed_origins]


def create_app(settings: Settings | None = None) -> FastAPI:
    """Construct and return the configured FastAPI application.

    The factory is the single source of truth for application wiring.
    Tests build a fresh instance per test (so dependency overrides
    don't leak across cases); the OpenAPI dump tool calls
    :func:`create_app` directly to obtain a spec without invoking the
    lifespan; uvicorn imports the module-level :data:`app` built from
    this same factory at import time.

    Args:
        settings: Optional :class:`~matchlayer_api.config.Settings`
            override. When omitted, the cached :func:`get_settings`
            accessor supplies the process-wide instance — the
            production code path. Tests that need a non-default
            ``environment`` (for example, the production-mode
            error-handler test in :mod:`tests.test_errors`) pass an
            explicitly-built Settings here.

    Returns:
        A fully wired :class:`fastapi.FastAPI` instance with logging
        configured, lifespan installed, middleware registered (CORS +
        RequestId), RFC 7807 error handlers attached, and the
        ``/healthz`` router mounted at the root.
    """
    cfg = settings or get_settings()

    # Step 1 — wire structlog before anything else can emit a log line.
    # ``configure_logging`` is idempotent (``structlog.configure``
    # replaces the prior config atomically), so calling it again from
    # tests that build a second app instance is safe.
    configure_logging(cfg)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        # Step 2 — fail-fast startup probe. ``verify_database_connection``
        # runs ``SELECT 1`` against the module-level engine and
        # re-raises any :class:`SQLAlchemyError`. Lifespan startup
        # failures propagate up through uvicorn, which exits non-zero
        # — exactly the behaviour Requirement 4.12 mandates.
        #
        # No explicit teardown is needed: the engine's connection pool
        # is released when the process exits, and Phase 1 has no other
        # resources (no Redis client, no S3 client) to drain.
        await verify_database_connection()
        _log.info(
            "application_started",
            environment=cfg.environment,
            log_level=cfg.log_level,
        )
        yield
        _log.info("application_stopping")

    app = FastAPI(
        title="MatchLayer API",
        version="0.0.0",
        lifespan=lifespan,
        # Hide the default docs surfaces in production. Phase 1
        # foundation does not expose authentication; leaving ``/docs``
        # open in production would let any internet caller enumerate
        # the (currently single-endpoint) API. Dev/staging keep the
        # surfaces for ergonomics.
        docs_url=None if cfg.environment == "production" else "/docs",
        redoc_url=None if cfg.environment == "production" else "/redoc",
        openapi_url=(None if cfg.environment == "production" else "/openapi.json"),
    )

    # ------------------------------------------------------------------
    # Middleware. ``add_middleware`` composes outermost-last: the LAST
    # added runs FIRST on the inbound path. We add CORS first and
    # RequestIdMiddleware second so the resulting stack is
    #
    #     inbound:  RequestIdMiddleware  →  CORSMiddleware  →  routes
    #     outbound: routes  →  CORSMiddleware  →  RequestIdMiddleware
    #
    # Binding the structlog ``request_id`` contextvar before CORS runs
    # means CORS-rejected preflights still get a request_id in any log
    # line they emit, and the outbound ``X-Request-Id`` header is set
    # last so it's never stripped by an earlier-running middleware.
    # ------------------------------------------------------------------
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_format_cors_origins(cfg),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-Id"],
    )
    app.add_middleware(RequestIdMiddleware)

    # Step 5 — RFC 7807 error envelope. Pass ``cfg`` explicitly so the
    # production-vs-development branch in the catch-all handler is
    # driven by the same Settings the caller supplied (rather than
    # re-reading the cached one — important for tests).
    register_exception_handlers(app, settings=cfg)

    # Step 6 — mount the only Phase 1 router. No prefix: container
    # healthchecks probe ``/healthz`` directly (Design §11.1).
    app.include_router(health_router)

    return app


# Module-level instance for ``uvicorn matchlayer_api.main:app``. This
# call reads ``MATCHLAYER_*`` from the environment (and ``.env``) at
# import time; missing or malformed values raise
# :class:`pydantic.ValidationError` before uvicorn binds a port — the
# fail-fast-at-startup half of Requirement 4.3.
app: FastAPI = create_app()


__all__ = ["app", "create_app"]
