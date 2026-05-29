"""Process-singleton store for dev-mode password-reset links.

Phase 1 has no email provider; the password-reset confirmation flow
needs a way for a developer running MatchLayer locally to retrieve the
plaintext link the API just minted. The contract (Requirement 13,
Dev-Mode Reset-Link Surface §12.1) is:

* A single-slot LRU. ``record(link)`` overwrites whatever was there
  before. ``latest()`` returns the most recent record, or ``None`` if
  no link has been recorded since process start.
* In-process memory only. The store SHALL NEVER persist the
  plaintext token to disk, Redis, or any external service
  (Requirement 13.6).
* On every ``record(...)`` call the store emits exactly one structured
  ``info``-level log line whose payload contains the field
  ``password_reset_link`` (Requirement 13.1). The foundation
  structlog redaction processor (foundation Logging §6.3) does *not*
  match this compound key by intent -- the carve-out is documented
  in Requirements Appendix A and Design §12.4.

The :func:`is_dev_environment` helper centralises the
``settings.environment == "development"`` predicate the auth service
uses to decide whether to call :meth:`DevResetLinkStore.record` at
all. Putting the gate in one named function (a) keeps the auth
service's code readable, (b) gives the test suite one clear seam to
exercise rather than re-deriving the predicate at each call site,
and (c) makes the design intent ("emit the link only in development")
visible at the import site instead of buried inside an ``if``.

The router that exposes this store over HTTP lives in
``dev/router.py`` (added in task 7.3) and is mounted onto the
FastAPI app only when ``MATCHLAYER_ENVIRONMENT == "development"``
(Design §12.3); this module itself is gate-agnostic, so unit tests
can record and read regardless of the active environment.

Design references:
  * Dev-Mode Reset-Link Surface §12.1 (module shape).
  * Dev-Mode Reset-Link Surface §12.4 (structured-log shape).
  * Dev-Mode Reset-Link Surface §12.5 (persistence forbidden).

Validates: Requirements 13.1, 13.5, 13.6.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import UTC, datetime

import structlog

from matchlayer_api.config import Settings

# Module-level logger so the redaction processor sees the same chain
# every test capture sees. ``cache_logger_on_first_use=False`` in
# :mod:`core.logging` means this binding is a thin proxy over the
# active configuration -- it never freezes a stale processor list.
_log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ResetLinkRecord:
    """One slot in :class:`DevResetLinkStore`.

    ``frozen=True`` so a recorded link cannot be mutated after the
    store hands it back to the router; ``slots=True`` because the
    record holds exactly two attributes and we want the ``__dict__``
    overhead gone (the store may be hit on every reset request in
    development).
    """

    link: str
    created_at: datetime


class DevResetLinkStore:
    """Single-slot LRU. Capacity is 1 by design.

    "LRU" is degenerate at this capacity: every :meth:`record` call
    replaces the previous slot, and :meth:`latest` returns whatever
    was last recorded (or ``None`` before any record). The class is
    deliberately small and lock-protected so a single uvicorn process
    serving multiple workers can share an instance safely.

    The store has *no* file handle, no Redis client, no S3 client, no
    network socket. The persistence-forbidden contract from
    Requirement 13.6 is enforced structurally: there is nowhere for
    a write to leak.
    """

    __slots__ = ("_latest", "_lock")

    def __init__(self) -> None:
        # Lock protects the single-slot replacement against concurrent
        # ``record`` calls from different request workers. The
        # critical section is two assignments long; contention is
        # therefore irrelevant in practice but the lock keeps the
        # invariant ``latest() returns the most recently completed
        # record()`` true under any interleaving.
        self._lock = threading.Lock()
        self._latest: ResetLinkRecord | None = None

    def record(self, link: str) -> None:
        """Replace the slot with a new record and emit one log line.

        Args:
            link: The plaintext reset link to retain. The store does
                not validate the URL shape -- the auth service
                (Design §12.3) is the single caller and constructs
                the link from ``settings.web_base_url`` plus the
                generated token. Validation here would duplicate
                trust without adding any.

        Side effect: emits one structlog ``info`` event named
        ``password_reset_link_generated`` with the link in the
        ``password_reset_link`` field (Requirement 13.1, Design
        §12.4). The log emission is intentionally a side effect of
        :meth:`record` rather than a separate caller responsibility
        -- one place to audit, one place to test.
        """
        record = ResetLinkRecord(link=link, created_at=datetime.now(UTC))
        with self._lock:
            self._latest = record
        # Log emitted outside the lock. The structlog processor chain
        # is thread-safe and the lock has no business blocking a log
        # write.
        _log.info("password_reset_link_generated", password_reset_link=link)

    def latest(self) -> ResetLinkRecord | None:
        """Return the current slot or ``None`` if no link was recorded.

        The returned record is the live frozen dataclass instance.
        Because :class:`ResetLinkRecord` is ``frozen=True``, the
        caller cannot mutate the slot through the returned reference;
        the next :meth:`record` call simply rebinds ``self._latest``.
        """
        with self._lock:
            return self._latest


# Process-wide singleton. The auth service imports this name directly
# (Design §12.3) rather than constructing its own instance so every
# code path -- request handlers, background flush hooks if any are
# ever added -- sees the same slot. Tests that need an isolated store
# instantiate :class:`DevResetLinkStore` directly.
DEV_RESET_LINK_STORE = DevResetLinkStore()


def is_dev_environment(settings: Settings) -> bool:
    """Return ``True`` iff the API is running in the dev environment.

    Centralises the ``settings.environment == "development"`` check
    so the auth service has a single named seam to call into when
    deciding whether to emit the dev-mode reset link (Requirement
    13.1, 13.2). Callers should branch on this helper rather than
    re-deriving the predicate inline -- keeping the gate in one
    place means a future expansion of "development-only behaviour"
    (e.g., a richer stub for staging-style smoke tests) updates a
    single function instead of a scattered set of comparisons.

    Validates: Requirements 13.1, 13.2.
    """
    return settings.environment == "development"


__all__ = [
    "DEV_RESET_LINK_STORE",
    "DevResetLinkStore",
    "ResetLinkRecord",
    "is_dev_environment",
]
