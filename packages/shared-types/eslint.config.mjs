// Flat ESLint config for @matchlayer/shared-types.
//
// `src/api-types.ts` and `src/api-schemas.ts` are produced by `pnpm codegen`
// (openapi-typescript + openapi-zod-client) and must not be linted — the
// generators emit code patterns we don't control. The same paths are listed
// in `.prettierignore` so the format/lint gates agree.
//
// `scripts/codegen.mjs` is a Node ESM script that uses `console`/`process`;
// the `node` globals are pulled in from the `globals` package (already a
// transitive devDep of typescript-eslint) so the recommended `no-undef`
// rule doesn't flag those references.
import js from "@eslint/js";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
  {
    ignores: [
      "src/api-types.ts",
      "src/api-schemas.ts",
      "node_modules/**",
      "dist/**",
    ],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      globals: {
        ...globals.node,
      },
    },
  },
);
