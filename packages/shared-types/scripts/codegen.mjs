/**
 * MatchLayer OpenAPI → TypeScript + Zod codegen orchestrator.
 *
 * Design reference: §8.1, §8.2.
 * Requirements covered: 7.2, 7.3, 7.4, 7.5, 7.6, 7.7.
 *
 * The FastAPI app (`apps/api`) is the single source of truth for the API
 * contract. This script runs four steps, in order, to keep
 * `packages/shared-types/src/api-types.ts` and
 * `packages/shared-types/src/api-schemas.ts` in lockstep with that contract:
 *
 *   1. Shell out to the API's OpenAPI dump CLI
 *      (`uv run --project ../../apps/api python -m matchlayer_api.tools.dump_openapi`)
 *      and capture its stdout into a transient `openapi.json` at the package root.
 *      This is captured-then-written via `fs.writeFileSync`, NOT shell redirection,
 *      because `execa` does not invoke a shell by default — keeping the call
 *      shell-free removes any shell-injection surface for argv values.
 *   2. Run `openapi-typescript openapi.json --output src/api-types.ts` to emit the
 *      TypeScript `paths`/`components` type tree.
 *   3. Run `openapi-zod-client openapi.json --output src/api-schemas.ts --with-alias`
 *      to emit Zod schemas for runtime validation and form resolvers.
 *   4. Delete `openapi.json` ON SUCCESS only. The file is a transient artifact and
 *      is gitignored (see Task 1.1). If any of steps 1–3 fails, we deliberately
 *      LEAVE `openapi.json` in place so a developer can `cat` it to debug.
 *
 * Always-re-derive invariant
 * --------------------------
 * Step 1 always overwrites `openapi.json` with fresh stdout from a live
 * `app.openapi()` call. The script never reads `openapi.json` itself, never
 * falls back to a cached/committed copy, and never accepts a pre-existing
 * `openapi.json` as input on its own. Steps 2 and 3 only ever consume the file
 * just produced by a successful step 1. This is the contract called out in
 * Requirement 7.7 and Design §8.2.
 *
 * Self-locating cwd
 * -----------------
 * We resolve the script's own directory via `fileURLToPath(import.meta.url)` and
 * use the parent (`packages/shared-types/`) as the `cwd` for every shell-out.
 * That means the script behaves identically whether it's invoked as
 * `pnpm codegen` (from the repo root, via the root `package.json` script that
 * does `node packages/shared-types/scripts/codegen.mjs`),
 * `pnpm --filter @matchlayer/shared-types codegen`, or
 * `cd packages/shared-types && pnpm codegen`. All relative paths used below
 * (`../../apps/api`, `openapi.json`, `src/api-types.ts`,
 * `src/api-schemas.ts`) are resolved against the package root, not the
 * caller's cwd.
 *
 * Binary resolution
 * -----------------
 * `openapi-typescript` and `openapi-zod-client` are invoked via `pnpm exec` so
 * we always pick up the workspace's pinned versions (declared in this
 * package's `devDependencies`). Going through `pnpm exec` is more robust than
 * a direct `node_modules/.bin/<binary>` call because pnpm's hoisting layout
 * (virtual store, `node_modules/.pnpm`) does not always materialise binaries
 * at the canonical `.bin` path the way npm does, especially in monorepos.
 * `pnpm exec` consults pnpm's own resolver and works regardless of
 * `shamefully-hoist` settings.
 */

