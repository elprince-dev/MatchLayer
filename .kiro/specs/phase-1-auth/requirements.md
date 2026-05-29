# Requirements Document

## Introduction

`phase-1-auth` is the second of three sequential specs that together deliver Phase 1 of the MatchLayer roadmap. It builds directly on `phase-1-foundation` and delivers the authentication and account surface that every subsequent feature depends on: registration, login, refresh-token rotation, logout, password reset, the audit log, the `/me` and `PATCH /me` endpoints, the matching frontend pages, and the cross-cutting controls (rate limiting, account lockout, security headers for cookie-authenticated requests) those flows require.

This spec deliberately stops short of any domain logic that is not authentication. There is no resume upload, no parsing, no scoring, no email verification, no MFA, no OAuth, no admin surface, and no production email delivery — those land in `phase-1-matching`, Phase 6, or Phase 7 per `product.md`. The success condition is that a user can register, log in, refresh, log out, reset a forgotten password, edit their display name, and have every security-relevant action recorded in an append-only audit log, against a green CI run on the `phase-1/auth` branch.

Every requirement below assumes the foundation scaffold from `phase-1-foundation` is in place: the FastAPI app factory, async SQLAlchemy session, structlog with PII redaction, the request-id middleware, the RFC 7807 error envelope, the OpenAPI codegen pipeline, the Next.js App Router scaffold with the design tokens and security-headers proxy, and the `docker-compose.yml` Postgres + Redis services. Where this spec extends the scaffold, that extension is called out explicitly.

The cross-cutting `security.md` baseline applies in full. This document does not re-state every rule from `security.md`; instead, individual requirements reference the `security.md` clauses they depend on so the contract remains traceable without duplication.

Scope boundaries:

- **In scope:** the `users` and `audit_events` tables; the `refresh_tokens` table (stateful refresh-token rotation with family-based reuse detection); the `password_reset_tokens` table; the `User` SQLAlchemy model and the first real Alembic migration `0001_users_and_auth.py`; password hashing via `argon2-cffi`; JWT issuance/verification via PyJWT with an explicit algorithm allowlist; CSRF protection on the cookie-authenticated refresh endpoint via the double-submit pattern; sliding-window rate limiting via Redis; account lockout after repeated failed login attempts; an append-only audit log; the dev-only password-reset link surface (structured log line plus `GET /api/v1/dev/last-reset-link` guarded by `MATCHLAYER_ENVIRONMENT=development`); the API endpoints `POST /api/v1/auth/register`, `POST /api/v1/auth/login`, `POST /api/v1/auth/refresh`, `POST /api/v1/auth/logout`, `POST /api/v1/auth/password-reset/request`, `POST /api/v1/auth/password-reset/confirm`, `GET /api/v1/auth/me`, and `PATCH /api/v1/auth/me`; the Next.js pages `/register`, `/login`, `/forgot-password`, `/reset-password`, and an authenticated layout shell; the `useAuth` TanStack Query hook contract; OpenAPI codegen consuming the new endpoints; updates to `.env.example` and the README runbook for the new environment variables.
- **Out of scope:** resume upload, parsing, and scoring (deferred to `phase-1-matching`); email verification on registration (Phase 7); MFA / TOTP (Phase 7); OAuth and social login (later phases); production email delivery via SES, Postmark, or any other paid provider (Phase 6 — Phase 1 only logs the reset link); admin endpoints and admin UI (Phase 7); per-tenant isolation (Phase 7); RS256/JWKS key infrastructure (Phase 6 — Phase 1 uses HS256 with a single secret); GDPR-style data export and deletion endpoints (Phase 7).

## Glossary

