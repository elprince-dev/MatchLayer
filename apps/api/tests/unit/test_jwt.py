"""Unit tests for ``core/security/jwt.py``.

Pins down four contracts the rest of the auth surface depends on:

1. **Claim shape.** Every issued token carries exactly the five
   claims ``{sub, iat, exp, jti, type}`` and nothing else —
   Requirement 7.4 / Design §6.1. Pinned with a strict set
   comparison so a future change that quietly adds a sixth claim
   (e.g. ``email``) fails this test before it can land in
   production. The ``no email / no PII`` companion test anchors
   Requirement 7.8.
2. **``expected_type`` enforcement.** ``verify_token`` raises
   :class:`InvalidTokenError` when the token's ``type`` claim does
   not match the caller's ``expected_type`` argument — Requirement
   7.6. Both directions are covered: an access token is rejected
   when the caller asks for a refresh token, and a refresh token
   is rejected when the caller asks for an access token.
3. **Algorithm allowlist.** Hand-crafted tokens with ``alg=none``
   and ``alg=HS512`` (signed correctly with the configured secret)
   are rejected — Requirement 7.3 / Design §6.3. Hand-crafting the
   header is the only way to test this honestly: PyJWT's own
   ``encode`` refuses to mint ``alg=none`` without an opt-in flag,
   so we build the bytes by hand and confirm the verifier's
   allowlist gate is what stops the attack.
4. **Secret-length floor.** Constructing :class:`Settings` with a
   31-byte ``jwt_secret`` raises :class:`pydantic.ValidationError`
   — Requirement 7.7 / Design §6.4. The boundary case (32 bytes is
   accepted) and the multi-byte-UTF-8 case (the floor counts bytes,
   not codepoints) are pinned alongside.

The tests drive the module surface directly. The
``_override_jwt_settings`` autouse fixture replaces the module-level
:func:`get_settings` binding inside the JWT wrapper so every test
runs against a deterministic in-memory :class:`Settings` and never
touches the repo's ``.env``. Hand-crafted tokens use
:mod:`base64`/``hmac``/``json`` from the stdlib so the test prose
shows the wire format we're asserting on.

References:
* Requirements 7.3, 7.4, 7.6, 7.7, 7.8.
* Design §6.1 (claim shape), §6.3 (algorithm allowlist), §6.4
  (secret-length floor).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any
from uuid import uuid4

import jwt as pyjwt
import pytest
from pydantic import ValidationError

from matchlayer_api.config import Settings
from matchlayer_api.core.security import jwt as jwt_module
from matchlayer_api.core.security.jwt import (
    InvalidTokenError,
    issue_access_token,
    issue_refresh_token,
    verify_token,
)

# ---------------------------------------------------------------------------
# Settings construction
#
# Tests build :class:`Settings` directly so they don't depend on the
# repo's ``.env`` and so the ``jwt_secret`` field can be parameterized
# per case. All other required fields take placeholder values that pass
# Pydantic validation (URL shape, secret length floor) without touching
# any external service.
# ---------------------------------------------------------------------------

# 33 bytes UTF-8 — clears the 32-byte floor in
# ``Settings._jwt_secret_min_length``. The value is a deliberate
# constant rather than a randomly generated one so two test runs of
# the same suite produce byte-identical tokens, which keeps test-
# failure debugging trivial.
_TEST_SECRET = "test-jwt-secret-32-byte-floor-pad"  # gitleaks:allow — synthetic test value

_BASE_SETTINGS_KWARGS: dict[str, Any] = {
    "environment": "development",
    "log_level": "info",
    "database_url": "postgresql+asyncpg://u:p@localhost:5432/db",
    "redis_url": "redis://localhost:6379/0",
    "s3_endpoint_url": None,
    "s3_region": "us-east-1",
    "s3_access_key_id": "test",
    "s3_secret_access_key": "test",
    "s3_bucket": "test-bucket",
    "cors_allowed_origins": [],
    "jwt_secret": _TEST_SECRET,
    # Default TTLs from §17.1 — pinned here so the claim-shape tests
    # observe the documented values rather than whatever the default
    # happens to be when the test runs.
    "auth_access_token_ttl_seconds": 900,
    "auth_refresh_token_ttl_seconds": 604800,
}


@pytest.fixture
def settings() -> Settings:
    """A :class:`Settings` instance built from :data:`_BASE_SETTINGS_KWARGS`.

    Every other test fixture and the autouse override depends on this
    one so the assembled kwargs are the single source of truth.
    """
    return Settings(**_BASE_SETTINGS_KWARGS)


@pytest.fixture(autouse=True)
def _override_jwt_settings(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    """Make ``jwt.get_settings()`` return our test :class:`Settings`.

    The wrapper module imports the accessor with
    ``from matchlayer_api.config import get_settings``, so the
    function is bound at module level. Replacing that binding for the
    duration of the test is sufficient: every call inside
    :func:`issue_access_token`, :func:`issue_refresh_token`, and
    :func:`verify_token` resolves through it.

    Using ``monkeypatch`` instead of clearing
    :func:`get_settings.cache_clear` means tests do not depend on
    whatever happens to be in the repo-level ``.env`` and do not
    pollute the cached settings instance other tests may have warmed.
    """
    monkeypatch.setattr(jwt_module, "get_settings", lambda: settings)


# ---------------------------------------------------------------------------
# Hand-crafted-token helpers
#
# PyJWT 2.x's :func:`jwt.encode` refuses to mint ``alg=none`` tokens
# unless the caller opts in with ``algorithm="none"`` *and* the
# library version supports the unsafe path; the safer (and more
# self-documenting) move is to build the wire format ourselves. The
# helpers below produce a real, parseable token whose header carries
# the ``alg`` value the test wants, signed correctly for that
# algorithm. ``verify_token`` is therefore the only line of defence
# the assertion is exercising.
# ---------------------------------------------------------------------------


def _b64url(data: bytes) -> str:
    """Base64url-encode ``data`` without ``=`` padding (RFC 7515 §2)."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _craft_token(
    *,
    header: dict[str, Any],
    payload: dict[str, Any],
    secret: str | None,
) -> str:
    """Build a JWT with arbitrary ``header.alg`` and a correct signature.

    For ``alg=none`` the signature segment is empty (the canonical
    on-the-wire shape: ``"h.p."``). For ``alg=HS512`` the signature
    is computed with HMAC-SHA512 over the ASCII signing input
    ``"h.p"`` so the only thing differentiating a valid HS256 token
    from this one is the ``alg`` header — which is precisely what
    the allowlist assertion targets.
    """
    h = _b64url(json.dumps(header, separators=(",", ":")).encode())
    p = _b64url(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{h}.{p}".encode()
    alg = header["alg"]
    if alg == "none":
        sig = b""
    elif alg == "HS512":
        assert secret is not None, "HS512 requires the configured secret"
        sig = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha512).digest()
    else:
        raise NotImplementedError(f"_craft_token does not support alg={alg!r}")
    return f"{h}.{p}.{_b64url(sig)}"


