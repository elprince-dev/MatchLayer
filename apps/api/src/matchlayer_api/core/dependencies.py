"""FastAPI dependencies for auth, CSRF, and rate limiting.

This module exposes the three cross-cutting dependencies every auth
endpoint composes against:

* :func:`get_current_user` — Bearer-token → :class:`User` resolution
  for `Authorization: Bearer <jwt>` requests.
* :func:`csrf_required` — double-submit-cookie CSRF check for the
  cookie-authenticated surface (`/auth/refresh`, `/auth/logout`).
* :func:`rate_limit` — a dependency factory that runs one or more
  rate-limit categories per request and emits the
  ``rate_limit_rejected`` audit row on rejection.

The dependencies map their failure modes onto domain exceptions
(:class:`UnauthenticatedError`, :class:`CsrfMismatchError`,
:class:`RateLimited`, :class:`RateLimiterUnavailableError`). The
foundation error-handling layer (`core/errors.py`) is responsible for
translating each exception into the RFC 7807 envelope shape
prescribed by Error Handling §15. Setting the ``Retry-After`` header on
the 429 response is the dependency's responsibility (Requirement 10.7);
the foundation handler does not own the rate-limit-specific header.

Design reference: Components and Interfaces, CSRF Strategy §9.3,
Rate Limiting §10.5, Audit Log §11.3.
Requirements: 6.2, 6.4, 9.3, 9.4, 10.5, 10.7.
"""

from __future__ import annotations

import json
import secrets
from collections.abc import AsyncIterator, Callable, Coroutine
from dataclasses import dataclass
from typing import Annotated, Any, Final, Literal
from uuid import UUID

import redis.asyncio as aioredis
import structlog
from fastapi import Depends, Request, Response
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from matchlayer_api.config import Settings, get_settings
from matchlayer_api.core.db import get_session
from matchlayer_api.core.rate_limit import RateLimitDecision, RateLimiter, get_rate_limiter
from matchlayer_api.core.security.jwt import InvalidTokenError, verify_token
from matchlayer_api.db.models import AuditEvent, User

_log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Reusable Annotated dependency aliases. Defining the ``Depends(...)`` call
# at module scope (rather than as a function-default) avoids ruff's B008
# warning ("Do not perform function call ``Depends`` in argument defaults")
# while keeping the FastAPI dependency-injection contract identical.
# ---------------------------------------------------------------------------
_SessionDep = Annotated[AsyncSession, Depends(get_session)]
_SettingsDep = Annotated[Settings, Depends(get_settings)]
_RateLimiterDep = Annotated[RateLimiter, Depends(get_rate_limiter)]


# ---------------------------------------------------------------------------
# Domain exceptions — mapped to RFC 7807 envelopes by the foundation
# ``core/errors.py`` handler. These types intentionally carry no ``detail``
# strings: the user-facing copy lives in the handler so the same envelope
# shape is produced regardless of which dependency raised.
# ---------------------------------------------------------------------------


class UnauthenticatedError(Exception):
    """HTTP 401 — missing, invalid, expired, or wrong-type access token, or
    the resolved User_Account has ``deleted_at`` set (Requirement 6.4)."""


class CsrfMismatchError(Exception):
    """HTTP 403 — ``matchlayer_csrf`` cookie value does not match the
    ``X-CSRF-Token`` request header value (Requirement 9.3)."""


class RateLimited(Exception):  # noqa: N818  # design-mandated name (no Error suffix)
    """HTTP 429 — request rejected by the sliding-window rate limiter.

    Carries the rejecting endpoint, key category, and the ``Retry-After``
    seconds value so the dependency can set the response header before
    the exception bubbles to the foundation error handler (Requirement
    10.7).
    """

    def __init__(self, *, endpoint: str, category: str, retry_after_seconds: int) -> None:
        super().__init__(f"rate limited on {endpoint} by {category}")
        self.endpoint = endpoint
        self.category = category
        self.retry_after_seconds = retry_after_seconds


