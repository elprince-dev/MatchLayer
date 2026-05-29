import { makeApi, Zodios, type ZodiosOptions } from "@zodios/core";
import { z } from "zod";

const HealthResponse = z
  .object({ status: z.string().default("ok") })
  .partial()
  .passthrough();
const HealthUnhealthyResponse = z
  .object({
    status: z.string().optional().default("unhealthy"),
    reason: z.string(),
  })
  .passthrough();
const RegisterRequest = z.object({
  email: z.string().email(),
  password: z.string().min(12),
  display_name: z.union([z.string(), z.null()]).optional(),
});
const UserResponse = z
  .object({
    id: z.string(),
    email: z.string(),
    display_name: z.string(),
    created_at: z.string().datetime({ offset: true }),
    updated_at: z.string().datetime({ offset: true }),
  })
  .passthrough();
const TokenPairResponse = z.object({
  access_token: z.string(),
  user: UserResponse,
});
const ValidationError = z
  .object({
    loc: z.array(z.union([z.string(), z.number()])),
    msg: z.string(),
    type: z.string(),
    input: z.unknown().optional(),
    ctx: z.object({}).partial().passthrough().optional(),
  })
  .passthrough();
const HTTPValidationError = z
  .object({ detail: z.array(ValidationError) })
  .partial()
  .passthrough();
const LoginRequest = z.object({
  email: z.string().email(),
  password: z.string().min(1),
});
const MeResponse = z
  .object({
    id: z.string(),
    email: z.string(),
    display_name: z.string(),
    created_at: z.string().datetime({ offset: true }),
    updated_at: z.string().datetime({ offset: true }),
  })
  .passthrough();
const MePatchRequest = z
  .object({ display_name: z.union([z.string(), z.null()]) })
  .partial();
const PasswordResetRequestRequest = z.object({ email: z.string().email() });
const PasswordResetConfirmRequest = z.object({
  token: z.string().min(1),
  new_password: z.string().min(12),
});
const LastResetLinkResponse = z
  .object({
    link: z.union([z.string(), z.null()]),
    created_at: z.union([z.string(), z.null()]),
  })
  .partial();

export const schemas = {
  HealthResponse,
  HealthUnhealthyResponse,
  RegisterRequest,
  UserResponse,
  TokenPairResponse,
  ValidationError,
  HTTPValidationError,
  LoginRequest,
  MeResponse,
  MePatchRequest,
  PasswordResetRequestRequest,
  PasswordResetConfirmRequest,
  LastResetLinkResponse,
};

const endpoints = makeApi([
  {
    method: "post",
    path: "/api/v1/auth/login",
    alias: "login_api_v1_auth_login_post",
    requestFormat: "json",
    parameters: [
      {
        name: "body",
        type: "Body",
        schema: LoginRequest,
      },
    ],
    response: TokenPairResponse,
    errors: [
      {
        status: 422,
        description: `Validation Error`,
        schema: HTTPValidationError,
      },
    ],
  },
  {
    method: "post",
    path: "/api/v1/auth/logout",
    alias: "logout_api_v1_auth_logout_post",
    requestFormat: "json",
    response: z.void(),
  },
  {
    method: "get",
    path: "/api/v1/auth/me",
    alias: "get_me_api_v1_auth_me_get",
    requestFormat: "json",
    response: MeResponse,
  },
  {
    method: "patch",
    path: "/api/v1/auth/me",
    alias: "patch_me_api_v1_auth_me_patch",
    requestFormat: "json",
    parameters: [
      {
        name: "body",
        type: "Body",
        schema: MePatchRequest,
      },
    ],
    response: MeResponse,
    errors: [
      {
        status: 422,
        description: `Validation Error`,
        schema: HTTPValidationError,
      },
    ],
  },
  {
    method: "post",
    path: "/api/v1/auth/password-reset/confirm",
    alias: "password_reset_confirm_api_v1_auth_password_reset_confirm_post",
    requestFormat: "json",
    parameters: [
      {
        name: "body",
        type: "Body",
        schema: PasswordResetConfirmRequest,
      },
    ],
    response: z.void(),
    errors: [
      {
        status: 422,
        description: `Validation Error`,
        schema: HTTPValidationError,
      },
    ],
  },
  {
    method: "post",
    path: "/api/v1/auth/password-reset/request",
    alias: "password_reset_request_api_v1_auth_password_reset_request_post",
    requestFormat: "json",
    parameters: [
      {
        name: "body",
        type: "Body",
        schema: z.object({ email: z.string().email() }),
      },
    ],
    response: z.unknown(),
    errors: [
      {
        status: 422,
        description: `Validation Error`,
        schema: HTTPValidationError,
      },
    ],
  },
  {
    method: "post",
    path: "/api/v1/auth/refresh",
    alias: "refresh_api_v1_auth_refresh_post",
    requestFormat: "json",
    response: TokenPairResponse,
  },
  {
    method: "post",
    path: "/api/v1/auth/register",
    alias: "register_api_v1_auth_register_post",
    requestFormat: "json",
    parameters: [
      {
        name: "body",
        type: "Body",
        schema: RegisterRequest,
      },
    ],
    response: TokenPairResponse,
    errors: [
      {
        status: 422,
        description: `Validation Error`,
        schema: HTTPValidationError,
      },
    ],
  },
  {
    method: "get",
    path: "/api/v1/dev/last-reset-link",
    alias: "last_reset_link_api_v1_dev_last_reset_link_get",
    requestFormat: "json",
    response: LastResetLinkResponse,
  },
  {
    method: "get",
    path: "/healthz",
    alias: "healthz_healthz_get",
    description: `Probe Postgres and return the canonical health envelope.

The handler intentionally returns :class:&#x60;fastapi.responses.JSONResponse&#x60;
rather than the Pydantic model directly so the failure branch can
set the 503 status code without raising an exception (which would
route through the RFC 7807 catch-all in
:mod:&#x60;matchlayer_api.core.errors&#x60; and produce the wrong response
shape for a healthcheck).

Args:
    session: Request-scoped async SQLAlchemy session, yielded by
        :func:&#x60;~matchlayer_api.core.db.get_session&#x60;. Tests override
        this dependency via FastAPI&#x27;s &#x60;&#x60;app.dependency_overrides&#x60;&#x60;
        mapping (task 3.11).

Returns:
    :class:&#x60;JSONResponse&#x60; with status 200 and body &#x60;&#x60;{&quot;status&quot;: &quot;ok&quot;}&#x60;&#x60;
    when the &#x60;&#x60;SELECT 1&#x60;&#x60; probe succeeds; status 503 and body
    &#x60;&#x60;{&quot;status&quot;: &quot;unhealthy&quot;, &quot;reason&quot;: &quot;database_unreachable&quot;}&#x60;&#x60;
    when SQLAlchemy raises any subclass of :class:&#x60;SQLAlchemyError&#x60;.`,
    requestFormat: "json",
    response: z
      .object({ status: z.string().default("ok") })
      .partial()
      .passthrough(),
    errors: [
      {
        status: 503,
        description: `Postgres is unreachable. The probe never returns DSN or credentials in the response body.`,
        schema: HealthUnhealthyResponse,
      },
    ],
  },
]);

export const api = new Zodios(endpoints);

export function createApiClient(baseUrl: string, options?: ZodiosOptions) {
  return new Zodios(baseUrl, endpoints, options);
}