# ---------------------------------------------------------------------------
# 1. Claim shape — Requirement 7.4 / Design §6.1
# ---------------------------------------------------------------------------


class TestClaimShape:
    """Issued tokens carry exactly ``{sub, iat, exp, jti, type}``."""

    # Strict equality with this set is the contract: a sixth claim
    # (``email``, ``role``, ``display_name``, anything) must fail this
    # test before landing.
    _EXPECTED_CLAIMS: frozenset[str] = frozenset({"sub", "iat", "exp", "jti", "type"})

    def test_access_token_carries_only_the_five_claims(self) -> None:
        """Every claim on an access token is one of the five — and all five are present.

        Decoded with ``verify_signature=False`` because the assertion
        is on the payload shape, not on the signature; the signature
        is exercised by :class:`TestExpectedTypeEnforcement` and
        :class:`TestAlgorithmAllowlist` below.
        """
        token = issue_access_token(sub="alice-user-id")
        payload = pyjwt.decode(token, options={"verify_signature": False})
        assert set(payload.keys()) == self._EXPECTED_CLAIMS
        assert payload["sub"] == "alice-user-id"
        assert payload["type"] == "access"
        # ``exp - iat`` matches the configured TTL — Design §6.2.
        assert payload["exp"] - payload["iat"] == 900

    def test_refresh_token_carries_only_the_five_claims(self) -> None:
        """Same shape contract for refresh tokens; ``type=refresh`` and the caller-supplied jti.

        The refresh issuer takes a caller-supplied ``jti`` because the
        Auth_Service writes a row to ``refresh_tokens`` keyed on that
        value before issuing the token (Design §7). The string form
        is what lands in the JWT.
        """
        jti = uuid4()
        token = issue_refresh_token(sub="bob-user-id", jti=jti)
        payload = pyjwt.decode(token, options={"verify_signature": False})
        assert set(payload.keys()) == self._EXPECTED_CLAIMS
        assert payload["sub"] == "bob-user-id"
        assert payload["type"] == "refresh"
        assert payload["jti"] == str(jti)
        assert payload["exp"] - payload["iat"] == 604800

    def test_no_email_or_other_pii_in_payload(self) -> None:
        """Requirement 7.8 anchor: no email, password_hash, or PII claims.

        The strict-set test above already excludes anything outside
        the five whitelisted claims; this test pins the explicit
        absence of common PII keys so a regression is named in the
        failure output rather than buried under a set-difference.
        """
        token = issue_access_token(sub="charlie-user-id")
        payload = pyjwt.decode(token, options={"verify_signature": False})
        for forbidden in ("email", "password_hash", "name", "display_name", "username"):
            assert forbidden not in payload, (
                f"Claim {forbidden!r} must never appear in an issued token (Requirement 7.8)"
            )


