"""``Auth_Service``: business logic for the authentication surface.

This is the ONLY module in the API that writes to ``users``,
``refresh_tokens``, and ``password_reset_tokens`` (Components and
Interfaces, import-boundary table). Every auth mutation goes through
here so the matching ``audit_events`` row commits in the same
transaction as the auth mutation itself (Audit Log §11.3, Requirement
15.4).

This sub-task (tasks.md 6.2) implements only ``register`` and
``authenticate``; ``rotate_refresh_token`` / ``logout`` /
``request_password_reset`` / ``confirm_password_reset`` /
``get_user_by_id`` / ``update_display_name`` land in tasks 6.3-6.5
and extend this same class.

Outcome model
-------------
Both public methods return a small frozen dataclass that the router
maps directly onto an HTTP envelope. Encoding the success/failure
shape as a discriminated union (rather than raising on every "this
isn't an error, just a different response" branch) keeps the timing
contract on ``authenticate`` honest: the dummy-hash unknown-email
branch and the known-email-wrong-password branch both return the
same outcome variant, and there is exactly one place in the router
that translates that variant to the 401 envelope.

Outcome variants:

* :class:`RegistrationOutcome` -- ``created`` (fresh User_Account +
  Token_Pair) or ``existing_email`` (enumeration-defense path; same
  response shape, no token).
* :class:`AuthenticateOutcome` -- ``success`` (Token_Pair issued) or
  ``invalid_credentials`` (HTTP 401, byte-for-byte identical for
  unknown email and wrong password) or ``locked`` (HTTP 423).

Design references
-----------------
* Components and Interfaces -- import-boundary rules and the
  ``Auth_Service`` dependency graph.
* Password Handling §8.3 -- the dummy-hash timing-defense contract
  on the unknown-email path of ``authenticate``.
* Password Handling §8.5 -- pre-NFKC length / post-NFKC blocklist
  ordering. The :mod:`core.security.passwords` module owns both
  rules; this service just chains them in the right order.
* Audit Log §11.2 -- the enumerated ``event_type`` payload schemas
  consumed by ``Audit_Service.emit``.
* Audit Log §11.3 -- same-session audit insert.
* Refresh-Token Rotation §7.1 -- happy-path ``family_id`` allocation
  on registration and login (one fresh family per fresh login).

Validates: Requirements 1.6, 1.7, 1.8, 2.2, 2.3, 2.5, 2.6, 2.7, 2.8,
2.9.
"""

from __future__ import annotations

import hashlib
import secrets
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final
from uuid import UUID

import structlog
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils.compat import uuid7

from matchlayer_api.config import Settings, get_settings
from matchlayer_api.core.security import jwt as jwt_service
from matchlayer_api.core.security.passwords import (
    DUMMY_HASH,
    PasswordTooShortError,
    hash_password,
    is_blocked,
    verify_password,
)
from matchlayer_api.db.models import PasswordResetToken, RefreshToken, User
from matchlayer_api.dev.reset_links import DEV_RESET_LINK_STORE, is_dev_environment
from matchlayer_api.services.audit import Audit_Service

_log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Public outcome dataclasses.
#
# ``frozen=True, slots=True``: the router only reads these and we want a
# typo on a result attribute to be a build failure. ``slots=True`` keeps
# them cheap to allocate -- one outcome is constructed per auth request.
# ---------------------------------------------------------------------------


class _RegistrationStatus:
    """Sentinel namespace for :class:`RegistrationOutcome` discriminator
    values.

    A class with class-attribute strings is intentional: it gives the
    discriminator a single dotted path (``_RegistrationStatus.CREATED``)
    that mypy can narrow on without the runtime cost of an
    :class:`enum.Enum` -- the auth path is on the latency-budget
    critical line per Requirement 2.11 / 15.1.
    """

    CREATED: Final[str] = "created"
    EXISTING_EMAIL: Final[str] = "existing_email"


@dataclass(frozen=True, slots=True)
class RegistrationOutcome:
    """The result of :meth:`Auth_Service.register`.

    The router translates this to one of two HTTP envelopes:

    * ``status == "created"`` -- HTTP 201 with the
      :class:`auth.schemas.TokenPairResponse` body, plus the refresh
      and CSRF cookies. ``user``, ``access_token``, and
      ``refresh_token`` are populated.
    * ``status == "existing_email"`` -- HTTP 200 with the same body
      shape (Requirement 1.6 -- enumeration defense). ``user`` carries
      the existing User_Account row so the response is shape-stable;
      ``access_token`` and ``refresh_token`` are ``None`` and the
      router intentionally omits the cookies on this branch.
    """

    status: str  # one of _RegistrationStatus.*
    user: User
    access_token: str | None
    refresh_token: str | None