- **User_Account** — A row in the `users` table representing an authenticated identity. Stores `id` (UUIDv7), `email`, `password_hash`, `display_name`, `failed_login_count`, `locked_until`, `created_at`, `updated_at`, and `deleted_at`. The User_Account is the principal subject of every JWT issued by the Auth_Service.
- **Auth_Service** — The Python module at `apps/api/src/matchlayer_api/services/auth.py` that owns the business logic for registration, credential verification, token issuance, token rotation, logout, password change, and password reset. Auth_Service is invoked by the auth routers and is the only module permitted to read or write `users`, `refresh_tokens`, and `password_reset_tokens`.
- **Auth_Router** — The FastAPI router(s) under `apps/api/src/matchlayer_api/auth/` that expose the authentication HTTP surface. Auth_Router is responsible for HTTP-shape concerns (status codes, cookies, headers, RFC 7807 envelopes) and delegates all business logic to Auth_Service.
- **Password_Hasher** — The wrapper around `argon2-cffi` at `apps/api/src/matchlayer_api/core/security/passwords.py` exposing `hash_password(plaintext) -> str` and `verify_password(plaintext, hash) -> bool`. The Password_Hasher is the only module permitted to import `argon2-cffi` directly.
- **JWT_Service** — The wrapper around PyJWT at `apps/api/src/matchlayer_api/core/security/jwt.py` exposing `issue_access_token(...)`, `issue_refresh_token(...)`, and `verify_token(token, expected_type)`. The JWT_Service is the only module permitted to import PyJWT directly and is responsible for enforcing the algorithm allowlist.
- **Token_Pair** — The `{access_token, refresh_token}` produced by Auth_Service on successful registration, login, or refresh. The access token is a signed JWT with `type="access"` and a 15-minute lifetime. The refresh token is a signed JWT with `type="refresh"`, a 7-day lifetime, and a `jti` claim that maps one-to-one to a row in the `refresh_tokens` table.
- **Refresh_Cookie** — The `HttpOnly`, `Secure`, `SameSite=Lax` cookie named `matchlayer_refresh` that carries the refresh token. The cookie has `Path=/api/v1/auth`, a `Max-Age` matching the refresh-token lifetime, and is only emitted by Auth_Router. The cookie's `Secure` attribute is omitted when `MATCHLAYER_ENVIRONMENT=development` so local HTTP development works; in any other environment the attribute is mandatory.
- **CSRF_Token** — The unguessable random value emitted as a non-HttpOnly cookie named `matchlayer_csrf` and required as the `X-CSRF-Token` request header on every cookie-authenticated state-changing request to the Auth_Router. The CSRF_Token implements the double-submit-cookie pattern.
- **Refresh_Token_Family** — A `family_id` UUIDv7 shared by every refresh token issued from a single login. Rotation creates a new refresh-token row in the same family and marks its predecessor revoked. Detected reuse of a revoked refresh token revokes the entire family and emits an Audit_Event.
- **Reset_Token** — A 256-bit cryptographically random value issued by Auth_Service on a password-reset request. The Reset_Token is hashed at rest (SHA-256) in the `password_reset_tokens` table, has a 1-hour TTL, and is single-use. The plaintext value is delivered only via the dev-mode log surface in Phase 1.
- **Audit_Event** — A row in the `audit_events` table. Audit_Events are append-only (no `UPDATE` and no `DELETE` permitted from application code), retain at minimum 1 year per `security.md`, and capture security-relevant events: registration, login success/failure, logout, refresh-token rotation, refresh-token reuse detection, password change, password-reset request, password-reset confirmation, account lockout, account unlock, and account deletion.
- **Rate_Limiter** — The Redis-backed sliding-window rate-limit primitive at `apps/api/src/matchlayer_api/core/rate_limit.py`. The Rate_Limiter exposes `check(key, limit, window) -> RateLimitDecision` and is consumed by FastAPI dependencies attached to the auth endpoints.
- **Account_Lockout_Policy** — The rule that locks a User_Account for a configurable duration after a configurable number of consecutive failed login attempts within a configurable window. The defaults are 10 failed attempts within 15 minutes, locked for 15 minutes, taken from `security.md`.
- **Password_Blocklist** — The static list of the top 1,000 common passwords distributed inside the API package as a sorted file at `apps/api/src/matchlayer_api/core/security/password_blocklist.txt`. The Password_Hasher consults the blocklist before accepting a new password.
- **Auth_State_Hook** — The TanStack Query hook `useAuth` exported from `apps/web/src/lib/auth.ts` that exposes the current User_Account (or `null`), a `signIn` mutation, a `signOut` mutation, and a `refresh` query whose success keeps the in-memory access token current. The Auth_State_Hook is the single source of truth for auth state on the frontend.
- **Auth_Pages** — The Next.js App Router pages at `/register`, `/login`, `/forgot-password`, and `/reset-password`. Each page follows the `design.md` "auth pages: restrained" rule (centered card, brand mark, simple form, subtle background only).
- **Authenticated_Shell** — The Next.js App Router layout at `apps/web/src/app/(app)/layout.tsx` that gates access for authenticated routes. The Authenticated_Shell verifies the access token on the server (via the refresh cookie if necessary) and redirects unauthenticated visitors to `/login` with the original path preserved as a `next` query parameter.
- **Dev_Reset_Link_Surface** — The mechanism that exposes generated password-reset links during local development without sending email. Comprises (a) a single structured log line emitted at `info` level whose payload includes a `password_reset_link` field, and (b) a `GET /api/v1/dev/last-reset-link` HTTP endpoint that returns the most recently generated link. Both are guarded by `MATCHLAYER_ENVIRONMENT=development` and refuse to operate in any other environment.
- **PII** — Personally identifiable information as classified in `security.md` (resume contents, names, emails, phone numbers, etc.). For this spec the directly-handled PII is the user's email address.
- **Login_Latency_P95** — The 95th-percentile wall-clock latency of `POST /api/v1/auth/login` measured from request acceptance at FastAPI through response emission, with Postgres and Redis local on the same host.

## Requirements

### Requirement 1: User Registration

**User Story:** As a prospective user, I want to register with an email and a password and immediately have an authenticated session, so that I can begin using MatchLayer without an extra confirmation step in Phase 1.

#### Acceptance Criteria

1. THE Auth_Router SHALL expose `POST /api/v1/auth/register` accepting a JSON body with the fields `email` (string), `password` (string), and an optional `display_name` (string).
2. WHEN `POST /api/v1/auth/register` is invoked with a body that fails Pydantic validation, THE Auth_Router SHALL return HTTP 422 with the RFC 7807 error envelope defined in `phase-1-foundation` design §6.8 and SHALL NOT create a User_Account.
3. WHEN `POST /api/v1/auth/register` is invoked with a syntactically valid email that is not RFC 5321 compliant, THE Auth_Service SHALL reject the request with HTTP 422 and SHALL NOT create a User_Account.
4. WHEN `POST /api/v1/auth/register` is invoked with a `password` shorter than 12 characters, THE Auth_Service SHALL reject the request with HTTP 422 with a `detail` field that names the minimum length and SHALL NOT create a User_Account.
5. WHEN `POST /api/v1/auth/register` is invoked with a `password` whose case-folded form appears in the Password_Blocklist, THE Auth_Service SHALL reject the request with HTTP 422 with a generic `detail` field that names "common password" without echoing the submitted value, and SHALL NOT create a User_Account.
6. WHEN `POST /api/v1/auth/register` is invoked with an `email` that already exists in the `users` table (case-insensitive comparison), THE Auth_Service SHALL return HTTP 200 with the same successful-response shape used for a fresh registration but SHALL NOT alter the existing User_Account, SHALL NOT issue a Token_Pair, and SHALL emit an Audit_Event of type `registration_attempt_existing_email` so account enumeration via the registration endpoint is impossible per `security.md`.
7. WHEN `POST /api/v1/auth/register` is invoked with valid inputs and the email is not yet registered, THE Auth_Service SHALL hash the password via the Password_Hasher, persist a new User_Account row with `display_name` defaulted to the local part of the email when the optional field is absent, issue a Token_Pair via the JWT_Service, persist the refresh token's metadata in the `refresh_tokens` table with a fresh `family_id`, set the Refresh_Cookie and a CSRF_Token cookie on the response, and return HTTP 201 with a body containing the access token and the User_Account fields `{id, email, display_name, created_at, updated_at}`.
8. WHEN `POST /api/v1/auth/register` succeeds per criterion 7, THE Auth_Service SHALL emit an Audit_Event of type `registration_success` referencing the newly created User_Account's `id`.
9. THE Auth_Router SHALL NEVER include the `password`, the `password_hash`, or any partial form of either in any response body, log line, error message, or telemetry signal emitted from a `/api/v1/auth/register` request.
10. THE Password_Hasher SHALL invoke `argon2-cffi`'s `PasswordHasher.hash` with parameters configured per `security.md` and design §6.2, and SHALL return a string in PHC format suitable for direct storage in the `users.password_hash` column.