# ---------------------------------------------------------------------------
# 2. ``expected_type`` enforcement — Requirement 7.6
# ---------------------------------------------------------------------------


class TestExpectedTypeEnforcement:
    """``verify_token`` rejects when ``type`` claim != ``expected_type``."""

    def test_access_token_rejected_when_refresh_expected(self) -> None:
        """An access token presented at the refresh endpoint is rejected.

        This is the path the Auth_Router takes on every refresh: the
        server reads the cookie, calls
        ``verify_token(token, expected_type="refresh")``, and gets
        :class:`InvalidTokenError` if a client somehow swaps an
        access token into the cookie. Without this gate, a leaked
        access token could be used as if it were a refresh token.
        """
        token = issue_access_token(sub="alice-user-id")
        with pytest.raises(InvalidTokenError):
            verify_token(token, expected_type="refresh")

    def test_refresh_token_rejected_when_access_expected(self) -> None:
        """A refresh token presented as a Bearer access token is rejected.

        Symmetric to the case above: ``get_current_user`` calls
        ``verify_token(..., expected_type="access")``, so a refresh
        token cannot impersonate an access token even though both
        carry the same ``sub`` and the same secret-derived signature.
        """
        token = issue_refresh_token(sub="alice-user-id", jti=uuid4())
        with pytest.raises(InvalidTokenError):
            verify_token(token, expected_type="access")

    def test_access_token_accepted_when_access_expected(self) -> None:
        """Round-trip happy path for the access type.

        Pairs with the rejection cases so the test class pins both
        directions of the gate: a matching ``type`` returns the
        decoded payload; a mismatching ``type`` raises.
        """
        token = issue_access_token(sub="alice-user-id")
        payload = verify_token(token, expected_type="access")
        assert payload["type"] == "access"
        assert payload["sub"] == "alice-user-id"

    def test_refresh_token_accepted_when_refresh_expected(self) -> None:
        """Round-trip happy path for the refresh type."""
        jti = uuid4()
        token = issue_refresh_token(sub="alice-user-id", jti=jti)
        payload = verify_token(token, expected_type="refresh")
        assert payload["type"] == "refresh"
        assert payload["sub"] == "alice-user-id"
        assert payload["jti"] == str(jti)


# ---------------------------------------------------------------------------
# 3. Algorithm allowlist — Requirement 7.3 / Design §6.3
# ---------------------------------------------------------------------------


class TestAlgorithmAllowlist:
    """``alg=none`` and ``alg=HS512`` are rejected even when otherwise well-formed."""

    @staticmethod
    def _well_formed_payload() -> dict[str, Any]:
        """A claim set that would pass every check *except* the algorithm gate.

        Built fresh per test so ``iat`` is a current Unix timestamp
        and ``exp`` is comfortably in the future. The ``type`` is
        ``access`` so the test could not be passing because of the
        ``expected_type`` gate.
        """
        now = int(time.time())
        return {
            "sub": "evil-attacker-id",
            "iat": now,
            "exp": now + 900,
            "jti": str(uuid4()),
            "type": "access",
        }

    def test_alg_none_rejected(self) -> None:
        """A token with ``{"alg": "none"}`` and an empty signature segment is rejected.

        The unsigned-JWT attack: an attacker who knows the claim
        shape can hand-write a header/payload pair, append an empty
        third segment, and present the result. PyJWT 2.x rejects
        because ``"none"`` is not in the allowlist passed to
        :func:`jwt.decode` (``algorithms=["HS256"]``); the JWT_Service
        wraps that as :class:`InvalidTokenError`.
        """
        token = _craft_token(
            header={"alg": "none", "typ": "JWT"},
            payload=self._well_formed_payload(),
            secret=None,
        )
        with pytest.raises(InvalidTokenError):
            verify_token(token, expected_type="access")

    def test_alg_hs512_rejected_even_with_correct_signature(self) -> None:
        """An HS512-signed token using the configured secret is still rejected.

        HS512 is a perfectly-valid HMAC algorithm; the rejection here
        is on the *allowlist*, not on the signature. Signing with the
        same secret the JWT_Service uses for HS256 makes the test
        explicit: even an attacker who has access to the secret
        cannot escalate the algorithm because PyJWT verifies with
        ``algorithms=["HS256"]`` and treats a foreign ``alg`` as a
        verification failure. This is the structural defence against
        algorithm-confusion attacks (Design §6.3, **PBT-2**).
        """
        token = _craft_token(
            header={"alg": "HS512", "typ": "JWT"},
            payload=self._well_formed_payload(),
            secret=_TEST_SECRET,
        )
        with pytest.raises(InvalidTokenError):
            verify_token(token, expected_type="access")


