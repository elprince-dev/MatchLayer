# `@matchlayer/web`

Next.js (App Router, TypeScript) frontend for MatchLayer.

This package is part of the `matchlayer` pnpm workspace; commands are typically
run from the repository root via `pnpm --filter @matchlayer/web <script>`.

## Scripts

| Script      | What it does                                    |
| ----------- | ----------------------------------------------- |
| `dev`       | Start the Next.js dev server on port 3000       |
| `build`     | Production build (`output: "standalone"`)       |
| `start`     | Start the production server                     |
| `lint`      | ESLint (flat config, Next + TypeScript presets) |
| `typecheck` | `tsc --noEmit`                                  |
| `test`      | `vitest run --passWithNoTests`                  |
| `format`    | `prettier --check` over `src/`                  |

## Local dev

From the repo root:

```bash
pnpm install
pnpm --filter @matchlayer/web dev
```

Required environment variables are documented in the repo-root `.env.example`.

## Production image

Built by `infra/docker/web.Dockerfile`, which depends on
`next.config.mjs` setting `output: "standalone"`.
