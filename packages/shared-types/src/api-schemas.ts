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

export const schemas = {
  HealthResponse,
  HealthUnhealthyResponse,
};

const endpoints = makeApi([
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
