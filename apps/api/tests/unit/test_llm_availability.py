"""Unit tests for ``ml/llm/availability.py`` (phase-3-llm-layer task 3.3).

Covers the startup availability contract:

* Key absent or empty → ``initialize_llm_availability`` returns normally
  (the app starts) with ``llm_key_present() is False`` and no credential
  validation attempted (Requirement 1.8).
* Key present + successful ``validate_credentials()`` →
  ``llm_key_present() is True`` (Requirement 1.10).
* Key present + failed validation → :class:`LLMStartupValidationError`
  naming the cause category, never the key value (Requirements 1.9, 1.10).

The provider adapter itself is exercised by ``test_openrouter_adapter``
(task 3.4); here a scripted fake standing in for the ``LLMClient`` protocol
isolates the wiring logic.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from pydantic import SecretStr

from matchlayer_api.config import Settings
from matchlayer_api.ml.llm import availability
from matchlayer_api.ml.llm.availability import (
    LLMStartupValidationError,
    initialize_llm_availability,
    llm_key_present,
)
from matchlayer_api.ml.llm.client import (
    LLMCompletion,
    LLMError,
    LLMRequest,
    LLMStreamChunk,
)

# 33 bytes UTF-8 — clears the 32-byte JWT-secret floor. Same synthetic
# constant the other unit tests use so the value is recognizably a fixture.
_TEST_SECRET = "test-jwt-secret-32-byte-floor-pad"  # gitleaks:allow — synthetic test value

# Recognizably-fake provider key used to assert non-leakage.
_FAKE_LLM_KEY = "sk-test-fake-availability-key"  # gitleaks:allow — synthetic test value

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


def _build_settings(**overrides: Any) -> Settings:
    kwargs: dict[str, Any] = {**_BASE_SETTINGS_KWARGS, **overrides}
    return Settings(**kwargs)


class _FakeLLMClient:
    """Scripted ``LLMClient`` for the startup wiring tests."""

    def __init__(self, validation_error: LLMError | None = None) -> None:
        self.validation_error = validation_error
        self.validate_calls = 0

    async def validate_credentials(self) -> None:
        self.validate_calls += 1
        if self.validation_error is not None:
            raise self.validation_error

    def stream(self, request: LLMRequest) -> AsyncIterator[LLMStreamChunk]:
        raise NotImplementedError

    async def result(self) -> LLMCompletion:
        raise NotImplementedError


@pytest.fixture(autouse=True)
def _reset_availability_state() -> Iterator[None]:
    """Isolate the module-level ``_key_present`` state per test."""
    original = availability._key_present
    yield
    availability._key_present = original


@pytest.mark.asyncio
async def test_key_absent_starts_normally_with_llm_unavailable() -> None:
    """``llm_api_key=None`` → no error, no validation, key not present (1.8)."""
    settings = _build_settings(llm_api_key=None)
    client = _FakeLLMClient()

    await initialize_llm_availability(settings, client=client)

    assert llm_key_present() is False
    assert client.validate_calls == 0


@pytest.mark.asyncio
async def test_key_empty_treated_as_absent() -> None:
    """An empty ``SecretStr`` counts as absent — LLM_Unavailable (1.8)."""
    settings = _build_settings(llm_api_key=SecretStr(""))
    client = _FakeLLMClient()

    await initialize_llm_availability(settings, client=client)

    assert llm_key_present() is False
    assert client.validate_calls == 0


@pytest.mark.asyncio
async def test_key_present_validation_success_records_available() -> None:
    """Key present + validation success → exactly one check, key present (1.10)."""
    settings = _build_settings(llm_api_key=SecretStr(_FAKE_LLM_KEY))
    client = _FakeLLMClient()

    await initialize_llm_availability(settings, client=client)

    assert llm_key_present() is True
    assert client.validate_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("category", ["invalid_key", "unreachable", "timeout"])
async def test_validation_failure_aborts_naming_category_never_key(category: str) -> None:
    """Each failure category aborts startup, message names the category (1.9, 1.10)."""
    settings = _build_settings(llm_api_key=SecretStr(_FAKE_LLM_KEY))
    client = _FakeLLMClient(validation_error=LLMError(category=category))

    with pytest.raises(LLMStartupValidationError) as excinfo:
        await initialize_llm_availability(settings, client=client)

    message = str(excinfo.value)
    assert category in message
    assert _FAKE_LLM_KEY not in message
    # The chained cause must not leak the key either (Requirement 1.9).
    assert _FAKE_LLM_KEY not in str(excinfo.value.__cause__)
    assert llm_key_present() is False
