"""Pydantic v2 request/response models for the auth surface.

These models are the source of truth for the auth section of the
OpenAPI schema FastAPI emits at ``app.openapi()``. ``pnpm codegen``
consumes that schema to regenerate ``packages/shared-types/src/api-types.ts``
(via ``openapi-typescript``) and ``api-schemas.ts`` (via
``openapi-zod-client``), which the Next.js auth pages bind to via
``zodResolver`` and TanStack Query. Anything missing here is missing
on the frontend; anything wrong here is wrong on the frontend.

Model coverage (one Pydantic class per HTTP body in
``phase-1-auth/requirements.md`` §1, §2, §3, §5, §6, §13):

Requests:
  * :class:`RegisterRequest` -- ``POST /api/v1/auth/register``
  * :class:`LoginRequest` -- ``POST /api/v1/auth/login``
  * :class:`PasswordResetRequestRequest` -- ``POST /api/v1/auth/password-reset/request``
  * :class:`PasswordResetConfirmRequest` -- ``POST /api/v1/auth/password-reset/confirm``
  * :class:`MePatchRequest` -- ``PATCH /api/v1/auth/me``

  ``POST /api/v1/auth/refresh`` and ``POST /api/v1/auth/logout`` take
  no JSON body -- the refresh token rides the ``matchlayer_refresh``
  cookie and the CSRF check rides the ``X-CSRF-Token`` header (see
  CSRF Strategy §9.3) -- so no request schema is declared for them.

Responses:
  * :class:`UserResponse` -- the User_Account projection embedded in
    auth responses; never carries ``password_hash``,
    ``failed_login_count``, ``locked_until``, or ``deleted_at``
    (Requirement 6.8).
  * :class:`TokenPairResponse` -- ``register`` / ``login`` / ``refresh``
    success body. Shape per design §7.3 mermaid line 370:
    ``{access_token, user}``. The refresh token itself is **not** in
    the body -- it rides the ``matchlayer_refresh`` HttpOnly cookie
    (security.md "Anti-patterns" forbids storing JWTs in
    ``localStorage``).
  * :class:`MeResponse` -- ``GET /api/v1/auth/me`` and ``PATCH
    /api/v1/auth/me``. Same field set as :class:`UserResponse`,
    declared as a distinct class so the OpenAPI schema names the
    ``/me`` response shape independently of the embedded
    auth-response shape; downstream codegen produces a precise
    re-export ``MeResponse`` rather than aliasing the nested type.
  * :class:`LastResetLinkResponse` -- ``GET /api/v1/dev/last-reset-link``
    (dev-only; never registered outside ``MATCHLAYER_ENVIRONMENT=development``,
    see Dev-Mode Reset-Link Surface §12).

Design references:
  * Components and Interfaces -- ``auth/schemas.py`` is "Pydantic
    request/response models" only; no business logic, no DB calls.
  * OpenAPI Codegen Impact -- these classes drive the curated
    re-exports in ``packages/shared-types/src/index.ts``.
  * Password Handling §8.5 -- minimum-length / blocklist enforcement
    is **not** done at the schema layer because §8.5 specifies the
    length check runs against the **pre-NFKC** codepoint count and
    the blocklist check runs against the **post-NFKC, case-folded**
    form. Both happen inside :class:`Password_Hasher`. The schema
    enforces only a tiny non-empty floor that protects FastAPI's
    request parser from accepting an empty string.

Requirements covered: 1.1, 1.2, 1.3, 2.1, 5.1, 5.5, 6.5, 6.6.
"""

from __future__ import annotations

import unicodedata
from datetime import datetime
from typing import Final

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

# ---------------------------------------------------------------------------
# Display-name validator (Requirement 6.6).
# ---------------------------------------------------------------------------
#
# Requirement 6.6 enumerates the allowed Unicode general categories for
# ``display_name``: ``L`` (Letter, every subcategory), ``M`` (Mark, every
# subcategory), ``N`` (Number, every subcategory), ``Pd`` (Dash
# punctuation), ``Pc`` (Connector punctuation), ``Zs`` (Space separator).
# The first three are top-level classes (any subcategory matches); the
# last three are exact two-letter categories.
#
# Splitting the allowlist into a top-level set and an exact-match set
# makes the membership check trivially correct: top-level if the
# category's first letter matches; exact-match otherwise.

