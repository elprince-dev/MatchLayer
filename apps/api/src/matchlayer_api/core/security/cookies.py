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
_COOKIE_PATH = "/api/v1/auth"


def _is_secure(settings: Settings) -> bool:
    return settings.environment != "development"


def set_refresh_cookie(response: Response, *, value: str, max_age: int, settings: Settings) -> None:
    """Set the HttpOnly refresh-token cookie."""
    response.set_cookie(
        key=_REFRESH_COOKIE,
        value=value,
        max_age=max_age,
        path=_COOKIE_PATH,
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
        path=_COOKIE_PATH,
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
        path=_COOKIE_PATH,
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
        path=_COOKIE_PATH,
        httponly=False,
        secure=_is_secure(settings),
        samesite="lax",
    )
