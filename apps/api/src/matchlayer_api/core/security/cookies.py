"""Cookie helpers for refresh and CSRF tokens.

This is the ONLY module that calls ``Response.set_cookie`` for the
names ``matchlayer_refresh`` or ``matchlayer_csrf``.

Design reference: CSRF Strategy §9.2.
"""

from __future__ import annotations

from fastapi.responses import Response

from matchlayer_api.config import Settings

_REFRESH_COOKIE = "matchlayer_refresh"
_CSRF_COOKIE = "matchlayer_csrf"
# The HttpOnly refresh token is scoped tightly: it is only ever consumed by
# `/api/v1/auth/refresh` and `/api/v1/auth/logout`, so the browser should only
# attach it there (limits exposure of the sensitive token).
_REFRESH_COOKIE_PATH = "/api/v1/auth"
# The CSRF token is a NON-secret random double-submit value the frontend must
# read via `document.cookie` to echo as `X-CSRF-Token`. `document.cookie` only
# exposes cookies whose path is a prefix of the current page path, so a page
# like `/upload` could not read a cookie scoped to `/api/v1/auth`. Scope it to
# `/` so it is readable from every page. This is safe: the value is not a
# credential, it only has to round-trip to prove same-origin script access.
_CSRF_COOKIE_PATH = "/"


def _is_secure(settings: Settings) -> bool:
    return settings.environment != "development"


def set_refresh_cookie(response: Response, *, value: str, max_age: int, settings: Settings) -> None:
    """Set the HttpOnly refresh-token cookie."""
    response.set_cookie(
        key=_REFRESH_COOKIE,
        value=value,
        max_age=max_age,
        path=_REFRESH_COOKIE_PATH,
        httponly=True,
        secure=_is_secure(settings),
        samesite="lax",
    )


def clear_refresh_cookie(response: Response, *, settings: Settings) -> None:
    """Clear the refresh-token cookie."""
    response.set_cookie(
        key=_REFRESH_COOKIE,
        value="",
        max_age=0,
        path=_REFRESH_COOKIE_PATH,
        httponly=True,
        secure=_is_secure(settings),
        samesite="lax",
    )


def set_csrf_cookie(response: Response, *, value: str, max_age: int, settings: Settings) -> None:
    """Set the non-HttpOnly CSRF cookie (readable by JS)."""
    response.set_cookie(
        key=_CSRF_COOKIE,
        value=value,
        max_age=max_age,
        path=_CSRF_COOKIE_PATH,
        httponly=False,
        secure=_is_secure(settings),
        samesite="lax",
    )


def clear_csrf_cookie(response: Response, *, settings: Settings) -> None:
    """Clear the CSRF cookie."""
    response.set_cookie(
        key=_CSRF_COOKIE,
        value="",
        max_age=0,
        path=_CSRF_COOKIE_PATH,
        httponly=False,
        secure=_is_secure(settings),
        samesite="lax",
    )