_DISPLAY_NAME_TOPLEVEL_CATEGORIES: Final[frozenset[str]] = frozenset({"L", "M", "N"})
"""Unicode top-level categories whose every subcategory is allowed."""

_DISPLAY_NAME_EXACT_CATEGORIES: Final[frozenset[str]] = frozenset({"Pd", "Pc", "Zs"})
"""Exact two-letter Unicode categories allowed in display names."""

_DISPLAY_NAME_MAX_LENGTH: Final[int] = 64
"""Upper bound on ``display_name`` after stripping leading/trailing whitespace."""


def _validate_display_name(value: str) -> str:
    """Validate and canonicalize a ``display_name`` per Requirement 6.6.

    Rules:
      1. Strip leading and trailing whitespace before any further check
         (Requirement 6.6 evaluates length and emptiness "after stripping").
      2. The stripped value must be non-empty.
      3. The stripped value's character count (codepoints) must be
         ``<= _DISPLAY_NAME_MAX_LENGTH``.
      4. Every character's Unicode general category must be either in
         :data:`_DISPLAY_NAME_TOPLEVEL_CATEGORIES` (matched by the first
         letter of the category) or in :data:`_DISPLAY_NAME_EXACT_CATEGORIES`
         (matched as a two-letter string).

    The function returns the stripped form so the caller persists the
    canonical value. Audit emissions in :mod:`services.audit` log only
    the *length* of old and new display names (Audit Log §11.2,
    Requirement 11.4) -- never the strings themselves -- so canonical
    form here does not leak through audit.

    Raises:
        ValueError: When any rule above fails. The message names the
            failing rule but never echoes the offending string in full
            (we report the failing codepoint by U+ escape, which is
            safe to surface in the RFC 7807 ``detail`` field).
    """
    stripped = value.strip()
    if not stripped:
        raise ValueError("display_name must not be empty after trimming whitespace")
    if len(stripped) > _DISPLAY_NAME_MAX_LENGTH:
        raise ValueError(
            f"display_name must be at most {_DISPLAY_NAME_MAX_LENGTH} characters; "
            f"got {len(stripped)}"
        )
    for ch in stripped:
        category = unicodedata.category(ch)
        # Top-level match: ``L``, ``M``, ``N`` -- any subcategory.
        if category[0] in _DISPLAY_NAME_TOPLEVEL_CATEGORIES:
            continue
        # Exact-string match: ``Pd``, ``Pc``, ``Zs``.
        if category in _DISPLAY_NAME_EXACT_CATEGORIES:
            continue
        raise ValueError(
            f"display_name contains disallowed character "
            f"U+{ord(ch):04X} (Unicode category {category})"
        )
    return stripped


# Strict ``model_config`` for every request schema in this module:
# ``extra="forbid"`` means a request body with unknown fields is a 422,
# not a silent ignore. That tightens the contract so the OpenAPI codegen
# output is the literal wire shape -- no client can stuff a stray field
# through. Response models keep the default (``extra="ignore"``) so the
# server-side mapping from SQLAlchemy rows is forgiving when an unrelated
# attribute happens to share a name.
_STRICT_CONFIG: Final[ConfigDict] = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Request models.
# ---------------------------------------------------------------------------


class RegisterRequest(BaseModel):
    """Body of ``POST /api/v1/auth/register`` (Requirement 1.1).

    Fields:
      * ``email`` -- :class:`pydantic.EmailStr` triggers RFC 5321 validation
        through ``email-validator``, which is what Requirement 1.3 requires.
        A syntactically valid but RFC-5321-non-compliant address (e.g. a
        local part longer than 64 octets) raises a Pydantic
        ``ValidationError`` and FastAPI maps it to 422 via the foundation
        RFC 7807 envelope.
      * ``password`` -- minimum 12 *codepoints* (Requirement 1.4). The
        blocklist check (Requirement 1.5) runs deeper in
        :class:`Password_Hasher`, not here, because §8.5 evaluates it
        against the post-NFKC case-folded form.
      * ``display_name`` -- optional. When omitted, :class:`Auth_Service`
        defaults it to the local part of the email (Requirement 1.7).
        When present, it is validated against Requirement 6.6 by
        :func:`_validate_display_name`.
    """

    model_config = _STRICT_CONFIG

    email: EmailStr = Field(
        description="RFC 5321-compliant email address. Stored as submitted; "
        "lookups are case-insensitive via the functional unique index.",
    )
    password: str = Field(
        min_length=12,
        description="Plaintext password; minimum 12 codepoints (Requirement 1.4). "
        "Blocklist enforcement runs server-side after NFKC normalization.",
    )
    display_name: str | None = Field(
        default=None,
        description="Optional display name. Defaults to the local part of the "
        "email when omitted. Validated per Requirement 6.6 when present.",
    )

    @field_validator("display_name")
    @classmethod
    def _check_display_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return _validate_display_name(v)