### Requirement 2: User Login

**User Story:** As a registered user, I want to log in with my email and password and receive a session, so that I can call authenticated endpoints from the frontend.

#### Acceptance Criteria

1. THE Auth_Router SHALL expose `POST /api/v1/auth/login` accepting a JSON body with the fields `email` (string) and `password` (string).
2. WHEN `POST /api/v1/auth/login` is invoked with an email for which no User_Account exists, THE Auth_Service SHALL invoke the Password_Hasher's `verify` method against a precomputed dummy hash and SHALL return HTTP 401 with the RFC 7807 envelope whose `type` is `invalid_credentials` and whose `detail` is the literal string "Email or password is incorrect."
3. WHEN `POST /api/v1/auth/login` is invoked with an email for which a User_Account exists but the supplied password does not verify against `users.password_hash`, THE Auth_Service SHALL return HTTP 401 with the identical envelope produced by criterion 2 so the response is byte-for-byte indistinguishable from the unknown-email case.
4. THE Auth_Service SHALL ensure the median wall-clock processing time of criterion 2 (unknown email) and the median wall-clock processing time of criterion 3 (known email, wrong password) differ by no more than 25 milliseconds when measured locally over a sample of at least 100 requests per case.
5. WHEN `POST /api/v1/auth/login` succeeds against a valid email and password, THE Auth_Service SHALL reset `users.failed_login_count` to 0, clear `users.locked_until`, issue a fresh Token_Pair via the JWT_Service, persist the refresh token in the `refresh_tokens` table with a new `family_id`, set the Refresh_Cookie and a CSRF_Token cookie on the response, and return HTTP 200 with a body containing the access token and the User_Account fields `{id, email, display_name, created_at, updated_at}`.
6. WHEN `POST /api/v1/auth/login` succeeds, THE Auth_Service SHALL emit an Audit_Event of type `login_success` referencing the User_Account's `id`.
7. WHEN `POST /api/v1/auth/login` fails per criterion 2 or criterion 3, THE Auth_Service SHALL emit an Audit_Event of type `login_failure` whose payload includes the lower-cased submitted email and the request's source IP and SHALL NOT include the submitted password or any partial form thereof.
8. WHILE `users.locked_until` is in the future relative to the request's wall-clock time, THE Auth_Service SHALL return HTTP 423 with the RFC 7807 envelope whose `type` is `account_locked` and whose `detail` is the literal string "Account is temporarily locked. Try again later." regardless of whether the supplied password would otherwise verify, and SHALL NOT increment `users.failed_login_count`.
9. WHEN a sequence of failed `POST /api/v1/auth/login` attempts against the same User_Account causes `users.failed_login_count` to reach the value defined by `MATCHLAYER_AUTH_LOCKOUT_THRESHOLD` (default 10) within the rolling window defined by `MATCHLAYER_AUTH_LOCKOUT_WINDOW_SECONDS` (default 900), THE Auth_Service SHALL set `users.locked_until` to `now() + MATCHLAYER_AUTH_LOCKOUT_DURATION_SECONDS` (default 900), reset `users.failed_login_count` to 0, and emit an Audit_Event of type `account_locked`.
10. THE Auth_Router SHALL NEVER include the `password`, the `password_hash`, or any partial form of either in any response body, log line, error message, or telemetry signal emitted from a `/api/v1/auth/login` request.
11. THE Login_Latency_P95 measured against a local Postgres + Redis stack on a developer laptop SHALL be less than or equal to 300 milliseconds, and the same metric measured in the GitHub Actions CI runner SHALL be less than or equal to 600 milliseconds.

### Requirement 3: Refresh-Token Rotation

**User Story:** As an authenticated user, I want my access token to renew silently without re-typing my password, so that my session can outlive a single 15-minute access-token lifetime.

#### Acceptance Criteria

1. THE Auth_Router SHALL expose `POST /api/v1/auth/refresh` that accepts the Refresh_Cookie via the request's `Cookie` header and accepts the CSRF_Token via the `X-CSRF-Token` request header.
2. WHEN `POST /api/v1/auth/refresh` is invoked without the Refresh_Cookie, THE Auth_Router SHALL return HTTP 401 with the RFC 7807 envelope whose `type` is `missing_refresh_cookie`.
3. WHEN `POST /api/v1/auth/refresh` is invoked with a Refresh_Cookie present but without a matching `X-CSRF-Token` header whose value equals the `matchlayer_csrf` cookie's value, THE Auth_Router SHALL return HTTP 403 with the RFC 7807 envelope whose `type` is `csrf_mismatch`.
4. WHEN `POST /api/v1/auth/refresh` is invoked with a Refresh_Cookie whose JWT signature does not validate against `MATCHLAYER_JWT_SECRET` under the algorithm allowlist defined by the JWT_Service, THE JWT_Service SHALL reject the token and THE Auth_Router SHALL return HTTP 401 with the RFC 7807 envelope whose `type` is `invalid_refresh_token`.
5. WHEN `POST /api/v1/auth/refresh` is invoked with a Refresh_Cookie whose `type` claim is not `refresh` or whose `exp` claim is in the past, THE JWT_Service SHALL reject the token and THE Auth_Router SHALL return HTTP 401 with the RFC 7807 envelope whose `type` is `invalid_refresh_token`.
6. WHEN `POST /api/v1/auth/refresh` is invoked with a Refresh_Cookie whose `jti` does not exist as a row in the `refresh_tokens` table, THE Auth_Service SHALL return HTTP 401 with the RFC 7807 envelope whose `type` is `invalid_refresh_token`.
7. WHEN `POST /api/v1/auth/refresh` is invoked with a Refresh_Cookie whose `jti` exists in the `refresh_tokens` table and whose corresponding row has `revoked_at` set, THE Auth_Service SHALL set `revoked_at` on every other non-revoked row in the same Refresh_Token_Family, return HTTP 401 with the RFC 7807 envelope whose `type` is `refresh_token_reused`, clear the Refresh_Cookie and the CSRF_Token cookie on the response, and emit an Audit_Event of type `refresh_token_reuse_detected` referencing the User_Account's `id` and the offending `family_id`.
8. WHEN `POST /api/v1/auth/refresh` is invoked with a Refresh_Cookie whose `jti` exists, has `revoked_at = NULL`, and whose User_Account has `deleted_at = NULL`, THE Auth_Service SHALL mark the existing row's `revoked_at` to the current time, insert a new row in the same Refresh_Token_Family with a fresh `jti` and a fresh 7-day `expires_at`, issue a fresh Token_Pair via the JWT_Service, set the Refresh_Cookie and a fresh CSRF_Token cookie on the response, and return HTTP 200 with a body containing only the access token and the User_Account fields `{id, email, display_name, created_at, updated_at}`.
9. WHEN `POST /api/v1/auth/refresh` succeeds per criterion 8, THE Auth_Service SHALL emit an Audit_Event of type `refresh_token_rotated` referencing the User_Account's `id`, the previous `jti`, and the new `jti`; THE Auth_Service SHALL NOT emit `refresh_token_rotated` for any failure path defined by criteria 2 through 7.
10. THE Auth_Service SHALL NEVER reuse the same `jti` value across two distinct rows in the `refresh_tokens` table.

