# 0005 — Package managers: pnpm + uv

**Status:** Accepted
**Date:** 2026-05-23

## Context

The monorepo is mixed-language: TypeScript (frontend, shared libs, CDK) and Python (backend, ML, eval). Each ecosystem has multiple package managers. The choice affects daily developer experience, monorepo workspace ergonomics, install speed, and supply-chain security.

JS/TS options: npm, yarn, pnpm.
Python options: pip + venv, poetry, uv.

## Decision

- **JS/TS:** **pnpm**, configured via `pnpm-workspace.yaml`.
- **Python:** **uv**, configured per-package via `pyproject.toml` and a top-level `uv.lock` per Python project.

## Rationale

### pnpm

- **Strict dependency isolation.** pnpm's symlinked `node_modules/` only allows imports of declared dependencies. npm's hoisted layout silently allows phantom dependencies that break in unrelated environments.
- **Disk efficiency + speed.** Content-addressable global store; same package across multiple workspace projects is stored once. Installs are typically 2–3x faster than npm in monorepos.
- **First-class workspaces.** `pnpm --filter ./apps/web build` is clean and well-documented.
- **Industry direction.** Vercel, Vite, Astro, Nuxt, and most modern OSS projects use pnpm.
- **Lockfile (`pnpm-lock.yaml`)** is well-defined and committed.

### uv

- **Speed.** uv is dramatically faster than pip + venv or poetry on resolution and install — important when CI runs on every PR.
- **Single-binary tool.** Replaces pip, pip-tools, virtualenv, pyenv, and partly poetry.
- **Lockfile (`uv.lock`)** committed; CI uses `uv sync --frozen`.
- **Standard `pyproject.toml`** — no proprietary metadata, easy to switch back to poetry or vanilla pip if uv ever stalls.
- **Compatible with security tooling.** `pip-audit` works against uv-managed projects since uv produces standard lockfile + `pyproject.toml` outputs.

## Consequences

**Positive**

- Both tools are fast, both have strict lockfiles, both work cleanly in CI.
- Both are the modern picks in their respective ecosystems — good resume signal and good AI-assistant familiarity.

**Negative**

- Two different package managers to remember. Mitigated by the same conceptual model: install, lock, sync, run.
- pnpm has a small ramp from npm. Listed in `conventions.md`.
- uv is younger than poetry. If uv stalls, falling back to poetry is straightforward (same `pyproject.toml`).

## Alternatives considered

- **npm + pip:** rejected. Default tooling, but slower, no monorepo workspace ergonomics for npm, and pip lacks a real lockfile workflow without extra tools.
- **yarn + poetry:** considered. yarn classic is unmaintained; yarn berry is fine but less momentum than pnpm. Poetry is solid but slower than uv.
- **One tool for both ecosystems** (e.g., Bazel): vastly over-engineered for this scale.