class LoginRequest(BaseModel):
    """Body of ``POST /api/v1/auth/login`` (Requirement 2.1).

    The login schema deliberately does **not** enforce the registration
    minimum-length floor on the supplied password: doing so would be an
    enumeration vector -- ``"a"`` would 422 instantly while a 12-char
    wrong password would burn the dummy-Argon2id-verify path (§8.3).
    The schema only checks that the field is non-empty so FastAPI's
    request parser doesn't dispatch to the service with an empty string.
    """

    model_config = _STRICT_CONFIG

    email: EmailStr = Field(
        description="RFC 5321-compliant email address.",
    )
    password: str = Field(
        min_length=1,
        description="Plaintext password. No upper-floor length enforcement here -- "
        "the dummy-hash timing equalizer in Auth_Service relies on every "
        "non-empty input reaching the verify step (Password Handling §8.3).",
    )


class PasswordResetRequestRequest(BaseModel):
    """Body of ``POST /api/v1/auth/password-reset/request`` (Requirement 5.1).

    Single field: the email address requesting a reset link. The
    response is always 202 with an empty body whether or not the
    address matches a User_Account (Requirement 5.2 -- anti-enumeration
    per ``security.md`` "no account enumeration").
    """

    model_config = _STRICT_CONFIG

    email: EmailStr = Field(
        description="Email address to issue a reset link for.",
    )


class PasswordResetConfirmRequest(BaseModel):
    """Body of ``POST /api/v1/auth/password-reset/confirm`` (Requirement 5.5).

    Fields:
      * ``token`` -- opaque reset token produced by the request endpoint.
        Validated server-side by hashing and looking up
        ``password_reset_tokens.token_hash``; missing / expired / used
        all collapse to a single ``invalid_reset_token`` envelope
        (Requirements 5.6, 5.7, 5.8) so the client can't distinguish.
      * ``new_password`` -- same 12-codepoint floor as registration
        (Requirement 5.9 references Requirement 1.4 + 1.5 directly).
    """

    model_config = _STRICT_CONFIG

    token: str = Field(
        min_length=1,
        description="Opaque reset token from the issued reset link.",
    )
    new_password: str = Field(
        min_length=12,
        description="New plaintext password; minimum 12 codepoints. "
        "Blocklist enforcement runs server-side after NFKC normalization.",
    )


class MePatchRequest(BaseModel):
    """Body of ``PATCH /api/v1/auth/me`` (Requirement 6.5).

    Per Requirement 6.5 ``display_name`` is the "optional field"; this
    is what makes the endpoint forward-compatible -- adding a second
    optional field in a later spec is a non-breaking change. In
    Phase 1 the only writable column is ``display_name``, so a request
    with no fields set is a no-op (returns 200 with the unchanged
    user); Auth_Service only emits the ``display_name_changed`` audit
    event when the field is actually present.
    """

    model_config = _STRICT_CONFIG

    display_name: str | None = Field(
        default=None,
        description="New display name. When present, validated per "
        "Requirement 6.6: non-empty after strip, <=64 chars after strip, "
        "characters limited to Unicode classes L, M, N, Pd, Pc, Zs.",
    )

    @field_validator("display_name")
    @classmethod
    def _check_display_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return _validate_display_name(v)


# ---------------------------------------------------------------------------
# Response models.
# ---------------------------------------------------------------------------