### Requirement 4: Logout

**User Story:** As an authenticated user, I want to log out and have my refresh token revoked server-side, so that a subsequent request from a stolen cookie cannot succeed.

#### Acceptance Criteria

1. THE Auth_Router SHALL expose `POST /api/v1/auth/logout` that accepts the Refresh_Cookie via the request's `Cookie` header and the CSRF_Token via the `X-CSRF-Token` header.
2. WHEN `POST /api/v1/auth/logout` is invoked without the Refresh_Cookie, THE Auth_Router SHALL return HTTP 204 and SHALL clear the Refresh_Cookie and the CSRF_Token cookie on the response so a logged-out client receives the same observable outcome regardless of prior state.
3. WHEN `POST /api/v1/auth/logout` is invoked with a Refresh_Cookie present but without a matching `X-CSRF-Token` header whose value equals the `matchlayer_csrf` cookie's value, THE Auth_Router SHALL return HTTP 403 with the RFC 7807 envelope whose `type` is `csrf_mismatch`.
4. WHEN `POST /api/v1/auth/logout` is invoked with a Refresh_Cookie whose JWT signature does not validate or whose `type` claim is not `refresh`, THE Auth_Router SHALL return HTTP 204 and SHALL clear the Refresh_Cookie and the CSRF_Token cookie on the response without modifying any database row.
5. WHEN `POST /api/v1/auth/logout` is invoked with a Refresh_Cookie whose `jti` exists in the `refresh_tokens` table with `revoked_at = NULL`, THE Auth_Service SHALL set `revoked_at` to the current time on that single row, return HTTP 204, clear the Refresh_Cookie and the CSRF_Token cookie on the response, and emit an Audit_Event of type `logout` referencing the User_Account's `id` and the revoked `jti`.
6. WHEN `POST /api/v1/auth/logout` is invoked with a Refresh_Cookie whose `jti` exists in the `refresh_tokens` table with `revoked_at` already set, THE Auth_Service SHALL return HTTP 204 and SHALL clear the Refresh_Cookie and the CSRF_Token cookie on the response without inserting a duplicate Audit_Event for the same `jti`.

### Requirement 5: Password Reset

**User Story:** As a user who has forgotten my password, I want to request a single-use, time-limited reset link and use it to set a new password, so that I can recover access without contacting support.

#### Acceptance Criteria

1. THE Auth_Router SHALL expose `POST /api/v1/auth/password-reset/request` accepting a JSON body with the field `email` (string).
2. WHEN `POST /api/v1/auth/password-reset/request` is invoked with a syntactically valid email that does not match any User_Account (case-insensitive comparison), THE Auth_Service SHALL return HTTP 202 with an empty body and SHALL NOT create a Reset_Token, so account enumeration via the password-reset endpoint is impossible per `security.md`.
3. WHEN `POST /api/v1/auth/password-reset/request` is invoked with a syntactically valid email that matches a User_Account whose `deleted_at` is null, THE Auth_Service SHALL generate a 256-bit cryptographically-random Reset_Token, persist a row in `password_reset_tokens` containing the SHA-256 hash of the token, the User_Account's `id`, an `expires_at` set to one hour after creation, and a `used_at` of null, and SHALL return HTTP 202 with an empty body.
4. WHEN `POST /api/v1/auth/password-reset/request` succeeds per criterion 3, THE Auth_Service SHALL invoke the Dev_Reset_Link_Surface to record the reset link including the plaintext token, and SHALL emit an Audit_Event of type `password_reset_requested` referencing the User_Account's `id`.
5. THE Auth_Router SHALL expose `POST /api/v1/auth/password-reset/confirm` accepting a JSON body with the fields `token` (string) and `new_password` (string).
6. WHEN `POST /api/v1/auth/password-reset/confirm` is invoked with a `token` whose SHA-256 hash does not match any row in `password_reset_tokens`, THE Auth_Service SHALL return HTTP 400 with the RFC 7807 envelope whose `type` is `invalid_reset_token`.
7. WHEN `POST /api/v1/auth/password-reset/confirm` is invoked with a `token` whose corresponding row has `expires_at` in the past, THE Auth_Service SHALL return HTTP 400 with the RFC 7807 envelope whose `type` is `invalid_reset_token`.
8. WHEN `POST /api/v1/auth/password-reset/confirm` is invoked with a `token` whose corresponding row has `used_at` already set, THE Auth_Service SHALL return HTTP 400 with the RFC 7807 envelope whose `type` is `invalid_reset_token`.
9. WHEN `POST /api/v1/auth/password-reset/confirm` is invoked with a `new_password` that fails the same length and Password_Blocklist checks defined by Requirement 1 acceptance criteria 4 and 5, THE Auth_Service SHALL return HTTP 422 with the RFC 7807 envelope and SHALL NOT alter `users.password_hash`.
10. WHEN `POST /api/v1/auth/password-reset/confirm` succeeds, THE Auth_Service SHALL hash the new password via the Password_Hasher, update `users.password_hash` and `users.updated_at`, set the matching `password_reset_tokens.used_at` to the current time, set `revoked_at` on every non-revoked row in `refresh_tokens` belonging to the User_Account, return HTTP 204 with an empty body, and emit an Audit_Event of type `password_reset_confirmed` referencing the User_Account's `id`.
11. THE Auth_Router SHALL NEVER include the `new_password`, the `password_hash`, or any partial form of either in any response body, log line, error message, or telemetry signal emitted from a `/api/v1/auth/password-reset/*` request.

