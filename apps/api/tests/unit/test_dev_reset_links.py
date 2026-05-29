"""Unit tests for ``dev/reset_links.py``.

Pins down the three contracts the rest of the auth surface depends on:

1. **Single-slot LRU eviction.** ``record(a); record(b); latest() == b``.
   The store has capacity 1 by design (Dev-Mode Reset-Link Surface §12.2);
   each ``record(...)`` call replaces the previous slot and ``latest()``
   returns the most recently recorded value (or ``None`` before any
   record). Validates Requirement 13.5.

2. **No-persist contract.** The store has no filesystem path, no Redis
   client, no S3 client, no network socket — there is nowhere for a
   write to leak. Validates Requirement 13.6 and Design §12.5
   (persistence forbidden).

3. **Env-gating helper.** :func:`is_dev_environment` returns ``True``
   iff ``settings.environment == "development"``. The auth service
   uses this seam to decide whether to call
   :meth:`DevResetLinkStore.record` at all — pinning the predicate
   here means a future refactor of the gate (e.g. adding a staging-style
   carve-out) updates one named function rather than scattered
   comparisons. Validates the §12.3 env-gate contract that backs
   Requirements 13.1 / 13.2.

The tests drive the module surface directly — no FastAPI app, no HTTP
client. Each LRU/persistence test instantiates a fresh
:class:`DevResetLinkStore` so the process-singleton
:data:`DEV_RESET_LINK_STORE` is not mutated by the suite (a separate
test asserts the singleton itself is wired correctly).

Design references:
  * Dev-Mode Reset-Link Surface §12.1 (module shape).
  * Dev-Mode Reset-Link Surface §12.2 (single-slot semantics).
  * Dev-Mode Reset-Link Surface §12.3 (env-gating).
  * Dev-Mode Reset-Link Surface §12.4 (structured-log shape).
  * Dev-Mode Reset-Link Surface §12.5 (persistence forbidden).
"""

from __future__ import annotations

import dataclasses
import inspect
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import structlog

from matchlayer_api.dev import reset_links as reset_links_module
from matchlayer_api.dev.reset_links import (
    DEV_RESET_LINK_STORE,
    DevResetLinkStore,
    ResetLinkRecord,
    is_dev_environment,
)

# ---------------------------------------------------------------------------
# Test fixtures and constants
#
# Two URL-shaped strings that are obviously different so an off-by-one
# replacement bug in the store is caught by an equality check rather than
# a same-prefix assertion. The shape mirrors the production link
# (``${web_base_url}/reset-password?token=<plaintext>``) so a future
# refactor that tries to URL-validate inside the store fails loud here
# rather than silently in the auth service.
# ---------------------------------------------------------------------------

_LINK_A = "http://localhost:3000/reset-password?token=alpha-token-aaaaaaaaaaaa"
_LINK_B = "http://localhost:3000/reset-password?token=beta-token-bbbbbbbbbbbbb"
_LINK_C = "http://localhost:3000/reset-password?token=gamma-token-cccccccccccc"


# ---------------------------------------------------------------------------
# 1. Single-slot LRU eviction — Requirement 13.5
# ---------------------------------------------------------------------------