class UserResponse(BaseModel):
    """User_Account projection embedded in auth responses.

    Carries exactly the five fields Requirements 1.7, 2.5, 3.8, and
    6.3 enumerate: ``id``, ``email``, ``display_name``, ``created_at``,
    ``updated_at``. ``password_hash``, ``failed_login_count``,
    ``locked_until``, and ``deleted_at`` are intentionally absent
    (Requirement 6.8 + ``security.md`` "Logging").

    Datetimes serialize as ISO 8601 with timezone (the database column
    is ``timestamptz``); the foundation convention "ISO 8601 UTC with Z
    suffix" is honored on the wire by Pydantic's default datetime
    formatter when the source value is timezone-aware.

    ``from_attributes=True`` lets the router build a response directly
    from the SQLAlchemy ``User`` row via ``UserResponse.model_validate(user)``.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(
        description="UUIDv7 of the User_Account, encoded as a string.",
    )
    email: str = Field(
        description="Email address as submitted at registration.",
    )
    display_name: str = Field(
        description="Display name. Always populated; defaults to the local part "
        "of the email when not supplied at registration.",
    )
    created_at: datetime = Field(
        description="Registration timestamp (timezone-aware).",
    )
    updated_at: datetime = Field(
        description="Last-modified timestamp (timezone-aware). Bumped on "
        "display-name change and password change.",
    )


class TokenPairResponse(BaseModel):
    """Body of register / login / refresh on success.

    Shape per Design §7.3 mermaid line 370 -- ``{access_token, user}``.
    The refresh token is **not** in the body: it rides the
    ``matchlayer_refresh`` HttpOnly cookie set on the same response
    (CSRF Strategy §9.2). The CSRF token rides a sibling
    ``matchlayer_csrf`` cookie. This split is what lets the access
    token live in a JS-readable in-memory store on the frontend
    (``security.md`` forbids ``localStorage`` for JWTs) while the
    refresh token stays out of every JS surface entirely.
    """

    model_config = _STRICT_CONFIG

    access_token: str = Field(
        description="Short-lived (15 min by default) JWT for Authorization "
        "Bearer use. Held in memory on the frontend, never persisted.",
    )
    user: UserResponse = Field(
        description="The authenticated User_Account projection.",
    )


class MeResponse(BaseModel):
    """Body of ``GET /api/v1/auth/me`` and ``PATCH /api/v1/auth/me``.

    Identical field set to :class:`UserResponse` -- the redundancy is
    deliberate. Declaring a distinct class produces a distinct OpenAPI
    schema name, so the curated frontend re-export
    ``packages/shared-types/src/index.ts`` produces a top-level
    ``MeResponse`` type rather than aliasing the embedded
    auth-response shape. ``conventions.md`` "Shared schemas" requires
    that re-export to be a precise type, not a path-derived alias.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(
        description="UUIDv7 of the User_Account, encoded as a string.",
    )
    email: str = Field(
        description="Email address as submitted at registration.",
    )
    display_name: str = Field(
        description="Display name.",
    )
    created_at: datetime = Field(
        description="Registration timestamp (timezone-aware).",
    )
    updated_at: datetime = Field(
        description="Last-modified timestamp (timezone-aware).",
    )


class LastResetLinkResponse(BaseModel):
    """Body of ``GET /api/v1/dev/last-reset-link`` (dev-only).

    Returns the most recently generated password-reset link, or both
    fields ``null`` when the in-process store is empty (Requirement
    13.3, Dev-Mode Reset-Link Surface §12.1). The router that exposes
    this endpoint is only mounted onto the FastAPI app when
    ``MATCHLAYER_ENVIRONMENT=development``; in any other environment
    the path returns the foundation 404 envelope (Requirement 13.4).
    """

    model_config = _STRICT_CONFIG

    link: str | None = Field(
        default=None,
        description="Plaintext reset link including the single-use token, or null "
        "when no reset has been requested since the API process started.",
    )
    created_at: datetime | None = Field(
        default=None,
        description="When the link was recorded, or null when the store is empty.",
    )


__all__ = [
    "LastResetLinkResponse",
    "LoginRequest",
    "MePatchRequest",
    "MeResponse",
    "PasswordResetConfirmRequest",
    "PasswordResetRequestRequest",
    "RegisterRequest",
    "TokenPairResponse",
    "UserResponse",
]