### Requirement 6: Authenticated Identity Endpoints

**User Story:** As a frontend developer, I want a single authenticated endpoint that returns the current user's identity and a single endpoint to update their display name, so that the UI can render and edit "who am I" without a bespoke flow per surface.

#### Acceptance Criteria

1. THE Auth_Router SHALL expose `GET /api/v1/auth/me` that requires a valid access token presented via the `Authorization: Bearer <token>` request header.
2. WHEN `GET /api/v1/auth/me` is invoked without an `Authorization` header, with an `Authorization` header whose scheme is not `Bearer`, or with a token whose JWT signature does not validate or whose `type` claim is not `access`, THE Auth_Router SHALL return HTTP 401 with the RFC 7807 envelope whose `type` is `unauthenticated`.
3. WHEN `GET /api/v1/auth/me` is invoked with a valid access token whose `sub` claim matches a User_Account whose `deleted_at` is null, THE Auth_Router SHALL return HTTP 200 with a JSON body containing the fields `{id, email, display_name, created_at, updated_at}`.
4. WHEN `GET /api/v1/auth/me` is invoked with a valid access token whose `sub` claim matches a User_Account whose `deleted_at` is non-null, THE Auth_Router SHALL return HTTP 401 with the RFC 7807 envelope whose `type` is `unauthenticated`.
5. THE Auth_Router SHALL expose `PATCH /api/v1/auth/me` that requires a valid access token presented via the `Authorization: Bearer <token>` request header and accepts a JSON body with the optional field `display_name` (string).
6. WHEN `PATCH /api/v1/auth/me` is invoked with a `display_name` that is empty after stripping leading and trailing whitespace, or whose stripped form exceeds 64 characters, or that contains characters outside the Unicode classes `L`, `M`, `N`, `Pd`, `Pc`, `Zs`, THE Auth_Router SHALL return HTTP 422 with the RFC 7807 envelope and SHALL NOT alter the User_Account.
7. WHEN `PATCH /api/v1/auth/me` is invoked with a valid `display_name`, THE Auth_Service SHALL update `users.display_name` and `users.updated_at`, emit an Audit_Event of type `display_name_changed` referencing the User_Account's `id`, and return HTTP 200 with the same body shape as criterion 3.
8. THE Auth_Router SHALL NEVER include the `password_hash`, the `failed_login_count`, the `locked_until`, or any internal book-keeping field in the response bodies of `GET /api/v1/auth/me` or `PATCH /api/v1/auth/me`.

### Requirement 7: JWT Issuance and Verification

**User Story:** As the platform owner, I want every JWT issued and verified by exactly one well-configured wrapper, so that the algorithm allowlist, claim shape, and signing secret are owned in one place.

#### Acceptance Criteria

1. THE JWT_Service SHALL be the only module in the API package that imports the `jwt` (PyJWT) library directly.
2. THE JWT_Service SHALL sign every issued token with `HS256` using the secret stored in the `MATCHLAYER_JWT_SECRET` environment variable.
3. THE JWT_Service SHALL reject any token presented for verification whose header `alg` claim is not exactly `HS256`, including the literal string `none` and any other value.
4. THE JWT_Service SHALL include the claims `sub` (User_Account `id` as string), `iat`, `exp`, `jti`, and `type` on every issued token; `type` SHALL be exactly one of the strings `access` or `refresh`.
5. THE JWT_Service SHALL set `exp - iat` to `MATCHLAYER_AUTH_ACCESS_TOKEN_TTL_SECONDS` (default 900) for tokens with `type=access` and to `MATCHLAYER_AUTH_REFRESH_TOKEN_TTL_SECONDS` (default 604800) for tokens with `type=refresh`.
6. WHEN the JWT_Service is asked to verify a token while passing `expected_type`, THE JWT_Service SHALL reject the token unless its `type` claim is exactly `expected_type`.
7. IF `MATCHLAYER_JWT_SECRET` is shorter than 32 bytes when encoded in UTF-8, THEN THE API_App SHALL fail to start and SHALL log a structured error naming the violated minimum length without echoing the secret value.
8. THE JWT_Service SHALL NEVER include the User_Account's `email`, `password_hash`, or any other PII in the claim set of an issued token.

### Requirement 8: Refresh-Token Storage and Family Reuse Detection

**User Story:** As the platform owner, I want refresh tokens to be tracked server-side with family-based rotation, so that a stolen refresh token can be detected and the entire compromised session terminated.

#### Acceptance Criteria

1. THE `refresh_tokens` table SHALL contain at minimum the columns `jti` (UUIDv7 primary key), `family_id` (UUIDv7, indexed), `user_id` (foreign key to `users.id`, indexed), `issued_at` (timestamp with time zone), `expires_at` (timestamp with time zone), and `revoked_at` (nullable timestamp with time zone).
2. WHEN the Auth_Service issues a refresh token as the result of registration or login, THE Auth_Service SHALL allocate a new `family_id` for that row.
3. WHEN the Auth_Service issues a refresh token as the result of a successful rotation per Requirement 3 acceptance criterion 8, THE Auth_Service SHALL reuse the predecessor row's `family_id` for the new row.
4. WHEN the Auth_Service detects that a presented refresh token's `jti` resolves to a `refresh_tokens` row with `revoked_at` already set, THE Auth_Service SHALL update every other row sharing the same `family_id` and having `revoked_at = NULL` to set `revoked_at` to the current time before returning the response defined by Requirement 3 acceptance criterion 7.
5. THE Auth_Service SHALL set `revoked_at` on every non-revoked row in `refresh_tokens` belonging to a User_Account when that User_Account's password is changed via the password-reset confirmation flow.
6. THE Auth_Service SHALL NEVER store the JWT bytes themselves in the `refresh_tokens` table; the JWT-side `jti` claim and the table's `jti` column are the sole linkage.

