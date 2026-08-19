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
from decimal import Decimal
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

    # ---- semantic pipeline (phase-2-nlp-embeddings §10) -------------------
    # Pinned Sentence Transformers model identity (Requirement 2.2). The
    # name+revision pair makes every stored Embedding reproducible and
    # drives the reuse-versus-regenerate decision at match time
    # (Requirement 2.7, 2.10). These values are read only by the
    # ML_Adapter and injected into the Scoring_Core (Requirement 12.5).
    embedding_model_name: str = "sentence-transformers/all-MiniLM-L6-v2"
    # Pinned Hugging Face commit SHA for ``embedding_model_name``. The
    # container build stage snapshots exactly this revision into
    # ``embedding_model_path`` so runtime never contacts the hub
    # (Requirement 10.6).
    embedding_model_revision: str = "c9745ed1d9f207416be6d2e6f8de32d1f16199bf"
    # Local directory holding the baked model artifact inside the API
    # image (design §10: "baked image path"; the image roots the app at
    # ``/app`` — see infra/docker/api.Dockerfile). Local development
    # overrides this to wherever the snapshot was downloaded.
    embedding_model_path: str = "/app/models/all-MiniLM-L6-v2"
    # Expected encoder output dimension. Must equal the ``vector(384)``
    # DDL literal declared by the pgvector migration; the ML_Adapter's
    # startup check fails fast on any mismatch (Requirement 1.6, 1.7).
    embedding_dimension: int = 384
    # Per-input wall-clock bound for embedding generation end-to-end,
    # including chunking and aggregation (Requirement 2.8). Exceeding it
    # triggers the per-request Phase 1 fallback, never a 5xx.
    embedding_timeout_seconds: int = 20
    # Pinned spaCy pipeline name for the Skill_Extractor (Requirement
    # 10.2). The installed package version is read from package metadata
    # at load time and stamped into the Scorer_Version.
    spacy_pipeline: str = "en_core_web_sm"

    # ---- LLM layer (phase-3-llm-layer §"Configuration") -------------------
    # Provider base URL for the LLM_Client's OpenAI-compatible API. Only
    # the single provider adapter module reads this (Requirement 1.4);
    # swapping providers in Phase 6 means a new adapter + this URL.
    llm_base_url: str = "https://openrouter.ai/api/v1"
    # App-owned provider API key. Intentionally optional: absent/empty
    # means the app starts normally with LLM_Features in the
    # LLM_Unavailable state serving fallbacks (Requirement 1.8).
    # ``SecretStr`` keeps the key out of ``repr()``, logs, and error
    # messages (Requirement 1.9).
    llm_api_key: SecretStr | None = None
    # Model identifier sent to the provider. The one designated source
    # for the model — no other source file hardcodes one (Requirement 1.3).
    llm_model: str = "anthropic/claude-haiku-4.5"
    # Per-request wall-clock timeout covering the full provider call,
    # streaming included (Requirement 1.6). Expiry aborts the call and
    # takes the Fallback_Response path, never a 5xx.
    llm_timeout_seconds: int = 60
    # Per-request maximum output tokens, always set on every provider
    # call (Requirement 1.4).
    llm_max_output_tokens: int = 4096
    # Per-user daily LLM-call quota (UTC calendar day) enforced on Redis
    # as the cost-as-DoS control (Requirement 13.1 → 429).
    llm_daily_quota: int = 25
    # Global monthly spend ceiling backing the Spend_Circuit_Breaker
    # (Requirement 14.1 → 503 while open). Derived from persisted
    # invocation-log costs, fail-safe open on tracking failure.
    llm_monthly_spend_limit_usd: Decimal = Decimal("10")
    # Bullet_Rewriter request bounds: max bullets per request and max
    # characters per bullet (Requirement 6.3 → 422 pre-LLM).
    llm_max_bullets: int = 5
    llm_max_bullet_chars: int = 500
    # Interview_Question_Generator upper bound on questions per set. The
    # schema floor is 5; a configured value below 5 fails startup via the
    # ``_llm_numeric_settings_positive`` validator (Requirement 7.8).
    llm_max_questions: int = 15
    # TTL for the per-user LLM_Cache entries on Redis (Requirement 15.6).
    # Default 24 hours.
    llm_cache_ttl_seconds: int = 86400
    # Computed-cost fallback pricing (USD per million tokens) used when
    # the provider's usage payload carries no reported cost
    # (Requirement 12.2, ``cost_basis="computed"``).
    llm_price_input_usd_per_mtok: Decimal = Decimal("1.00")
    llm_price_output_usd_per_mtok: Decimal = Decimal("5.00")

    # ---- agentic workflows (phase-4-agentic §11 "Configuration") ----------
    # Job_Queue (SQS) location. LocalStack locally, real AWS SQS in
    # production — the two environments differ only in these values
    # (Requirement 11.3). Read ONLY by services/agent_jobs/queue.py; no
    # Phase 4 business logic touches the environment directly
    # (Requirement 11.1).
    sqs_queue_url: str = "http://localstack:4566/000000000000/matchlayer-agent-jobs"
    sqs_region: str = "us-east-1"
    # Intentionally optional, mirroring ``s3_endpoint_url``: production
    # leaves it unset so aioboto3 talks to real AWS SQS, while LocalStack
    # supplies a non-AWS URL during local development.
    sqs_endpoint_url: str | None = "http://localstack:4566"
    # Per-node wall-clock timeout inside the Agent_Graph (seconds).
    # Expiry degrades the node's output, never fails the job
    # (Requirement 8.3).
    agent_node_timeout_seconds: int = 20
    # Maximum delivery attempts per Agent_Job before the worker marks it
    # failed and acknowledges the message (Requirement 11.5).
    agent_max_attempts: int = 2
    # Per-user per-minute sliding-window limits for the async endpoints:
    # POST /matches/{id}/analyze and GET /jobs/{id} respectively.
    agent_analyze_rate_limit_per_minute: int = 10
    agent_job_poll_rate_limit_per_minute: int = 120
    # TTL for Agent_Cache entries on Redis (Requirement 9.7). Default
    # 24 hours, mirroring ``llm_cache_ttl_seconds``.
    agent_cache_ttl_seconds: int = 86400
    # OTLP exporter endpoint. Empty/unset leaves the no-op tracer so
    # traced and untraced runs have identical outcomes (Requirement 13.5).
    otel_exporter_otlp_endpoint: str = ""
    # OTel service resource attribute; the worker overrides this to
    # ``matchlayer-worker`` via its own environment.
    otel_service_name: str = "matchlayer-api"

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
        """Reject invalid score weights at startup.

        The final score is a convex blend of the similarity and
        keyword-coverage components (phase-1-matching Requirement 5.3,
        extended by phase-2-nlp-embeddings Requirement 3.9). Two
        misconfigurations are rejected, in order:

        1. Either weight outside the inclusive range ``[0, 1]`` — a
           negative or >1 weight breaks the convex-blend contract even if
           the pair happens to sum to 1.0 (e.g. ``1.5 + -0.5``).
        2. The pair not summing to ``1.0`` within an absolute tolerance of
           ``±0.001`` (Requirement 3.9's documented tolerance, replacing
           the Phase 1 ``1e-9`` IEEE-754-only allowance).

        Failing fast here mirrors the JWT-secret length floor: a
        misconfiguration raises ``ValidationError`` before the app accepts
        traffic rather than producing wrong scores in production.
        """
        weights = (
            ("MATCHLAYER_SCORE_WEIGHT_SIMILARITY", self.score_weight_similarity),
            ("MATCHLAYER_SCORE_WEIGHT_KEYWORD", self.score_weight_keyword),
        )
        for env_name, value in weights:
            if not 0.0 <= value <= 1.0:
                raise ValueError(
                    "MATCHLAYER_SCORE_WEIGHT_SIMILARITY and "
                    "MATCHLAYER_SCORE_WEIGHT_KEYWORD must each lie in the "
                    f"inclusive range [0, 1]; {env_name} is {value}"
                )
        total = self.score_weight_similarity + self.score_weight_keyword
        if not math.isclose(total, 1.0, abs_tol=1e-3):
            raise ValueError(
                "MATCHLAYER_SCORE_WEIGHT_SIMILARITY + "
                "MATCHLAYER_SCORE_WEIGHT_KEYWORD must sum to 1.0 "
                "within a tolerance of ±0.001; "
                f"received {self.score_weight_similarity} + "
                f"{self.score_weight_keyword} = {total}"
            )
        return self

    @model_validator(mode="after")
    def _llm_numeric_settings_positive(self) -> Settings:
        """Reject invalid LLM cost-control and bounds settings at startup.

        Every numeric setting in the Phase 3 LLM block must be strictly
        positive (phase-3-llm-layer Requirement 18.2): a non-positive
        timeout, quota, spend limit, or bound would silently disable a
        cost control or produce a nonsensical request shape. Additionally
        ``llm_max_questions`` must be at least 5 because the
        Interview_Question_Set schema enforces a floor of 5 questions
        (Requirement 7.8) — an upper bound below the floor would make
        every LLM response an automatic validation failure.

        Failing fast here mirrors the JWT-secret length floor and the
        score-weight validator above: the error message names the
        offending setting so an operator can fix the misconfiguration
        before the app accepts traffic.
        """
        numeric_settings: tuple[tuple[str, int | Decimal], ...] = (
            ("MATCHLAYER_LLM_TIMEOUT_SECONDS", self.llm_timeout_seconds),
            ("MATCHLAYER_LLM_MAX_OUTPUT_TOKENS", self.llm_max_output_tokens),
            ("MATCHLAYER_LLM_DAILY_QUOTA", self.llm_daily_quota),
            ("MATCHLAYER_LLM_MONTHLY_SPEND_LIMIT_USD", self.llm_monthly_spend_limit_usd),
            ("MATCHLAYER_LLM_MAX_BULLETS", self.llm_max_bullets),
            ("MATCHLAYER_LLM_MAX_BULLET_CHARS", self.llm_max_bullet_chars),
            ("MATCHLAYER_LLM_MAX_QUESTIONS", self.llm_max_questions),
            ("MATCHLAYER_LLM_CACHE_TTL_SECONDS", self.llm_cache_ttl_seconds),
            ("MATCHLAYER_LLM_PRICE_INPUT_USD_PER_MTOK", self.llm_price_input_usd_per_mtok),
            ("MATCHLAYER_LLM_PRICE_OUTPUT_USD_PER_MTOK", self.llm_price_output_usd_per_mtok),
        )
        for env_name, value in numeric_settings:
            if value <= 0:
                raise ValueError(f"{env_name} must be a positive value; received {value}")
        if self.llm_max_questions < 5:
            raise ValueError(
                "MATCHLAYER_LLM_MAX_QUESTIONS must be at least 5 — the "
                "Interview_Question_Set schema enforces a floor of 5 "
                f"questions; received {self.llm_max_questions}"
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
