"""Structlog configuration with PII-redaction defense-in-depth.

Exposes :func:`configure_logging`, the single entry point that wires
structlog's processor chain for the API process. Called once from the
FastAPI application factory (Design §6.1).

Behaviour follows ``security.md`` and ``conventions.md``:

* **JSON in non-development**, colourised console renderer in
  development. Production logs are ingested by Fly.io / CloudWatch /
  Loki and need to parse cleanly.
* **Per-request context** (``request_id``, ``user_id``, ``route``,
  ``method``) flows in via the structlog ``contextvars`` integration.
  The request-id middleware (Design §6.4, task 3.4) binds those keys;
  every log line emitted under the same task automatically inherits
  them.
* **PII redaction is enforced at the processor layer.** Even if a
  caller writes ``log.info("login", email=user.email)`` by mistake, the
  redaction processor scrubs the value before any renderer sees it —
  Restricted data per ``security.md`` data classification never reaches
  stdout.

Design reference: §6.3.
Requirements covered: 4.4, 4.14.
"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog
from structlog.typing import EventDict, Processor, WrappedLogger

from matchlayer_api.config import LogLevel, Settings

# Map the ``MATCHLAYER_LOG_LEVEL`` literal to the numeric level that
# ``structlog.make_filtering_bound_logger`` expects. Filtering at the
# bound-logger layer means below-threshold calls short-circuit before any
# processor (including PII redaction) runs — no wasted work in hot paths.
_LOG_LEVELS_BY_NAME: dict[LogLevel, int] = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}

# Defense-in-depth: any event-dict key whose name contains one of these
# substrings (case-insensitive) has its value replaced with a sentinel
# before the renderer runs. Substring rather than full-match so
# adjacent-context fields are also caught: ``access_token``,
# ``refresh_token``, ``user_email``, ``oauth_secret``, ``api_key_token``,
# ``MATCHLAYER_S3_SECRET_ACCESS_KEY``. See ``security.md`` —
# "Logging & audit" and "Data classification".
_PII_KEY_PATTERN = re.compile(
    r"password|token|secret|email|resume_text|parsed_text",
    re.IGNORECASE,
)

# Single sentinel value so log consumers can grep for accidental leaks
# (the redactor *should* never fire in production; if it does, that's a
# bug in a call site that needs fixing at source).
_REDACTED_VALUE = "***REDACTED***"


def _redact_pii(
    _logger: WrappedLogger,
    _method_name: str,
    event_dict: EventDict,
) -> EventDict:
    """Scrub values whose key matches :data:`_PII_KEY_PATTERN`.

    Recurses into nested ``dict``/``list`` payloads so a structure such
    as ``{"user": {"email": "...", "id": "..."}}`` still has the
    ``email`` field redacted. Mutation is in-place — structlog allows
    processors to either mutate or return a new dict, and in-place is
    cheaper.
    """
    _scrub_in_place(event_dict)
    return event_dict


def _scrub_in_place(value: Any) -> None:
    # ``Any`` is unavoidable here: structlog event dicts carry arbitrary
    # caller-supplied values. ``conventions.md`` permits ``Any`` with an
    # explicit justification, which this comment supplies.
    if isinstance(value, MutableMapping):
        for key in list(value):
            child = value[key]
            if isinstance(key, str) and _PII_KEY_PATTERN.search(key):
                value[key] = _REDACTED_VALUE
                continue
            _scrub_in_place(child)
    elif isinstance(value, list):
        for item in value:
            _scrub_in_place(item)
    # Tuples are immutable; we deliberately do not rebuild them. Log
    # payloads should not nest PII inside tuples in practice, and the
    # alternative — silently allocating new tuples — hides bugs.


def configure_logging(settings: Settings) -> None:
    """Configure structlog for the API process.

    Called once from the FastAPI application factory at startup. Safe to
    call multiple times — :func:`structlog.configure` replaces the prior
    configuration atomically, which keeps test fixtures simple.

    Args:
        settings: The validated :class:`~matchlayer_api.config.Settings`
            instance. ``settings.environment`` selects the renderer and
            ``settings.log_level`` sets the bound-logger threshold.
    """
    processors: list[Processor] = [
        # 1. Pull request-scoped context (request_id, route, method,
        #    user_id) bound by the request-id middleware (§6.4) into
        #    every record. Must run first so subsequent processors see
        #    the merged keys.
        structlog.contextvars.merge_contextvars,
        # 2. Redact PII before any other processor or renderer can see
        #    a sensitive value. Position is fixed by Design §6.3
        #    ("between (1) and (2)").
        _redact_pii,
        # 3. Stamp the level name so JSON consumers can filter cleanly.
        structlog.processors.add_log_level,
        # 4. ISO-8601 UTC timestamps — matches the API timestamp format
        #    convention (``conventions.md`` "Timestamps").
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        # 5. Capture stack frames when callers pass ``stack_info=True``.
        structlog.processors.StackInfoRenderer(),
    ]

    if settings.environment == "development":
        # Local developers read this directly in the terminal: colours,
        # indented tracebacks, key=value formatting.
        # ``ConsoleRenderer`` handles ``exc_info`` itself (rendering a
        # pretty, indented traceback) and explicitly warns when
        # ``format_exc_info`` is also present in the chain — the two
        # processors are redundant in that direction. The warning is
        # promoted to an exception by ``filterwarnings = ["error"]`` in
        # ``pyproject.toml``, which would crash any handler that calls
        # ``log.exception(...)`` in development. We therefore omit
        # ``format_exc_info`` here and let the renderer own exception
        # formatting in development.
        processors.append(structlog.dev.ConsoleRenderer(colors=True))
    else:
        # Machines read this. Strict JSON, one record per line —
        # ingested by CloudWatch / Fly.io / Loki without further
        # massaging. ``JSONRenderer`` does NOT format ``exc_info`` on
        # its own; without this processor a traceback object would
        # arrive at the renderer and serialize as ``"<traceback object
        # at 0x...>"``. Inserted just before the renderer so the
        # exception string is the last thing added to the event dict.
        processors.append(structlog.processors.format_exc_info)
        processors.append(structlog.processors.JSONRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            _LOG_LEVELS_BY_NAME[settings.log_level],
        ),
        context_class=dict,
        # Containerised 12-factor: write to stdout, let the runtime
        # collect. Stdlib-logging interop (uvicorn access logs, SQL
        # statement logs) lands in a follow-up task; this factory keeps
        # the foundation minimal and self-contained.
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        # Caching is intentionally disabled. Modules such as
        # ``core.middleware`` and ``core.errors`` create
        # ``_log = structlog.get_logger(__name__)`` at import time; if
        # those bound loggers were cached, they would freeze the
        # processor chain that was active at first use and would not
        # observe later reconfiguration. ``structlog.testing.capture_logs``
        # works by reconfiguring the active processor chain, so caching
        # would silently break log-capture tests (events would still
        # flow through the original chain to stdout instead of into the
        # capture buffer). The structlog docs call this out explicitly
        # ("caching is incompatible with capture_logs"). The runtime
        # cost is a single dict lookup per log call — not measurable
        # for an HTTP API.
        cache_logger_on_first_use=False,
    )