### Requirement 9: CSRF Protection on Cookie-Authenticated Endpoints

**User Story:** As the platform owner, I want every cookie-authenticated state-changing request to carry a matching CSRF token, so that a malicious cross-site form submission cannot rotate or revoke a session.

#### Acceptance Criteria

1. WHEN the Auth_Service sets the Refresh_Cookie on a response, THE Auth_Service SHALL also set a non-HttpOnly cookie named `matchlayer_csrf` whose value is a fresh 256-bit cryptographically-random URL-safe string.
2. THE `matchlayer_csrf` cookie SHALL have `Path=/api/v1/auth`, `SameSite=Lax`, the same `Max-Age` as the Refresh_Cookie, and SHALL include the `Secure` attribute under the same conditions defined for the Refresh_Cookie.
3. THE Auth_Router SHALL require the request header `X-CSRF-Token` on every request to `POST /api/v1/auth/refresh` and `POST /api/v1/auth/logout` whose `Cookie` header includes a `matchlayer_refresh` value.
4. WHEN a request to `POST /api/v1/auth/refresh` or `POST /api/v1/auth/logout` carries a `matchlayer_refresh` cookie but the `X-CSRF-Token` header value does not exactly equal the `matchlayer_csrf` cookie value, THE Auth_Router SHALL return HTTP 403 with the RFC 7807 envelope whose `type` is `csrf_mismatch`.
5. THE Auth_Service SHALL clear both the `matchlayer_refresh` cookie and the `matchlayer_csrf` cookie on every response that revokes the underlying refresh token.

### Requirement 10: Rate Limiting on Authentication Endpoints

**User Story:** As the platform owner, I want every authentication endpoint to be rate-limited via Redis, so that automated credential-stuffing and password-reset spam are bounded.

#### Acceptance Criteria

1. THE Rate_Limiter SHALL implement a sliding-window counter against Redis using a single atomic Lua script per check, returning a `RateLimitDecision` that distinguishes "allowed" from "rejected" and exposes the `Retry-After` value to populate the response header.
2. THE Auth_Router SHALL apply the Rate_Limiter to `POST /api/v1/auth/register` keyed on the source IP address with a default limit of 10 requests per 15-minute window.
3. THE Auth_Router SHALL apply the Rate_Limiter to `POST /api/v1/auth/login` keyed on the lower-cased submitted `email` with a default limit of 10 requests per 15-minute window AND keyed on the source IP address with a default limit of 50 requests per 15-minute window; if either limit is exceeded the request SHALL be rejected.
4. THE Auth_Router SHALL apply the Rate_Limiter to `POST /api/v1/auth/refresh` keyed on the source IP address with a default limit of 60 requests per 1-minute window.
5. THE Auth_Router SHALL apply the Rate_Limiter to `POST /api/v1/auth/password-reset/request` keyed on the lower-cased submitted `email` with a default limit of 5 requests per 1-hour window AND keyed on the source IP address with a default limit of 20 requests per 1-hour window.
6. THE Auth_Router SHALL apply the Rate_Limiter to `POST /api/v1/auth/password-reset/confirm` keyed on the source IP address with a default limit of 20 requests per 1-hour window.
7. WHEN the Rate_Limiter rejects a request, THE Auth_Router SHALL return HTTP 429 with the RFC 7807 envelope whose `type` is `rate_limited`, set the `Retry-After` response header to the integer number of seconds returned by the Rate_Limiter, and emit an Audit_Event of type `rate_limit_rejected` whose payload includes the endpoint and the rejecting key category (`ip` or `email`) without including the raw key value when the key category is `email`.
8. THE Rate*Limiter SHALL accept overrides for every default limit and window via environment variables prefixed with `MATCHLAYER_AUTH_RATE_LIMIT*`, listed in the design document, so a deployment can tighten or relax the policy without a code change.
9. IF Redis is unreachable when the Rate_Limiter executes a check, THEN THE Rate_Limiter SHALL return a "rejected" decision (fail-closed) and THE Auth_Router SHALL return HTTP 503 with the RFC 7807 envelope whose `type` is `rate_limiter_unavailable`.

### Requirement 11: Audit Log

**User Story:** As the platform owner, I want every security-relevant action recorded in an append-only audit log, so that I can investigate incidents and satisfy the `security.md` retention requirement.

#### Acceptance Criteria

1. THE `audit_events` table SHALL contain at minimum the columns `id` (UUIDv7 primary key), `event_type` (text), `user_id` (nullable foreign key to `users.id`), `ip_address` (nullable text), `user_agent` (nullable text, truncated to 1024 characters), `payload` (JSONB), and `created_at` (timestamp with time zone, default `now()`).
2. THE database role used by the API_App SHALL hold `INSERT` and `SELECT` privileges on `audit_events` and SHALL NOT hold `UPDATE` or `DELETE` privileges; the Alembic migration that creates the table SHALL grant exactly that privilege set.
3. WHEN the Auth_Service or its dependencies emit any of the Audit_Event types named in this specification (`registration_success`, `registration_attempt_existing_email`, `login_success`, `login_failure`, `account_locked`, `logout`, `refresh_token_rotated`, `refresh_token_reuse_detected`, `password_reset_requested`, `password_reset_confirmed`, `display_name_changed`, `account_deleted`, `rate_limit_rejected`), THE Auth_Service SHALL insert one row into `audit_events` whose `event_type` matches the named string.
4. THE Auth_Service SHALL NEVER insert a row into `audit_events` whose `payload` field contains the values of `password`, `new_password`, `password_hash`, `token` (in plaintext), `Reset_Token` (in plaintext), or any field named in `security.md` as PII other than `email` and `ip_address`.
5. THE Auth_Service SHALL truncate any `user_agent` value longer than 1024 characters before inserting it into `audit_events.user_agent`.
6. THE README runbook entry referenced by Requirement 14 SHALL document the 1-year minimum retention requirement for the `audit_events` table, the deferred-to-Phase-6 plan for archiving older rows to S3 Glacier, and the manual-for-now query a developer would use to inspect recent events.

