"""Unit tests for ``core/security/cookies.py``.

Validates the cookie-attribute set produced by every helper in
:mod:`matchlayer_api.core.security.cookies`. The contract under test is
the row-by-row attribute table from CSRF Strategy §9.2:

* ``HttpOnly=True`` for ``matchlayer_refresh``; ``HttpOnly=False`` for
  ``matchlayer_csrf`` (the frontend has to read it to echo it).
* ``SameSite=Lax`` on both cookies.
* ``Path=/api/v1/auth`` on both cookies.
* ``Domain`` unset (host-only) on both cookies.
* ``Secure=True`` in ``production`` and ``staging``; ``Secure=False`` in
  ``development`` so ``http://localhost`` works during local dev.
* ``set_*`` helpers emit ``Max-Age`` matching the value the caller
  passes (auth router supplies the configured refresh-token TTL).
* ``clear_*`` helpers emit ``Max-Age=0`` and an empty cookie value.

These are the only set-cookie emissions for ``matchlayer_refresh`` and
``matchlayer_csrf`` anywhere in the API (Components and Interfaces
import-boundary rule), so locking the attribute set here pins the
on-the-wire shape for every consumer.

Tests parse the raw ``Set-Cookie`` header string rather than going
through ``http.cookies.SimpleCookie``: the boolean flags ``Secure`` and
``HttpOnly`` round-trip more uniformly when read by hand than across
CPython releases of :mod:`http.cookies`, and the parsing is trivial
enough that hiding it behind a parser earns nothing.

References:
* Requirements 9.1, 9.2, 9.5 (CSRF cookie attributes, lifecycle).
* Design §9.2 (cookie attribute table).
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.responses import Response

from matchlayer_api.config import Environment, Settings
from matchlayer_api.core.security.cookies import (
    clear_csrf_cookie,
    clear_refresh_cookie,
    set_csrf_cookie,
    set_refresh_cookie,
)

# ---------------------------------------------------------------------------
# Settings construction
#
# Tests build :class:`Settings` directly so they don't depend on the
# repo's ``.env`` and so the ``environment`` field can be parameterized
# per case. All other required fields take placeholder values that pass
# Pydantic validation (URL shape, secret length floor) without touching
# any external service.
# ---------------------------------------------------------------------------

_BASE_SETTINGS_KWARGS: dict[str, Any] = {
    "log_level": "info",
    "database_url": "postgresql+asyncpg://u:p@localhost:5432/db",
    "redis_url": "redis://localhost:6379/0",
    "s3_endpoint_url": None,
    "s3_region": "us-east-1",
    "s3_access_key_id": "test",
    "s3_secret_access_key": "test",
    "s3_bucket": "test-bucket",
    "cors_allowed_origins": [],
    # 33 bytes UTF-8 — clears the 32-byte floor in
    # ``Settings._jwt_secret_min_length``.
    "jwt_secret": "test-jwt-secret-32-byte-floor-pad",  # gitleaks:allow — synthetic test value
}


def _build_settings(environment: Environment) -> Settings:
    """Return a Settings instance with the given ``environment`` value.

    Every other field comes from :data:`_BASE_SETTINGS_KWARGS` so the
    only knob a test varies is the one that drives the ``Secure``
    attribute (CSRF Strategy §9.2 dev carve-out).
    """
    return Settings(environment=environment, **_BASE_SETTINGS_KWARGS)


# ---------------------------------------------------------------------------
# Set-Cookie header parsing
# ---------------------------------------------------------------------------


REFRESH_NAME = "matchlayer_refresh"
CSRF_NAME = "matchlayer_csrf"
COOKIE_PATH = "/api/v1/auth"

# Every environment that should emit ``Secure`` (i.e., everything but
# the development carve-out). Used by parametrized tests so a future
# environment value (e.g., ``"qa"``) gets a deliberate decision rather
# than a silent default.
SECURE_ENVIRONMENTS: tuple[Environment, ...] = ("production", "staging")
ALL_ENVIRONMENTS: tuple[Environment, ...] = ("development", "staging", "production")


def _set_cookie_header_for(response: Response, name: str) -> str:
    """Return the ``Set-Cookie`` header whose cookie name matches ``name``.

    ``Response.set_cookie`` adds one ``Set-Cookie`` header per call;
    :meth:`MutableHeaders.getlist` returns them as a list of strings.
    """
    headers = response.headers.getlist("set-cookie")
    for header in headers:
        # The first ``;``-separated chunk is ``name=value``.
        if header.split(";", 1)[0].split("=", 1)[0] == name:
            return header
    pytest.fail(f"Cookie {name!r} not in Set-Cookie headers: {headers!r}")


def _parse_set_cookie(header: str) -> tuple[str, str, dict[str, str | bool]]:
    """Decompose a Set-Cookie header into (name, value, attributes).

    Boolean attributes (``Secure``, ``HttpOnly``) become
    ``attrs[key] is True``; absent flags are simply not in ``attrs`` so
    the caller asserts via ``not in``. Attribute keys are lower-cased
    so the test reads consistently regardless of Starlette's casing.
    """
    parts = [chunk.strip() for chunk in header.split(";")]
    name, _, raw_value = parts[0].partition("=")
    # RFC 6265 cookie-value grammar permits the value to be DQUOTE-wrapped
    # (``cookie-value = *cookie-octet / ( DQUOTE *cookie-octet DQUOTE )``).
    # Starlette emits an empty value as ``""``; strip a single matched
    # surrounding pair so the parsed value reflects the cookie's logical
    # content regardless of which form the framework chose.
    if len(raw_value) >= 2 and raw_value[0] == '"' and raw_value[-1] == '"':
        value = raw_value[1:-1]
    else:
        value = raw_value
    attrs: dict[str, str | bool] = {}
    for chunk in parts[1:]:
        if not chunk:
            continue
        if "=" in chunk:
            key, _, val = chunk.partition("=")
            attrs[key.strip().lower()] = val
        else:
            attrs[chunk.strip().lower()] = True
    return name, value, attrs


def _assert_common_attrs(
    attrs: dict[str, str | bool],
    *,
    expect_secure: bool,
) -> None:
    """Attribute assertions shared by every helper.

    ``Path``, ``SameSite``, and ``Domain`` are identical across all
    four helpers (CSRF Strategy §9.2). ``Secure`` flips with the
    environment.
    """
    assert attrs.get("path") == COOKIE_PATH
    # SameSite values are case-insensitive per RFC 6265bis; normalize
    # for comparison so the test passes whether Starlette emits "Lax"
    # or "lax".
    samesite = attrs.get("samesite")
    assert isinstance(samesite, str), "SameSite attribute must be a value, not a flag"
    assert samesite.lower() == "lax"
    # ``Domain`` unset means the cookie is host-only — the attribute
    # MUST be absent from the header (Requirement 9.2).
    assert "domain" not in attrs, (
        f"Domain attribute must be unset for host-only cookies; got {attrs.get('domain')!r}"
    )
    if expect_secure:
        assert attrs.get("secure") is True
    else:
        assert "secure" not in attrs


# ---------------------------------------------------------------------------
# set_refresh_cookie
# ---------------------------------------------------------------------------


class TestSetRefreshCookie:
    """``matchlayer_refresh`` is HttpOnly and carries the configured TTL."""

    @pytest.mark.parametrize("environment", SECURE_ENVIRONMENTS)
    def test_secure_true_in_production_and_staging(self, environment: Environment) -> None:
        response = Response()
        set_refresh_cookie(
            response,
            value="opaque-jwt",
            max_age=604800,
            settings=_build_settings(environment),
        )
        header = _set_cookie_header_for(response, REFRESH_NAME)
        name, value, attrs = _parse_set_cookie(header)

        assert name == REFRESH_NAME
        assert value == "opaque-jwt"
        assert attrs.get("max-age") == "604800"
        assert attrs.get("httponly") is True
        _assert_common_attrs(attrs, expect_secure=True)

    def test_secure_false_in_development(self) -> None:
        """``Secure`` is omitted under the documented dev carve-out.

        ``http://localhost`` cannot accept ``Secure`` cookies, so the
        helper drops the attribute when ``environment == "development"``
        (CSRF Strategy §9.2 carve-out row).
        """
        response = Response()
        set_refresh_cookie(
            response,
            value="opaque-jwt",
            max_age=604800,
            settings=_build_settings("development"),
        )
        header = _set_cookie_header_for(response, REFRESH_NAME)
        name, value, attrs = _parse_set_cookie(header)

        assert name == REFRESH_NAME
        assert value == "opaque-jwt"
        assert attrs.get("max-age") == "604800"
        assert attrs.get("httponly") is True
        _assert_common_attrs(attrs, expect_secure=False)

    def test_max_age_passes_through_unchanged(self) -> None:
        """Caller-supplied ``max_age`` round-trips into ``Max-Age``.

        The auth router computes the value from
        ``settings.auth_refresh_token_ttl_seconds``; using a non-default
        value here proves the helper doesn't hard-code the TTL.
        """
        response = Response()
        set_refresh_cookie(
            response,
            value="opaque-jwt",
            max_age=42,
            settings=_build_settings("production"),
        )
        _, _, attrs = _parse_set_cookie(_set_cookie_header_for(response, REFRESH_NAME))
        assert attrs.get("max-age") == "42"


# ---------------------------------------------------------------------------
# set_csrf_cookie
# ---------------------------------------------------------------------------


class TestSetCsrfCookie:
    """``matchlayer_csrf`` is *not* HttpOnly so the frontend can echo it."""

    @pytest.mark.parametrize("environment", SECURE_ENVIRONMENTS)
    def test_secure_true_in_production_and_staging(self, environment: Environment) -> None:
        response = Response()
        set_csrf_cookie(
            response,
            value="random-csrf",
            max_age=604800,
            settings=_build_settings(environment),
        )
        header = _set_cookie_header_for(response, CSRF_NAME)
        name, value, attrs = _parse_set_cookie(header)

        assert name == CSRF_NAME
        assert value == "random-csrf"
        assert attrs.get("max-age") == "604800"
        # CSRF cookie MUST NOT be HttpOnly — frontend reads it to mirror
        # the value into the X-CSRF-Token header (CSRF Strategy §9.2).
        assert "httponly" not in attrs
        _assert_common_attrs(attrs, expect_secure=True)

    def test_secure_false_in_development(self) -> None:
        response = Response()
        set_csrf_cookie(
            response,
            value="random-csrf",
            max_age=604800,
            settings=_build_settings("development"),
        )
        header = _set_cookie_header_for(response, CSRF_NAME)
        name, value, attrs = _parse_set_cookie(header)

        assert name == CSRF_NAME
        assert value == "random-csrf"
        assert attrs.get("max-age") == "604800"
        assert "httponly" not in attrs
        _assert_common_attrs(attrs, expect_secure=False)

    def test_max_age_passes_through_unchanged(self) -> None:
        response = Response()
        set_csrf_cookie(
            response,
            value="random-csrf",
            max_age=42,
            settings=_build_settings("production"),
        )
        _, _, attrs = _parse_set_cookie(_set_cookie_header_for(response, CSRF_NAME))
        assert attrs.get("max-age") == "42"


# ---------------------------------------------------------------------------
# clear_refresh_cookie
# ---------------------------------------------------------------------------


class TestClearRefreshCookie:
    """``clear_refresh_cookie`` emits an empty value with ``Max-Age=0``."""

    @pytest.mark.parametrize("environment", ALL_ENVIRONMENTS)
    def test_zero_max_age_and_empty_value(self, environment: Environment) -> None:
        response = Response()
        clear_refresh_cookie(response, settings=_build_settings(environment))
        header = _set_cookie_header_for(response, REFRESH_NAME)
        name, value, attrs = _parse_set_cookie(header)

        assert name == REFRESH_NAME
        # Requirement 9.5 anchor: clearing the refresh cookie blanks
        # the value and zeros the lifetime.
        assert value == ""
        assert attrs.get("max-age") == "0"
        assert attrs.get("httponly") is True
        _assert_common_attrs(
            attrs,
            expect_secure=environment in SECURE_ENVIRONMENTS,
        )


# ---------------------------------------------------------------------------
# clear_csrf_cookie
# ---------------------------------------------------------------------------


class TestClearCsrfCookie:
    """``clear_csrf_cookie`` emits an empty value with ``Max-Age=0``."""

    @pytest.mark.parametrize("environment", ALL_ENVIRONMENTS)
    def test_zero_max_age_and_empty_value(self, environment: Environment) -> None:
        response = Response()
        clear_csrf_cookie(response, settings=_build_settings(environment))
        header = _set_cookie_header_for(response, CSRF_NAME)
        name, value, attrs = _parse_set_cookie(header)

        assert name == CSRF_NAME
        assert value == ""
        assert attrs.get("max-age") == "0"
        # CSRF cookie remains non-HttpOnly even when cleared so the
        # browser overwrites the JS-readable value with an empty
        # string consistently (no asymmetry between set and clear).
        assert "httponly" not in attrs
        _assert_common_attrs(
            attrs,
            expect_secure=environment in SECURE_ENVIRONMENTS,
        )
