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

# -----------------------------------------------------------------------------
# Phase 2 (phase-2-nlp-embeddings, Requirements 10.2, 10.6): bake the pinned
# SentenceTransformer Embedding_Model into the image at BUILD time.
#
# `huggingface_hub` is a resolved runtime dependency (via sentence-transformers
# in uv.lock), so the snapshot uses the venv interpreter — no extra installs.
# The name + revision below mirror the application defaults in
# `matchlayer_api.config` (MATCHLAYER_EMBEDDING_MODEL_NAME / _REVISION); the
# revision is a full commit SHA so the artifact is immutable and the build is
# reproducible. The snapshot lands exactly where the runtime expects it
# (MATCHLAYER_EMBEDDING_MODEL_PATH default: /app/models/all-MiniLM-L6-v2), and
# the runtime never contacts the hub — HF_HUB_OFFLINE=1 is set in the final
# stage below.
#
# The spaCy pipeline (en_core_web_sm) needs no step here: it is a pinned wheel
# dependency in apps/api/pyproject.toml, installed by the `uv sync --frozen`
# above (Requirement 10.2).
#
# NOTE: this step must run BEFORE the venv re-bind below — after the re-bind,
# /app/.venv/bin/python points at the distroless interpreter path, which does
# not exist in this builder stage.
ARG EMBEDDING_MODEL_NAME="sentence-transformers/all-MiniLM-L6-v2"
ARG EMBEDDING_MODEL_REVISION="c9745ed1d9f207416be6d2e6f8de32d1f16199bf"
ARG EMBEDDING_MODEL_PATH="/app/models/all-MiniLM-L6-v2"
# ARG values are exposed as environment variables to RUN steps in this stage.
# ``allow_patterns`` is load-bearing, not an optimization detail. An
# unfiltered snapshot_download pulls EVERY file in the repo, which for
# all-MiniLM-L6-v2 means five redundant copies of the same weights — the
# PyTorch .bin, TensorFlow .h5, Rust .ot, ONNX, and OpenVINO variants — and
# produced a 977 MB image layer for a model whose safetensors weights are
# ~90 MB. Only the files sentence-transformers actually reads when loading
# with local_files_only=True are fetched: the module manifest, the
# transformer + tokenizer config/vocab, the safetensors weights, and the
# Pooling/Normalize module dirs named by modules.json. The list is explicit
# (no globs) so the artifact set is deterministic and reviewable, and so a
# new file appearing upstream can never silently re-inflate the image.
# If a required file were omitted, the adapter's local load fails and the
# instance starts in Degraded_Mode (Requirement 7.1) rather than reaching
# for the hub — HF_HUB_OFFLINE=1 is set in the final stage.
RUN /app/.venv/bin/python -c "\
import os; \
from huggingface_hub import snapshot_download; \
snapshot_download( \
    repo_id=os.environ['EMBEDDING_MODEL_NAME'], \
    revision=os.environ['EMBEDDING_MODEL_REVISION'], \
    local_dir=os.environ['EMBEDDING_MODEL_PATH'], \
    allow_patterns=[ \
        'config.json', \
        'config_sentence_transformers.json', \
        'sentence_bert_config.json', \
        'modules.json', \
        'model.safetensors', \
        'tokenizer.json', \
        'tokenizer_config.json', \
        'special_tokens_map.json', \
        'vocab.txt', \
        '1_Pooling/config.json', \
        '2_Normalize/*', \
    ], \
)"

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
# Phase 2: the baked Embedding_Model snapshot (see the builder-stage note).
COPY --from=builder /app/models     /app/models

# PATH puts the venv's console scripts (uvicorn, alembic) ahead of the system path.
# PYTHONPATH lets the interpreter resolve `matchlayer_api` from /app/src directly, so the
# package doesn't need to be pip-installed into site-packages.
#
# HF_HUB_OFFLINE=1 (Phase 2, Requirements 10.2, 10.6): the runtime must NEVER
# contact the Hugging Face hub — the model was baked at build time from the
# pinned name + revision, and the semantic adapter loads it with
# local_files_only=True from MATCHLAYER_EMBEDDING_MODEL_PATH (whose default
# matches the bake path above). A missing/broken artifact degrades the
# instance (Degraded_Mode) rather than triggering a network fetch.
ENV PATH="/app/.venv/bin:/usr/bin:${PATH}" \
    PYTHONPATH="/app/src" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HUB_OFFLINE=1

USER nonroot

EXPOSE 8000

# HEALTHCHECK uses the Python interpreter directly because distroless has no shell.
# Returns 0 only on HTTP 200 from /healthz; anything else (timeout, connection refused,
# non-200) exits non-zero and Docker marks the container unhealthy.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz').status == 200 else 1)"]

ENTRYPOINT ["uvicorn", "matchlayer_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
