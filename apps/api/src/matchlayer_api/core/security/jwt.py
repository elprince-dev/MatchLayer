"""JWT issuance and verification (HS256 only).

This is the ONLY module in the API that imports ``jwt`` (PyJWT).
Import-boundary enforced by ``tests/unit/test_import_boundaries.py``.

Design reference: JWT Design §6.1-§6.4.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

import jwt
import uuid_utils

from matchlayer_api.config import get_settings

_ALGORITHM = "HS256"
_ALGORITHMS_ALLOWLIST = [_ALGORITHM]


class InvalidTokenError(Exception):
    """Raised when a token fails verification."""


def issue_access_token(*, sub: str) -> str:
    """Issue a short-lived access token (§6.1)."""
    settings = get_settings()
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": sub,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=settings.auth_access_token_ttl_seconds)).timestamp()),
        "jti": str(uuid_utils.uuid7()),
        "type": "access",
    }
    return jwt.encode(payload, settings.jwt_secret.get_secret_value(), algorithm=_ALGORITHM)


def issue_refresh_token(*, sub: str, jti: UUID) -> str:
    """Issue a refresh token with a caller-supplied jti (§6.1)."""
    settings = get_settings()
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": sub,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=settings.auth_refresh_token_ttl_seconds)).timestamp()),
        "jti": str(jti),
        "type": "refresh",
    }
    return jwt.encode(payload, settings.jwt_secret.get_secret_value(), algorithm=_ALGORITHM)


def verify_token(token: str, *, expected_type: Literal["access", "refresh"]) -> dict[str, Any]:
    """Verify and decode a token. Raises InvalidTokenError on any failure.

    Enforces the algorithm allowlist (§6.3) and the type claim (Req 7.6).
    """
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=_ALGORITHMS_ALLOWLIST,
        )
    except jwt.PyJWTError as exc:
        raise InvalidTokenError(str(exc)) from exc

    if payload.get("type") != expected_type:
        raise InvalidTokenError(
            f"Expected token type '{expected_type}', got '{payload.get('type')}'"
        )
    return payload
