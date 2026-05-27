# syntax=docker/dockerfile:1.7
#
# MatchLayer Web — production container image (Next.js App Router, standalone).
#
# Acceptance criteria (Phase 1 spec, Requirement 10):
#   10.2  Located at infra/docker/web.Dockerfile.
#   10.3  Base images pinned by digest; final stage is a minimal distroless image.
#   10.4  Runs as a non-root user with UID >= 10000 (distroless `nonroot` is UID 65532).
#   10.5  Runnable with `--read-only`. Required writable scratch is /tmp, mounted as tmpfs.
#   10.6  HEALTHCHECK targets GET / on the web app.
#   10.8  Final image contains only the Next.js standalone server output, its Node
#         runtime, and its production-only dependencies.
#   10.9  `docker build -f infra/docker/web.Dockerfile .` from a fresh clone exits 0.
# Design reference: phase-1-foundation §11.2.
#
# Read-only runtime contract:
#   docker run --read-only --tmpfs /tmp -p 3000:3000 <image>
#
# The Next.js standalone server (apps/web/server.js) does not write to disk at runtime.
# /tmp is mounted as tmpfs to absorb any transient scratch (Node module loader caches,
# v8 compile cache, OS-level temp files for streaming bodies). Do not unseal the rootfs
# in production — every other writable path is a violation.
#
# Build context is the repository root, so paths below are repo-relative:
#   docker build -f infra/docker/web.Dockerfile .

# -----------------------------------------------------------------------------
# Stage 1: builder
# -----------------------------------------------------------------------------
# Tag pin (for human review): docker.io/library/node:24-bookworm-slim
# Debian-bookworm based; ships Node.js 24 LTS — same major as the distroless final stage.
FROM node@sha256:242549cd46785b480c832479a730f4f2a20865d61ea2e404fdb2a5c3d3b73ecf AS builder

# Activate the pnpm version pinned by `packageManager` in the root package.json.
# corepack downloads it on first invocation and caches it in the builder layer.
RUN corepack enable

WORKDIR /repo

# Resolve the dependency graph first. Copying lockfile + workspace manifests + every
# package's package.json (without source) lets `pnpm install` produce a complete
# node_modules layer that is independent of source edits, so changes under apps/web/src/
# don't bust this cache.
COPY pnpm-lock.yaml pnpm-workspace.yaml package.json ./
COPY apps/web/package.json apps/web/
COPY packages/shared-types/package.json packages/shared-types/

RUN pnpm install --frozen-lockfile

# Now bring in the rest of the source tree and run the production build. The Next.js
# `output: "standalone"` setting (apps/web/next.config.mjs) emits a self-contained
# server bundle under apps/web/.next/standalone that we copy into the final stage.
COPY . .
RUN pnpm --filter @matchlayer/web build

# -----------------------------------------------------------------------------
# Stage 2: final (distroless nodejs)
# -----------------------------------------------------------------------------
# Tag pin (for human review): gcr.io/distroless/nodejs24-debian12:nonroot
# Debian-bookworm distroless with Node.js 24 and the `nonroot` user (UID/GID 65532).
# No shell, no package manager, no setuid binaries. The default entrypoint is
# /nodejs/bin/node — we override it explicitly with the standalone server path.
FROM gcr.io/distroless/nodejs24-debian12@sha256:14d42e2511532589a7c7e01a753667a74fcc96266e137e8125006b87b0c32d0a

WORKDIR /app

# Standalone output layout (Next.js docs):
#   /repo/apps/web/.next/standalone/        — server.js, minimal node_modules, package.json
#   /repo/apps/web/.next/static/            — hashed JS/CSS bundles (NOT in standalone)
#   /repo/apps/web/public/                  — public assets (NOT in standalone)
# The standalone bundle preserves the workspace path, so server.js lands at
# /app/apps/web/server.js and its trace-resolved node_modules at /app/node_modules.
# Static and public assets must be served from the workspace-relative paths the server
# expects: /app/apps/web/.next/static and /app/apps/web/public.
COPY --from=builder /repo/apps/web/.next/standalone ./
COPY --from=builder /repo/apps/web/.next/static ./apps/web/.next/static
COPY --from=builder /repo/apps/web/public ./apps/web/public

# NODE_ENV gates production-only code paths (logging, error rendering, Next dev tools).
# PORT and HOSTNAME are read by the Next.js standalone server: HOSTNAME=0.0.0.0 makes
# it bind to all interfaces inside the container, PORT=3000 matches EXPOSE below.
ENV NODE_ENV=production \
    PORT=3000 \
    HOSTNAME=0.0.0.0

USER nonroot

EXPOSE 3000

# HEALTHCHECK uses the Node runtime directly because distroless has no shell. Node 24
# ships a built-in global `fetch`, so this needs no additional dependencies. Exits 0
# only on a 2xx response from the landing route; anything else (timeout, connection
# refused, non-2xx) exits non-zero and Docker marks the container unhealthy.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["/nodejs/bin/node", "-e", "fetch('http://127.0.0.1:3000/').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"]

ENTRYPOINT ["/nodejs/bin/node", "apps/web/server.js"]
