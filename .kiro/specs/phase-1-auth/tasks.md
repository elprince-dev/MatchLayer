# Implementation Plan: phase-1-auth

## Overview

This spec lands as **one PR** on branch `phase-1/auth` (per `conventions.md` "Branch naming") that delivers the authentication and account surface every later Phase 1 feature depends on: register, login, refresh-token rotation with family-reuse detection, logout, password reset, the `/me` and `PATCH /me` endpoints, the append-only audit log, double-submit CSRF on the cookie surface, sliding-window rate limiting via Redis, account lockout, and the matching Next.js frontend route groups. The PR is parented on the foundation baseline (`phase-1/foundation`) and consumed by the sibling spec `phase-1-matching`, which builds resume upload and TF-IDF scoring on top of the authenticated user identity this spec ships.

The implementation is the test: a green CI run on the auth PR — the same six required checks the foundation locked in (`backend`, `frontend`, `shared-types`, `security`, `openapi-drift`, `required-checks`) — plus the explicit unit, integration, and property-based test suites enumerated below, plus a final manual end-to-end smoke against the README, are the acceptance signal. The local-only login-timing test (INV-5) runs on a developer laptop and is excluded from CI by the `not timing` pytest marker because the runners are too noisy for sub-30ms timing assertions.

Languages (fixed by `tech.md` and `design.md`): **Python 3.13** for the API, **TypeScript** for the web app. The design specifies concrete stacks throughout (FastAPI, Pydantic v2, SQLAlchemy 2.x async, PyJWT, argon2-cffi, Hypothesis, React Hook Form, Zod) — no language selection is required. Pseudocode in `design.md` (Refresh-Token Rotation §7.3, CSRF Strategy §9.3, Configuration and Environment Variables §17.2, Frontend Architecture §13) is shape-only and has a fixed target language at every site.

This plan keeps the work granular: each leaf sub-task is a single PR-ready unit small enough to invoke as one `spec-task-execution` subagent run, large enough to land meaningful behavior. Every leaf carries `_Requirements:` and `_Design:` reference lines that anchor it back to the locked contract. Mandatory PBT-1..5 and INV-1..5 sub-tasks have no `*` postfix; only example-based unit/integration tests that supplement those properties are marked optional.

## Tasks

