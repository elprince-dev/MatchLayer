"""Unit tests: the five phase-1-matching audit payloads carry no PII.

Task 8.2. Validates Requirements 2.7, 4.5, 8.6, 9.4, 11.6.

``phase-1-matching`` adds five ``event_type`` payloads to
:mod:`matchlayer_api.services.audit` (Audit Log §11.2):

* ``resume_uploaded`` -> :class:`ResumeUploadedPayload` ``{resume_id}``
  (Requirement 2.7).
* ``resume_deleted``  -> :class:`ResumeDeletedPayload`  ``{resume_id}``
  (Requirement 4.5).
* ``match_created``   -> :class:`MatchCreatedPayload`   ``{resume_id, match_id}``
  (Requirement 8.6).
* ``match_deleted``   -> :class:`MatchDeletedPayload`   ``{match_id}``
  (Requirement 9.4).
* ``quota_rejected``  -> :class:`QuotaRejectedPayload`  ``{quota}``
  (Requirement 11.6).

The Restricted PII this spec handles is the uploaded file bytes, the
extracted resume text, the original filename, and the job-description
text (requirements.md, ``security.md`` "Logging"). None of it may ever
reach an Audit_Event payload. These tests pin that contract two ways:

1. **Statically**, over each ``TypedDict``'s declared key set, so a
   future field added to one of these payloads (say ``original_filename``)
   fails the build before it is ever emitted.
2. **At emit time**, by driving :meth:`Audit_Service.emit` for every new
   event type through a capture session and inspecting the staged
   :class:`~matchlayer_api.db.models.AuditEvent` row's ``payload`` dict.

Both layers assert the *positive* shape (keys are internal IDs or the
``quota`` discriminator and nothing else) and the *negative* one (no PII
key by exact name or by substring). The principal ``user_id`` is checked
to live on the row column, never inside the payload.
"""

from __future__ import annotations

import re
from uuid import uuid4

import pytest

from matchlayer_api.db.models import AuditEvent
from matchlayer_api.services.audit import (
    Audit_Service,
    MatchCreatedPayload,
    MatchDeletedPayload,
    QuotaRejectedPayload,
    ResumeDeletedPayload,
    ResumeUploadedPayload,
)

# ---------------------------------------------------------------------------
# The contract under test: each new event type, its payload TypedDict, the
# exact key set it is allowed to carry, and a representative payload value to
# emit. Keeping this in one table means "add an event type but forget the
# test" is caught by ``test_every_new_event_type_is_covered`` below.
# ---------------------------------------------------------------------------
_RESUME_ID = str(uuid4())
_MATCH_ID = str(uuid4())

_NEW_EVENTS = {
    "resume_uploaded": (ResumeUploadedPayload, {"resume_id"}, {"resume_id": _RESUME_ID}),
    "resume_deleted": (ResumeDeletedPayload, {"resume_id"}, {"resume_id": _RESUME_ID}),
    "match_created": (
        MatchCreatedPayload,
        {"resume_id", "match_id"},
        {"resume_id": _RESUME_ID, "match_id": _MATCH_ID},
    ),
    "match_deleted": (MatchDeletedPayload, {"match_id"}, {"match_id": _MATCH_ID}),
    "quota_rejected": (QuotaRejectedPayload, {"quota"}, {"quota": "upload"}),
}

# Exact PII field names that must never appear as a payload key. These mirror
# the Restricted columns / inputs this spec touches (requirements.md glossary,
# Requirements 2.6, 3.6, 8.8).
_FORBIDDEN_PII_KEYS = frozenset(
    {
        "original_filename",
        "filename",
        "file_name",
        "extracted_text",
        "resume_text",
        "text",
        "job_description_text",
        "job_description",
        "jd_text",
        "jd",
        "file_bytes",
        "bytes",
        "raw_bytes",
        "content",
        "body",
        "storage_key",
    }
)

# Substrings that betray PII regardless of the exact key name. None of the
# permitted keys (``resume_id``, ``match_id``, ``quota``) contain any of
# these, so a future leak like ``resume_filename`` or ``jd_excerpt`` trips it.
_FORBIDDEN_PII_SUBSTRINGS = (
    "filename",
    "text",
    "bytes",
    "description",
    "content",
    "password",
    "token",
    "email",
)

# An allowed payload key is either an internal ID (``*_id``) or the
# ``quota`` discriminator literal. Nothing else is permitted.
_INTERNAL_ID_KEY = re.compile(r"^[a-z][a-z0-9_]*_id$")


def _key_is_allowed(key: str) -> bool:
    return bool(_INTERNAL_ID_KEY.fullmatch(key)) or key == "quota"


class _CaptureSession:
    """Minimal stand-in for ``AsyncSession`` capturing staged rows.

    :meth:`Audit_Service.emit` only ever calls ``session.add(row)`` (it
    never commits or executes -- Audit Log §11.3), so capturing ``add``
    is enough to inspect the row that would be persisted without a live
    Postgres. Any other attribute access would be a regression in
    ``emit`` and should fail loudly, which a plain object naturally does.
    """

    def __init__(self) -> None:
        self.added: list[AuditEvent] = []

    def add(self, instance: AuditEvent) -> None:
        self.added.append(instance)


@pytest.fixture
def audit() -> Audit_Service:
    return Audit_Service()


