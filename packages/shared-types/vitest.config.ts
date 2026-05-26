import { defineConfig } from "vitest/config";

// Type-level tests don't need the DOM. The shared-types package may have no
// tests in this spec; --passWithNoTests in package.json keeps CI green until
// a sibling spec adds one.
export default defineConfig({
  test: {
    environment: "node",
    include: ["src/**/*.{test,spec}.ts", "tests/**/*.{test,spec}.ts"],
    passWithNoTests: true,
  },
});
