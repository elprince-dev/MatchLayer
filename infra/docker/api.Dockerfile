# syntax=docker/dockerfile:1.7
#
# MatchLayer API — production container image.
#
# Acceptance criteria (Phase 1 spec, Requirement 10):
#   10.1  Located at infra/docker/api.Dockerfile.
#   10.3  Base images pinned by digest; final stage is a minimal distroless image.
#   10.4  Runs as a non-root user with UID >= 10000 (distroless `nonroot` is UID 65532).
#   10.5  Runnable with `--read-only`. Required writable scratch is /tmp, mounted as tmpfs.
#   10.6  HEALTHCHECK targets GET /healthz on the API.
#   10.7  Final image contains only the Python interpreter, the application code, and the
#         runtime deps resolved from apps/api/uv.lock.
#   10.9  `docker build -f infra/docker/api.Dockerfile .` from a fresh clone exits 0.
# Design reference: phase-1-foundation §11.1.
#
# Read-only runtime contract:
#   docker run --read-only --tmpfs /tmp \
#     -e MATCHLAYER_DATABASE_URL=postgresql+asyncpg://... \
#     -p 8000:8000 <image>
#
# uvicorn itself does not write to disk; /tmp is mounted as tmpfs to absorb any transient
# interpreter scratch (resolver/import caches, multipart spooling, asyncio fallbacks). Do
# not unseal the rootfs in production — every other writable path is a violation.
#
# Build context is the repository root, so paths below are repo-relative:
#   docker build -f infra/docker/api.Dockerfile .

# -----------------------------------------------------------------------------
# Stage 1: builder
# -----------------------------------------------------------------------------
# Tag pin (for human review): docker.io/library/python:3.13-slim
# Debian-trixie based; ships CPython 3.13 — same minor as the distroless final stage.
FROM python@sha256:b04b5d7233d2ad9c379e22ea8927cd1378cd15c60d4ef876c065b25ea8fb3bf3 AS builder

# Tag pin (for human review): ghcr.io/astral-sh/uv:0.11.16
# Bring in `uv` and `uvx` as static binaries; no apt installs needed in this stage.
COPY --from=ghcr.io/astral-sh/uv@sha256:440fd6477af86a2f1b38080c539f1672cd22acb1b1a47e321dba5158ab08864d \
     /uv /uvx /usr/local/bin/

# Copy mode keeps the venv self-contained instead of hardlinking into uv's cache, which
# matters because we ship the venv to a different filesystem (the final stage). Bytecode
# precompile so the runtime image doesn't fault in .pyc files on first request.
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Resolve and install runtime dependencies first (no project install). Keeping this layer
# independent of source means edits under apps/api/src/ don't bust the dependency cache.
COPY apps/api/pyproject.toml apps/api/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Now copy application code and Alembic, then re-sync to install the project itself.
# README.md is required because pyproject.toml declares `readme = "README.md"` and the
# hatchling build backend resolves it at wheel-build time during `uv sync`'s project
# install step. Without it, `uv sync --frozen --no-dev` fails with:
#   OSError: Readme file does not exist: README.md
COPY apps/api/README.md ./
COPY apps/api/src ./src
COPY apps/api/alembic.ini ./
COPY apps/api/alembic ./alembic
RUN uv sync --frozen --no-dev

# Re-bind the venv to the distroless runtime interpreter.
#
# uv creates `/app/.venv/bin/python` as a symlink to the *builder* Python at
# /usr/local/bin/python3.13, and writes pyvenv.cfg with `home = /usr/local/bin`.
# The distroless final stage ships CPython 3.13 at /usr/bin/python3.13 — same minor,
# different path. Without this fixup the venv's `python` is a dangling symlink and the
# ENTRYPOINT (uvicorn — a `#!/app/.venv/bin/python` script) cannot exec.
RUN set -eux; \
    rm -f /app/.venv/bin/python /app/.venv/bin/python3 /app/.venv/bin/python3.13; \
    ln -s /usr/bin/python3.13 /app/.venv/bin/python3.13; \
    ln -s python3.13          /app/.venv/bin/python3; \
    ln -s python3             /app/.venv/bin/python; \
    sed -i 's|^home = .*|home = /usr/bin|' /app/.venv/pyvenv.cfg

# -----------------------------------------------------------------------------
# Stage 2: final (distroless)
# -----------------------------------------------------------------------------
# Tag pin (for human review): gcr.io/distroless/python3-debian13:nonroot
# Debian-trixie distroless with CPython 3.13 and the `nonroot` user (UID/GID 65532).
# No shell, no package manager, no setuid binaries.
FROM gcr.io/distroless/python3-debian13@sha256:614040f7f08b3f0dca943ea54eae94ea555ea2b9ca83d1acda1b7e4238ce91fb

WORKDIR /app

COPY --from=builder /app/.venv      /app/.venv
COPY --from=builder /app/src        /app/src
COPY --from=builder /app/alembic.ini /app/alembic.ini
COPY --from=builder /app/alembic    /app/alembic

# PATH puts the venv's console scripts (uvicorn, alembic) ahead of the system path.
# PYTHONPATH lets the interpreter resolve `matchlayer_api` from /app/src directly, so the
# package doesn't need to be pip-installed into site-packages.
ENV PATH="/app/.venv/bin:/usr/bin:${PATH}" \
    PYTHONPATH="/app/src" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER nonroot

EXPOSE 8000

# HEALTHCHECK uses the Python interpreter directly because distroless has no shell.
# Returns 0 only on HTTP 200 from /healthz; anything else (timeout, connection refused,
# non-200) exits non-zero and Docker marks the container unhealthy.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz').status == 200 else 1)"]

ENTRYPOINT ["uvicorn", "matchlayer_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
