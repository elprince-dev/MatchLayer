"""``Audit_Service``: append-only writer for the ``audit_events`` table.

This is the ONLY module in the API that inserts into ``audit_events``.
Every auth mutation routes through here so the audit row commits in
the same transaction as the mutation that produced it (Audit Log
§11.3, Requirement 15.4) -- the API role's grants on ``audit_events``
(``INSERT, SELECT`` only; ``UPDATE, DELETE, TRUNCATE`` revoked, see
§4.5 / Requirement 11.2) make the table forensically meaningful at
the database boundary regardless of what happens above it.

Design references:
  * Audit Log §11.2 -- the enumerated ``event_type`` strings and the
    payload schema each one accepts.
  * Audit Log §11.3 -- the same-transaction insert rule.
  * Audit Log §11.4 -- the role-grant rationale.
  * Data Models §4.4 -- the column shape on the table.

Typing strategy. Each ``event_type`` from §11.2 is paired with a
``TypedDict`` describing its required payload keys. ``emit`` is
overloaded so mypy strict refuses both:

  1. an unrecognised ``event_type`` literal (typo), and
  2. a payload whose keys don't match the schema for the chosen
     ``event_type`` (e.g., spelling ``family_id`` as ``familyid`` on a
     reuse-detection event).

Forbidden keys per Requirement 11.4 / ``security.md`` "Logging":
``password``, ``password_hash``, ``new_password``, plaintext
``Reset_Token``, JWT bytes, and any display-name string. The
``TypedDict`` schemas below simply do not contain those keys, so the
mypy overload is the enforcement mechanism -- there is no runtime
allow/deny list to keep in sync with the design.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, TypedDict, overload
from uuid import UUID

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from matchlayer_api.db.models import AuditEvent

# ---------------------------------------------------------------------------
# user_agent truncation cap (Requirement 11.5).
# Stored as `text` so Postgres has no fixed limit; the cap exists to keep
# pathological client UAs from bloating the audit table.
# ---------------------------------------------------------------------------
_USER_AGENT_MAX_LENGTH = 1024


# ---------------------------------------------------------------------------
# Per-event TypedDict payload schemas (Audit Log §11.2).
#
# Every ``event_type`` enumerated in §11.2 has exactly one ``TypedDict``
# below. ``total=True`` is the default; ``EmptyPayload`` is reused for
# every event whose payload is documented as ``{}``.
# ---------------------------------------------------------------------------


class EmptyPayload(TypedDict):
    """Payload shape for events whose §11.2 schema is ``{}``.

    Used by: ``registration_success``,
    ``registration_attempt_existing_email``, ``login_success``,
    ``password_reset_requested``, ``password_reset_confirmed``,
    ``account_deleted``.
    """


class LoginFailurePayload(TypedDict):
    """Payload for ``login_failure`` (§11.2).

    The lower-cased submitted email is permitted by Requirement 2.7
    and ``security.md`` "Logging" (emails are not in the never-log
    list). The plaintext password is, of course, never recorded.
    """

    submitted_email: str


class AccountLockedPayload(TypedDict):
    """Payload for ``account_locked`` (§11.2)."""

    failed_login_count_at_lock: int
    window_seconds: int


class LogoutPayload(TypedDict):
    """Payload for ``logout`` (§11.2). The ``jti`` is the UUID, not
    the JWT bytes."""

    jti: str


class RefreshTokenRotatedPayload(TypedDict):
    """Payload for ``refresh_token_rotated`` (§11.2)."""

    prev_jti: str
    new_jti: str


class RefreshTokenReuseDetectedPayload(TypedDict):
    """Payload for ``refresh_token_reuse_detected`` (§11.2)."""

    family_id: str


class DisplayNameChangedPayload(TypedDict):
    """Payload for ``display_name_changed`` (§11.2).

    Only the *lengths* of old and new display names are recorded --
    never the strings themselves (Requirement 11.4, defense in depth
    because display names are user-supplied free text).
    """

    previous_display_name_length: int
    new_display_name_length: int


class RateLimitRejectedPayload(TypedDict):
    """Payload for ``rate_limit_rejected`` (§11.2).

    Per Requirement 10.7 the raw key value is forbidden when
    ``category == "email"`` (would echo the submitted email and
    re-introduce account enumeration). Callers therefore record only
    the endpoint and category; the email itself is never in the
    payload.
    """

    endpoint: str
    category: Literal["ip", "email"]


# Convenience aliases for the literal sets so the overload list below
# stays readable.
_EmptyEvent = Literal[
    "registration_success",
    "registration_attempt_existing_email",
    "login_success",
    "password_reset_requested",
    "password_reset_confirmed",
    "account_deleted",
]


class Audit_Service:  # noqa: N801 -- design uses the underscored class name.
    """Insert one ``audit_events`` row in the caller's session.

    ``Audit_Service`` is intentionally stateless and tiny: a single
    ``emit`` method with no other public surface. The caller supplies
    the active SQLAlchemy session so the audit row participates in
    the same transaction as the mutation that produced it (Audit Log
    §11.3). There is no overload that opens its own connection -- if
    you want an audit row, you commit it through your transaction.
    """

    # ---- Empty-payload events ------------------------------------------------
    @overload
    async def emit(
        self,
        session: AsyncSession,
        *,
        event_type: _EmptyEvent,
        user_id: UUID | None = None,
        request: Request | None = None,
        payload: EmptyPayload | None = None,
    ) -> None: ...

    # ---- login_failure -------------------------------------------------------
    @overload
    async def emit(
        self,
        session: AsyncSession,
        *,
        event_type: Literal["login_failure"],
        user_id: UUID | None = None,
        request: Request | None = None,
        payload: LoginFailurePayload,
    ) -> None: ...

    # ---- account_locked ------------------------------------------------------
    @overload
    async def emit(
        self,
        session: AsyncSession,
        *,
        event_type: Literal["account_locked"],
        user_id: UUID | None = None,
        request: Request | None = None,
        payload: AccountLockedPayload,
    ) -> None: ...

    # ---- logout --------------------------------------------------------------
    @overload
    async def emit(
        self,
        session: AsyncSession,
        *,
        event_type: Literal["logout"],
        user_id: UUID | None = None,
        request: Request | None = None,
        payload: LogoutPayload,
    ) -> None: ...

    # ---- refresh_token_rotated -----------------------------------------------
    @overload
    async def emit(
        self,
        session: AsyncSession,
        *,
        event_type: Literal["refresh_token_rotated"],
        user_id: UUID | None = None,
        request: Request | None = None,
        payload: RefreshTokenRotatedPayload,
    ) -> None: ...

    # ---- refresh_token_reuse_detected ----------------------------------------
    @overload
    async def emit(
        self,
        session: AsyncSession,
        *,
        event_type: Literal["refresh_token_reuse_detected"],
        user_id: UUID | None = None,
        request: Request | None = None,
        payload: RefreshTokenReuseDetectedPayload,
    ) -> None: ...

    # ---- display_name_changed ------------------------------------------------
    @overload
    async def emit(
        self,
        session: AsyncSession,
        *,
        event_type: Literal["display_name_changed"],
        user_id: UUID | None = None,
        request: Request | None = None,
        payload: DisplayNameChangedPayload,
    ) -> None: ...

    # ---- rate_limit_rejected -------------------------------------------------
    @overload
    async def emit(
        self,
        session: AsyncSession,
        *,
        event_type: Literal["rate_limit_rejected"],
        user_id: UUID | None = None,
        request: Request | None = None,
        payload: RateLimitRejectedPayload,
    ) -> None: ...

    # ---- runtime implementation ----------------------------------------------
    async def emit(
        self,
        session: AsyncSession,
        *,
        event_type: str,
        user_id: UUID | None = None,
        request: Request | None = None,
        payload: Mapping[str, object] | None = None,
    ) -> None:
        """Insert one row into ``audit_events`` using *session*.

        The row is staged on the session via ``session.add`` so it
        commits with the surrounding transaction; this method does
        not call ``session.commit()`` itself (Audit Log §11.3). If
        the surrounding transaction rolls back, the audit row rolls
        back with it -- which is the desired behaviour per
        Requirement 15.4: an unaudited side effect is never an
        acceptable outcome.

        Args:
            session: The request-scoped :class:`AsyncSession` opened
                by the foundation ``get_session`` dependency. The
                audit row is staged on this session and committed by
                the dependency's exit hook, so even pre-mutation
                rejections (rate-limit reject, CSRF mismatch) flush
                the audit row before the response leaves the app
                (Audit Log §11.3).
            event_type: One of the strings enumerated in §11.2. The
                ``@overload`` chain above makes mypy reject any
                literal that isn't on the list, so a typo is a build
                failure rather than a runtime mystery.
            user_id: The principal the event belongs to, when known.
                Nullable on the column because some events (notably
                ``registration_attempt_existing_email`` and
                ``rate_limit_rejected``) precede a known principal
                (§4.4).
            request: The active FastAPI :class:`Request`, when one
                exists. ``ip_address`` is sourced from
                ``request.client.host`` and ``user_agent`` from the
                ``User-Agent`` header. Both columns are NULL when
                *request* is ``None`` (e.g., a background task or a
                test that doesn't go through the HTTP stack).
            payload: The ``TypedDict`` for the chosen *event_type*.
                ``None`` is permitted only for the events whose §11.2
                schema is ``{}`` and is normalised to an empty dict
                before insert.
        """
        ip_address: str | None = None
        user_agent: str | None = None
        if request is not None:
            ip_address = request.client.host if request.client is not None else None
            raw_user_agent = request.headers.get("user-agent")
            if raw_user_agent is not None:
                # Truncate to the §4.4 / Requirement 11.5 cap. Postgres
                # would happily store the full string (the column is
                # ``text``, not ``varchar(N)``), but a pathological UA
                # bloats the audit table without adding signal.
                user_agent = raw_user_agent[:_USER_AGENT_MAX_LENGTH]

        # Normalise payload to an empty dict for the §11.2 ``{}``-schema
        # events. We persist a JSON object, never JSON ``null``. The
        # ``@overload`` chain above guarantees the runtime shape of
        # *payload* matches the TypedDict for the chosen event_type;
        # ``dict(payload)`` produces a plain ``dict[str, object]`` that
        # SQLAlchemy's JSONB type can serialise directly.
        payload_to_store: dict[str, object] = {} if payload is None else dict(payload)

        row = AuditEvent(
            event_type=event_type,
            user_id=user_id,
            ip_address=ip_address,
            user_agent=user_agent,
            payload=payload_to_store,
        )
        session.add(row)
