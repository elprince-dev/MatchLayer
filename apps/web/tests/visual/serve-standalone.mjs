// @ts-check
/**
 * Serve the Next.js **standalone** production build for the Playwright visual
 * gates (task 9.1; design Section 9.1).
 *
 * ## Why this script exists (the bug it fixes)
 * `apps/web/next.config.mjs` sets `output: "standalone"` (required by
 * `infra/docker/web.Dockerfile`). With that option, **`next start` does not
 * work** — Next.js prints `⚠ "next start" does not work with "output:
 * standalone" configuration. Use "node .next/standalone/server.js" instead` and
 * serves an app whose client JS chunks 404/500, so the page hydrates into the
 * `__next_error__` boundary. The earlier `webServer.command` (`next start`) hit
 * exactly that, and every gate failed against an error page.
 *
 * The standalone bundle also does **not** include `.next/static` or `public`
 * (Next.js docs; mirrored by the Dockerfile), so those must be staged next to
 * the generated `server.js` before launching it:
 *
 *   .next/standalone/apps/web/server.js          ← entrypoint (generated)
 *   .next/standalone/apps/web/.next/static/...    ← copied from .next/static
 *   .next/standalone/apps/web/public/...          ← copied from public/
 *
 * This script performs that staging copy (idempotently) and then execs the
 * standalone server, inheriting the environment — crucially `PLAYWRIGHT_VISUAL`,
 * which the harness route reads to enable `/visual-harness/*`. It assumes
 * `next build` has already run (the `test:visual` script chains it first).
 */

import { spawn } from "node:child_process";
import { cpSync, existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
// tests/visual → apps/web
const webRoot = path.resolve(here, "..", "..");

const standaloneAppDir = path.join(
  webRoot,
  ".next",
  "standalone",
  "apps",
  "web",
);
const serverEntry = path.join(standaloneAppDir, "server.js");

if (!existsSync(serverEntry)) {
  console.error(
    `[serve-standalone] Missing ${serverEntry}.\n` +
      `Run \`next build\` first (the test:visual script does this automatically).`,
  );
  process.exit(1);
}

/** Stage an asset dir into the standalone tree (overwrite to stay in sync). */
function stage(srcRel, destAbs) {
  const src = path.join(webRoot, srcRel);
  if (!existsSync(src)) {
    return;
  }
  cpSync(src, destAbs, { recursive: true });
}

// .next/static and public are excluded from the standalone bundle; copy them to
// the workspace-relative paths the server expects (same layout as the Docker image).
stage(
  path.join(".next", "static"),
  path.join(standaloneAppDir, ".next", "static"),
);
stage("public", path.join(standaloneAppDir, "public"));

const port = process.env.PORT ?? "3000";
const hostname = process.env.HOSTNAME ?? "127.0.0.1";

// Exec the standalone server, inheriting the env (PLAYWRIGHT_VISUAL, etc.).
const child = spawn(process.execPath, [serverEntry], {
  stdio: "inherit",
  env: { ...process.env, PORT: port, HOSTNAME: hostname },
});

const forward = (signal) => () => child.kill(signal);
process.on("SIGTERM", forward("SIGTERM"));
process.on("SIGINT", forward("SIGINT"));
child.on("exit", (code) => process.exit(code ?? 0));