class RateLimiterUnavailableError(Exception):
    """HTTP 503 — Redis unreachable; rate limiter fails closed per
    Requirement 10.9 / Rate Limiting §10.4."""

    def __init__(self, *, retry_after_seconds: int) -> None:
        super().__init__("rate limiter unavailable")
        self.retry_after_seconds = retry_after_seconds


# ---------------------------------------------------------------------------
# get_current_user — Bearer token → User row
# ---------------------------------------------------------------------------


async def get_current_user(
    request: Request,
    session: _SessionDep,
) -> User:
    """Resolve the User_Account for an incoming Bearer-authenticated request.

    Implements Requirements 6.2 and 6.4: the request must carry an
    ``Authorization: Bearer <jwt>`` header whose token verifies against
    the JWT_Service with ``expected_type="access"`` and whose ``sub``
    claim resolves to a non-soft-deleted User_Account row.

    Raises :class:`UnauthenticatedError` for every failure path so the
    foundation error handler emits the single
    ``type="unauthenticated"`` envelope with a 401 status (per
    Requirement 6.2 the response is identical for missing / wrong-scheme
    / invalid-signature / wrong-type / expired tokens).
    """
    auth_header = request.headers.get("Authorization", "")
    # ``Bearer `` (case-sensitive) is the only accepted scheme. The
    # 7-character literal prefix is from RFC 6750 §2.1 — anything else
    # (missing header, ``Basic ...``, ``bearer ...`` lower-cased, or a
    # bare token with no scheme) is rejected as unauthenticated.
    if not auth_header.startswith("Bearer "):
        raise UnauthenticatedError()

    token = auth_header[len("Bearer ") :].strip()
    if not token:
        raise UnauthenticatedError()

    try:
        claims = verify_token(token, expected_type="access")
    except InvalidTokenError as exc:
        # Wrong signature, wrong alg, expired, wrong ``type`` claim, or
        # any other PyJWT-level failure all collapse to one 401.
        raise UnauthenticatedError() from exc

    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub:
        raise UnauthenticatedError()

    try:
        user_id = UUID(sub)
    except ValueError as exc:
        # A token whose ``sub`` is syntactically not a UUID cannot match
        # any user row; treat as unauthenticated rather than letting the
        # ``select`` raise a database-driver error downstream.
        raise UnauthenticatedError() from exc

    result = await session.execute(
        select(User).where(User.id == user_id, User.deleted_at.is_(None))
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise UnauthenticatedError()
    return user


# ---------------------------------------------------------------------------
# csrf_required — double-submit-cookie CSRF check
# ---------------------------------------------------------------------------


async def csrf_required(request: Request) -> None:
    """Enforce the double-submit CSRF check on cookie-authenticated routes.

    Per CSRF Strategy §9.3 and the Requirement 9.4 carve-out spelled out
    in the requirements analyzer (see ``requirements.md`` Appendix A):

    * No ``matchlayer_refresh`` cookie → no cookie-derived authority on
      the request, so the CSRF check is N/A. Return without raising;
      the calling router decides what the missing-cookie outcome is
      (401 ``missing_refresh_cookie`` for ``/refresh``, 204 for
      ``/logout``).
    * ``matchlayer_refresh`` cookie present → both the
      ``matchlayer_csrf`` cookie and the ``X-CSRF-Token`` header MUST
      be present and equal under :func:`secrets.compare_digest` (the
      constant-time compare defends against per-byte timing oracles
      on the token).
    """
    refresh_cookie = request.cookies.get("matchlayer_refresh")
    if not refresh_cookie:
        # Requirement 9.4 anchor: no cookie authority → no CSRF check.
        return

    cookie_csrf = request.cookies.get("matchlayer_csrf")
    header_csrf = request.headers.get("X-CSRF-Token")

    if not cookie_csrf or not header_csrf:
        raise CsrfMismatchError()

    if not secrets.compare_digest(cookie_csrf, header_csrf):
        raise CsrfMismatchError()


# ---------------------------------------------------------------------------
# rate_limit — dependency factory
# ---------------------------------------------------------------------------


_KeyCategory = Literal["ip", "email"]


def rate_limit(
    *, endpoint: str, by: tuple[_KeyCategory, ...]
) -> Callable[..., Coroutine[Any, Any, None]]:
    """Build a FastAPI dependency that enforces sliding-window rate limits.

    Parameters
    ----------
    endpoint:
        One of ``"register"``, ``"login"``, ``"refresh"``,
        ``"password_reset_request"``, ``"password_reset_confirm"``.
        Used both to look up the per-endpoint policy (Rate Limiting
        §10.3) and as the audit-row ``payload.endpoint`` value
        (Audit Log §11.2).
    by:
        Ordered tuple of key categories to check, e.g. ``("email",
        "ip")`` for ``/login``. Each category is checked independently;
        the first rejection wins and short-circuits the remaining
        categories (the rejecting category is the one recorded in the
        audit row).

    The returned dependency:

    * Reads the per-endpoint and per-category limit and window from the
      :class:`Settings` object (Requirement 10.8).
    * Calls :meth:`RateLimiter.check` for each category in ``by``.
    * On a Redis-unavailable decision (``redis_unavailable=True``)
      raises :class:`RateLimiterUnavailableError`; the foundation error
      handler maps this to HTTP 503 ``rate_limiter_unavailable``
      (Requirement 10.9).
    * On a normal rejection sets the ``Retry-After`` response header,
      emits a ``rate_limit_rejected`` audit row in the request's
      session (Requirement 10.7), and raises :class:`RateLimited`. The
      audit ``payload`` carries ``{"endpoint", "category"}`` only —
      never the raw email value when the category is ``"email"``
      (Requirement 10.7, Audit Log §11.2).
    * On allow returns ``None`` so the request proceeds to the route
      handler.
    """

    async def _dependency(
        request: Request,
        response: Response,
        settings: _SettingsDep,
        session: _SessionDep,
        rl: _RateLimiterDep,
    ) -> None:
        for category in by:
            key_value = _resolve_key_value(category, request)
            redis_key = _build_redis_key(endpoint=endpoint, category=category, value=key_value)
            limit, window_seconds = _resolve_policy(
                endpoint=endpoint, category=category, settings=settings
            )

            decision: RateLimitDecision = await rl.check(
                redis_key, limit=limit, window_seconds=window_seconds
            )

            if decision.allowed:
                continue

            # Set Retry-After on every non-allowed decision (both 429 and
            # 503 responses carry it per Requirement 10.7 / 10.9).
            response.headers["Retry-After"] = str(decision.retry_after_seconds)

            if decision.redis_unavailable:
                # Fail-closed Redis outage. Don't try to write an audit
                # row — the database connection may also be impaired,
                # and Requirement 10.9 mandates a 503 envelope rather
                # than the 429 ``rate_limited`` shape.
                raise RateLimiterUnavailableError(retry_after_seconds=decision.retry_after_seconds)

            await _emit_rate_limit_rejected(
                session=session, request=request, endpoint=endpoint, category=category
            )

            raise RateLimited(
                endpoint=endpoint,
                category=category,
                retry_after_seconds=decision.retry_after_seconds,
            )

    return _dependency


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve_key_value(category: _KeyCategory, request: Request) -> str:
    """Return the raw key value for a given category from the request."""
    if category == "ip":
        # ``request.client`` is ``None`` in some ASGI test transports.
        # Falling back to a literal sentinel keeps the rate limiter
        # functional in tests; production traffic always carries a
        # client tuple after the foundation request middleware runs.
        return request.client.host if request.client else "unknown"

    # ``category == "email"`` — the router parses the JSON body and
    # stashes the lower-cased email on ``request.state`` before this
    # dependency runs (Rate Limiting §10.5). If the router has not done
    # so, fall back to a sentinel: a missing email key still produces
    # *some* limit per IP-less request, which is the safer default
    # while the auth router is wired up in later tasks.
    email = getattr(request.state, "rate_limit_email", None)
    if not isinstance(email, str) or not email:
        return "unknown"
    return email.lower()


def _build_redis_key(*, endpoint: str, category: _KeyCategory, value: str) -> str:
    """Return the Redis key for the rate-limiter ZSET (Rate Limiting §10.2)."""
    return f"rl:auth:{endpoint}:{category}:{value}"


def _resolve_policy(
    *, endpoint: str, category: _KeyCategory, settings: Settings
) -> tuple[int, int]:
    """Return ``(limit, window_seconds)`` for an (endpoint, category) pair.

    Sourced from the per-endpoint settings fields enumerated in
    ``config.py``. Defaults match Rate Limiting §10.3.
    """
    if category == "ip":
        return _IP_POLICY[endpoint](settings)
    # category == "email"
    return _EMAIL_POLICY[endpoint](settings)


# Per-endpoint policy lookups. Using closures over ``settings`` keeps
# the mapping declarative — adding a new endpoint is one line in each
# table — and avoids the ``getattr(settings, dynamic_name)`` shape that
# defeats mypy's ``--strict`` checks.
_IP_POLICY: dict[str, Callable[[Settings], tuple[int, int]]] = {
    "register": lambda s: (
        s.auth_rate_limit_register_ip_limit,
        s.auth_rate_limit_register_ip_window_seconds,
    ),
    "login": lambda s: (
        s.auth_rate_limit_login_ip_limit,
        s.auth_rate_limit_login_ip_window_seconds,
    ),
    "refresh": lambda s: (
        s.auth_rate_limit_refresh_ip_limit,
        s.auth_rate_limit_refresh_ip_window_seconds,
    ),
    "password_reset_request": lambda s: (
        s.auth_rate_limit_reset_request_ip_limit,
        s.auth_rate_limit_reset_request_ip_window_seconds,
    ),
    "password_reset_confirm": lambda s: (
        s.auth_rate_limit_reset_confirm_ip_limit,
        s.auth_rate_limit_reset_confirm_ip_window_seconds,
    ),
}

_EMAIL_POLICY: dict[str, Callable[[Settings], tuple[int, int]]] = {
    "login": lambda s: (
        s.auth_rate_limit_login_email_limit,
        s.auth_rate_limit_login_email_window_seconds,
    ),
    "password_reset_request": lambda s: (
        s.auth_rate_limit_reset_request_email_limit,
        s.auth_rate_limit_reset_request_email_window_seconds,
    ),
}


async def _emit_rate_limit_rejected(
    *,
    session: AsyncSession,
    request: Request,
    endpoint: str,
    category: _KeyCategory,
) -> None:
    """Insert one ``rate_limit_rejected`` audit row.

    Direct ``audit_events`` insert in the request-scoped session per
    Audit Log §11.3 (same transaction as the rejection decision). When
    ``services/audit.py`` lands in task 6.1 this helper is the natural
    seam to swap for ``Audit_Service.emit(session, ...)``.

    The payload carries the rejecting endpoint and category only —
    never the raw email value when ``category == "email"`` (Requirement
    10.7, Audit Log §11.2). The IP address column is populated from
    ``request.client.host`` (also captured by the foundation request
    middleware in structured logs); the user-agent is truncated to
    1024 chars per Requirement 11.5.
    """
    user_agent = request.headers.get("User-Agent")
    if user_agent is not None and len(user_agent) > 1024:
        user_agent = user_agent[:1024]

    ip_address = request.client.host if request.client else None

    try:
        await session.execute(
            insert(AuditEvent).values(
                event_type="rate_limit_rejected",
                user_id=None,
                ip_address=ip_address,
                user_agent=user_agent,
                payload={"endpoint": endpoint, "category": category},
            )
        )
    except Exception:
        # Audit-write failure on a *rejection* path must not mask the
        # 429: the rate-limit rejection itself is the user-facing
        # outcome. Log the failure for operator visibility (the row is
        # still discoverable via the structured log line) and let the
        # rejection bubble. Audit Log §11.3's "abort the auth mutation
        # on audit failure" rule applies to *mutation* paths; this is
        # a pre-mutation reject.
        _log.error("rate_limit_audit_emit_failed", endpoint=endpoint, category=category)


# ---------------------------------------------------------------------------
# user_rate_limit — per-user sliding-window dependency (phase-1-matching)
#
# The auth surface keys its limits on IP/email (pre-authentication). The
# resume/match surface is always Bearer-authenticated, so it keys on the
# resolved ``User_Account.id`` instead. This factory mirrors ``rate_limit``
# above but composes ``Depends(get_current_user)`` to obtain the principal
# and reuses the existing :class:`RateLimiter`, :class:`RateLimitDecision`,
# :class:`RateLimited`, and :class:`RateLimiterUnavailableError` — no new
# limiter or error type is introduced (Requirements 11.1, 11.2, 11.3, 11.7).
# ---------------------------------------------------------------------------


_UserRateLimitEndpoint = Literal["resume", "match"]

# Per-user limits use a fixed 60-second window (Requirement 11.1/11.2 "per
# 1-minute window"); only the request budget is configurable per endpoint.
_USER_RATE_LIMIT_WINDOW_SECONDS: Final[int] = 60

# Closures over ``Settings`` keep the limit lookup declarative and avoid the
# ``getattr(settings, dynamic_name)`` shape that defeats mypy ``--strict``,
# matching the ``_IP_POLICY`` / ``_EMAIL_POLICY`` tables above.
_USER_RATE_LIMIT_LIMIT: dict[_UserRateLimitEndpoint, Callable[[Settings], int]] = {
    "resume": lambda s: s.resume_rate_limit_per_min,
    "match": lambda s: s.match_rate_limit_per_min,
}

# Annotated alias for the authenticated principal. Defined here (after
# ``get_current_user``) so the nested dependency can compose it without a
# function-default ``Depends`` call (ruff B008).
_CurrentUserDep = Annotated[User, Depends(get_current_user)]


def user_rate_limit(
    endpoint: _UserRateLimitEndpoint,
) -> Callable[..., Coroutine[Any, Any, None]]:
    """Build a FastAPI dependency enforcing a per-user, per-minute limit.

    Parameters
    ----------
    endpoint:
        ``"resume"`` (``POST /api/v1/resumes``) or ``"match"``
        (``POST /api/v1/matches``). Selects the per-minute budget from
        :class:`Settings` (``resume_rate_limit_per_min`` /
        ``match_rate_limit_per_min``) and forms the Redis key category.

    The returned dependency:

    * Resolves the caller via :func:`get_current_user` (so an
      unauthenticated request is rejected with the 401
      ``unauthenticated`` envelope before any rate-limit work).
    * Calls :meth:`RateLimiter.check` once with the key
      ``rl:{endpoint}:user:{user_id}``, a 60-second window, and the
      configured per-minute limit (Requirements 11.1, 11.2).
    * On a Redis-unavailable decision raises
      :class:`RateLimiterUnavailableError` after setting ``Retry-After``;
      the foundation handler maps it to HTTP 503
      ``rate_limiter_unavailable`` (Requirement 11.7, fail-closed).
    * On a normal rejection sets ``Retry-After`` and raises
      :class:`RateLimited`; the foundation handler maps it to HTTP 429
      ``rate_limited`` (Requirement 11.3).
    * On allow returns ``None`` so the request proceeds.

    Unlike the auth ``rate_limit`` factory this writes no audit row: the
    matching spec's audit surface is ``quota_rejected`` (emitted by the
    service layer on a daily-quota breach, Requirement 11.6), not the
    per-minute rate-limit decision.
    """

    async def _dependency(
        response: Response,
        settings: _SettingsDep,
        rl: _RateLimiterDep,
        user: _CurrentUserDep,
    ) -> None:
        limit = _USER_RATE_LIMIT_LIMIT[endpoint](settings)
        redis_key = f"rl:{endpoint}:user:{user.id}"

        decision: RateLimitDecision = await rl.check(
            redis_key, limit=limit, window_seconds=_USER_RATE_LIMIT_WINDOW_SECONDS
        )

        if decision.allowed:
            return

        # Both the 429 and the fail-closed 503 carry ``Retry-After``
        # (Requirement 11.3 / 11.7), mirroring the auth dependency.
        response.headers["Retry-After"] = str(decision.retry_after_seconds)

        if decision.redis_unavailable:
            raise RateLimiterUnavailableError(retry_after_seconds=decision.retry_after_seconds)

        raise RateLimited(
            endpoint=endpoint,
            category="user",
            retry_after_seconds=decision.retry_after_seconds,
        )

    return _dependency


# ---------------------------------------------------------------------------
# Idempotency for mutating POSTs (phase-1-matching) — Requirements 2.8, 8.9
#
# Mutating uploads/match-creations accept an ``Idempotency-Key`` header.
# The first successful request stores the created resource id and the
# serialized 201 response in Redis under ``idem:{user_id}:{route}:{key}``
# with a 24h TTL; a replay within that window returns the stored response
# without a second object write or row insert. Redis-backed (rather than a
# third table) keeps the migration to the two tables Requirement 14.2
# enumerates. The ``user_rate_limit`` dependency runs first on these routes,
# so the rate limiter's fail-closed 503 already gates the Redis-down case
# before any idempotency lookup executes (Design §"Per-user rate limiting
# and idempotency").
# ---------------------------------------------------------------------------


# 24 hours, per ``security.md`` ("Persist keys for 24h") and the design.
_IDEMPOTENCY_TTL_SECONDS: Final[int] = 24 * 60 * 60


@dataclass(frozen=True, slots=True)
class IdempotencyRecord:
    """A stored idempotent outcome for a single ``Idempotency-Key``.

    Holds the created resource's id and the serialized 201 response so a
    replay can be answered without re-running the mutation.

    Attributes
    ----------
    resource_id:
        The created Resume / MatchResult ``id`` as a string.
    status_code:
        The HTTP status of the stored response (always ``201`` for the
        create endpoints; retained explicitly so the router can replay it
        without hard-coding the value).
    body:
        The JSON-serializable response body (the Pydantic response model
        dumped with ``mode="json"``).
    """

    resource_id: str
    status_code: int
    body: dict[str, Any]


def _build_idempotency_key(*, user_id: UUID, route: str, key: str) -> str:
    """Return the Redis key for an idempotent outcome (Design §idempotency).

    Scoped by ``user_id`` so one account's key can never replay another's
    stored response, and by ``route`` so the same client-supplied key on a
    different endpoint is treated independently.
    """
    return f"idem:{user_id}:{route}:{key}"


class IdempotencyStore:
    """Redis-backed store for idempotent mutation outcomes.

    Constructed with an injected ``redis.asyncio.Redis`` client; the public
    :func:`get_idempotency_store` factory builds the per-request client the
    dependency layer wires through ``Depends`` (mirroring
    :func:`get_rate_limiter`). Lookups and writes fail soft: a Redis error
    on :meth:`get` is treated as a miss (the caller performs the mutation),
    and a Redis error on :meth:`put` is logged but never turns an
    already-successful creation into a request failure. The preceding
    ``user_rate_limit`` dependency has already fail-closed on a Redis
    outage, so these soft paths only cover a mid-request blip.
    """

    def __init__(self, redis_client: aioredis.Redis) -> None:
        self._redis = redis_client

    async def get(self, *, user_id: UUID, route: str, key: str) -> IdempotencyRecord | None:
        """Return the stored outcome for ``key`` if one exists, else ``None``."""
        redis_key = _build_idempotency_key(user_id=user_id, route=route, key=key)
        try:
            raw = await self._redis.get(redis_key)
        except Exception:
            # Treat a lookup failure as a miss: the caller will perform the
            # mutation. Never log the client-supplied key value.
            _log.warning("idempotency_lookup_failed", route=route)
            return None

        if raw is None:
            return None

        try:
            data = json.loads(raw)
            return IdempotencyRecord(
                resource_id=str(data["resource_id"]),
                status_code=int(data["status_code"]),
                body=dict(data["body"]),
            )
        except (ValueError, TypeError, KeyError):
            # A corrupt / unexpected payload is treated as a miss rather
            # than failing the request.
            _log.warning("idempotency_record_corrupt", route=route)
            return None

    async def put(self, *, user_id: UUID, route: str, key: str, record: IdempotencyRecord) -> None:
        """Store ``record`` under ``key`` with a 24h TTL (first writer wins).

        Uses ``SET ... NX EX 86400`` so a concurrent replay never clobbers
        the originally stored response; the first writer's outcome is the
        one every later replay sees (Requirements 2.8, 8.9).
        """
        redis_key = _build_idempotency_key(user_id=user_id, route=route, key=key)
        payload = json.dumps(
            {
                "resource_id": record.resource_id,
                "status_code": record.status_code,
                "body": record.body,
            }
        )
        try:
            await self._redis.set(redis_key, payload, ex=_IDEMPOTENCY_TTL_SECONDS, nx=True)
        except Exception:
            # The resource was already created; a failure to memoize the
            # outcome must not surface as a 5xx. Log for operator visibility.
            _log.warning("idempotency_store_failed", route=route)


async def get_idempotency_store() -> AsyncIterator[IdempotencyStore]:
    """Yield a per-request :class:`IdempotencyStore`; close it on teardown.

    Built per request for the same reason as :func:`get_rate_limiter`: the
    underlying ``redis.asyncio.Redis`` client's asyncio resources must not
    outlive the event loop that allocates them (pytest-asyncio creates one
    loop per test). redis-py pools connections lazily, so a request that
    carries no ``Idempotency-Key`` (and therefore never touches the store)
    opens no TCP connection. The teardown drains the pool to avoid the
    ``ResourceWarning`` the API test suite's ``filterwarnings = ["error"]``
    would otherwise escalate to a failure.
    """
    settings = get_settings()
    client = aioredis.from_url(str(settings.redis_url), decode_responses=False)  # type: ignore[no-untyped-call]
    try:
        yield IdempotencyStore(client)
    finally:
        try:
            await client.aclose(close_connection_pool=True)
            await client.connection_pool.disconnect()
        except Exception:  # pragma: no cover - defensive
            _log.warning("idempotency_client_close_failed")


# Annotated alias for routers wiring the store via ``Depends`` (parallel to
# ``_RateLimiterDep`` above). Exported so the resumes/matches routers can
# inject it without re-declaring the ``Depends(...)`` call.
IdempotencyStoreDep = Annotated[IdempotencyStore, Depends(get_idempotency_store)]


__all__ = [
    "CsrfMismatchError",
    "IdempotencyRecord",
    "IdempotencyStore",
    "IdempotencyStoreDep",
    "RateLimited",
    "RateLimiterUnavailableError",
    "UnauthenticatedError",
    "csrf_required",
    "get_current_user",
    "get_idempotency_store",
    "rate_limit",
    "user_rate_limit",
]