class _AuthenticateStatus:
    """Sentinel namespace for :class:`AuthenticateOutcome` discriminator
    values."""

    SUCCESS: Final[str] = "success"
    INVALID_CREDENTIALS: Final[str] = "invalid_credentials"
    LOCKED: Final[str] = "locked"


@dataclass(frozen=True, slots=True)
class AuthenticateOutcome:
    """The result of :meth:`Auth_Service.authenticate`.

    Three variants the router maps directly to HTTP responses:

    * ``status == "success"`` -- HTTP 200 with
      :class:`auth.schemas.TokenPairResponse`, refresh + CSRF cookies
      set. ``user``, ``access_token``, ``refresh_token`` populated.
    * ``status == "invalid_credentials"`` -- HTTP 401 with the
      ``invalid_credentials`` envelope, ``detail`` literally "Email
      or password is incorrect." (Requirement 2.2, 2.3). The unknown-
      email and wrong-password paths both produce this variant -- the
      caller has no observable signal distinguishing them.
    * ``status == "locked"`` -- HTTP 423 with the ``account_locked``
      envelope (Requirement 2.8).
    """

    status: str  # one of _AuthenticateStatus.*
    user: User | None
    access_token: str | None
    refresh_token: str | None


class _RefreshStatus:
    ROTATED: Final[str] = "rotated"
    INVALID: Final[str] = "invalid"
    REUSED: Final[str] = "reused"


@dataclass(frozen=True, slots=True)
class RefreshOutcome:
    """The result of :meth:`Auth_Service.rotate_refresh_token`."""

    status: str  # one of _RefreshStatus.*
    access_token: str | None = None
    refresh_token: str | None = None


# ---------------------------------------------------------------------------
# Small helpers.
# ---------------------------------------------------------------------------


def _now() -> datetime:
    """Return a timezone-aware "now".

    Centralised so the same UTC clock is used by the lockout window
    arithmetic, the ``last_failed_login_at`` stamp, and the
    ``refresh_tokens.expires_at`` computation. Tests that need to
    freeze time monkey-patch this function via the
    ``services.auth._now`` attribute.
    """
    return datetime.now(UTC)


def _default_display_name_from_email(email: str) -> str:
    """Return the local part of *email* for the display-name default
    (Requirement 1.7).

    "Local part" is everything before the first ``@``. Pydantic's
    ``EmailStr`` has already validated RFC-5321 compliance at the
    schema layer, so a missing ``@`` here would be a contract bug
    upstream rather than user input -- we still defensively fall
    back to the full string in that case so the column stays
    non-null.
    """
    local_part, _, _ = email.partition("@")
    return local_part or email


# ---------------------------------------------------------------------------
# Auth_Service.
# ---------------------------------------------------------------------------


