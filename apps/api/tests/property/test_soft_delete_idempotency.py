"""Feature: phase-1-matching — Property 21.

Property 21: Soft deletion is idempotent.

    *For any* owned resume or match, issuing ``DELETE`` twice returns 204 both
    times and results in exactly one corresponding ``resume_deleted`` /
    ``match_deleted`` audit event (the second delete inserts no additional audit
    row).

**Validates: Requirements 4.6, 9.5**

Why this is a *static* (no-DB) property test
---------------------------------------------
Requirement 4.6 (resumes) and 9.5 (matches) make soft deletion idempotent: the
FIRST delete of an owned, non-deleted row stamps ``deleted_at`` and emits one
``resume_deleted`` / ``match_deleted`` audit row; a SECOND (or Nth) delete of an
already-soft-deleted row is a no-op that emits NO further audit row. The
*runtime*, end-to-end proof — that a real ``DELETE`` request returns 204 both
times and the ``audit_events`` table holds exactly one matching row — is
exercised by the integration suites against a real Postgres (the resume
delete/idempotency coverage in task 10.7 and the match coverage in task 11.5).
Those tests require a database; per the design's Testing Strategy this property
test deliberately does **not** (the task forbids Postgres/Redis here), and the
strategy says "idempotence"-style guarantees are validated by property tests
"where feasible and integration tests otherwise."

The feasible, DB-free formulation of "deleting twice emits exactly one audit
row" is the *service-level* invariant the runtime behavior rests on. Both
soft-delete methods follow the identical shape:

    result = await session.execute(select(Model).where(Model.id == ..., user_id))
    row = result.scalar_one_or_none()
    if row is None:               # (resume: raise NotFoundError; match: no-op)
        ...
    if row.deleted_at is not None:  # already deleted -> idempotent no-op
        return
    row.deleted_at = _now()         # first delete: stamp + one audit row
    row.updated_at = _now()
    await self._audit.emit(session, event_type="...deleted", user_id=..., payload=...)

So this module drives the **real** ``Resume_Service.soft_delete_resume`` and
``Scoring_Service.soft_delete_match`` method bodies against:

* an **in-memory fake ``AsyncSession``** whose ``execute(...)`` returns an object
  whose ``scalar_one_or_none()`` yields the generated row (or ``None``). The
  fake returns the *same* row object on every call, faithfully modelling a DB
  row that survives between requests — so the in-place ``deleted_at`` stamp the
  first delete writes is exactly what the second delete reads back; and
* a **recording ``Audit_Service`` stub** capturing every ``emit`` call so the
  "exactly one audit row, then none" invariant is directly observable.

Over Hypothesis-generated scenarios (row exists+active / row exists+already
soft-deleted / row missing → ``None``) and a generated repeat count ``N`` in
``1..6``, the real method is driven ``N`` times and the property is asserted:

* **(a)** an owned, active row → after the FIRST call ``deleted_at`` is stamped
  and exactly ONE audit ``*_deleted`` row is emitted (with the row's id in the
  payload and the owner as ``user_id``);
* **(b)** every SUBSEQUENT call → the audit-emit count stays at one and
  ``deleted_at`` is unchanged (the idempotent no-op);
* **(c)** a missing / ``None`` row → zero audit emits, and the per-method
  documented contract holds: the resume soft-delete raises ``NotFoundError``
  (its 404-no-disclosure contract for a missing / other-owner id), while the
  match soft-delete is a silent no-op.

Both services are covered. No FastAPI app, database, network, or Redis is
touched: the real service objects are constructed with the recording audit stub
injected, and the fake session stands in for the SQLAlchemy session.

The clock is frozen (``services.resumes._now`` / ``services.matching._now`` are
patched to a fixed instant) so the first-delete stamp is a known value and the
"unchanged after the no-op" assertion is exact rather than merely "equal to
whatever was first written".
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from unittest.mock import patch
from uuid import UUID

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from matchlayer_api.core.errors import NotFoundError
from matchlayer_api.db.models import MatchResult, Resume, User
from matchlayer_api.services import matching as matching_module
from matchlayer_api.services import resumes as resumes_module
from matchlayer_api.services.matching import Scoring_Service
from matchlayer_api.services.resumes import Resume_Service

# The two services under test, identified by a small literal so the generated
# scenarios can parametrize over both with one driver.
ServiceKind = Literal["resume", "match"]

# A fixed instant the patched ``_now()`` returns, so the first delete stamps a
# *known* ``deleted_at`` and the "unchanged after a no-op" assertion is exact.
_FROZEN_NOW: datetime = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# In-memory fakes — the only collaborators the soft-delete paths touch.
#
# Both ``soft_delete_resume`` and ``soft_delete_match`` call exactly:
#     result = await session.execute(<select>)
#     row = result.scalar_one_or_none()
# and (on the first delete only) ``await self._audit.emit(...)``. The fakes
# below model precisely that surface and nothing more — no real Postgres/Redis.
# ---------------------------------------------------------------------------


class _FakeResult:
    """Stands in for the SQLAlchemy ``Result`` of ``session.execute``.

    Exposes only ``scalar_one_or_none()`` — the single accessor both
    soft-delete methods use to turn the scoped ``select`` into "the owned row,
    or ``None``".
    """

    def __init__(self, row: Resume | MatchResult | None) -> None:
        self._row = row

    def scalar_one_or_none(self) -> Resume | MatchResult | None:
        return self._row


class _FakeSession:
    """In-memory fake ``AsyncSession`` returning one fixed row (or ``None``).

    Crucially the *same* row object is handed back on every ``execute`` call,
    so the in-place ``deleted_at`` the first delete writes is exactly what a
    subsequent delete reads — the faithful no-DB analogue of a persisted row
    surviving between requests. ``execute_calls`` records how many queries the
    driven method issued so the test can assert one query per delete.
    """

    def __init__(self, row: Resume | MatchResult | None) -> None:
        self._row = row
        self.execute_calls = 0

    async def execute(self, _statement: Any) -> _FakeResult:
        self.execute_calls += 1
        return _FakeResult(self._row)


class _RecordingAudit:
    """Recording ``Audit_Service`` stub: captures every ``emit`` call.

    Mirrors the real ``Audit_Service.emit`` keyword signature so the service
    calls it unchanged. Each call is appended to :attr:`emitted` as a plain
    dict, so the "exactly one ``*_deleted`` row then none" invariant — including
    the event type, the internal-id-only payload, and the owning ``user_id`` —
    is directly observable without a database.
    """

    def __init__(self) -> None:
        self.emitted: list[dict[str, Any]] = []

    async def emit(
        self,
        session: Any,
        *,
        event_type: str,
        user_id: UUID | None = None,
        request: Any = None,
        payload: Any = None,
    ) -> None:
        self.emitted.append({"event_type": event_type, "user_id": user_id, "payload": payload})


# ---------------------------------------------------------------------------
# Driving helpers.
# ---------------------------------------------------------------------------


def _run_sync(coro_factory: Callable[[], Awaitable[None]]) -> None:
    """Run an async test body via :class:`asyncio.Runner`.

    Mirrors ``tests/property/test_rate_limit_window.py`` /
    ``test_keyed_mutation_idempotency.py``: ``Runner`` closes its event loop
    deterministically on ``__exit__`` so the ``ResourceWarning("unclosed event
    loop")`` a bare ``asyncio.run`` can leak in teardown — which this suite's
    ``filterwarnings = ["error"]`` promotes to a failure — cannot occur.
    """
    with asyncio.Runner() as runner:
        runner.run(coro_factory())


def _make_row(
    kind: ServiceKind,
    *,
    row_id: UUID,
    owner_id: UUID,
    deleted_at: datetime | None,
    updated_at: datetime,
) -> Resume | MatchResult:
    """Build a transient ORM row exposing only the fields the path reads/writes.

    The soft-delete methods read ``id`` / ``user_id`` / ``deleted_at`` and write
    ``deleted_at`` / ``updated_at``; transient ORM instances need no session, so
    only those attributes are set (DB ``NOT NULL`` constraints don't apply to an
    un-persisted Python object).
    """
    if kind == "resume":
        return Resume(
            id=row_id,
            user_id=owner_id,
            deleted_at=deleted_at,
            updated_at=updated_at,
        )
    return MatchResult(
        id=row_id,
        user_id=owner_id,
        deleted_at=deleted_at,
        updated_at=updated_at,
    )


def _make_service(kind: ServiceKind, audit: _RecordingAudit) -> Resume_Service | Scoring_Service:
    """Construct the real service with the recording audit stub injected.

    ``storage`` / ``settings`` are left to their lazy defaults: the soft-delete
    paths touch neither (storage is resolved only on the upload path), so no
    boto3 client or extra wiring is built.
    """
    if kind == "resume":
        return Resume_Service(audit=audit)  # type: ignore[arg-type]
    return Scoring_Service(audit=audit)  # type: ignore[arg-type]


async def _delete_once(
    kind: ServiceKind,
    service: Resume_Service | Scoring_Service,
    session: _FakeSession,
    *,
    owner_id: UUID,
    row_id: UUID,
) -> None:
    """Invoke the real soft-delete method once for *kind*."""
    if kind == "resume":
        assert isinstance(service, Resume_Service)
        await service.soft_delete_resume(
            session,  # type: ignore[arg-type]
            user=User(id=owner_id),
            resume_id=row_id,
        )
    else:
        assert isinstance(service, Scoring_Service)
        await service.soft_delete_match(
            session,  # type: ignore[arg-type]
            user_id=owner_id,
            match_id=row_id,
        )


def _expected_event(kind: ServiceKind) -> str:
    return "resume_deleted" if kind == "resume" else "match_deleted"


def _expected_payload(kind: ServiceKind, row_id: UUID) -> dict[str, str]:
    key = "resume_id" if kind == "resume" else "match_id"
    return {key: str(row_id)}


def _patched_clock() -> Any:
    """Patch both services' module-level ``_now`` to return :data:`_FROZEN_NOW`."""
    return patch.multiple(
        resumes_module,
        _now=lambda: _FROZEN_NOW,
    ), patch.multiple(
        matching_module,
        _now=lambda: _FROZEN_NOW,
    )


# ===========================================================================
# (a) + (b): an owned, ACTIVE row deleted N times stamps once, audits once.
# ===========================================================================


@settings(max_examples=200, deadline=None)
@given(
    kind=st.sampled_from(("resume", "match")),
    owner_id=st.uuids(),
    row_id=st.uuids(),
    repeat=st.integers(min_value=1, max_value=6),
)
def test_repeated_delete_of_owned_row_emits_exactly_one_audit(
    kind: ServiceKind,
    owner_id: UUID,
    row_id: UUID,
    repeat: int,
) -> None:
    """Deleting an owned, active row N times stamps once and audits exactly once.

    Property 21 (Requirements 4.6, 9.5), the core idempotence facet: for an
    owned row that starts active (``deleted_at IS NULL``), the FIRST
    ``soft_delete_*`` call stamps ``deleted_at`` (and ``updated_at``) and emits
    exactly one ``resume_deleted`` / ``match_deleted`` audit row carrying the
    row's id and the owner's ``user_id``; every one of the remaining ``N-1``
    calls is a no-op that emits NO further audit row and leaves ``deleted_at``
    untouched. The driven method bodies are the real services'; only the session
    and audit collaborators are in-memory fakes.
    """

    async def _run() -> None:
        audit = _RecordingAudit()
        service = _make_service(kind, audit)
        row = _make_row(
            kind,
            row_id=row_id,
            owner_id=owner_id,
            deleted_at=None,
            updated_at=_FROZEN_NOW - timedelta(days=1),
        )
        session = _FakeSession(row)

        resumes_patch, matching_patch = _patched_clock()
        with resumes_patch, matching_patch:
            for _ in range(repeat):
                # Each call returns ``None`` and never raises — the router maps
                # that to HTTP 204 for both the first and every later delete.
                await _delete_once(kind, service, session, owner_id=owner_id, row_id=row_id)

        # (a) Exactly one audit row, for the right event, with internal ids only.
        assert len(audit.emitted) == 1, (
            f"{kind}: expected exactly one audit row across {repeat} deletes, "
            f"got {len(audit.emitted)}"
        )
        event = audit.emitted[0]
        assert event["event_type"] == _expected_event(kind)
        assert event["payload"] == _expected_payload(kind, row_id)
        assert event["user_id"] == owner_id

        # (a) The first delete stamped ``deleted_at`` / ``updated_at``; (b) every
        # later no-op left ``deleted_at`` at exactly that frozen instant.
        assert row.deleted_at == _FROZEN_NOW
        assert row.updated_at == _FROZEN_NOW

        # One scoped query was issued per delete (the no-op still looks the row
        # up to decide it is already deleted).
        assert session.execute_calls == repeat

    _run_sync(_run)


# ===========================================================================
# (b): an ALREADY soft-deleted owned row is a no-op every time (no audit).
# ===========================================================================


@settings(max_examples=200, deadline=None)
@given(
    kind=st.sampled_from(("resume", "match")),
    owner_id=st.uuids(),
    row_id=st.uuids(),
    repeat=st.integers(min_value=1, max_value=6),
    prior_offset_seconds=st.integers(min_value=1, max_value=10_000_000),
)
def test_delete_of_already_soft_deleted_row_never_audits(
    kind: ServiceKind,
    owner_id: UUID,
    row_id: UUID,
    repeat: int,
    prior_offset_seconds: int,
) -> None:
    """An already-soft-deleted owned row is an idempotent no-op (no audit row).

    Property 21 (Requirements 4.6, 9.5), no-op facet: when the owned row already
    carries a non-null ``deleted_at`` (a prior delete), every subsequent
    ``soft_delete_*`` call returns without raising, emits NO ``*_deleted`` audit
    row, and leaves the original ``deleted_at`` exactly as it was — so a repeated
    DELETE can never insert a second audit event or move the deletion timestamp.
    """
    prior_deleted_at = _FROZEN_NOW - timedelta(seconds=prior_offset_seconds)

    async def _run() -> None:
        audit = _RecordingAudit()
        service = _make_service(kind, audit)
        row = _make_row(
            kind,
            row_id=row_id,
            owner_id=owner_id,
            deleted_at=prior_deleted_at,
            updated_at=prior_deleted_at,
        )
        session = _FakeSession(row)

        resumes_patch, matching_patch = _patched_clock()
        with resumes_patch, matching_patch:
            for _ in range(repeat):
                await _delete_once(kind, service, session, owner_id=owner_id, row_id=row_id)

        assert audit.emitted == [], (
            f"{kind}: an already-soft-deleted row must emit no audit row, got {audit.emitted}"
        )
        # The pre-existing deletion timestamp is never overwritten by a no-op.
        assert row.deleted_at == prior_deleted_at
        assert session.execute_calls == repeat

    _run_sync(_run)


# ===========================================================================
# (c): a MISSING / None row — each method's documented contract.
# ===========================================================================


@settings(max_examples=200, deadline=None)
@given(
    kind=st.sampled_from(("resume", "match")),
    owner_id=st.uuids(),
    row_id=st.uuids(),
    repeat=st.integers(min_value=1, max_value=6),
)
def test_delete_of_missing_row_follows_each_methods_contract(
    kind: ServiceKind,
    owner_id: UUID,
    row_id: UUID,
    repeat: int,
) -> None:
    """A missing / other-owner row emits no audit and follows the method contract.

    Property 21 boundary (Requirements 4.6, 9.5): when the scoped lookup yields
    ``None`` (the id is missing, soft-deleted out of an unscoped view, or owned
    by another account), no ``*_deleted`` audit row is ever emitted. The two
    services' documented contracts for that case differ, and both are asserted:

    * ``soft_delete_resume`` raises :class:`NotFoundError` on every call (its
      404-no-disclosure contract — Requirements 1.5, 1.6, 4.6); and
    * ``soft_delete_match`` is a silent no-op on every call (Requirement 9.5),
      which the router maps to 204.
    """

    async def _run() -> None:
        audit = _RecordingAudit()
        service = _make_service(kind, audit)
        session = _FakeSession(None)  # scoped lookup finds nothing

        resumes_patch, matching_patch = _patched_clock()
        with resumes_patch, matching_patch:
            for _ in range(repeat):
                if kind == "resume":
                    with pytest.raises(NotFoundError):
                        await _delete_once(kind, service, session, owner_id=owner_id, row_id=row_id)
                else:
                    # Match soft-delete is a silent no-op for a missing row.
                    await _delete_once(kind, service, session, owner_id=owner_id, row_id=row_id)

        assert audit.emitted == [], (
            f"{kind}: a missing row must emit no audit row, got {audit.emitted}"
        )
        assert session.execute_calls == repeat

    _run_sync(_run)