### Requirement 12: Frontend Authentication Surface

**User Story:** As a user, I want clear, accessible registration, login, forgot-password, and reset-password pages, plus an authenticated app shell that knows whether I'm signed in, so that the auth experience matches the design system and works without surprises.

#### Acceptance Criteria

1. THE Web_App SHALL expose Auth_Pages at the routes `/register`, `/login`, `/forgot-password`, and `/reset-password`, each rendered as a centered card on a subtle background per the `design.md` "auth pages: restrained" section, each containing the MatchLayer wordmark, a single primary form, and no navigation chrome other than a link between sibling auth pages where relevant.
2. EACH form on every Auth_Page SHALL be implemented with React Hook Form using the Zod schema for the corresponding endpoint imported from `@matchlayer/shared-types`, SHALL associate every input with its label via the `for` attribute and an `id`, and SHALL announce server-side validation errors via `aria-live="polite"` on the form's error region.
3. THE `/reset-password` page SHALL read the reset token from the `?token=` query parameter and SHALL display a friendly empty state when the parameter is absent.
4. THE Authenticated_Shell SHALL be implemented as a Next.js Server Component that wraps the `(app)` route group (`/dashboard` and any future authenticated routes). It calls the API to verify the access token (using the refresh cookie when the access token is missing or expired), rendering the children when verification succeeds and redirecting to `/login?next=<original_path>` via Next.js's `redirect()` when it does not. The marketing landing at `/` is a public Server Component outside this layout.
5. THE Auth_State_Hook SHALL expose, at minimum, the values `user` (the User_Account fields `{id, email, display_name, created_at, updated_at}` or `null`), `isAuthenticated` (boolean), `isLoading` (boolean), `signIn(email, password)` (async function), `signOut()` (async function), and `refresh()` (async function).
6. THE Auth_State_Hook SHALL store the access token in memory only (a closure or React state) and SHALL NEVER write the access token to `window.localStorage`, `window.sessionStorage`, or `document.cookie`.
7. WHEN the Auth_State_Hook's `signIn` mutation receives an HTTP 423 response, THE Web_App SHALL render the literal user-facing message "Account is temporarily locked. Try again later." in the form's error region per criterion 2.
8. WHEN the Auth_State_Hook's `signIn` mutation receives an HTTP 429 response, THE Web_App SHALL render a user-facing message that includes the rounded-up number of seconds returned in the `Retry-After` response header.
9. EACH Auth_Page SHALL pass WCAG AA color-contrast in both light and dark themes for every text element, focusable element, and form-error region.

### Requirement 13: Dev-Mode Password-Reset Link Surface

**User Story:** As a developer running MatchLayer locally, I want to retrieve generated password-reset links without integrating with a real email provider, so that I can exercise the reset flow end-to-end without leaving the laptop.

#### Acceptance Criteria

1. WHEN a Reset_Token is created per Requirement 5 acceptance criterion 3 AND `MATCHLAYER_ENVIRONMENT` equals `development`, THE Dev_Reset_Link_Surface SHALL emit a single structured log line at level `info` whose payload contains the field `password_reset_link` set to the URL `${MATCHLAYER_WEB_BASE_URL}/reset-password?token=<plaintext_token>`.
2. WHEN a Reset_Token is created per Requirement 5 acceptance criterion 3 AND `MATCHLAYER_ENVIRONMENT` is not `development`, THE Dev_Reset_Link_Surface SHALL NOT emit the link to logs and SHALL NOT cache the plaintext token anywhere outside the response path of the Auth_Service.
3. THE Auth_Router SHALL expose `GET /api/v1/dev/last-reset-link` that returns HTTP 200 with a JSON body containing the most recently generated plaintext reset link when `MATCHLAYER_ENVIRONMENT` equals `development`.
4. WHEN `GET /api/v1/dev/last-reset-link` is invoked AND `MATCHLAYER_ENVIRONMENT` is not `development`, THE Auth_Router SHALL return HTTP 404 with the RFC 7807 envelope whose `type` is `not_found`, regardless of any cached state, so the endpoint behaves as if it did not exist outside development.
5. THE Dev_Reset_Link_Surface SHALL retain at most the most recent generated link in process memory; older links SHALL be evicted on each new generation.
6. THE Dev_Reset_Link_Surface SHALL NEVER persist the plaintext Reset_Token to disk, to Redis, or to any external service.

### Requirement 14: Migrations and Environment Variable Contract

**User Story:** As a developer onboarding to the repo or returning after a break, I want one Alembic migration that creates every auth table and one set of `.env.example` entries that documents every new environment variable, so that the foundation contract continues to hold.

#### Acceptance Criteria

