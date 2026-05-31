"""Unit tests for the phase-1-matching configuration, error types, and env contract.

Covers task 1.5 of the ``phase-1-matching`` spec — the three foundation
contracts wave 0 landed in ``config.py``, ``core/errors.py``, and
``.env.example``:

* **Score-weight validator (Requirement 5.3).** ``Settings`` carries a
  ``model_validator`` that asserts
  ``score_weight_similarity + score_weight_keyword == 1.0`` at startup,
  failing fast like the existing JWT-secret length floor. These tests
  pin both directions: the documented defaults (``0.6 + 0.4``) build a
  ``Settings`` instance, and any pair that does not sum to ``1.0`` raises
  :class:`pydantic.ValidationError` before the app can accept traffic.

* **New RFC 7807 error subclasses (Requirements 1.5, 1.6, 2.2, 2.4, 8.5,
  11.6).** Each of the six new :class:`MatchLayerError` subclasses pins a
  ``status_code`` / ``error_type`` / ``title`` so that raising it with
  only a ``detail`` string yields the right envelope through the already
  registered ``matchlayer_error_handler``. These tests assert both the
  class-level attributes and the end-to-end serialized
  ``{type, title, detail, status, request_id}`` body, mirroring the
  foundation's ``tests/test_errors.py`` round-trip style.

* **Environment-variable contract (Requirement 14.9).** The foundation's
  ``tools/check_env_drift.py`` drift check must pass against the updated
  ``.env.example`` — every ``MATCHLAYER_*`` field the matching ``Settings``
  declares is present, and nothing is stale.

The tests construct :class:`Settings` directly with explicit kwargs (rather
than leaning on the repo ``.env``) so the cases are hermetic and the
``score_weight_*`` fields can be parameterized per case.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import structlog
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from matchlayer_api.config import Settings
from matchlayer_api.core.errors import (
    MalformedUploadError,
    MatchLayerError,
    NotFoundError,
    PayloadTooLargeError,
    QuotaExceededError,
    ResumeNotExtractableError,
    UnsupportedMediaTypeError,
    register_exception_handlers,
)
from matchlayer_api.core.middleware import RequestIdMiddleware

# 33 bytes UTF-8 — clears the 32-byte floor in
# ``Settings._jwt_secret_min_length``. Reuses the same synthetic constant
# the auth unit tests use so the value is recognizably a test fixture and
# the suite never depends on a repo ``.env`` being present.
_TEST_SECRET = "test-jwt-secret-32-byte-floor-pad"  # gitleaks:allow — synthetic test value

# Every field ``Settings`` requires, with placeholder values that pass
# Pydantic validation (DSN shape, secret-length floor) without touching the
# repo's ``.env``. The ``score_weight_*`` fields are intentionally omitted
# here so each test supplies the pair under test (or relies on the defaults).
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
}


@pytest.fixture(autouse=True)
def _clear_contextvars() -> Iterator[None]:
    """Reset structlog contextvars around every test.

    The error-envelope round-trips bind a ``request_id`` via
    :class:`RequestIdMiddleware`; a hard reset keeps one test's bound
    contextvars from bleeding into another's assertions.
    """
    structlog.contextvars.clear_contextvars()
    yield
    structlog.contextvars.clear_contextvars()


# ---------------------------------------------------------------------------
# Score-weight validator (Requirement 5.3)
# ---------------------------------------------------------------------------


def test_weight_validator_accepts_documented_defaults() -> None:
    """The default ``0.6 + 0.4`` weights build a ``Settings`` instance.

    Omitting both weights exercises the field defaults; the validator must
    accept them since ``0.6 + 0.4`` is (within IEEE-754 tolerance) ``1.0``.
    """
    settings = Settings(**_BASE_SETTINGS_KWARGS)

    assert settings.score_weight_similarity == pytest.approx(0.6)
    assert settings.score_weight_keyword == pytest.approx(0.4)


def test_weight_validator_accepts_non_default_pair_summing_to_one() -> None:
    """A non-default pair that still sums to ``1.0`` is accepted.

    Guards against a validator that hard-codes the defaults instead of
    checking the sum.
    """
    settings = Settings(
        **_BASE_SETTINGS_KWARGS,
        score_weight_similarity=0.75,
        score_weight_keyword=0.25,
    )

    assert settings.score_weight_similarity == pytest.approx(0.75)
    assert settings.score_weight_keyword == pytest.approx(0.25)


@pytest.mark.parametrize(
    ("w_similarity", "w_keyword"),
    [
        (0.5, 0.4),  # sums to 0.9 — under
        (0.7, 0.4),  # sums to 1.1 — over
        (0.6, 0.6),  # sums to 1.2 — over
        (0.0, 0.0),  # sums to 0.0 — degenerate
        (1.0, 1.0),  # sums to 2.0 — degenerate
    ],
)
def test_weight_validator_rejects_pairs_not_summing_to_one(
    w_similarity: float, w_keyword: float
) -> None:
    """Any weight pair whose sum is not ``1.0`` raises at construction.

    The misconfiguration must surface as a :class:`pydantic.ValidationError`
    (fail-fast at startup) rather than silently distorting every score.
    """
    with pytest.raises(ValidationError) as excinfo:
        Settings(
            **_BASE_SETTINGS_KWARGS,
            score_weight_similarity=w_similarity,
            score_weight_keyword=w_keyword,
        )

    # The validator's message names both weight env vars so an operator can
    # find the misconfiguration from the error alone.
    message = str(excinfo.value)
    assert "MATCHLAYER_SCORE_WEIGHT_SIMILARITY" in message
    assert "MATCHLAYER_SCORE_WEIGHT_KEYWORD" in message


# ---------------------------------------------------------------------------
# New RFC 7807 error subclasses (Requirements 1.5, 1.6, 2.2, 2.4, 8.5, 11.6)
# ---------------------------------------------------------------------------

# (subclass, expected error_type, expected status_code, expected title).
_ERROR_CASES: list[tuple[type[MatchLayerError], str, int, str]] = [
    (NotFoundError, "not_found", 404, "Not Found"),
    (PayloadTooLargeError, "payload_too_large", 413, "Payload Too Large"),
    (UnsupportedMediaTypeError, "unsupported_media_type", 415, "Unsupported Media Type"),
    (MalformedUploadError, "malformed_upload", 422, "Malformed Upload"),
    (ResumeNotExtractableError, "resume_not_extractable", 422, "Resume Not Extractable"),
    (QuotaExceededError, "quota_exceeded", 429, "Quota Exceeded"),
]


@pytest.mark.parametrize(
    ("error_cls", "error_type", "status_code", "title"),
    _ERROR_CASES,
    ids=[case[1] for case in _ERROR_CASES],
)
def test_error_subclass_class_attributes(
    error_cls: type[MatchLayerError], error_type: str, status_code: int, title: str
) -> None:
    """Each subclass pins ``status_code`` / ``error_type`` / ``title``.

    Asserting on a constructed instance (not just the class) confirms the
    attributes survive ``MatchLayerError.__init__`` when only a ``detail``
    string is supplied.
    """
    instance = error_cls("synthetic detail")

    assert isinstance(instance, MatchLayerError)
    assert instance.error_type == error_type
    assert instance.status_code == status_code
    assert instance.title == title
    assert instance.detail == "synthetic detail"


def _build_app_raising(exc: MatchLayerError) -> FastAPI:
    """Build a FastAPI app whose ``/raise`` route raises ``exc``.

    Wires :class:`RequestIdMiddleware` and the real
    :func:`register_exception_handlers` so the response round-trips through
    the registered ``matchlayer_error_handler`` exactly as it would in
    production.
    """
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)
    register_exception_handlers(app, settings=Settings(**_BASE_SETTINGS_KWARGS))

    @app.get("/raise")
    async def _raise() -> None:
        raise exc

    return app


@pytest.mark.parametrize(
    ("error_cls", "error_type", "status_code", "title"),
    _ERROR_CASES,
    ids=[case[1] for case in _ERROR_CASES],
)
async def test_error_subclass_serializes_to_rfc7807_envelope(
    error_cls: type[MatchLayerError], error_type: str, status_code: int, title: str
) -> None:
    """Raising a subclass yields the canonical RFC 7807 envelope.

    Drives the registered handler end-to-end and asserts the full
    ``{type, title, detail, status, request_id}`` body plus the matching
    HTTP status code.
    """
    detail = f"synthetic {error_type} detail"
    app = _build_app_raising(error_cls(detail))
    transport = ASGITransport(app=app, raise_app_exceptions=False)

    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/raise", headers={"X-Request-Id": "envelope-canary"})

    assert response.status_code == status_code
    assert response.json() == {
        "type": error_type,
        "title": title,
        "detail": detail,
        "status": status_code,
        "request_id": "envelope-canary",
    }


# ---------------------------------------------------------------------------
# Environment-variable contract (Requirement 14.9)
# ---------------------------------------------------------------------------

# This file lives at apps/api/tests/unit/test_*.py; the repo root is four
# parents up (unit -> tests -> api -> apps -> root).
_REPO_ROOT = Path(__file__).resolve().parents[4]
_ENV_DRIFT_CHECK = _REPO_ROOT / "tools" / "check_env_drift.py"


def _load_env_drift_module() -> Any:
    """Import ``tools/check_env_drift.py`` by file path.

    Loading by path (rather than by module name) avoids polluting
    ``sys.path`` with the repo-root ``tools/`` directory and keeps the test
    independent of the working directory pytest happens to run from.
    """
    spec = importlib.util.spec_from_file_location("_check_env_drift", _ENV_DRIFT_CHECK)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_env_drift_check_script_exists() -> None:
    """Sanity check: the foundation drift-check script resolves to a real file.

    If the repo layout shifts, this fails with a focused message instead of
    an opaque import error inside :func:`test_env_example_has_no_drift`.
    """
    assert _ENV_DRIFT_CHECK.is_file(), (
        f"Expected the env-drift check at {_ENV_DRIFT_CHECK}; the matching "
        f".env.example contract cannot be verified without it."
    )


def test_env_example_has_no_drift() -> None:
    """The foundation ``.env`` drift check passes against the updated contract.

    ``main()`` returns ``0`` when ``.env.example`` and the codebase agree
    (every referenced ``MATCHLAYER_*`` variable is declared and none is
    stale) and ``1`` on any drift. Asserting ``0`` confirms the matching
    ``Settings`` fields added in task 1.1 each gained a ``.env.example``
    entry in task 1.3 and that no entry is unused (Requirement 14.9).
    """
    module = _load_env_drift_module()

    exit_code = module.main()

    assert exit_code == 0, (
        "tools/check_env_drift.py reported drift between .env.example and the "
        "codebase; see the captured stdout/stderr above for the offending vars."
    )
