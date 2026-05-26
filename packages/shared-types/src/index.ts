// Curated public API of `@matchlayer/shared-types`.
//
// This module is the single import surface for sibling packages. The two files
// it re-exports from — `./api-types` and `./api-schemas` — are auto-generated
// by `pnpm codegen` from the FastAPI OpenAPI spec and must not be imported
// directly by app code. Keeping consumers behind named curated exports here
// (e.g. `HealthResponse`, `HealthResponseSchema`) means the ugly path-indexed
// `paths["/healthz"]["get"]["responses"]["200"]["content"]["application/json"]`
// type and the `schemas.HealthResponse` Zod object never leak into the rest
// of the monorepo. Satisfies AC 7.9 (design §8.4).

import type { paths } from "./api-types";
import { schemas } from "./api-schemas";

/**
 * Body returned by `GET /healthz` on the success path.
 *
 * Derived from the generated OpenAPI types so it stays exact: a
 * `{ status: "ok" }` literal. Used by the web app to type the parsed
 * response from the health probe.
 */
export type HealthResponse =
  paths["/healthz"]["get"]["responses"]["200"]["content"]["application/json"];

/**
 * Zod schema validating the `GET /healthz` 200 response body.
 *
 * Re-exported from the generated `schemas` object under the curated
 * `HealthResponseSchema` name so consumers do not depend on the
 * `openapi-zod-client` output shape.
 */
export const HealthResponseSchema = schemas.HealthResponse;
