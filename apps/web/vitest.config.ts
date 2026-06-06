import { defineConfig, configDefaults } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "node",
    include: [
      "tests/**/*.{test,spec}.{ts,tsx}",
      "src/**/*.{test,spec}.{ts,tsx}",
    ],
    // `tests/visual/**` holds the Playwright visual/layout specs (task 9.1's
    // `results.spec.ts`, plus task 9.2's upload/auth/landing specs). Those use
    // `@playwright/test`'s `test()`/`expect()` runner, which throws under
    // Vitest ("Playwright Test did not expect test() to be called here"), so
    // Vitest must never COLLECT them — they run only via `pnpm test:visual`
    // (Playwright). The `include` globs above match `*.spec.ts` broadly, so we
    // exclude the whole visual directory here (keeping Vitest's defaults —
    // node_modules, dist, .next/build, etc. — which a bare `exclude` would
    // otherwise replace). This is the owned, shared boundary between the two
    // runners (tasks 9.1/9.2).
    exclude: [...configDefaults.exclude, "tests/visual/**"],
    passWithNoTests: true,
  },
  resolve: {
    alias: {
      "@": path.resolve(here, "src"),
      // Mirror the tsconfig `paths` mapping so tests (and the components under
      // test) resolve the workspace package to its TypeScript source. The
      // package's `main`/`exports` point at `./src/index.ts`, but Vite needs
      // the explicit alias to follow it from inside the app's test run.
      "@matchlayer/shared-types": path.resolve(
        here,
        "../../packages/shared-types/src",
      ),
    },
  },
});