- [ ] 1. Dependencies and project configuration
  - [ ] 1.1 Add the new runtime and dev dependencies to `apps/api/pyproject.toml`
    - Add runtime deps with pinned majors: `email-validator` (so Pydantic `EmailStr` validates), `argon2-cffi`, `PyJWT`. Add dev deps: `hypothesis` (for the property-based suite), `freezegun` (for time-controlled tests in `test_rate_limit.py`).
    - Run `uv sync` from `apps/api/` and commit the updated `apps/api/uv.lock`.
    - _Requirements: 1.10, 7.1, 14.4_
    - _Design: Components and Interfaces, Password Handling, JWT Design_

  - [ ] 1.2 Configure Hypothesis defaults in `apps/api/tests/conftest.py`
    - Register a Hypothesis profile named `"auth"` via `settings(deadline=None, max_examples=200)` so the hash-heavy property tests in `tests/property/` aren't aborted by Hypothesis's per-example deadline (Argon2id at the design's parameters takes ≥80 ms on a laptop). Activate the profile from a `conftest.py` autouse fixture or `pytest.ini` block.
    - Add a `pytest.ini`/`pyproject.toml` `markers` entry for `timing` so `pytest -m "not timing"` is a recognized invocation.
    - _Requirements: 15.2_
    - _Design: Testing Strategy §18, Password Handling §8.1_

- [ ] 2. Configuration, secrets, and `.env.example`
  - [ ] 2.1 Extend `Settings` in `apps/api/src/matchlayer_api/config.py` with every new auth field
    - Add fields per Configuration and Environment Variables §17.1: `jwt_secret: SecretStr`; `auth_access_token_ttl_seconds: int = 900`; `auth_refresh_token_ttl_seconds: int = 604800`; `auth_lockout_threshold: int = 10`; `auth_lockout_window_seconds: int = 900`; `auth_lockout_duration_seconds: int = 900`; `web_base_url: AnyHttpUrl`; `database_app_role: str = "matchlayer"`; the eleven `auth_rate_limit_*` int fields enumerated in §17.1.
    - Implement the `_jwt_secret_min_length` validator from §17.2 that rejects any `MATCHLAYER_JWT_SECRET` whose UTF-8 byte length is below 32 with a message that names the byte count and the minimum, never echoing the secret value. Validation must run at `Settings` construction so `create_app()` aborts before uvicorn binds the socket.
    - _Requirements: 7.2, 7.7, 14.4_
    - _Design: Configuration and Environment Variables §17.1, §17.2, JWT Design §6.4_

  - [ ] 2.2 Update root `.env.example` with every new variable from §17.1
    - Add one row per variable in §17.1 with the documented default. The `MATCHLAYER_JWT_SECRET` placeholder is the literal `change-me-development-only-32+chars` (33 bytes) so a developer who runs `cp .env.example .env` clears the floor without learning a key-generation routine (Requirement 14.5).
    - Verify `python tools/check_env_drift.py` (foundation §9.5) exits 0 against the new tree.
    - _Requirements: 14.4, 14.5_
    - _Design: Configuration and Environment Variables §17.1_

- [ ] 3. Database schema, models, and migration
  - [ ] 3.1 Author `apps/api/src/matchlayer_api/db/models.py` (SQLAlchemy 2.x declarative)
    - Create `apps/api/src/matchlayer_api/db/__init__.py` and `db/models.py` declaring the `Base = DeclarativeBase` plus four mapped classes: `User`, `RefreshToken`, `PasswordResetToken`, `AuditEvent` mirroring the column tables in Data Models §4.1–§4.4. UUIDv7 primary keys via `uuid_utils`. `users.email` is `Text` (not `String(N)`); the case-insensitive uniqueness lives in the functional index, not in the column.
    - Use `Mapped[...]` and `mapped_column(...)` consistently with the foundation's SQLAlchemy 2.x style.
    - _Requirements: 8.1, 11.1_
    - _Design: Data Models §4.1, §4.2, §4.3, §4.4_

  - [ ] 3.2 Author the Alembic migration `apps/api/alembic/versions/0001_users_and_auth.py`
    - `down_revision = "0000_baseline"`. `upgrade()` creates the four tables in the order `users → refresh_tokens → password_reset_tokens → audit_events` with every column, default, and FK from §4 and the indexes from §16.2 (including the functional `users_email_lower_uniq` and the `audit_events_created_at_idx` Requirement 14.2 calls out). Emit the `GRANT INSERT, SELECT` and `REVOKE UPDATE, DELETE, TRUNCATE` for `audit_events` keyed on `MATCHLAYER_DATABASE_APP_ROLE` per §4.5 and §16.2.
    - `downgrade()` is the exact reverse from §16.3: re-grant `UPDATE, DELETE, TRUNCATE`, revoke `INSERT, SELECT`, drop indexes, drop tables in reverse order, and `DROP INDEX IF EXISTS users_email_lower_uniq` (created via `op.execute` so Alembic auto-tracking can't see it).
    - _Requirements: 8.1, 11.1, 11.2, 14.1, 14.2, 14.3_
    - _Design: Migrations §16.1, §16.2, §16.3, Data Models §4.5_

  - [ ] 3.3 Verify the migration applies cleanly against the docker-compose Postgres
    - With `docker compose up -d --wait` running, execute `uv run --project apps/api alembic upgrade head`, confirm the four tables exist with `\d+` in `psql`, then `alembic downgrade -1` and confirm every table and index drops cleanly. Re-run `alembic upgrade head` to leave the local stack in the post-migration state.
    - _Requirements: 14.1, 14.2, 14.3_
    - _Design: Migrations §16.4_

- [ ] 4. Core security primitives
  - [ ] 4.1 Ship the password blocklist file
    - Create `apps/api/src/matchlayer_api/core/security/password_blocklist.txt` with the SecLists `Passwords/Common-Credentials/10-million-password-list-top-1000.txt`, lower-cased, NFKC-normalized, deduplicated, and sorted lexicographically. Top-of-file comment names the source and the permissive license per Password Handling §8.4. Add a matching `core/security/__init__.py`.
    - _Requirements: 1.5_
    - _Design: Password Handling §8.4_

  - [ ] 4.2 Implement `core/security/passwords.py` (`Password_Hasher`)
    - This is the **only** module in the API that imports `argon2-cffi`. Expose a class-or-module-level `Password_Hasher` with `hash_password(plaintext) -> str`, `verify_password(stored, plaintext) -> bool`, `is_blocked(plaintext) -> bool`, and a precomputed `DUMMY_HASH` (one Argon2id hash of a fixed plaintext, computed at import time) for the `Auth_Service.authenticate` unknown-email branch (§8.3). Argon2id parameters from §8.1 (`time_cost=2`, `memory_cost=65536`, `parallelism=1`, `hash_len=32`, `salt_len=16`).
    - `hash_password` and `verify_password` apply `unicodedata.normalize("NFKC", plaintext)` per §8.5; the minimum-length check (≥ 12 chars, Requirement 1.4) is applied to the **pre-NFKC** codepoint count.
    - The blocklist is loaded once at import via `bisect.bisect_left` against the sorted file (§8.4). `verify_password` calls `argon2.PasswordHasher().check_needs_rehash(stored)` and exposes the boolean to callers so `Auth_Service.authenticate` can transparently re-hash on login (§8.2).
    - _Requirements: 1.4, 1.5, 1.10_
    - _Design: Password Handling §8.1, §8.2, §8.3, §8.4, §8.5_

  - [ ] 4.3 Implement `core/security/jwt.py` (`JWT_Service`)
    - This is the **only** module in the API that imports `jwt` (PyJWT). Expose `issue_access_token(*, sub: str) -> str`, `issue_refresh_token(*, sub: str, jti: UUID) -> str`, and `verify_token(token: str, *, expected_type: Literal["access", "refresh"]) -> dict` per JWT Design §6.1 and §6.3. Header is fixed `{"alg": "HS256", "typ": "JWT"}`. Verification calls PyJWT with `algorithms=["HS256"]` so alg-confusion (`none`, `HS512`, `RS256`, etc.) is rejected by the library.
    - Claims on every issued token: `sub`, `iat`, `exp`, `jti`, `type` — and nothing else (§6.1, Requirement 7.4, 7.8). TTLs come from `Settings` (`auth_access_token_ttl_seconds`, `auth_refresh_token_ttl_seconds`).
    - `verify_token` rejects when claim `type != expected_type` (Requirement 7.6).
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.8_
    - _Design: JWT Design §6.1, §6.2, §6.3_

  - [ ] 4.4 Implement `core/security/cookies.py` (cookie helpers)
    - Expose `set_refresh_cookie(response, *, value, max_age, settings)`, `clear_refresh_cookie(response, *, settings)`, `set_csrf_cookie(response, *, value, max_age, settings)`, `clear_csrf_cookie(response, *, settings)`. Attribute set per CSRF Strategy §9.2: `HttpOnly=True` for refresh and `False` for csrf, `SameSite="Lax"`, `Path="/api/v1/auth"`, `Domain` unset (host-only). `Secure=True` except when `settings.environment == "development"` (the documented dev carve-out for `http://localhost`).
    - This is the **only** module in the API that calls `Response.set_cookie` for the names `matchlayer_refresh` or `matchlayer_csrf`.
    - _Requirements: 9.1, 9.2, 9.5_
    - _Design: CSRF Strategy §9.2, §9.3_

  - [ ] 4.5 Implement `core/rate_limit.py` (`Rate_Limiter`)
    - This is the **only** module in the API that imports `redis-py`. Implement the sliding-window-via-SORTED-SET algorithm from Rate Limiting §10.1 as a single Lua script registered with Redis at startup and invoked via `EVALSHA`/`EVAL`. Wrap in an async `Rate_Limiter.check(key, *, limit, window_seconds) -> RateLimitDecision` returning a `@dataclass(frozen=True, slots=True)` with `allowed: bool` and `retry_after_seconds: int`.
    - On any Redis exception (timeout, connection refused, Lua error), return `RateLimitDecision(allowed=False, retry_after_seconds=60)` per §10.4 — fail-closed at the request layer, not at startup, so the API stays alive when Redis blips. Bound key category and endpoint into the Redis key per §10.2.
    - _Requirements: 10.1, 10.7, 10.8, 10.9_
    - _Design: Rate Limiting §10.1, §10.2, §10.4_

- [ ] 5. FastAPI dependencies and shared schemas
  - [ ] 5.1 Implement `core/dependencies.py`
    - Expose `get_current_user(...)` (Bearer token → `JWT_Service.verify_token(expected_type="access")` → load `User` row by `sub`, raising `UnauthenticatedError` for missing/invalid token, expired token, mismatched `type` claim, or a User_Account whose `deleted_at` is non-null per Requirement 6.4).
    - Expose `csrf_required(request)` per CSRF Strategy §9.3: when no `matchlayer_refresh` cookie is present, return without raising (per Requirement 9.4 the CSRF check is N/A); otherwise compare `matchlayer_csrf` cookie value to `X-CSRF-Token` header value via `secrets.compare_digest` and raise `CsrfMismatchError` on mismatch.
    - Expose `rate_limit(*, endpoint, by)` factory per Rate Limiting §10.5 returning a FastAPI dependency that runs every key category in `by`, sets the `Retry-After` response header on 429, and triggers the `rate_limit_rejected` audit emission with `endpoint` + `category` (never the raw email value when category is `email`).
    - _Requirements: 6.2, 6.4, 9.3, 9.4, 10.5, 10.7_
    - _Design: Components and Interfaces, CSRF Strategy §9.3, Rate Limiting §10.5_

  - [ ] 5.2 Implement `auth/schemas.py` (Pydantic v2 request/response models)
    - Create `apps/api/src/matchlayer_api/auth/__init__.py` and `auth/schemas.py` defining the request and response Pydantic models for every Auth_Router endpoint plus the dev surface response: `RegisterRequest`, `LoginRequest`, `MePatchRequest`, `PasswordResetRequestRequest`, `PasswordResetConfirmRequest`, `TokenPairResponse` (the body of register/login/refresh containing `access_token` plus the User_Account fields), `MeResponse`, `LastResetLinkResponse`. Email fields use `EmailStr` (so RFC-5321 validation runs per Requirement 1.3). `display_name` uses the Unicode-class validator from Requirement 6.6.
    - Models are the source of truth for OpenAPI, which is what `pnpm codegen` consumes.
    - _Requirements: 1.1, 1.2, 1.3, 2.1, 5.1, 5.5, 6.5, 6.6_
    - _Design: Components and Interfaces, OpenAPI Codegen Impact_

- [ ] 6. Service layer
  - [ ] 6.1 Implement `services/audit.py` (`Audit_Service`)
    - Create `apps/api/src/matchlayer_api/services/__init__.py` and `services/audit.py` exposing a single `emit(session, *, event_type, user_id=None, request=None, payload=None)` method that inserts one row into `audit_events` using the caller's session — no overload that opens a fresh connection (Audit Log §11.3, Requirement 15.4). Map every named `event_type` from §11.2 to a typed `TypedDict` payload schema so a typo can't introduce an unexpected key. Truncate `user_agent` to 1024 chars before insert (Requirement 11.5).
    - Per `security.md` "Logging" and Requirement 11.4: never insert `password`, `password_hash`, `new_password`, plaintext `Reset_Token`, JWT bytes, or display-name strings into `payload`.
    - _Requirements: 11.1, 11.3, 11.4, 11.5, 15.4_
    - _Design: Audit Log §11.2, §11.3_

  - [ ] 6.2 Implement `services/auth.py` (`Auth_Service`) — registration, login, lockout
    - Implement `register(session, *, email, password, display_name)` per Requirement 1.7: NFKC-normalize, length + blocklist checks, hash via `Password_Hasher`, insert User row (default `display_name` to local-part of email), allocate fresh `family_id` + `jti`, insert `refresh_tokens` row, issue Token_Pair via `JWT_Service`, emit `registration_success` audit. The existing-email enumeration-defense path (Requirement 1.6) returns the same response shape but emits `registration_attempt_existing_email` and issues no token.
    - Implement `authenticate(session, *, email, password)` per Requirements 2.2, 2.3, 2.5, 2.7, 2.8, 2.9: case-insensitive email lookup; on unknown email run `Password_Hasher.verify_password(DUMMY_HASH, ...)` and return the same 401 envelope as wrong-password (§8.3); on locked account return 423 without incrementing counters; on success reset counters, allocate fresh `family_id`, issue Token_Pair; on failure increment `failed_login_count`/`last_failed_login_at` and trigger lockout when threshold reached.
    - Both methods return outcome dataclasses the router can map directly to HTTP responses; both audit through `Audit_Service.emit` in the same session.
    - _Requirements: 1.6, 1.7, 1.8, 2.2, 2.3, 2.5, 2.6, 2.7, 2.8, 2.9_
    - _Design: Components and Interfaces, Password Handling §8.3, Audit Log §11.2_

  - [ ] 6.3 Extend `Auth_Service` with refresh rotation and logout
    - Implement `rotate_refresh_token(session, *, presented_jti, user_id)` per the pseudocode in Refresh-Token Rotation and Family Reuse §7.3: `SELECT ... FOR UPDATE` on the row, return `invalid()` for missing/expired/wrong-user, return `reused()` and revoke every sibling in the same `family_id` when `revoked_at` is already set, otherwise revoke the predecessor and insert a successor with the **same** `family_id` (Requirement 8.3). Audit `refresh_token_rotated` only on the success branch (Requirement 3.9).
    - Implement `logout(session, *, presented_jti)` per Requirement 4.5: revoke exactly one row, emit `logout` audit; idempotent re-logout against an already-revoked `jti` returns 204 with no duplicate audit row (Requirement 4.6). Both methods take the same row lock so logout-vs-rotate races resolve correctly (§7.3 concurrency note).
    - _Requirements: 3.7, 3.8, 3.9, 3.10, 4.5, 4.6, 8.2, 8.3, 8.4, 8.6_
    - _Design: Refresh-Token Rotation and Family Reuse §7.1, §7.2, §7.3_

  - [ ] 6.4 Extend `Auth_Service` with password-reset request and confirm
    - Implement `request_password_reset(session, *, email)` per Requirements 5.2, 5.3, 5.4: case-insensitive lookup; on unknown email return success silently with no DB write; on known email generate a 256-bit cryptographically-random Reset_Token via `secrets.token_urlsafe(32)`, persist a `password_reset_tokens` row with the SHA-256 hash of the plaintext (the plaintext never touches the DB), invoke `Dev_Reset_Link_Surface.record(...)` only when `settings.environment == "development"` (Requirement 13.1, 13.2), emit `password_reset_requested`.
    - Implement `confirm_password_reset(session, *, token, new_password)` per Requirements 5.6, 5.7, 5.8, 5.9, 5.10: hash the presented token, look up by hash; reject missing/expired/used with the single `invalid_reset_token` envelope; run length + blocklist checks on `new_password`; update `password_hash` + `updated_at`, set `used_at`, revoke every non-revoked refresh-token row for the user (Requirement 8.5), emit `password_reset_confirmed`.
    - _Requirements: 5.2, 5.3, 5.4, 5.6, 5.7, 5.8, 5.9, 5.10, 5.11, 8.5, 13.1, 13.2_
    - _Design: Components and Interfaces, Audit Log §11.2_

  - [ ] 6.5 Extend `Auth_Service` with `get_user_by_id` and `update_display_name`
    - `get_user_by_id(session, *, user_id)` returns the User row when `deleted_at IS NULL`, raising the same error path that `get_current_user` maps to 401 when not (Requirement 6.4).
    - `update_display_name(session, *, user, new_display_name)` validates the codepoint classes (Requirement 6.6: Unicode `L`, `M`, `N`, `Pd`, `Pc`, `Zs`; ≤ 64 chars after strip; non-empty after strip), updates `display_name` + `updated_at`, emits `display_name_changed` audit with only `previous_display_name_length` and `new_display_name_length` (never the strings — §11.2).
    - _Requirements: 6.4, 6.6, 6.7, 6.8_
    - _Design: Audit Log §11.2_

- [ ] 7. Routers, dev surface, and app wiring
  - [ ] 7.1 Implement `auth/router.py` (`Auth_Router`) — register, login, me, patch me
    - Mount at `/api/v1/auth`. Implement `POST /register` (Requirements 1.1–1.9), `POST /login` (Requirements 2.1–2.10), `GET /me` (Requirements 6.1–6.4, 6.8), `PATCH /me` (Requirements 6.5–6.8). Apply `Depends(rate_limit(endpoint="register", by=("ip",)))` to register (Requirement 10.2) and `Depends(rate_limit(endpoint="login", by=("email", "ip")))` to login (Requirement 10.3). Emit cookies via the `core/security/cookies.py` helpers only.
    - On 429 rejection the dependency sets the `Retry-After` header and audits `rate_limit_rejected` (Requirement 10.7); the router maps the `RateLimited` exception to the RFC 7807 `rate_limited` envelope. On 503 (rate-limiter unavailable) the envelope is `rate_limiter_unavailable` (Requirement 10.9).
    - Pure HTTP-shape concerns: no business logic in this file (Components and Interfaces import-boundary rule).
    - _Requirements: 1.1, 1.2, 1.3, 1.7, 1.8, 1.9, 2.1, 2.2, 2.3, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10, 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8, 10.2, 10.3, 10.7, 10.9_
    - _Design: Components and Interfaces, Rate Limiting §10.5, Error Handling_

  - [ ] 7.2 Extend `Auth_Router` with refresh, logout, and password-reset endpoints
    - Implement `POST /refresh` (Requirements 3.1–3.10) wired with `Depends(csrf_required)` and `Depends(rate_limit(endpoint="refresh", by=("ip",)))` per §10.5; clear cookies on the reuse-detection (Requirement 3.7) and the missing/invalid-token paths.
    - Implement `POST /logout` (Requirements 4.1–4.6) wired with `Depends(csrf_required)`; always clear `matchlayer_refresh` and `matchlayer_csrf` on the response.
    - Implement `POST /password-reset/request` (Requirements 5.1–5.4) wired with `Depends(rate_limit(endpoint="password_reset_request", by=("email", "ip")))` and `POST /password-reset/confirm` (Requirements 5.5–5.11) wired with `Depends(rate_limit(endpoint="password_reset_confirm", by=("ip",)))`.
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10, 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 5.1, 5.5, 5.11, 9.3, 9.4, 9.5, 10.4, 10.5, 10.6_
    - _Design: Refresh-Token Rotation and Family Reuse §7.1, §7.2, CSRF Strategy §9.3, Rate Limiting §10.3, §10.5_

  - [ ] 7.3 Implement `dev/reset_links.py` and `dev/router.py`
    - Create `apps/api/src/matchlayer_api/dev/__init__.py`, `dev/reset_links.py`, and `dev/router.py`. `reset_links.py` exposes `DEV_RESET_LINK_STORE` (the single-slot LRU process-singleton with `record(link)` and `latest()` per Dev-Mode Reset-Link Surface §12.1) — never persists to disk, Redis, or external services (Requirements 13.5, 13.6). Records emit one structured `info`-level log line with `password_reset_link` field on `record(...)` per Requirement 13.1.
    - `dev/router.py` exposes `GET /api/v1/dev/last-reset-link` returning `{"link": ..., "created_at": ...}` from the store (Requirement 13.3); when the store is empty return both fields as `null`.
    - _Requirements: 13.1, 13.2, 13.3, 13.5, 13.6_
    - _Design: Dev-Mode Reset-Link Surface §12.1_

  - [ ] 7.4 Wire the new routers and exception handlers into `main.py`
    - Edit `apps/api/src/matchlayer_api/main.py` to: include `auth.router.router` at `/api/v1/auth`; include `dev.router.router` at `/api/v1/dev` only when `settings.environment == "development"` (the `if` lives in `main.py`, not inside the dev router — Architecture, Dev-Mode Reset-Link Surface); register the new exception types (`UnauthenticatedError`, `InvalidCredentialsError`, `InvalidRefreshTokenError`, `RefreshTokenReusedError`, `CsrfMismatchError`, `AccountLockedError`, `InvalidResetTokenError`, `RateLimited`, `RateLimiterUnavailableError`) with the foundation `errors.py` so the RFC 7807 envelope shape is preserved (Error Handling).
    - _Requirements: 4.1 (foundation), 13.4_
    - _Design: Architecture, Components and Interfaces, Error Handling_

- [ ] 8. Backend test suites
  - [ ] 8.1 Extend `apps/api/tests/conftest.py` with auth fixtures
    - Add fixtures: `app` (returns `create_app()` with a temp env that satisfies `MATCHLAYER_JWT_SECRET` length floor); `client` (`httpx.AsyncClient`); `db_session` (per-test transaction with rollback, opened against the docker-compose Postgres); `redis_client` (against the docker-compose Redis with a per-test key prefix and post-test flush); `factory_user` and `factory_user_with_refresh` builders for integration tests; a `freeze_time` helper for the rate-limiter property test.
    - _Requirements: 15.3_
    - _Design: Testing Strategy §18_

  - [ ] 8.2 Unit test: `apps/api/tests/unit/test_passwords.py`
    - Cover `Password_Hasher.hash_password` / `verify_password` round-trip on a plain ASCII password (smoke), blocklist hit returns the correct error, NFKC normalization (compose-vs-decompose form of the same character verifies the same hash), pre-NFKC length check (a single combining glyph that NFKC-expands does not bypass the 12-char floor — Requirement 1.4 + §8.5), and a p95-latency assertion that 100 hashes finish under the §15.2 / Requirement 15.2 budget on this host.
    - _Requirements: 1.4, 1.5, 1.10, 15.2_
    - _Design: Password Handling §8.1, §8.4, §8.5_

  - [ ] 8.3 Unit test: `apps/api/tests/unit/test_jwt.py`
    - Cover claim shape (only `sub`, `iat`, `exp`, `jti`, `type` — Requirement 7.4); `expected_type` enforcement (an access token rejected when refresh is expected and vice versa — Requirement 7.6); the alg allowlist by hand-crafting tokens with `alg=none` and `alg=HS512` and asserting `verify_token` raises (Requirement 7.3); the secret-length floor (constructing `Settings` with a 31-byte secret raises `ValidationError` — Requirement 7.7).
    - _Requirements: 7.3, 7.4, 7.6, 7.7, 7.8_
    - _Design: JWT Design §6.1, §6.3, §6.4_

  - [ ] 8.4 Unit test: `apps/api/tests/unit/test_rate_limit.py`
    - Cover sliding-window correctness (a sequence of 10 hits at `t`, `t+1ms`, ..., `t+9ms` against `limit=10, window=1s` all succeed; the 11th rejects with `retry_after_seconds > 0`; at `t+1100ms` the next hit succeeds again). Cover fail-closed: when the injected Redis client raises, the wrapper returns `allowed=False, retry_after_seconds=60` and the router maps it to 503 `rate_limiter_unavailable`.
    - _Requirements: 10.1, 10.7, 10.9_
    - _Design: Rate Limiting §10.1, §10.4_

  - [ ] 8.5 Unit test: `apps/api/tests/unit/test_cookies.py`
    - Cover the exact attribute set produced by every helper: `HttpOnly`, `SameSite=Lax`, `Path=/api/v1/auth`, `Max-Age` matching the configured TTL, `Domain` unset, `Secure=True` in `production` and `staging`, `Secure=False` in `development`. `clear_*` helpers set `Max-Age=0` and an empty value.
    - _Requirements: 9.1, 9.2, 9.5_
    - _Design: CSRF Strategy §9.2_

  - [ ] 8.6 Unit test: `apps/api/tests/unit/test_dev_reset_links.py`
    - Cover the single-slot LRU eviction (`record(a); record(b); latest() == b`); cover the no-persist contract (the store has no filesystem, Redis, or external write paths); cover the env-gating helper that the auth service uses to decide whether to call `record` at all.
    - _Requirements: 13.5, 13.6_
    - _Design: Dev-Mode Reset-Link Surface §12.1_

  - [ ] 8.7 Unit test: `apps/api/tests/unit/test_import_boundaries.py`
    - Walk the `matchlayer_api` package source tree and grep-assert: `import jwt` and `from jwt import` appear only in `core/security/jwt.py`; `import argon2` and `from argon2 import` appear only in `core/security/passwords.py`; `Response.set_cookie(...,` for `matchlayer_refresh` or `matchlayer_csrf` appears only in `core/security/cookies.py`. Fails the build on any boundary violation.
    - _Requirements: 7.1, 1.10_
    - _Design: Components and Interfaces (import-boundary rules)_

  - [ ] 8.8 Integration test: `apps/api/tests/integration/test_register.py`
    - Cover happy 201 (cookies set, body contains user fields, audit row of type `registration_success`); 422 on Pydantic failure; 422 with the literal "common password" `detail` on a blocklist hit (no echo of the submitted value — Requirement 1.5); existing-email enumeration defense returning 200 with the same response shape but no token issuance and a `registration_attempt_existing_email` audit row (Requirement 1.6).
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9_
    - _Design: Components and Interfaces, Audit Log §11.2_

  - [ ] 8.9 Integration test: `apps/api/tests/integration/test_login.py`
    - Cover happy 200; unknown-email 401 with the literal "Email or password is incorrect." `detail`; wrong-password 401 with the byte-for-byte identical envelope (Requirements 2.2, 2.3); failed-login counter increments and the lockout transition at threshold (Requirement 2.9); a request to a locked account returns 423 without incrementing the counter (Requirement 2.8); audit emissions on every branch (`login_success`, `login_failure`, `account_locked`).
    - _Requirements: 2.1, 2.2, 2.3, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10_
    - _Design: Password Handling §8.3, Audit Log §11.2_

  - [ ] 8.10 Integration test: `apps/api/tests/integration/test_refresh.py`
    - Cover happy rotation (new `family_id` equals predecessor's, predecessor `revoked_at` set, fresh CSRF cookie issued, `refresh_token_rotated` audit); missing cookie → 401 `missing_refresh_cookie`; CSRF mismatch → 403 `csrf_mismatch`; alg-confusion-crafted token → 401 `invalid_refresh_token`; reuse-detection branch revokes every sibling in the family and returns 401 `refresh_token_reused` with `refresh_token_reuse_detected` audit (Requirement 3.7).
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10, 8.2, 8.3, 8.4_
    - _Design: Refresh-Token Rotation and Family Reuse §7.1, §7.2, §7.3, CSRF Strategy §9.3_

  - [ ] 8.11 Integration test: `apps/api/tests/integration/test_logout.py`
    - Cover happy 204 (one row revoked, cookies cleared, `logout` audit row); missing cookie → 204 with cookies cleared (Requirement 4.2); idempotent re-logout against an already-revoked `jti` → 204 with no duplicate audit (Requirement 4.6); CSRF mismatch → 403 `csrf_mismatch` (Requirement 4.3).
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6_
    - _Design: Components and Interfaces, CSRF Strategy §9.3_

  - [ ] 8.12 Integration test: `apps/api/tests/integration/test_password_reset.py`
    - Cover request silent-success (unknown email → 202 with no row inserted; known email → 202 with a row inserted whose `token_hash` is a 32-byte SHA-256 of the plaintext, the `Dev_Reset_Link_Surface.latest()` populated, a `password_reset_requested` audit row); confirm happy path (204, `password_hash` updated, `used_at` set, every `refresh_tokens` row for the user revoked per Requirement 8.5, `password_reset_confirmed` audit); missing/expired/used token returns the single 400 `invalid_reset_token` envelope (Requirements 5.6, 5.7, 5.8).
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8, 5.9, 5.10, 5.11, 8.5_
    - _Design: Components and Interfaces, Audit Log §11.2_

  - [ ] 8.13 Integration test: `apps/api/tests/integration/test_me.py`
    - Cover GET `/me` happy 200; missing/invalid token → 401 `unauthenticated`; valid token whose `sub` resolves to a User with `deleted_at != NULL` → 401 `unauthenticated` (Requirement 6.4); PATCH `/me` valid display-name update → 200 with the updated body and a `display_name_changed` audit row whose payload contains only the length fields (never the strings — §11.2); PATCH validation 422 on empty-after-strip, > 64 chars, or disallowed Unicode classes (Requirement 6.6); PATCH never returns `password_hash`, `failed_login_count`, or `locked_until` (Requirement 6.8).
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8_
    - _Design: Components and Interfaces, Audit Log §11.2_

  - [ ] 8.14 Integration test: `apps/api/tests/integration/test_audit_events_role_grants.py` (INV-1)
    - **INV-1: The application role cannot rewrite the audit log.** Connect to Postgres as `MATCHLAYER_DATABASE_APP_ROLE`; assert that `INSERT` and `SELECT` against `audit_events` succeed; assert that `UPDATE`, `DELETE`, and `TRUNCATE` against `audit_events` raise `psycopg.errors.InsufficientPrivilege`. The test connects as the application role even when the test session is opened as a superuser by setting `SET ROLE` for the duration of each forbidden statement.
    - _Requirements: 11.2_
    - _Design: Data Models §4.5, Audit Log §11.4, Correctness Properties (INV-1)_

  - [ ] 8.15 Integration test: `apps/api/tests/integration/test_logging_redaction.py` (INV-2)
    - **INV-2: No password, plaintext token, or non-allowlisted PII appears in any auth-surface log line.** Capture the structlog output across one full successful invocation of every Auth_Router endpoint (register, login, refresh, logout, password-reset/request, password-reset/confirm, me, patch me); grep-assert no occurrence of the submitted password value, the submitted `new_password` value, the plaintext Reset_Token, the JWT bytes, or any `password_hash` substring. The login failure path is exercised separately to cover the path that passes through the dummy-hash branch.
    - _Requirements: 1.9, 2.10, 5.11, 8.6, 11.4, 13.6_
    - _Design: Error Handling, Audit Log §11.4, Correctness Properties (INV-2)_

  - [ ] 8.16 Property-based test: `apps/api/tests/property/test_password_roundtrip.py` (PBT-1)
    - **Property 1: Argon2 hash/verify is a sound roundtrip.**
    - **Validates: Requirements 1.7, 1.10, 5.10**
    - Implement the two `@given` properties from Testing Strategy §18.3 PBT-1: for any `p` with `len(p) >= 12`, `verify(hash(p), p) is True`; for any pair `p != q`, `verify(hash(p), q) is False`. Add an explicit `@example(...)` for an NFKC-edge case (combining-mark form vs precomposed form of the same logical character — §8.5).
    - _Requirements: 1.7, 1.10, 5.10_
    - _Design: Correctness Properties (PBT-1), Testing Strategy §18.3, Password Handling §8.5_

  - [ ] 8.17 Property-based test: `apps/api/tests/property/test_jwt_roundtrip.py` (PBT-2)
    - **Property 2: JWT roundtrip preserves claims and the algorithm allowlist holds.**
    - **Validates: Requirements 7.2, 7.3, 7.4, 7.6, 7.8, 3.4, 3.5, 6.2**
    - Implement the `@given` round-trip from §18.3 PBT-2 (any `sub` UUID + any `type ∈ {access, refresh}` round-trips through `issue → verify` with the claims preserved) and the parametrized negative cases over `["none", "HS512", "RS256"]` — for each, hand-craft a token under that algorithm with a matching key, attempt `verify_token`, assert it raises.
    - _Requirements: 3.4, 3.5, 6.2, 7.2, 7.3, 7.4, 7.6, 7.8_
    - _Design: Correctness Properties (PBT-2), Testing Strategy §18.3, JWT Design §6.3_

  - [ ] 8.18 Property-based test: `apps/api/tests/property/test_rate_limit_window.py` (PBT-3)
    - **Property 3: Sliding-window rate limiter accounts monotonically.**
    - **Validates: Requirements 10.1, 10.7, 10.8**
    - Implement the property from §18.3 PBT-3: for any `(limit, window_ms, request_timestamps)`, at every step the count of `allowed=True` decisions whose timestamps fall within `[now - window_ms, now]` is at most `limit`; every `allowed=False` decision has `retry_after_seconds > 0`; every `allowed=True` decision has `retry_after_seconds == 0`. Use an injectable `now_ms` clock so the property runs deterministically against the docker-compose Redis (or `fakeredis` if the harness needs determinism beyond the real Redis).
    - _Requirements: 10.1, 10.7, 10.8_
    - _Design: Correctness Properties (PBT-3), Testing Strategy §18.3, Rate Limiting §10.1_

  - [ ] 8.19 Property-based test: `apps/api/tests/property/test_refresh_family.py` (PBT-4)
    - **Property 4: Refresh-token family rotation invariants.**
    - **Validates: Requirements 3.7, 3.8, 3.10, 4.5, 8.2, 8.3, 8.4**
    - Implement the §18.3 PBT-4 properties against the integration DB so the SQL semantics (FOR UPDATE) are exercised under property generation: every successful rotation produces a new `refresh_tokens` row with the same `family_id` as its predecessor; presenting any `jti` whose row has `revoked_at != NULL` revokes every other non-revoked sibling in the family before responding; logout against a single `jti` revokes exactly one row and touches no other family.
    - _Requirements: 3.7, 3.8, 3.10, 4.5, 8.2, 8.3, 8.4_
    - _Design: Correctness Properties (PBT-4), Testing Strategy §18.3, Refresh-Token Rotation and Family Reuse §7.3_

  - [ ] 8.20 Property-based test: `apps/api/tests/property/test_email_normalization.py` (PBT-5)
    - **Property 5: Email lookup is case-insensitive everywhere it is consumed.**
    - **Validates: Requirements 1.6, 2.2, 2.3, 5.2, 5.3, 14.2**
    - Implement the §18.3 PBT-5 property: for any registered email `E` and any case-permutation `E'` of `E`, the case-insensitive lookup at `/auth/register` (existing-email enumeration-defense path), `/auth/login`, and `/auth/password-reset/request` resolves to the same User_Account row.
    - _Requirements: 1.6, 2.2, 2.3, 5.2, 5.3, 14.2_
    - _Design: Correctness Properties (PBT-5), Testing Strategy §18.3_

  - [ ] 8.21 Local-only timing test: `apps/api/tests/timing/test_login_timing_local.py` (INV-5)
    - **INV-5: Login timing for unknown vs known-but-wrong-password is indistinguishable.**
    - **Validates: Requirement 2.4**
    - Run ≥ 100 trials each of (a) login with a never-registered email and (b) login with a registered email and a wrong password, against a fresh local Postgres + Redis. Compute the median wall-clock delta between the two distributions and assert ≤ 25 ms (Requirement 2.4). Mark with `@pytest.mark.timing` so `pytest -m "not timing"` (the default CI invocation) skips it. Top-of-file comment explains why CI is excluded (runner noise).
    - _Requirements: 2.4_
    - _Design: Correctness Properties (INV-5), Testing Strategy §18.4, Password Handling §8.3_

- [ ] 9. Backend checkpoint
  - [ ] 9.1 Backend checkpoint — every backend test passes
    - From `apps/api/`: `uv run alembic upgrade head` (against the docker-compose Postgres), `uv run pytest -m "not timing"` (every unit, integration, and property suite green), `uv run ruff format --check`, `uv run ruff check`, `uv run mypy`. Ensure all tests pass, ask the user if questions arise.
    - _Requirements: 15.3_
    - _Design: Testing Strategy §18_

- [ ] 10. Codegen pipeline integration
  - [ ] 10.1 Run `pnpm codegen` and commit the regenerated shared types
    - Prerequisites: `apps/api` deps installed via §1.1, `.env` in place, `Settings` clears the JWT-secret floor. Postgres does **not** need to be running — `app.openapi()` does not invoke the lifespan or open a DB connection.
    - From the repo root, run `pnpm codegen`. Confirm `packages/shared-types/src/api-types.ts` and `packages/shared-types/src/api-schemas.ts` now reference every new endpoint enumerated in OpenAPI Codegen Impact (`/api/v1/auth/{register,login,refresh,logout,password-reset/request,password-reset/confirm,me}`) and that the dev-only `/api/v1/dev/last-reset-link` is **absent** (the codegen invocation runs with the default — non-development — environment, per OpenAPI Codegen Impact). Commit both files.
    - _Requirements: 14.4 (codegen impact)_
    - _Design: OpenAPI Codegen Impact_

  - [ ] 10.2 Update `packages/shared-types/src/index.ts` curated re-exports
    - Add the curated `RegisterRequest`/`RegisterResponse`/`RegisterRequestSchema`/`RegisterResponseSchema` aliases plus the same pattern for `Login`, `Refresh`, `Logout`, `PasswordResetRequest`, `PasswordResetConfirm`, `Me` (GET response), `MePatchRequest` per OpenAPI Codegen Impact. Run `pnpm --filter @matchlayer/shared-types typecheck` and confirm green.
    - _Requirements: (foundation 7.9 contract)_
    - _Design: OpenAPI Codegen Impact_

- [ ] 11. Frontend dependencies and shared library
  - [ ] 11.1 Add `react-hook-form` and `@hookform/resolvers` to `apps/web/package.json`
    - Pin both at the latest known-good major. Run `pnpm install` from the repo root and commit the updated `pnpm-lock.yaml`.
    - _Requirements: 12.2_
    - _Design: Frontend Architecture §13.6_

  - [ ] 11.2 Implement `apps/web/src/lib/auth.ts` (`Auth_State_Hook` and token store)
    - Implement the closure-backed module-level token store + `subscribe`/`getAccessToken`/`setAccessToken` from Frontend Architecture §13.4. Expose `useAuth()` returning the `UseAuth` contract from Requirement 12.5: `user`, `isAuthenticated`, `isLoading`, `signIn(email, password)`, `signOut()`, `refresh()`. Composed via `useSyncExternalStore` over the closure store plus a TanStack Query `useQuery` for `/me`. The access token is held only in the module closure — never `localStorage`, `sessionStorage`, or `document.cookie` (Requirement 12.6).
    - Also export `verifySessionFromRefreshCookie({ headers, cookies })` per §13.5 for the Authenticated_Shell server-side path.
    - _Requirements: 12.5, 12.6_
    - _Design: Frontend Architecture §13.4, §13.5_

  - [ ] 11.3 Update `apps/web/src/lib/api.ts` with Bearer attach and 401 retry
    - Extend the foundation API client to (a) read the in-memory access token via `lib/auth.ts`'s `getAccessToken()` and attach `Authorization: Bearer <token>` to every outbound request, and (b) on a 401 response, attempt one `POST /api/v1/auth/refresh` (forwarding the cookies via `credentials: "include"`), update the in-memory token on success, and retry the original request exactly once. A second 401 propagates to the caller so the UI can react (typically by signing the user out).
    - _Requirements: 12.5, 12.6_
    - _Design: Frontend Architecture §13.5_

- [ ] 12. Frontend route groups and components
  - [ ] 12.1 Create the `(auth)` route group layout and shared components
    - Author `apps/web/src/app/(auth)/layout.tsx` rendering the centered-card shell from Auth Pages Design §14.1: brand wordmark in the violet→cyan gradient, `max-w-md` card with `rounded-2xl` and `border-strong` over `bg-bg-elevated`, subtle animated noise background gated by the foundation's `motion-safe.tsx` so `prefers-reduced-motion` is respected.
    - Author `apps/web/src/components/auth/auth-card.tsx`, `apps/web/src/components/auth/form-error.tsx` (the `<div role="alert" aria-live="polite">` region from Requirement 12.2), and `apps/web/src/components/auth/retry-after-message.tsx` (renders the rounded-up Retry-After seconds per Requirement 12.8).
    - _Requirements: 12.1, 12.2, 12.8, 12.9_
    - _Design: Auth Pages Design §14.1, §14.6_

  - [ ] 12.2 Build the `(auth)/register` page and form
    - Author `apps/web/src/app/(auth)/register/page.tsx` and `_form.tsx`. Form uses `useForm` + `zodResolver(RegisterRequestSchema)` from `@matchlayer/shared-types`. Submit calls `POST /api/v1/auth/register`. On 201 the in-memory access token is set and the router pushes to `/` (Authenticated_Shell). On the existing-email enumeration-defense 200 the UX is identical to a real success — the user cannot distinguish (Requirement 1.6, §14.2). On 422 render the API `detail`; on 429 render `<RetryAfterMessage>`.
    - Every input has an explicit `id` paired with `<label htmlFor>`; password input uses `autocomplete="new-password"`.
    - _Requirements: 1.1, 12.1, 12.2_
    - _Design: Auth Pages Design §14.2, Frontend Architecture §13.6_

  - [ ] 12.3 Build the `(auth)/login` page and form
    - Author `apps/web/src/app/(auth)/login/page.tsx` and `_form.tsx`. Reads `?next=` from the URL on mount; validates same-origin (`router.push(next)` only when `next` starts with `/` and contains no `://` after URL-decode — §13.7). Submit calls `POST /api/v1/auth/login`. On 200 set the access token via `useAuth().signIn(...)` and `router.push(next ?? "/")`. On 401 render the literal "Email or password is incorrect." (Requirement 12.7 / §14.3). On 423 render "Account is temporarily locked. Try again later." (Requirement 12.7). On 429 render `<RetryAfterMessage>` (Requirement 12.8).
    - Password input uses `autocomplete="current-password"`. The page also handles `?just-reset=1` by rendering an inline confirmation per §14.5.
    - _Requirements: 12.1, 12.2, 12.7, 12.8_
    - _Design: Auth Pages Design §14.3, Frontend Architecture §13.7_

  - [ ] 12.4 Build the `(auth)/forgot-password` page and form
    - Author `apps/web/src/app/(auth)/forgot-password/page.tsx` and `_form.tsx`. Submit calls `POST /api/v1/auth/password-reset/request`. On 202 always render the silent-success state "If that email is registered, we've sent password-reset instructions." regardless of whether the email matched (Requirement 5.2 / §14.4). When `process.env.NEXT_PUBLIC_API_BASE_URL` matches a localhost-style origin, render the dev-tip footer pointing to `GET /api/v1/dev/last-reset-link` per §14.4.
    - _Requirements: 12.1, 12.2_
    - _Design: Auth Pages Design §14.4_

  - [ ] 12.5 Build the `(auth)/reset-password` page and form
    - Author `apps/web/src/app/(auth)/reset-password/page.tsx` and `_form.tsx`. Read `token` from `?token=`; when absent render the friendly empty state "This page is for confirming a password reset. Open the link from your reset email." per Requirement 12.3 / §14.5. Form has `new_password` and `confirm_password` inputs (both `autocomplete="new-password"`); the Zod schema enforces equality client-side; only `new_password` is sent to the API. On 204 redirect to `/login?just-reset=1`. On 400 `invalid_reset_token` render the single message "This password-reset link is invalid or expired. Request a new one." (Requirements 5.6, 5.7, 5.8).
    - _Requirements: 12.1, 12.2, 12.3_
    - _Design: Auth Pages Design §14.5_

  - [ ] 12.6 Build the `(app)` route group with `Authenticated_Shell` and a placeholder dashboard
    - Author `apps/web/src/app/(app)/layout.tsx` as a Next.js Server Component implementing the §13.5 contract: call `verifySessionFromRefreshCookie({ headers, cookies })` from `lib/auth.ts`; on success render a thin `AppShellChrome` with the user's display name and the children; on failure call `redirect(\`/login?next=\${encodeURIComponent(currentPath)}\`)`per Requirement 12.4 and §13.7. On success inject the freshly-acquired access token into the client tree via a small server-rendered`<script>`that calls`setAccessToken(...)` on the client closure.
    - Author `apps/web/src/app/(app)/page.tsx` as a placeholder dashboard rendering "Welcome, {display_name}." plus a sign-out button wired to `useAuth().signOut()`.
    - _Requirements: 12.4, 12.5_
    - _Design: Frontend Architecture §13.5_

- [ ] 13. Frontend tests (Vitest + Testing Library)
  - [ ] 13.1 `apps/web/tests/auth-card.test.tsx`
    - Render `<AuthCard>` with arbitrary children; assert the brand wordmark appears, the sibling-page link slot renders when provided, the form children render in the body slot. Pass an axe-core baseline (no WCAG violations on the empty card).
    - _Requirements: 12.1, 12.9_
    - _Design: Auth Pages Design §14.1, §14.6_

  - [ ] 13.2 `apps/web/tests/login-form.test.tsx`
    - Stub the API client to return each of the four documented responses and assert: 401 → renders the literal "Email or password is incorrect." string; 423 → renders the literal "Account is temporarily locked. Try again later." string; 429 with `Retry-After: 30` → renders a message that includes "30" (Requirement 12.8); 200 → calls `useAuth().signIn(...)` and triggers the navigation to `next`.
    - _Requirements: 12.7, 12.8_
    - _Design: Auth Pages Design §14.3_

  - [ ] 13.3 `apps/web/tests/use-auth.test.tsx`
    - Drive the `useAuth` contract through `signIn`/`signOut`/`refresh`. Assert the contract surface from Requirement 12.5. Spy on `window.localStorage.setItem`, `window.sessionStorage.setItem`, and the `document.cookie` setter; assert none are called for the access token across the entire flow (Requirement 12.6).
    - _Requirements: 12.5, 12.6_
    - _Design: Frontend Architecture §13.4_

  - [ ] 13.4 `apps/web/tests/authenticated-shell.test.tsx`
    - Stub `verifySessionFromRefreshCookie` to return `null`; assert the shell calls `redirect("/login?next=...")` with a URL-encoded original path (Requirement 12.4 / §13.7). Stub it to return a session; assert the shell renders its children with the user's display name and that the access-token-injection `<script>` is present in the rendered output.
    - _Requirements: 12.4, 12.5_
    - _Design: Frontend Architecture §13.5_

- [ ] 14. Documentation
  - [ ] 14.1 Update the root `README.md` with the auth setup additions
    - Extend the foundation setup flow with: (a) a "Run the auth migration" step (`uv run --project apps/api alembic upgrade head` now applies `0001_users_and_auth`); (b) a "Set the new env vars" pointer noting that `cp .env.example .env` already covers everything, including the `MATCHLAYER_JWT_SECRET` placeholder that clears the 32-byte floor; (c) a "Retrieve the dev reset link" snippet showing `curl http://127.0.0.1:8000/api/v1/dev/last-reset-link` (Requirement 14.6); (d) an "Inspect recent audit events" `psql` snippet against `audit_events` ordered by `created_at DESC LIMIT 20` (Requirement 14.6) and a note about the 1-year minimum retention with archiving deferred to Phase 6 (Requirement 11.6); (e) a "Run the local timing test" snippet (`uv run pytest -m timing apps/api/tests/timing/`); (f) a `MATCHLAYER_DATABASE_APP_ROLE` parity note explaining that the docker-compose `POSTGRES_USER` is the role the auth migration grants `INSERT, SELECT` on `audit_events` to (§16.4).
    - _Requirements: 11.6, 14.6_
    - _Design: Migrations §16.4, Configuration and Environment Variables §17.1, Audit Log §11.4_

  - [ ] 14.2 Update `docs/runbooks/contributing-flow.md` with auth-specific notes (if any surface)
    - Add a short section flagging cookie-domain quirks on `localhost` (the `Secure` carve-out for `MATCHLAYER_ENVIRONMENT=development` is required for `http://localhost` to work) and the requirement that `pnpm codegen` be re-run any time an Auth_Router signature changes. Skip the section entirely if no new gotchas surfaced during implementation.
    - _Requirements: 14.6_
    - _Design: CSRF Strategy §9.2_

- [ ] 15. Final QA / smoke
  - [ ] 15.1 End-to-end smoke against a fresh local stack
    - Bring up `docker compose up -d --wait`, run `uv run --project apps/api alembic upgrade head`, start the API and the web app, then walk every flow in order: `/register` (assert redirected to `/`), `GET /api/v1/auth/me` via the web app (assert the user name appears), sign out, `/login` (assert 200 + redirect), force-rotate the refresh by clearing the access token and triggering the silent retry path on `lib/api.ts` (assert the new access token is acquired without a sign-out), `/forgot-password` → fetch the link via `GET /api/v1/dev/last-reset-link` → `/reset-password?token=...` → submit a new password → assert redirected to `/login?just-reset=1` → sign in with the new password → assert `/me` works again. Then `psql` into `audit_events` and confirm exactly one row of each documented `event_type` exists with the §11.2 payload schema.
    - _Requirements: 1.7, 2.5, 3.8, 4.5, 5.4, 5.10, 6.3, 11.3, 13.3, 14.6_
    - _Design: Components and Interfaces, Audit Log §11.2, Dev-Mode Reset-Link Surface §12.1_

  - [ ] 15.2 Confirm CI is green on the foundation's six required checks
    - Push branch `phase-1/auth`, open a PR targeting `main`, and confirm the same six required CI jobs the foundation locked in (`backend`, `frontend`, `shared-types`, `security`, `openapi-drift`, plus the `required-checks` aggregator) pass green. The new auth tests run inside the existing `backend` job; no new CI workflow file is added.
    - _Requirements: 14.4, 14.5_
    - _Design: OpenAPI Codegen Impact, Configuration and Environment Variables §17.1_

  - [ ] 15.3 Final checkpoint — Ensure all tests pass
    - Ensure all tests pass, ask the user if questions arise.

## Notes

- This spec lands as a single PR on branch `phase-1/auth`, parented on the foundation baseline. The PR's green CI run plus the unit, integration, and property-based suites enumerated above plus the §15.1 manual end-to-end smoke are the acceptance signal.
- The design has a "Correctness Properties" section. PBT-1..5 are universally-quantified properties implemented as Hypothesis tests under `apps/api/tests/property/`. INV-1..5 are security-critical invariants implemented as integration or timing tests; INV-3 and INV-4 are covered by the corresponding PBTs (PBT-5 and PBT-4 respectively) and need no separate task. INV-1, INV-2, and INV-5 each have their own dedicated test task above. None of these are marked optional — the Correctness Properties section is the contract.
- Test sub-tasks above are NOT marked optional. Every test task — unit, integration, property-based, and the local-only timing test — is mandatory because each is required by an acceptance criterion (the unit and integration tests by the per-endpoint requirements, the PBTs by the Correctness Properties section, and INV-5 by Requirement 2.4 explicitly). The only test exclusion at runtime is the `not timing` pytest marker on §15.2's CI invocation, which is a CI-noise mitigation, not a test-importance signal.
- Each task references the granular requirement IDs it satisfies and the design section name it implements, so traceability is preserved without duplicating design content.
- Sequential ordering: every task can be executed without forward references. Models (3.1) precede the migration (3.2) which precedes the integration tests that depend on the schema. Security primitives (4.x) precede services (6.x) which consume them. Routers (7.x) come after services. Codegen (10.x) runs only after routers exist so the OpenAPI dump is non-empty.
- Sibling specs `phase-1-foundation` (already merged) and `phase-1-matching` (next) bracket this spec; do not pull `phase-1-matching`'s work (resume upload, parsing, TF-IDF scoring, MinIO integration, results UI) into this spec.
- The dev-only `/api/v1/dev/last-reset-link` endpoint is, by design, absent from the codegen output — the codegen invocation runs with the default (non-development) environment, so the dev path does not pollute the committed `packages/shared-types/src/api-types.ts`. The web app constructs the dev-tip URL by hand on `/forgot-password` (§14.4).

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2", "2.2", "4.1", "11.1", "14.2"] },
    {
      "id": 1,
      "tasks": ["2.1", "3.1", "4.2", "4.3", "4.4", "4.5", "5.2", "11.2", "12.1"]
    },
    { "id": 2, "tasks": ["3.2", "5.1", "6.1", "8.5", "8.7", "11.3", "13.1"] },
    {
      "id": 3,
      "tasks": [
        "3.3",
        "6.2",
        "6.3",
        "6.4",
        "6.5",
        "8.2",
        "8.3",
        "8.4",
        "8.6",
        "13.3"
      ]
    },
    {
      "id": 4,
      "tasks": [
        "7.1",
        "7.2",
        "7.3",
        "8.16",
        "8.17",
        "8.18",
        "8.19",
        "8.20",
        "12.2",
        "12.3",
        "12.4",
        "12.5",
        "12.6"
      ]
    },
    { "id": 5, "tasks": ["7.4", "13.2", "13.4"] },
    {
      "id": 6,
      "tasks": [
        "8.8",
        "8.9",
        "8.10",
        "8.11",
        "8.12",
        "8.13",
        "8.14",
        "8.15",
        "8.21"
      ]
    },
    { "id": 7, "tasks": ["9.1"] },
    { "id": 8, "tasks": ["10.1"] },
    { "id": 9, "tasks": ["10.2"] },
    { "id": 10, "tasks": ["14.1"] },
    { "id": 11, "tasks": ["15.1", "15.2"] },
    { "id": 12, "tasks": ["15.3"] }
  ]
}
```