# ---------------------------------------------------------------------------
# 4. Secret-length floor — Requirement 7.7 / Design §6.4
# ---------------------------------------------------------------------------


class TestSecretLengthFloor:
    """:class:`Settings` rejects ``jwt_secret`` shorter than 32 bytes UTF-8."""

    def test_31_byte_secret_raises_validation_error(self) -> None:
        """31 bytes is below the floor and is rejected at construction time.

        Per Requirement 7.7 the API is supposed to fail to start with
        a sub-floor secret. Construction failure inside ``Settings``
        is exactly that signal: ``create_app()`` calls
        ``get_settings()``, which calls ``Settings()``, which raises
        before uvicorn binds the socket. The validator's structured
        message must name both the violated minimum (32) and the
        received byte count (31), and must NOT echo the secret value
        (security baseline: never log secrets).

        The assertion targets ``ValidationError.errors()[i]["msg"]``
        rather than ``str(excinfo.value)`` because Pydantic's
        top-level repr always appends an ``input_value=...`` chunk
        for diagnostics (`pydantic.dev/.../value_error`); the
        ``msg`` field is the validator's own string and is what
        ``errors.py`` and the startup logger format into the log
        line under Requirement 7.7. Asserting on the structured
        field also matches how a future logging filter would redact
        the value: it filters on ``input_value``, not on ``msg``.
        """
        too_short = "x" * 31
        assert len(too_short.encode("utf-8")) == 31, (
            "Test setup: ASCII 'x' is 1 byte/codepoint so 31 chars == 31 bytes."
        )
        kwargs = {**_BASE_SETTINGS_KWARGS, "jwt_secret": too_short}
        with pytest.raises(ValidationError) as excinfo:
            Settings(**kwargs)

        errors = excinfo.value.errors()
        # Find the structured error attached to the ``jwt_secret``
        # field; there's only one in this case but the locator is
        # explicit so a future failure of an unrelated field doesn't
        # mask this one.
        jwt_errors = [e for e in errors if e.get("loc") == ("jwt_secret",)]
        assert len(jwt_errors) == 1, (
            f"Expected exactly one validation error on 'jwt_secret'; got: {errors!r}"
        )
        validator_msg = jwt_errors[0]["msg"]
        # The validator message names the violated minimum and the
        # received byte count (Design §6.4 / config.py validator).
        assert "32" in validator_msg, (
            f"Validator message must name the minimum byte count; got: {validator_msg!r}"
        )
        assert "31" in validator_msg, (
            f"Validator message must name the received byte count; got: {validator_msg!r}"
        )
        # The secret value itself MUST NOT appear in the validator
        # message — that's the string the structured logger formats
        # into the startup-failure log line (Requirement 7.7 /
        # security.md "Logging — never log JWT tokens / signing keys").
        assert too_short not in validator_msg, (
            "Secret value must not appear in the validator message"
        )

    def test_32_byte_secret_accepted_at_the_boundary(self) -> None:
        """Exactly 32 bytes is accepted.

        Boundary case paired with the 31-byte rejection: together
        they pin the comparison as ``byte_len < 32`` rejects, ``>= 32``
        accepts.
        """
        boundary = "x" * 32
        assert len(boundary.encode("utf-8")) == 32
        kwargs = {**_BASE_SETTINGS_KWARGS, "jwt_secret": boundary}
        settings = Settings(**kwargs)
        # Confirms the value round-trips through SecretStr unchanged.
        assert settings.jwt_secret.get_secret_value() == boundary

    def test_floor_counts_bytes_not_codepoints(self) -> None:
        """Multi-byte secret of 30 bytes (15 codepoints) rejected; 32 bytes (16) accepted.

        ``ñ`` (U+00F1) is 2 bytes in UTF-8, so 15 of them weigh 30
        bytes — under the floor — even though the codepoint count
        is closer to the typical password-length intuition. Pinning
        this here guards against a future refactor that switches the
        validator to ``len(plaintext)`` (codepoints) instead of the
        ``len(plaintext.encode("utf-8"))`` (bytes) the design specifies
        (Design §6.4).
        """
        accepted = "ñ" * 16  # 32 bytes UTF-8
        rejected = "ñ" * 15  # 30 bytes UTF-8
        assert len(accepted.encode("utf-8")) == 32
        assert len(rejected.encode("utf-8")) == 30

        # 32-byte multi-byte secret: accepted.
        Settings(**{**_BASE_SETTINGS_KWARGS, "jwt_secret": accepted})

        # 30-byte multi-byte secret: rejected.
        with pytest.raises(ValidationError):
            Settings(**{**_BASE_SETTINGS_KWARGS, "jwt_secret": rejected})
