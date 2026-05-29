"""Test helpers for setting auth cookies on an ``httpx.AsyncClient`` (task 16.6).

httpx 0.27+ deprecated passing ``cookies=`` per-request to
``AsyncClient.post(...)`` (see httpx#3433); the project's pytest
``filterwarnings = ["error"]`` config escalates the
``DeprecationWarning`` into a real test failure. The migration
target is the client-instance API (``client.cookies.set(...)``,
``client.cookies.clear()``), which is also the shape CSRF Strategy
§9.3 implicitly assumes — the same value must be replayed across
the request and matched against the ``X-CSRF-Token`` header on
every cookie-authenticated mutating request.

This module collects the per-test cookie wiring into two small
helpers so the migration stays mechanical:

* :func:`set_auth_cookies` — set both ``matchlayer_refresh`` and
  ``matchlayer_csrf`` on a client. Returns the CSRF value so the
  caller can pass it as the ``X-CSRF-Token`` header.
* :func:`clear_auth_cookies` — clear both cookies. Used by tests
  that drive the unauthenticated path back-to-back with an
  authenticated path.
"""

from __future__ import annotations

from httpx import AsyncClient


def set_auth_cookies(client: AsyncClient, *, refresh: str, csrf: str = "csrf-token-value") -> str:
    """Set ``matchlayer_refresh`` and ``matchlayer_csrf`` on the client.

    Args:
        client: The :class:`httpx.AsyncClient` to mutate.
        refresh: The refresh-cookie value (typically a JWT issued by
            :func:`matchlayer_api.core.security.jwt.issue_refresh_token`).
        csrf: The CSRF value. The caller passes the same value as the
            ``X-CSRF-Token`` header so the server-side double-submit
            check (CSRF Strategy §9.3) succeeds.

    Returns:
        The CSRF value, for convenient pass-through into request
        headers.
    """
    client.cookies.set("matchlayer_refresh", refresh)
    client.cookies.set("matchlayer_csrf", csrf)
    return csrf


def clear_auth_cookies(client: AsyncClient) -> None:
    """Clear every cookie on the client.

    Used between authenticated and unauthenticated requests in the
    same test so the second request starts from a clean cookie jar.
    """
    client.cookies.clear()


__all__ = ["clear_auth_cookies", "set_auth_cookies"]
