// Curated public API of `@matchlayer/shared-types`.
//
// This module is the single import surface for sibling packages. The two files
// it re-exports from — `./api-types` and `./api-schemas` — are auto-generated
// by `pnpm codegen` from the FastAPI OpenAPI spec and must not be imported
// directly by app code. Keeping consumers behind named curated exports here
// (e.g. `LoginRequest`, `LoginRequestSchema`) means the ugly path-indexed
// `paths["/api/v1/auth/login"]["post"]["requestBody"]["content"]...` type and
// the `schemas.LoginRequest` Zod object never leak into the rest of the
// monorepo. Satisfies AC 7.9 (design §8.4) and OpenAPI Codegen Impact.

import type { paths } from "./api-types";
import { schemas } from "./api-schemas";

// ---------------------------------------------------------------------------
// Health
// ---------------------------------------------------------------------------

export type HealthResponse =
  paths["/healthz"]["get"]["responses"]["200"]["content"]["application/json"];

export const HealthResponseSchema = schemas.HealthResponse;

// ---------------------------------------------------------------------------
// Auth — Register
// ---------------------------------------------------------------------------

export type RegisterRequest =
  paths["/api/v1/auth/register"]["post"]["requestBody"]["content"]["application/json"];
export type RegisterResponse =
  paths["/api/v1/auth/register"]["post"]["responses"]["201"]["content"]["application/json"];

export const RegisterRequestSchema = schemas.RegisterRequest;
export const RegisterResponseSchema = schemas.TokenPairResponse;

// ---------------------------------------------------------------------------
// Auth — Login
// ---------------------------------------------------------------------------

export type LoginRequest =
  paths["/api/v1/auth/login"]["post"]["requestBody"]["content"]["application/json"];
export type LoginResponse =
  paths["/api/v1/auth/login"]["post"]["responses"]["200"]["content"]["application/json"];

export const LoginRequestSchema = schemas.LoginRequest;
export const LoginResponseSchema = schemas.TokenPairResponse;

// ---------------------------------------------------------------------------
// Auth — Refresh
// ---------------------------------------------------------------------------

export type RefreshResponse =
  paths["/api/v1/auth/refresh"]["post"]["responses"]["200"]["content"]["application/json"];

export const RefreshResponseSchema = schemas.TokenPairResponse;

// ---------------------------------------------------------------------------
// Auth — Logout (no body, 204)
// ---------------------------------------------------------------------------
// Logout has no request body and a 204 response, so no curated alias.

// ---------------------------------------------------------------------------
// Auth — Password reset
// ---------------------------------------------------------------------------

export type PasswordResetRequestRequest =
  paths["/api/v1/auth/password-reset/request"]["post"]["requestBody"]["content"]["application/json"];

export const PasswordResetRequestRequestSchema =
  schemas.PasswordResetRequestRequest;

export type PasswordResetConfirmRequest =
  paths["/api/v1/auth/password-reset/confirm"]["post"]["requestBody"]["content"]["application/json"];

export const PasswordResetConfirmRequestSchema =
  schemas.PasswordResetConfirmRequest;

// ---------------------------------------------------------------------------
// Auth — /me
// ---------------------------------------------------------------------------

export type MeResponse =
  paths["/api/v1/auth/me"]["get"]["responses"]["200"]["content"]["application/json"];

export const MeResponseSchema = schemas.MeResponse;

export type MePatchRequest =
  paths["/api/v1/auth/me"]["patch"]["requestBody"]["content"]["application/json"];

export const MePatchRequestSchema = schemas.MePatchRequest;

// ---------------------------------------------------------------------------
// Shared user response shape (embedded in token-pair responses)
// ---------------------------------------------------------------------------

export const UserResponseSchema = schemas.UserResponse;

// ---------------------------------------------------------------------------
// Resumes — upload / get (safe field set: no extracted_text or storage_key)
// ---------------------------------------------------------------------------

export type ResumeResponse =
  paths["/api/v1/resumes"]["post"]["responses"]["201"]["content"]["application/json"];

export const ResumeResponseSchema = schemas.ResumeResponse;

// ---------------------------------------------------------------------------
// Resumes — list (cursor-paginated)
// ---------------------------------------------------------------------------

export type ResumeListResponse =
  paths["/api/v1/resumes"]["get"]["responses"]["200"]["content"]["application/json"];

export const ResumeListResponseSchema = schemas.ResumeListResponse;

// ---------------------------------------------------------------------------
// Matches — create
// ---------------------------------------------------------------------------

export type CreateMatchRequest =
  paths["/api/v1/matches"]["post"]["requestBody"]["content"]["application/json"];
export type MatchResponse =
  paths["/api/v1/matches"]["post"]["responses"]["201"]["content"]["application/json"];

export const CreateMatchRequestSchema = schemas.CreateMatchRequest;
export const MatchResponseSchema = schemas.MatchResponse;

// ---------------------------------------------------------------------------
// Matches — list (items omit job_description_text)
// ---------------------------------------------------------------------------

export type MatchListResponse =
  paths["/api/v1/matches"]["get"]["responses"]["200"]["content"]["application/json"];

export const MatchListResponseSchema = schemas.MatchListResponse;