class TestSingleSlotLruEviction:
    """``record(a); record(b); latest() == b`` — capacity is 1."""

    def test_latest_is_none_before_any_record(self) -> None:
        """A freshly-constructed store has nothing to return.

        :meth:`DevResetLinkStore.latest` returns ``None`` (not an empty
        :class:`ResetLinkRecord`, not a sentinel) so the router can
        distinguish "no link recorded yet" from "link recorded with
        empty string" — the §12.1 router-shape contract returns both
        ``link`` and ``created_at`` as ``null`` only on this branch.
        """
        store = DevResetLinkStore()
        assert store.latest() is None

    def test_record_then_latest_returns_recorded_link(self) -> None:
        """A single ``record`` populates the slot and ``latest()`` returns it.

        The returned value is a :class:`ResetLinkRecord` whose ``link``
        equals the recorded string and whose ``created_at`` is a
        timezone-aware UTC :class:`~datetime.datetime` — the shape
        :func:`record` is contracted to produce per §12.1.
        """
        store = DevResetLinkStore()
        before = datetime.now(UTC)
        store.record(_LINK_A)
        after = datetime.now(UTC)

        recorded = store.latest()
        assert recorded is not None
        assert isinstance(recorded, ResetLinkRecord)
        assert recorded.link == _LINK_A
        # Timezone-aware so the created_at comparison below is valid;
        # the §12.4 log shape calls for ISO-8601 UTC, which can only
        # come from a tz-aware datetime.
        assert recorded.created_at.tzinfo is not None
        # The timestamp lives between the two perf samples bracketing
        # the call. Allow a small slack on either side so test-host
        # clock drift can't make the assertion flake.
        assert before - timedelta(seconds=1) <= recorded.created_at <= after + timedelta(seconds=1)

    def test_second_record_evicts_first_link(self) -> None:
        """``record(a); record(b)`` → ``latest().link == b``.

        Validates: Requirement 13.5.

        The store is a single-slot LRU by design (§12.2). The first
        link is evicted on the second record; a developer who requests
        a reset for User A and then for User B sees only User B's
        link via the dev surface. This matches the documented
        "last thing I clicked" workflow.
        """
        store = DevResetLinkStore()
        store.record(_LINK_A)
        store.record(_LINK_B)

        recorded = store.latest()
        assert recorded is not None
        assert recorded.link == _LINK_B

    def test_third_record_evicts_second_link(self) -> None:
        """Eviction is unconditional: capacity stays 1 across many records.

        Three sequential records leave the third in the slot. This
        pins down that there is no high-water mark, no ring buffer,
        no second-most-recent retention path — the contract is
        capacity = 1, full stop.
        """
        store = DevResetLinkStore()
        store.record(_LINK_A)
        store.record(_LINK_B)
        store.record(_LINK_C)

        recorded = store.latest()
        assert recorded is not None
        assert recorded.link == _LINK_C

    def test_record_emits_one_info_log_with_password_reset_link_key(self) -> None:
        """``record`` emits exactly one ``info`` event with the link in the payload.

        Per Requirement 13.1 and Design §12.4 the record side effect is
        a single structured ``info``-level log line whose payload
        contains a ``password_reset_link`` field set to the URL. This
        test pins the log shape because the Auth_Service's dev-mode
        log carve-out (Requirements Appendix A, REQ-13.6) depends on
        ``record`` being the single emission site.
        """
        store = DevResetLinkStore()
        with structlog.testing.capture_logs() as captured:
            store.record(_LINK_A)

        # Filter to the events this call emitted. ``capture_logs``
        # swaps the active processor chain wholesale, so anything in
        # ``captured`` came from the ``record`` call we just made.
        assert len(captured) == 1, (
            f"Expected exactly one log event from record(); got {len(captured)}: {captured!r}"
        )
        event = captured[0]
        assert event["log_level"] == "info"
        assert event["password_reset_link"] == _LINK_A

    def test_latest_does_not_emit_log(self) -> None:
        """:meth:`latest` is a pure read — no log line, no side effect.

        Pins the contract that only :meth:`record` emits the dev-mode
        log line; a developer polling :meth:`latest` (which the dev
        router does on every GET) cannot create a duplicate emission.
        """
        store = DevResetLinkStore()
        store.record(_LINK_A)
        with structlog.testing.capture_logs() as captured:
            _ = store.latest()
            _ = store.latest()
            _ = store.latest()
        assert captured == []


# ---------------------------------------------------------------------------
# 2. No-persist contract — Requirement 13.6
# ---------------------------------------------------------------------------


