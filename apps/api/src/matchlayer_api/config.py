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
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import AnyHttpUrl, Field, PostgresDsn, RedisDsn, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

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
