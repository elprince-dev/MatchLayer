"""Application configuration for the MatchLayer API.

This module exposes a single :class:`Settings` model that loads its values
from the process environment (and the local ``.env`` file when present).
Every other module in the API reads configuration through this object —
direct ``os.environ`` access is forbidden by ``conventions.md``.

Settings are validated at construction time. Missing or malformed required
values raise :class:`pydantic.ValidationError` before the FastAPI app
accepts traffic (Requirement 4.3). The cached :func:`get_settings`
accessor is the canonical entry point for both startup wiring and FastAPI
dependencies (Design §6.2).
"""

from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import (
    AnyHttpUrl,
    Field,
    PostgresDsn,
    RedisDsn,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Re-export for downstream convenience.
__all__ = ["Environment", "LogLevel", "Settings", "get_settings"]

Environment = Literal["development", "staging", "production"]
"""Allowed values for ``MATCHLAYER_ENVIRONMENT``."""

LogLevel = Literal["debug", "info", "warning", "error"]
"""Allowed values for ``MATCHLAYER_LOG_LEVEL``."""


def _find_repo_root_env() -> Path:
    """Resolve the repo-root ``.env`` path independently of the current CWD.

    ``pydantic-settings`` resolves :attr:`SettingsConfigDict.env_file` against
    the process's current working directory. That is fragile in a monorepo:
    the same API is launched from the repo root by ``uvicorn`` and from
    ``apps/api/`` by ``pytest``. Using a CWD-relative ``.env`` makes the
    second invocation silently fail to load the only ``.env`` we ship.

    Walk upward from this module looking for a marker that uniquely
    identifies the repo root (``.env.example`` is committed and lives at
    the repo root by design — see ``.env.example``'s own header comment).
    Fall back to the literal ``".env"`` if no marker is found, preserving
    the historical behavior for any deployment topology that doesn't ship
    an ``.env.example`` (production runtimes inject env vars directly and
    don't need an ``.env`` file at all).
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / ".env.example").exists():
            return parent / ".env"
    return Path(".env")


_REPO_ROOT_ENV: Path = _find_repo_root_env()


class Settings(BaseSettings):
    """Strongly-typed, env-driven configuration for the API.

    Every variable is read with the ``MATCHLAYER_`` prefix; ``.env`` at the
    repo root is consulted for local development. Production deployments
    inject values via the runtime environment (and, eventually, AWS Secrets
    Manager — see ``security.md``).

    Field types are deliberately strict:

    * ``PostgresDsn`` / ``RedisDsn`` reject malformed URLs at startup.
    * ``SecretStr`` keeps the S3 secret out of ``repr()`` and accidental
      log lines.
    * ``Literal`` types reject typos in ``ENVIRONMENT`` / ``LOG_LEVEL``.
    * ``list[AnyHttpUrl]`` validates each CORS origin individually.
    """

    model_config = SettingsConfigDict(
        env_prefix="MATCHLAYER_",
        # Resolved at import time so the same ``.env`` is read whether the
        # process starts from the repo root (``uvicorn``) or from
        # ``apps/api/`` (``pytest`` invoked from inside the API package
        # directory). See ``_find_repo_root_env`` above for the rationale.
        env_file=_REPO_ROOT_ENV,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- runtime ---------------------------------------------------------
    environment: Environment = "development"
    log_level: LogLevel = "info"

    # ---- data plane ------------------------------------------------------
    # Async driver: ``postgresql+asyncpg://...``. Alembic swaps the driver
    # to ``+psycopg`` internally for sync migrations (Design §6.7).
    database_url: PostgresDsn
    redis_url: RedisDsn

    # ---- object storage (MinIO locally, real S3 in production) ----------
    # ``s3_endpoint_url`` is intentionally optional: production leaves it
    # unset so boto3 talks to real AWS S3, while MinIO supplies a non-AWS
    # URL during local development.
    s3_endpoint_url: str | None = None
    s3_region: str = "us-east-1"
    s3_access_key_id: str
    s3_secret_access_key: SecretStr
    s3_bucket: str

    # ---- HTTP boundary ---------------------------------------------------
    # Comma-separated in ``.env`` for ergonomics; coerced into a typed list
    # of validated http(s) URLs so the CORS middleware never sees raw
    # strings. ``NoDecode`` disables ``pydantic-settings``'s automatic JSON
    # parsing for this field so the validator below can accept both shapes.
    cors_allowed_origins: Annotated[list[AnyHttpUrl], NoDecode] = Field(default_factory=list)

    # ---- authentication (phase-1-auth §17.1) -----------------------------
    jwt_secret: SecretStr
    auth_access_token_ttl_seconds: int = 900
    auth_refresh_token_ttl_seconds: int = 604800
    auth_lockout_threshold: int = 10
    auth_lockout_window_seconds: int = 900
    auth_lockout_duration_seconds: int = 900
    web_base_url: AnyHttpUrl = AnyHttpUrl("http://localhost:3000")
    database_app_role: str = "matchlayer_app"
    # Password used by the integration test suite (and any local
    # tooling) to open a connection authenticated as
    # ``database_app_role`` for INV-1 audit-grant verification
    # (task 16.4). The dev-stack value mirrors the password the
    # ``infra/docker/postgres-init/01-create-app-role.sql`` bootstrap
    # script seeds; production deployments inject a Secrets Manager-
    # backed value.
    database_app_role_password: SecretStr = SecretStr("dev_only_app_role_password")

    # ---- rate limiting (phase-1-auth §17.1) ------------------------------
    auth_rate_limit_register_ip_limit: int = 10
    auth_rate_limit_register_ip_window_seconds: int = 900
    auth_rate_limit_login_email_limit: int = 10
    auth_rate_limit_login_email_window_seconds: int = 900
    auth_rate_limit_login_ip_limit: int = 50
    auth_rate_limit_login_ip_window_seconds: int = 900
    auth_rate_limit_refresh_ip_limit: int = 60
    auth_rate_limit_refresh_ip_window_seconds: int = 60
    auth_rate_limit_reset_request_email_limit: int = 5
    auth_rate_limit_reset_request_email_window_seconds: int = 3600
    auth_rate_limit_reset_request_ip_limit: int = 20
    auth_rate_limit_reset_request_ip_window_seconds: int = 3600
    auth_rate_limit_reset_confirm_ip_limit: int = 20
    auth_rate_limit_reset_confirm_ip_window_seconds: int = 3600

    # ---- resume upload & extraction bounds (phase-1-matching §"Settings
    #      additions") ----------------------------------------------------
    # Hard upload size ceiling enforced at the router before any object
    # write (Requirement 2.2 → 413 ``payload_too_large``). 5 MiB.
    resume_max_bytes: int = 5_242_880
    # DOCX zip-bomb guards (Requirement 2.4 → 422 ``malformed_upload``):
    # total uncompressed size (50 MiB) and entry-count ceilings checked
    # via stdlib ``zipfile`` before extraction.
    resume_max_decompressed_bytes: int = 52_428_800
    resume_max_archive_entries: int = 256
    # Wall-clock bound on synchronous in-request extraction (Requirement
    # 3.2) and the retained-character ceiling extracted text is truncated
    # to (Requirement 3.3).
    resume_extraction_timeout_seconds: int = 15
    resume_max_extracted_chars: int = 200_000

    # ---- job-description input bounds (phase-1-matching §11.1) -----------
    # Trimmed length window enforced by the match request validator
    # (Requirement 8.3 → 422 ``validation_error``).
    jd_min_chars: int = 30
    jd_max_chars: int = 50_000

    # ---- scoring output bounds (phase-1-matching §6, §7) -----------------
    # Caps on the analyzed keyword set (Requirement 6.1) and generated
    # suggestions (Requirement 7.1).
    match_max_keywords: int = 50
    match_max_suggestions: int = 10

    # ---- score weights (phase-1-matching §5) -----------------------------
    # Fixed similarity/keyword-coverage blend weights. The
    # ``_score_weights_sum_to_one`` validator below asserts they sum to
    # 1.0 at startup (Requirement 5.3), failing fast like the JWT-secret
    # length floor.
    score_weight_similarity: float = 0.6
    score_weight_keyword: float = 0.4

    # ---- per-user rate limits & daily quotas (phase-1-matching §11) ------
    # Per-minute sliding-window limits (Requirement 11.1, 11.2) and
    # per-UTC-day quotas (Requirement 11.4, 11.5 → 429 ``quota_exceeded``).
    resume_rate_limit_per_min: int = 10
    match_rate_limit_per_min: int = 20
    resume_daily_quota: int = 20
    match_daily_quota: int = 50

    # ---- validators ------------------------------------------------------

    @field_validator("jwt_secret")
    @classmethod
    def _jwt_secret_min_length(cls, v: SecretStr) -> SecretStr:
        """Reject secrets shorter than 32 bytes UTF-8 at startup."""
        byte_len = len(v.get_secret_value().encode("utf-8"))
        if byte_len < 32:
            raise ValueError(
                "MATCHLAYER_JWT_SECRET must be at least 32 bytes when "
                f"UTF-8 encoded; received {byte_len} bytes"
            )
        return v

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def _split_cors_origins(cls, value: object) -> object:
        """Accept either a JSON array or a comma-separated string from env.

        ``pydantic-settings`` parses ``list`` fields as JSON by default; the
        ``NoDecode`` annotation on the field disables that for CORS so this
        validator owns the parsing. Both shapes are accepted so the
        committed ``.env.example`` can use the more familiar comma-separated
        form (``http://localhost:3000,https://app.example.com``) while
        operators who prefer a JSON array (``["http://localhost:3000"]``)
        get the same result.
        """
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return []
            if stripped.startswith("["):
                # JSON array form: parse here since NoDecode disabled
                # pydantic-settings's automatic JSON decoding.
                return json.loads(stripped)
            return [item.strip() for item in stripped.split(",") if item.strip()]
        return value

    @model_validator(mode="after")
    def _score_weights_sum_to_one(self) -> Settings:
        """Reject score weights that do not sum to 1.0 at startup.

        The final score is a convex blend of the similarity and
        keyword-coverage components (Requirement 5.3); weights that do not
        sum to exactly 1.0 would silently distort every score and break the
        ``0..100`` guarantee. Failing fast here mirrors the JWT-secret
        length floor: a misconfiguration raises ``ValidationError`` before
        the app accepts traffic rather than producing wrong scores in
        production.

        ``math.isclose`` with a tight absolute tolerance accommodates IEEE
        754 representation error (e.g. ``0.6 + 0.4`` is not bit-exactly
        ``1.0``) without admitting weights that are meaningfully off.
        """
        total = self.score_weight_similarity + self.score_weight_keyword
        if not math.isclose(total, 1.0, abs_tol=1e-9):
            raise ValueError(
                "MATCHLAYER_SCORE_WEIGHT_SIMILARITY + "
                "MATCHLAYER_SCORE_WEIGHT_KEYWORD must sum to 1.0; "
                f"received {self.score_weight_similarity} + "
                f"{self.score_weight_keyword} = {total}"
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide :class:`Settings` instance.

    Cached so repeated FastAPI dependency resolution does not re-parse the
    environment on every request. Tests that need to override values
    should either clear the cache via ``get_settings.cache_clear()`` or,
    preferably, use FastAPI's dependency override mechanism.
    """
    # ``Settings()`` populates required fields from the environment via
    # pydantic-settings at runtime; the ``pydantic.mypy`` plugin handles
    # the env-driven init shape so no ``# type: ignore[call-arg]`` is
    # needed here. A missing env var raises ``ValidationError`` at
    # construction, which is the fail-fast behavior Requirement 4.3
    # mandates.
    return Settings()