class TestNoPersistContract:
    """The store has no filesystem, Redis, or external write paths.

    Per Requirement 13.6 / Design §12.5, the plaintext Reset_Token must
    never be persisted to disk, Redis, or any external service. The
    contract is enforced *structurally*: the class holds two attributes
    (a lock and an optional record) and nowhere does it construct a
    file handle, Redis client, or network socket. These tests pin the
    structural shape so a future refactor that quietly adds a write
    path fails the build.
    """

    def test_store_attributes_are_only_lock_and_latest(self) -> None:
        """:class:`DevResetLinkStore` holds exactly two slots.

        The class declares ``__slots__ = ("_latest", "_lock")``; if
        someone later adds e.g. ``self._redis`` or ``self._fh`` for
        "spillover", this assertion fails and the reviewer sees the
        ``__slots__`` change in the same diff. The slot names
        themselves are part of the no-persist contract: there is no
        slot named after a backing store.
        """
        # Sorted comparison so ordering inside the tuple isn't the test.
        assert sorted(DevResetLinkStore.__slots__) == ["_latest", "_lock"]

    def test_no_filesystem_or_redis_imports_in_module(self) -> None:
        """The module imports nothing that could be a write path.

        The three import names that would indicate a leak — ``redis``
        (Redis client), ``boto3`` / ``aioboto3`` (S3 / AWS), and
        ``pathlib`` / ``shutil`` / ``tempfile`` (filesystem) — must
        not be present. :mod:`io` is not on the list because reading
        is allowed (e.g. test capture); the contract is about *write*
        paths leaving the process.

        Implementation detail: we inspect the module's source text,
        not just its global namespace, because an ``import`` inside
        a method body would not show up in ``dir(module)`` until the
        method ran. The textual check catches the import even if the
        method was never called.
        """
        source = inspect.getsource(reset_links_module)
        forbidden_imports = (
            "import redis",
            "from redis",
            "import boto3",
            "from boto3",
            "import aioboto3",
            "from aioboto3",
            "import pathlib",
            "from pathlib",
            "import shutil",
            "from shutil",
            "import tempfile",
            "from tempfile",
            "import socket",
            "from socket",
            "import requests",
            "from requests",
            "import httpx",
            "from httpx",
            "import urllib",
            "from urllib",
        )
        leaked: list[str] = []
        for line in source.splitlines():
            stripped = line.strip()
            # Skip comments and docstrings — the no-persist contract
            # is itself documented in the module docstring with the
            # word "Redis", and that mention must not be flagged.
            if stripped.startswith("#"):
                continue
            for needle in forbidden_imports:
                if stripped.startswith(needle):
                    leaked.append(line)
        assert leaked == [], (
            "Persistence-related imports leaked into dev/reset_links.py: "
            f"{leaked!r}. The store must hold no filesystem, Redis, or "
            "network client per Requirement 13.6 / Design §12.5."
        )

    def test_record_does_not_open_files(self, monkeypatch: Any) -> None:
        """``record(...)`` never calls :func:`builtins.open`.

        Belt-and-braces against a future refactor that adds a
        "convenience" sidecar file. Patching :func:`open` to a mock
        and asserting ``call_count == 0`` after a record is the
        cheapest behavioural check that catches this regression
        without having to walk every method body by hand.
        """
        open_mock = MagicMock(
            side_effect=AssertionError(
                "DevResetLinkStore.record must not open any file (Requirement 13.6)."
            )
        )
        monkeypatch.setattr("builtins.open", open_mock)

        store = DevResetLinkStore()
        store.record(_LINK_A)
        store.latest()  # also assert the read path is open-free

        assert open_mock.call_count == 0

    def test_reset_link_record_is_immutable(self) -> None:
        """:class:`ResetLinkRecord` is ``frozen=True``.

        A consumer of :meth:`latest` cannot mutate the slot through
        the returned reference (§12.1 specifies ``frozen=True,
        slots=True``). This means the only way a recorded link can
        change is via a new :meth:`record` call — which is itself
        the documented eviction event.
        """
        store = DevResetLinkStore()
        store.record(_LINK_A)
        recorded = store.latest()
        assert recorded is not None

        # The dataclass is frozen — attempting to assign to a field
        # raises ``FrozenInstanceError`` (a subclass of ``AttributeError``).
        try:
            recorded.link = _LINK_B  # type: ignore[misc]
        except dataclasses.FrozenInstanceError:
            pass
        else:
            raise AssertionError(
                "ResetLinkRecord must be frozen so callers cannot mutate "
                "the returned slot (Design §12.1)."
            )

    def test_module_exposes_process_singleton(self) -> None:
        """:data:`DEV_RESET_LINK_STORE` is a single shared instance.

        The auth service imports this name directly (Design §12.3) so
        the dev-router and the auth-service request handler see the
        same slot. A two-instance regression would silently break the
        dev workflow: the link recorded in the request handler would
        not appear in the GET response.
        """
        assert isinstance(DEV_RESET_LINK_STORE, DevResetLinkStore)
        # Re-importing the module yields the same object — module-level
        # bindings are cached by ``sys.modules`` so this also pins the
        # "process-singleton" wording in the module docstring.
        from matchlayer_api.dev import reset_links as second_module

        assert second_module.DEV_RESET_LINK_STORE is DEV_RESET_LINK_STORE


