# 0002 — Monorepo layout

**Status:** Accepted
**Date:** 2026-05-23

## Context

MatchLayer has a Next.js frontend, a FastAPI backend, a Python ML codebase (training, evals), AWS infrastructure code, and shared TypeScript libraries. Polyrepo would mean cross-repo PRs for every cross-cutting change — painful for a solo dev. Multiple monorepo layouts exist:

- Flat `packages/` (Lerna / classic Yarn workspaces).
- `apps/` + `packages/` split (Nx / Turborepo / modern JS monorepos).
- Everything under one bag (some Python monorepos).

## Decision

Single monorepo, single git repo. Top-level layout:

```
matchlayer/
├── apps/         # Deployable units (web, api)
├── packages/     # JS/TS libraries imported by apps
├── ml/           # Python ML pipelines + eval suites
├── infra/        # Dockerfiles, CDK, CI workflow definitions
├── docs/         # ADRs, runbooks
└── .kiro/        # Steering, specs, hooks
```

`apps/` + `packages/` follows the Nx / Turborepo convention. `ml/` and `infra/` sit at the top level (not under `apps/` or `packages/`) because they have different toolchains (Python via uv, IaC via CDK) and lifecycles.

Package managers: **pnpm** for JS/TS, **uv** for Python. No full Nx orchestration unless we outgrow that. Revisit if multiple JS apps appear.

## Consequences

**Positive**
- One PR can touch frontend + backend + infra together — common during refactors.
- Shared TS types between web and the OpenAPI-generated client live in `packages/shared-types/`.
- `ml/` and `infra/` remain visually separated, signaling their different lifecycles.
- pnpm's strict isolation catches phantom dependencies that npm hoisting hides.

**Negative**
- Mixed-language repo means CI workflows are more complex than a single-language repo.
- pnpm has a small learning curve over npm (covered in conventions).
- Python and TypeScript tooling don't share dependencies; each has its own lockfile.

## Alternatives considered

- **Polyrepo:** rejected. Cross-cutting changes too painful for a solo dev with AI assistance.
- **Flat `packages/`:** rejected. Doesn't enforce the apps-vs-libs distinction. `apps` can depend on `packages`; the reverse is structurally disallowed under the chosen layout.
- **Everything under `apps/`** (including `ml/`): considered. Rejected because notebooks and training scripts aren't "apps" in the deployable sense, and bundling them under `apps/api/` would bloat the API container.
- **`platform/` group for `ml/` + `infra/`:** considered for visual cleanliness. Rejected because the two have meaningfully different lifecycles and grouping them under one folder hid that.