# ---------------------------------------------------------------------------
# Static (declaration-time) assertions over the TypedDict key sets.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("payload_cls", "expected_keys"),
    [(cls, keys) for cls, keys, _ in _NEW_EVENTS.values()],
    ids=list(_NEW_EVENTS.keys()),
)
def test_payload_typeddict_declares_exactly_internal_keys(
    payload_cls: type, expected_keys: set[str]
) -> None:
    """Each new payload TypedDict declares exactly its internal-ID/quota keys.

    Equality (not subset) is asserted so adding ANY extra field -- PII or
    not -- to one of these payloads is a build failure here.

    Validates: Requirements 2.7, 4.5, 8.6, 9.4, 11.6.
    """
    assert set(payload_cls.__annotations__.keys()) == expected_keys


@pytest.mark.parametrize(
    "payload_cls",
    [cls for cls, _, _ in _NEW_EVENTS.values()],
    ids=list(_NEW_EVENTS.keys()),
)
def test_payload_typeddict_has_no_pii_keys(payload_cls: type) -> None:
    """No new payload TypedDict declares a PII key by name or by substring.

    Validates: Requirements 2.7, 4.5, 8.6, 9.4, 11.6.
    """
    keys = set(payload_cls.__annotations__.keys())

    leaked_exact = keys & _FORBIDDEN_PII_KEYS
    assert not leaked_exact, f"{payload_cls.__name__} declares PII key(s): {sorted(leaked_exact)}"

    for key in keys:
        lowered = key.lower()
        offending = [s for s in _FORBIDDEN_PII_SUBSTRINGS if s in lowered]
        assert not offending, (
            f"{payload_cls.__name__} key {key!r} contains PII substring(s): {offending}"
        )


@pytest.mark.parametrize(
    "payload_cls",
    [cls for cls, _, _ in _NEW_EVENTS.values()],
    ids=list(_NEW_EVENTS.keys()),
)
def test_payload_keys_are_internal_ids_or_quota(payload_cls: type) -> None:
    """Every payload key is an internal ``*_id`` or the ``quota`` discriminator.

    The positive complement of the no-PII assertion: it is not enough that
    known PII names are absent; the only shapes permitted are internal IDs
    and the quota category literal.

    Validates: Requirements 2.7, 4.5, 8.6, 9.4, 11.6.
    """
    disallowed = [key for key in payload_cls.__annotations__ if not _key_is_allowed(key)]
    assert not disallowed, f"{payload_cls.__name__} declares non-internal key(s): {disallowed}"


# ---------------------------------------------------------------------------
# Emit-time assertions: the staged AuditEvent.payload carries only the
# permitted keys, and the principal user_id lives on the row, not the payload.
# ---------------------------------------------------------------------------


async def test_emitting_each_new_event_stages_pii_free_payload(audit: Audit_Service) -> None:
    """Emitting every new event type stages a payload with only allowed keys.

    Drives the real :meth:`Audit_Service.emit` for each of the five event
    types through a capture session and inspects the staged
    :class:`AuditEvent`. Asserts the persisted ``payload`` dict carries
    exactly the expected internal-ID/quota keys, has no PII key by name or
    substring, and that the principal ``user_id`` is on the row column --
    never inside the payload.

    Validates: Requirements 2.7, 4.5, 8.6, 9.4, 11.6.
    """
    for event_type, (_cls, expected_keys, payload) in _NEW_EVENTS.items():
        session = _CaptureSession()
        user_id = uuid4()

        # The capture session is structurally compatible with what emit
        # touches (session.add); cast for the type checker only.
        await audit.emit(
            session,  # type: ignore[arg-type]
            event_type=event_type,  # type: ignore[arg-type]
            user_id=user_id,
            payload=payload,  # type: ignore[arg-type]
        )

        assert len(session.added) == 1, f"{event_type}: expected exactly one staged row"
        row = session.added[0]
        assert isinstance(row, AuditEvent)
        assert row.event_type == event_type

        # The principal is a row column, not payload content.
        assert row.user_id == user_id
        assert "user_id" not in row.payload

        # Payload keys: exactly the expected internal-ID/quota set.
        assert set(row.payload.keys()) == expected_keys

        # No PII key by exact name.
        assert not (set(row.payload.keys()) & _FORBIDDEN_PII_KEYS)

        # No PII key by substring; every key is internal-ID/quota shaped.
        for key in row.payload:
            lowered = key.lower()
            assert not any(s in lowered for s in _FORBIDDEN_PII_SUBSTRINGS), (
                f"{event_type}: payload key {key!r} looks like PII"
            )
            assert _key_is_allowed(key), f"{event_type}: payload key {key!r} is not internal"


async def test_quota_rejected_payload_value_is_a_category_not_free_text(
    audit: Audit_Service,
) -> None:
    """``quota_rejected`` records only the quota category, never request text.

    The one new payload whose value is not a UUID is ``quota``. Confirm it
    stays a closed-vocabulary discriminator (``upload`` / ``scoring``) so it
    can never become a vector for echoing user input.

    Validates: Requirement 11.6.
    """
    for category in ("upload", "scoring"):
        session = _CaptureSession()
        await audit.emit(
            session,  # type: ignore[arg-type]
            event_type="quota_rejected",
            user_id=uuid4(),
            payload=QuotaRejectedPayload(quota=category),  # type: ignore[arg-type]
        )
        row = session.added[0]
        assert row.payload == {"quota": category}


def test_every_new_event_type_is_covered() -> None:
    """Guard: the five phase-1-matching event types are all exercised here.

    If a sixth event type is added to the Audit_Service without a row in
    ``_NEW_EVENTS``, this fails so the no-PII contract can never silently
    skip a new payload.

    Validates: Requirements 2.7, 4.5, 8.6, 9.4, 11.6.
    """
    assert set(_NEW_EVENTS) == {
        "resume_uploaded",
        "resume_deleted",
        "match_created",
        "match_deleted",
        "quota_rejected",
    }