import { execa } from "execa";
import { existsSync, unlinkSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

// ---------------------------------------------------------------------------
// Resolve paths.
// ---------------------------------------------------------------------------

// The directory containing this script: `<repo>/packages/shared-types/scripts`.
const scriptDir = path.dirname(fileURLToPath(import.meta.url));

// The package root: `<repo>/packages/shared-types`. Every shell-out below
// uses this as `cwd`, so all relative paths are package-rooted.
const packageRoot = path.resolve(scriptDir, "..");

// Path to the transient OpenAPI artifact. Gitignored. Deleted on success.
const openapiJsonPath = path.join(packageRoot, "openapi.json");

// ---------------------------------------------------------------------------
// Step 1 — Dump the live OpenAPI spec from the FastAPI app.
//
// `--project ../../apps/api` makes uv resolve the API's pyproject.toml and
// uv.lock, ensuring this command runs against the exact pinned API
// dependencies even when invoked from anywhere in the workspace. The Python
// module is `matchlayer_api.tools.dump_openapi` (see Design §6.9): it imports
// `create_app()`, calls `app.openapi()`, and writes the JSON spec to stdout.
// `app.openapi()` is a pure synchronous traversal of FastAPI's routers — it
// does NOT enter the lifespan, so this command works without Postgres/Redis
// running, but it DOES require `.env` so Pydantic Settings can validate at
// import time.
//
// `--env-file ../../.env` instructs uv to load the repo-root `.env` into the
// spawned Python process's environment before `dump_openapi` imports
// `Settings`. We do NOT rely on Pydantic Settings's own `env_file=".env"`
// lookup here because that lookup is resolved against the Python process's
// cwd — and this script runs Python with cwd = `packages/shared-types/`, not
// the repo root, so the package's relative search would miss the repo-root
// `.env`. Pushing the load to uv keeps the codegen behavior identical
// regardless of where `pnpm codegen` is invoked from. The path is relative to
// `packageRoot` (i.e. resolves to `<repo>/.env`).
//
// We capture stdout (default execa pipe) and inherit stderr so any error
// output from uv/Python (missing `.env`, ImportError, etc.) is shown live in
// the developer's terminal. If the child exits non-zero, execa throws and the
// error propagates out of this script.
// ---------------------------------------------------------------------------

console.log("[codegen] step 1/4: dumping OpenAPI spec from FastAPI app");
const dumpResult = await execa(
  "uv",
  [
    "run",
    "--project",
    "../../apps/api",
    "--env-file",
    "../../.env",
    "python",
    "-m",
    "matchlayer_api.tools.dump_openapi",
  ],
  {
    cwd: packageRoot,
    stderr: "inherit",
  },
);

// `dump_openapi.py` writes UTF-8 JSON to stdout. We persist it verbatim so
// `openapi-typescript` and `openapi-zod-client` can each open it as a normal
// file. Using `writeFileSync` (synchronous) is fine here — the spec is small
// and the rest of the pipeline blocks on it anyway.
writeFileSync(openapiJsonPath, dumpResult.stdout, "utf8");

// ---------------------------------------------------------------------------
// Step 2 — Generate TypeScript types from the spec.
// ---------------------------------------------------------------------------

console.log("[codegen] step 2/4: generating src/api-types.ts");
await execa(
  "pnpm",
  [
    "exec",
    "openapi-typescript",
    "openapi.json",
    "--output",
    "src/api-types.ts",
  ],
  {
    cwd: packageRoot,
    stdio: "inherit",
  },
);

// ---------------------------------------------------------------------------
// Step 3 — Generate Zod schemas from the spec.
//
// `--with-alias` makes openapi-zod-client emit named exports (e.g.
// `HealthResponseSchema`) keyed off operation/response names rather than the
// anonymous numeric aliases it uses by default. Task 5.4 re-exports those
// named schemas through `src/index.ts`.
// ---------------------------------------------------------------------------

console.log("[codegen] step 3/4: generating src/api-schemas.ts");
await execa(
  "pnpm",
  [
    "exec",
    "openapi-zod-client",
    "openapi.json",
    "--output",
    "src/api-schemas.ts",
    "--with-alias",
  ],
  {
    cwd: packageRoot,
    stdio: "inherit",
  },
);

// ---------------------------------------------------------------------------
// Step 4 — Delete the transient artifact on full success.
//
// We only reach this point if all three previous steps succeeded; any earlier
// failure throws and skips this cleanup, leaving `openapi.json` on disk for
// the developer to inspect. The `existsSync` guard is defensive: a future
// refactor could move the unlink ahead of step 4, and we want the cleanup to
// be idempotent in either case.
// ---------------------------------------------------------------------------

if (existsSync(openapiJsonPath)) {
  unlinkSync(openapiJsonPath);
}
console.log("[codegen] step 4/4: cleaned up openapi.json");
console.log("[codegen] done");

// Process exit code defaults to 0; any unhandled rejection above propagates as
// a non-zero exit via Node's default unhandled-rejection handler, which
// satisfies the "any non-zero exit must propagate" contract for steps 1–3
// (Requirement 7.7, Design §8.2).
