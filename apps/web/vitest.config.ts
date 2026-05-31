import { defineConfig } from "vitest/config";
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