# ---------------------------------------------------------------------------
# 3. Env-gating helper — Requirements 13.1, 13.2 / Design §12.3
# ---------------------------------------------------------------------------


class TestIsDevEnvironment:
    """The seam the auth service uses to decide whether to record.

    :func:`is_dev_environment` returns ``True`` iff
    ``settings.environment == "development"``. The auth service's
    reset-request flow (Design §12.3) calls this helper before
    calling :meth:`DevResetLinkStore.record`; pinning the predicate
    here means changes to "development-only behaviour" land in one
    named function instead of scattered ``settings.environment ==``
    comparisons.
    """

    def _stub_settings(self, environment: str) -> Any:
        """Build a duck-typed settings stub with the given environment.

        :func:`is_dev_environment` only reads ``settings.environment``,
        so a stub is enough — pulling in a real
        :class:`~matchlayer_api.config.Settings` here would couple
        the test to every other environment variable on the model
        without measuring anything.
        """
        stub = MagicMock()
        stub.environment = environment
        return stub

    def test_returns_true_for_development(self) -> None:
        """Validates: Requirement 13.1.

        The development environment is the deliberate single carve-out
        (Requirements Appendix A, REQ-13.6) where the dev-mode log
        line is emitted and the store is updated.
        """
        assert is_dev_environment(self._stub_settings("development")) is True

    def test_returns_false_for_staging(self) -> None:
        """Staging is *not* development — Requirement 13.2.

        Staging deploys real domains and (in Phase 6+) real users; a
        plaintext reset link must never reach a staging log stream.
        """
        assert is_dev_environment(self._stub_settings("staging")) is False

    def test_returns_false_for_production(self) -> None:
        """Production is *not* development — Requirement 13.2.

        The production carve-out is closed by design: no dev-mode
        log, no store update, and (per §12.3) the dev router itself
        is not even mounted onto the FastAPI app.
        """
        assert is_dev_environment(self._stub_settings("production")) is False

    def test_returns_false_for_unrecognised_value(self) -> None:
        """A typo or future environment string defaults to "not dev".

        Defence in depth against a future refactor that introduces a
        new environment literal: the gate fails closed (no log, no
        store update) until the new environment is explicitly
        added to the predicate.
        """
        # ``MagicMock`` accepts any string for the ``environment``
        # attribute — :func:`is_dev_environment` reads but does not
        # validate the value, so the test stub bypasses the
        # ``Settings`` Literal validator on purpose.
        assert is_dev_environment(self._stub_settings("local")) is False
        assert is_dev_environment(self._stub_settings("DEVELOPMENT")) is False
        assert is_dev_environment(self._stub_settings("")) is False