1. THE Foundation_Repo SHALL contain an Alembic revision file at `apps/api/alembic/versions/0001_users_and_auth.py` whose `down_revision` is the foundation baseline `0000_baseline`.
2. THE migration `0001_users_and_auth` SHALL create the tables `users`, `refresh_tokens`, `password_reset_tokens`, and `audit_events` with the columns required by Requirements 1, 3, 5, 8, and 11; SHALL create the indexes required for `users.email` (unique, case-insensitive), `refresh_tokens.user_id`, `refresh_tokens.family_id`, `password_reset_tokens.user_id`, `audit_events.user_id`, and `audit_events.created_at`; AND SHALL grant `INSERT, SELECT` (and only those privileges) on `audit_events` to the API database role per Requirement 11 acceptance criterion 2.
3. THE migration `0001_users_and_auth` SHALL provide a working `downgrade()` that drops every table and index it created in reverse order.
4. THE `.env.example` file SHALL gain entries for every new environment variable introduced by this spec, including at minimum: `MATCHLAYER_JWT_SECRET`, `MATCHLAYER_AUTH_ACCESS_TOKEN_TTL_SECONDS`, `MATCHLAYER_AUTH_REFRESH_TOKEN_TTL_SECONDS`, `MATCHLAYER_AUTH_LOCKOUT_THRESHOLD`, `MATCHLAYER_AUTH_LOCKOUT_WINDOW_SECONDS`, `MATCHLAYER_AUTH_LOCKOUT_DURATION_SECONDS`, `MATCHLAYER_WEB_BASE_URL`, and the `MATCHLAYER_AUTH_RATE_LIMIT_*` overrides referenced by Requirement 10 acceptance criterion 8.
5. THE `.env.example` placeholder for `MATCHLAYER_JWT_SECRET` SHALL be a literal placeholder string longer than 32 bytes that is clearly not a real secret (for example, the literal `change-me-development-only-32+chars`) so a developer who copies the file gets a working dev environment without learning a secret-generation routine.
6. THE root README setup flow SHALL gain a step that documents how to retrieve the most recent dev-mode reset link via the Dev_Reset_Link_Surface and a step that documents how to inspect recent `audit_events` rows via `psql`.

### Requirement 15: Performance and Operational Budgets

**User Story:** As the platform owner, I want auth-endpoint latency and token-cost defaults bounded, so that authentication does not become a bottleneck or a credential-stuffing amplifier.

#### Acceptance Criteria

1. THE Login_Latency_P95 measured against a local Postgres + Redis stack on a developer laptop SHALL be less than or equal to 300 milliseconds AND the same metric measured in the GitHub Actions CI runner SHALL be less than or equal to 600 milliseconds.
2. THE Password_Hasher's Argon2id parameters SHALL be tuned so that `hash_password` returns within 100 milliseconds at the 95th percentile on a developer laptop AND within 200 milliseconds at the 95th percentile in the GitHub Actions CI runner; the parameters SHALL be documented in the design document and SHALL satisfy the lower-bound recommendations from `argon2-cffi`'s "low-memory" preset at minimum.
3. THE Auth_Service SHALL use a single async SQLAlchemy session per HTTP request, opened by the FastAPI dependency defined in `phase-1-foundation` design §6.6, so authentication endpoints do not allocate additional database connections beyond the existing pool.
4. THE `audit_events` table inserts SHALL execute within the same transaction as the auth mutation that produced them, so a failure to write the audit row aborts the auth mutation rather than leaving an unaudited side effect.

## Appendix A: Notes on requirements-analyzer feedback

The Kiro requirements analyzer ran against this document and surfaced nine clarifying questions. The decisions below are recorded so a later reader can see why each was accepted, declined, or already covered.

- **REQ-3.9 (audit event emission on failure paths).** Accepted. Requirement 3 acceptance criterion 9 was tightened to state that `refresh_token_rotated` is only emitted on the success path defined by criterion 8 and never on the failure paths in criteria 2 through 7. This was already the intent; the explicit clause closes the ambiguity.
- **REQ-4.3 (CSRF validation on logout when no refresh cookie is present).** Declined. The current Requirement 4 acceptance criterion 2 returns HTTP 204 for a logout request with no refresh cookie, which is the correct UX for an already-logged-out client and creates no security weakness because there is nothing to revoke. CSRF protection only matters when a request carries an authenticator the server would otherwise honor, and that authenticator is the refresh cookie.
- **REQ-7.6 (token rejection when other validation fails).** Already covered. Requirement 7 acceptance criteria 3 (alg allowlist), 4 (claim shape), 5 (lifetime), and 6 (`expected_type`) compose conjunctively because each is an independent SHALL clause; if any single one fails, the JWT_Service rejects the token. No textual change required.
- **REQ-8.3 (rotation when predecessor row is missing).** Declined as written. Requirement 3 acceptance criterion 6 explicitly returns HTTP 401 with `invalid_refresh_token` when a presented `jti` is absent from `refresh_tokens`. Allowing a fresh family in that case would let a forged `jti` mint a valid family at will. The rotation path runs only when the predecessor row exists, per the criterion 8 preconditions.
- **REQ-9.4 (CSRF mismatch when no refresh cookie is present).** Declined. Without a refresh cookie there is no cookie-derived authority on the request, so a CSRF mismatch on a header alone is not a meaningful attack vector. Requirement 9 acceptance criterion 3 binds the CSRF check to "requests whose `Cookie` header includes a `matchlayer_refresh` value" deliberately.
- **REQ-10.9 (Redis-down behavior).** Declined as written. Requirement 10 acceptance criterion 9 fails closed at request time, not at startup. Returning 503 globally before any request would couple the API's liveness to Redis and force the API_App to crash-loop whenever Redis blips, which contradicts the runtime-resilience guidance in `phase-1-foundation` requirement 4.13. Per-request fail-closed gives the same security outcome without the liveness coupling.
- **REQ-10.7 (audit emission on the success path).** Already covered. Requirement 10 acceptance criterion 7 only fires on the rejection branch ("When the Rate_Limiter rejects a request"); the success path does not emit `rate_limit_rejected`. No textual change required.
- **REQ-13.3 (dev endpoint returns HTTP 200 in production with no content).** Declined. Requirement 13 acceptance criterion 4 already returns HTTP 404 with the RFC 7807 `not_found` envelope outside development, which is exactly the "completely invisible" behavior the analyzer asks for; the analyzer's preview misread the criterion. No textual change required.
- **REQ-13.6 (logging plaintext reset tokens to log files).** Accepted with intent. Requirement 13 acceptance criterion 1 is the deliberate single carve-out: the dev-mode link is written to a log line and only when `MATCHLAYER_ENVIRONMENT=development`. Requirement 13 acceptance criterion 2 forbids logging the link in any other environment. Requirement 13 acceptance criterion 6 forbids persisting the plaintext token to disk, Redis, or external services. Together these are the "log in dev, never log in prod, never persist" contract; no textual change required.