class Auth_Service:  # noqa: N801 -- design uses the underscored class name.
    """Business logic for authentication mutations.

    Stateless and dependency-injected: every mutation takes the active
    request-scoped :class:`AsyncSession` so the audit row commits in
    the same transaction as the auth mutation that produced it
    (Audit Log §11.3). The service holds references to the audit
    service and to the active :class:`Settings` for window/threshold
    arithmetic; no ORM session is cached on the instance.

    A single instance is constructed per request by the router via
    a FastAPI dependency. Construction is cheap -- two attribute
    assignments -- so per-request allocation is fine; tests can also
    construct an instance inline.
    """

    __slots__ = ("_audit", "_settings")

    def __init__(
        self,
        *,
        audit: Audit_Service | None = None,
        settings: Settings | None = None,
    ) -> None:
        # The default audit service is fine for production use --
        # ``Audit_Service`` is stateless. Tests that want to assert on
        # emitted events can pass a stub that records to a list.
        self._audit = audit if audit is not None else Audit_Service()
        self._settings = settings if settings is not None else get_settings()

    # ------------------------------------------------------------------
    # Registration (Requirement 1.6, 1.7, 1.8).
    # ------------------------------------------------------------------

    async def register(
        self,
        session: AsyncSession,
        *,
        email: str,
        password: str,
        display_name: str | None,
    ) -> RegistrationOutcome:
        """Register a new User_Account or take the enumeration-defense
        path on an existing email.

        Validation contract:

        * Pydantic at the router edge has already enforced RFC-5321
          email shape and the 12-codepoint password floor at the
          *schema* level (Requirements 1.3, 1.4). This method
          re-runs the floor against the raw plaintext (defense in
          depth -- a future router refactor that stops using the
          strict schema must not silently drop the policy) and
          additionally enforces the Password_Blocklist gate
          (Requirement 1.5).
        * The blocklist check raises :class:`ValueError`. The router
          maps it to HTTP 422 with the literal ``detail`` "Password
          is in the common-password blocklist." -- the submitted
          value is never echoed (Requirement 1.5).

        On the existing-email path (Requirement 1.6) this method
        returns a ``status="existing_email"`` outcome carrying the
        existing User_Account row. The router maps that to HTTP 200
        with the same body shape it returns on the success path but
        does NOT issue tokens. The audit row is
        ``registration_attempt_existing_email`` -- never
        ``registration_success`` -- so post-incident review can
        always distinguish the two cases.

        Args:
            session: Active request-scoped session. The new User row,
                the new RefreshToken row, and the audit row are all
                staged on this session; the FastAPI dependency exit
                hook commits the transaction.
            email: Submitted email. Stored as supplied; lookups go
                through the case-insensitive functional index.
            password: Submitted plaintext. Validated, NFKC-normalised,
                and Argon2id-hashed downstream.
            display_name: Optional display name. When ``None`` the
                local part of *email* is used (Requirement 1.7).
                When supplied it has already been validated by the
                schema layer (Requirement 6.6) and is stored as
                received.

        Returns:
            RegistrationOutcome: see class docstring.

        Raises:
            PasswordTooShortError: When *password* has fewer than
                :data:`MIN_PASSWORD_LENGTH` codepoints. The router
                maps to HTTP 422.
            ValueError: When *password* is in the
                Password_Blocklist. The router maps to HTTP 422 with
                the generic "common password" ``detail`` (Requirement
                1.5).
        """
        # 1. Enforce blocklist BEFORE consulting the database. Both
        #    ``hash_password`` and the existing-email lookup are
        #    Argon2id-cost or DB roundtrips; we don't want to pay
        #    either on a request the policy will reject anyway.
        #    The Password_Hasher's ``is_blocked`` does the NFKC +
        #    casefold for us (§8.4). The pre-NFKC length floor is
        #    enforced inside ``hash_password`` per §8.5.
        if is_blocked(password):
            # Generic message; never echoes the submitted value
            # (Requirement 1.5).
            raise ValueError("Password is in the common-password blocklist.")

        # 2. Existing-email check. The functional unique index
        #    ``users_email_lower_uniq`` makes ``lower(email) =
        #    lower(:email)`` the lookup path (Data Models §4.1).
        existing = await session.execute(
            select(User).where(func.lower(User.email) == email.lower())
        )
        existing_user = existing.scalar_one_or_none()
        if existing_user is not None:
            # Enumeration defense (Requirement 1.6). Audit the attempt
            # so post-incident review can distinguish a fresh
            # registration from a probe; the audit row references the
            # *existing* user_id so the timeline of probes against the
            # same account lines up cleanly.
            await self._audit.emit(
                session,
                event_type="registration_attempt_existing_email",
                user_id=existing_user.id,
            )
            return RegistrationOutcome(
                status=_RegistrationStatus.EXISTING_EMAIL,
                user=existing_user,
                access_token=None,
                refresh_token=None,
            )

        # 3. Hash the password. ``hash_password`` enforces the
        #    pre-NFKC ≥12-codepoint floor (§8.5) and returns a PHC
        #    string suitable for direct storage. ``PasswordTooShortError``
        #    bubbles to the router unchanged -- the schema layer
        #    enforces the same floor, so reaching this branch means
        #    the schema was bypassed (defense in depth).
        password_hash = hash_password(password)

        # 4. Build and persist the User row. The display-name default
        #    (Requirement 1.7) is the local part of the email when
        #    the caller omitted one.
        resolved_display_name = (
            display_name if display_name is not None else _default_display_name_from_email(email)
        )
        new_user_id = uuid7()
        now = _now()
        user = User(
            id=new_user_id,
            email=email,
            password_hash=password_hash,
            display_name=resolved_display_name,
            failed_login_count=0,
            last_failed_login_at=None,
            locked_until=None,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        session.add(user)
        # Flush so the FK on ``refresh_tokens.user_id`` resolves
        # against a row Postgres can see within this transaction. We
        # do not commit here -- the FastAPI dependency exit hook
        # owns commit (Audit Log §11.3).
        await session.flush()

        # 5. Issue a fresh Token_Pair. A *registration* is the start
        #    of a new login lineage so it allocates a brand-new
        #    ``family_id`` (Refresh-Token Rotation §7.1, Requirement
        #    8.2).
        access_token, refresh_token, _ = self._issue_token_pair_for_user(
            session=session, user_id=new_user_id, family_id=uuid7()
        )

        # 6. Audit the success. Empty payload by §11.2 -- the user_id
        #    column is the only field needed.
        await self._audit.emit(
            session,
            event_type="registration_success",
            user_id=new_user_id,
        )

        return RegistrationOutcome(
            status=_RegistrationStatus.CREATED,
            user=user,
            access_token=access_token,
            refresh_token=refresh_token,
        )

    # ------------------------------------------------------------------
    # Login (Requirements 2.2, 2.3, 2.5, 2.6, 2.7, 2.8, 2.9).
    # ------------------------------------------------------------------

    async def authenticate(
        self,
        session: AsyncSession,
        *,
        email: str,
        password: str,
    ) -> AuthenticateOutcome:
        """Verify a credential pair and return the appropriate outcome.

        Branches in the order Requirements 2.2-2.9 prescribe:

        1. Look up the User_Account by case-insensitive email.
        2. **Unknown email** (Requirement 2.2): call
           ``verify_password(DUMMY_HASH, password)`` so the request
           pays the same Argon2id cost the known-email branch pays
           (§8.3). Discard the result. Audit ``login_failure`` with
           the lower-cased submitted email (Requirement 2.7) and a
           ``user_id`` of ``None`` since no principal is known.
           Return ``invalid_credentials``.
        3. **Locked account** (Requirement 2.8): when
           ``locked_until`` is in the future, return ``locked``
           *without* incrementing ``failed_login_count``. No audit
           row is emitted on this branch -- the lockout itself was
           audited when it was set (Requirement 2.9), and a request
           against an already-locked account is not a new event.
        4. **Wrong password** (Requirement 2.3): increment
           ``failed_login_count`` and stamp ``last_failed_login_at``;
           when the rolling window contains
           ``auth_lockout_threshold`` failures, set ``locked_until``
           and reset the counter (Requirement 2.9), and emit
           ``account_locked``. Always emit ``login_failure``. Return
           ``invalid_credentials`` -- the envelope must be byte-for-
           byte identical to the unknown-email branch.
        5. **Success** (Requirement 2.5, 2.6): reset
           ``failed_login_count`` to 0, clear ``locked_until``,
           rehash transparently when the stored hash is below current
           policy (§8.2), allocate a fresh ``family_id``, issue a
           Token_Pair, audit ``login_success``, and return
           ``success``.

        Args:
            session: Active request-scoped session. All mutations
                (counter bumps, lockout, refresh-token row, audit
                rows) are staged on this session.
            email: Submitted email. Lookups via
                ``func.lower(User.email) == email.lower()``.
            password: Submitted plaintext. Verified against the
                stored PHC hash (or the dummy hash on the unknown-
                email branch).

        Returns:
            AuthenticateOutcome: see class docstring.
        """
        # 1. Case-insensitive lookup. The functional unique index
        #    ``users_email_lower_uniq`` (Data Models §4.1) makes this
        #    an index seek, not a sequential scan.
        result = await session.execute(
            select(User).where(
                func.lower(User.email) == email.lower(),
                User.deleted_at.is_(None),
            )
        )
        user = result.scalar_one_or_none()

        if user is None:
            # 2. Unknown email -- timing-defense path (§8.3).
            #    ``verify_password`` always runs the Argon2id verify
            #    against the precomputed DUMMY_HASH and discards the
            #    result. A short submitted password does NOT short-
            #    circuit (verify_password has no length floor) so the
            #    request still pays the full hash-verify cost.
            verify_password(DUMMY_HASH, password)
            await self._audit.emit(
                session,
                event_type="login_failure",
                user_id=None,
                payload={"submitted_email": email.lower()},
            )
            return AuthenticateOutcome(
                status=_AuthenticateStatus.INVALID_CREDENTIALS,
                user=None,
                access_token=None,
                refresh_token=None,
            )

        now = _now()

        # 3. Locked account -- 423 without incrementing the counter
        #    (Requirement 2.8).
        if user.locked_until is not None and user.locked_until > now:
            return AuthenticateOutcome(
                status=_AuthenticateStatus.LOCKED,
                user=user,
                access_token=None,
                refresh_token=None,
            )

        # 4. Verify the supplied password against the stored hash.
        matches, needs_rehash = verify_password(user.password_hash, password)

        if not matches:
            await self._record_failed_login(session=session, user=user, now=now)
            await self._audit.emit(
                session,
                event_type="login_failure",
                user_id=user.id,
                payload={"submitted_email": email.lower()},
            )
            return AuthenticateOutcome(
                status=_AuthenticateStatus.INVALID_CREDENTIALS,
                user=None,
                access_token=None,
                refresh_token=None,
            )

        # 5. Success path. Reset counters, transparently rehash if the
        #    stored Argon2id parameters drifted below current policy
        #    (§8.2 -- this is purely a maintenance side effect; the
        #    user notices nothing).
        if needs_rehash:
            user.password_hash = hash_password(password)
        user.failed_login_count = 0
        user.last_failed_login_at = None
        user.locked_until = None
        user.updated_at = now

        # Each fresh login starts a brand-new refresh-token family.
        # Rotations within this login keep the same family; logging in
        # again allocates a new family so a compromise of an old
        # family doesn't bleed into the new session (§7.1, Requirement
        # 8.2).
        access_token, refresh_token, _ = self._issue_token_pair_for_user(
            session=session, user_id=user.id, family_id=uuid7()
        )

        await self._audit.emit(
            session,
            event_type="login_success",
            user_id=user.id,
        )

        return AuthenticateOutcome(
            status=_AuthenticateStatus.SUCCESS,
            user=user,
            access_token=access_token,
            refresh_token=refresh_token,
        )

    # ------------------------------------------------------------------
    # Internal helpers (private; reused by 6.3-6.5 sub-tasks).
    # ------------------------------------------------------------------

    def _issue_token_pair_for_user(
        self,
        *,
        session: AsyncSession,
        user_id: UUID,
        family_id: UUID,
    ) -> tuple[str, str, UUID]:
        """Allocate a refresh-token row and issue an access+refresh JWT
        pair against it.

        Used by both ``register`` and ``authenticate`` -- and, when
        sub-task 6.3 lands, by ``rotate_refresh_token`` (passing the
        *predecessor's* ``family_id`` to preserve lineage per
        Requirement 8.3). Keeping the row insert and the JWT issuance
        next to each other guarantees the row exists before the JWT
        leaves this process: a refresh attempt against a JWT whose
        ``jti`` row hasn't been committed simply maps to "row missing
        → invalid_refresh_token" (Requirement 3.6) rather than a
        timing race.

        The new row's ``jti`` is the same UUIDv7 stamped onto the
        JWT's ``jti`` claim. Per Requirement 8.6 the JWT bytes
        themselves are never persisted; this column is the sole
        linkage.
        """
        now = _now()
        new_jti = uuid7()
        session.add(
            RefreshToken(
                jti=new_jti,
                family_id=family_id,
                user_id=user_id,
                issued_at=now,
                expires_at=now + timedelta(seconds=self._settings.auth_refresh_token_ttl_seconds),
                revoked_at=None,
            )
        )
        access_token = jwt_service.issue_access_token(sub=str(user_id))
        refresh_token = jwt_service.issue_refresh_token(sub=str(user_id), jti=new_jti)
        return access_token, refresh_token, new_jti

    async def _record_failed_login(
        self,
        *,
        session: AsyncSession,
        user: User,
        now: datetime,
    ) -> None:
        """Bump the failed-login counter and trigger lockout when the
        threshold is hit within the rolling window (Requirement 2.9).

        Lockout policy uses the per-user ``last_failed_login_at``
        column (Data Models §4.1) to evaluate the rolling window
        without an auxiliary table:

        * If the previous failure happened *outside* the window, the
          counter is reset to 1 before the comparison -- this failure
          is the start of a fresh window. Without this reset a long
          stretch of sporadic failures would slowly accumulate to the
          threshold and lock an account that was never under attack.
        * If the failure happens *inside* the window, the counter
          increments and is compared to the threshold.

        On threshold-reach the account is locked for
        ``auth_lockout_duration_seconds`` and the counter is reset to
        0 (Requirement 2.9). The reset means the lockout itself is
        the gate -- a new failure burst after the lockout expires
        starts from a clean window rather than tipping straight into
        a second lockout.
        """
        threshold = self._settings.auth_lockout_threshold
        window_seconds = self._settings.auth_lockout_window_seconds
        duration_seconds = self._settings.auth_lockout_duration_seconds

        within_window = user.last_failed_login_at is not None and (
            now - user.last_failed_login_at
        ) <= timedelta(seconds=window_seconds)
        new_count = user.failed_login_count + 1 if within_window else 1

        if new_count >= threshold:
            # Lockout. Reset counter and stamp ``locked_until`` per
            # Requirement 2.9. The matching ``account_locked`` audit
            # row carries the count at lock-time and the window so a
            # post-incident reviewer can reproduce the policy that
            # was in force without consulting the deploy history.
            user.failed_login_count = 0
            user.last_failed_login_at = now
            user.locked_until = now + timedelta(seconds=duration_seconds)
            user.updated_at = now
            await self._audit.emit(
                session,
                event_type="account_locked",
                user_id=user.id,
                payload={
                    "failed_login_count_at_lock": threshold,
                    "window_seconds": window_seconds,
                },
            )
            return

        user.failed_login_count = new_count
        user.last_failed_login_at = now
        user.updated_at = now

    # ------------------------------------------------------------------
    # Refresh-token rotation (task 6.3, Requirements 3.7-3.10, 8.2-8.4).
    # ------------------------------------------------------------------

    async def rotate_refresh_token(
        self,
        session: AsyncSession,
        *,
        presented_jti: UUID,
        user_id: UUID,
    ) -> RefreshOutcome:
        """Rotate a refresh token or detect reuse per §7.3."""
        result = await session.execute(
            select(RefreshToken).where(RefreshToken.jti == presented_jti).with_for_update()
        )
        row = result.scalar_one_or_none()

        if row is None:
            return RefreshOutcome(status=_RefreshStatus.INVALID)

        now = _now()

        if row.user_id != user_id or row.expires_at < now:
            return RefreshOutcome(status=_RefreshStatus.INVALID)

        if row.revoked_at is not None:
            # Reuse detected — revoke every non-revoked sibling in the family.
            await session.execute(
                update(RefreshToken)
                .where(
                    RefreshToken.family_id == row.family_id,
                    RefreshToken.revoked_at.is_(None),
                )
                .values(revoked_at=now)
            )
            await self._audit.emit(
                session,
                event_type="refresh_token_reuse_detected",
                user_id=row.user_id,
                payload={"family_id": str(row.family_id)},
            )
            return RefreshOutcome(status=_RefreshStatus.REUSED)

        # Happy path: revoke predecessor, issue successor in same family.
        row.revoked_at = now
        access_token, refresh_token, new_jti = self._issue_token_pair_for_user(
            session=session, user_id=row.user_id, family_id=row.family_id
        )
        await self._audit.emit(
            session,
            event_type="refresh_token_rotated",
            user_id=row.user_id,
            payload={"prev_jti": str(presented_jti), "new_jti": str(new_jti)},
        )
        return RefreshOutcome(
            status=_RefreshStatus.ROTATED,
            access_token=access_token,
            refresh_token=refresh_token,
        )

    # ------------------------------------------------------------------
    # Logout (task 6.3, Requirements 4.5, 4.6).
    # ------------------------------------------------------------------

    async def logout(
        self,
        session: AsyncSession,
        *,
        presented_jti: UUID,
    ) -> None:
        """Revoke exactly one refresh token row. Idempotent."""
        result = await session.execute(
            select(RefreshToken).where(RefreshToken.jti == presented_jti).with_for_update()
        )
        row = result.scalar_one_or_none()

        if row is None:
            return

        # Idempotent: already-revoked row → no duplicate audit (Req 4.6).
        if row.revoked_at is not None:
            return

        row.revoked_at = _now()
        await self._audit.emit(
            session,
            event_type="logout",
            user_id=row.user_id,
            payload={"jti": str(presented_jti)},
        )

    # ------------------------------------------------------------------
    # Password reset (task 6.4, Requirements 5.2-5.10).
    # ------------------------------------------------------------------

    async def request_password_reset(
        self,
        session: AsyncSession,
        *,
        email: str,
    ) -> None:
        """Generate a reset token or silently succeed on unknown email."""
        result = await session.execute(
            select(User).where(
                func.lower(User.email) == email.lower(),
                User.deleted_at.is_(None),
            )
        )
        user = result.scalar_one_or_none()

        if user is None:
            return  # Requirement 5.2 — silent success

        plaintext_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(plaintext_token.encode("utf-8")).digest()

        now = _now()
        session.add(
            PasswordResetToken(
                user_id=user.id,
                token_hash=token_hash,
                expires_at=now + timedelta(hours=1),
                used_at=None,
            )
        )

        await self._audit.emit(
            session,
            event_type="password_reset_requested",
            user_id=user.id,
        )

        if is_dev_environment(self._settings):
            link = f"{self._settings.web_base_url}/reset-password?token={plaintext_token}"
            _log.info(
                "password_reset_link_generated",
                password_reset_link=link,
                user_id=str(user.id),
            )
            DEV_RESET_LINK_STORE.record(link)

    async def confirm_password_reset(
        self,
        session: AsyncSession,
        *,
        token: str,
        new_password: str,
    ) -> bool:
        """Confirm a password reset. Returns True on success, False on invalid token."""
        token_hash = hashlib.sha256(token.encode("utf-8")).digest()

        result = await session.execute(
            select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash)
        )
        row = result.scalar_one_or_none()

        now = _now()

        # Reject missing, expired, or already-used tokens (Reqs 5.6-5.8).
        if row is None or row.expires_at < now or row.used_at is not None:
            return False

        # Validate new password.
        if is_blocked(new_password):
            raise ValueError("Password is in the common-password blocklist.")

        new_hash = hash_password(new_password)

        # Update user's password.
        user_result = await session.execute(select(User).where(User.id == row.user_id))
        user = user_result.scalar_one()
        user.password_hash = new_hash
        user.updated_at = now

        # Mark token as used.
        row.used_at = now

        # Revoke all refresh tokens for this user (Requirement 8.5).
        await session.execute(
            update(RefreshToken)
            .where(
                RefreshToken.user_id == row.user_id,
                RefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )

        await self._audit.emit(
            session,
            event_type="password_reset_confirmed",
            user_id=row.user_id,
        )
        return True

    # ------------------------------------------------------------------
    # User profile (task 6.5, Requirements 6.4, 6.6-6.8).
    # ------------------------------------------------------------------

    async def get_user_by_id(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
    ) -> User | None:
        """Return the User row when not soft-deleted, else None."""
        result = await session.execute(
            select(User).where(User.id == user_id, User.deleted_at.is_(None))
        )
        return result.scalar_one_or_none()

    async def update_display_name(
        self,
        session: AsyncSession,
        *,
        user: User,
        new_display_name: str,
    ) -> User:
        """Validate and update display name. Raises ValueError on invalid input."""
        stripped = new_display_name.strip()
        if not stripped or len(stripped) > 64:
            raise ValueError("Display name must be 1-64 characters after trimming.")

        # Validate Unicode categories: L, M, N, Pd, Pc, Zs (Requirement 6.6).
        for ch in stripped:
            cat = unicodedata.category(ch)
            if cat[:1] not in ("L", "M", "N") and cat not in ("Pd", "Pc", "Zs"):
                raise ValueError(
                    f"Display name contains disallowed character U+{ord(ch):04X} ({cat})."
                )

        prev_len = len(user.display_name)
        user.display_name = stripped
        user.updated_at = _now()

        await self._audit.emit(
            session,
            event_type="display_name_changed",
            user_id=user.id,
            payload={
                "previous_display_name_length": prev_len,
                "new_display_name_length": len(stripped),
            },
        )
        return user


__all__ = [
    "Auth_Service",
    "AuthenticateOutcome",
    "PasswordTooShortError",
    "RefreshOutcome",
    "RegistrationOutcome",
]
