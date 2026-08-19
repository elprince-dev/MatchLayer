# syntax=docker/dockerfile:1.7
#
# MatchLayer Agent_Worker — production container image (phase-4-agentic).
#
# Acceptance criteria (Phase 4 spec, Requirement 11):
#   11.2  The Agent_Worker runs as a separate process from the API_App: module
#         apps/api/src/matchlayer_api/workers/agent_worker.py, image built from
#         infra/docker/worker.Dockerfile, consuming the Job_Queue and executing
#         the Agent_Graph. No HTTP server runs in this container.
#   11.3  Local development wires this image against LocalStack SQS via
#         docker-compose.yml; the deployed environment points the same image at
#         real AWS SQS — the two differ only in configuration values.
#
# The image REUSES the API codebase wholesale: same base images (pinned by
# digest), same uv-resolved dependency set from apps/api/uv.lock, same baked
# Embedding_Model snapshot (the worker loads the Phase 2 semantic pipeline at
# startup, exactly like the API lifespan), and the same distroless non-root
# final stage. Only the entrypoint differs: the long-running SQS consumer
# instead of uvicorn. Keeping the two Dockerfiles structurally identical means
# a dependency or model bump lands in both images from the same lockfile edit.
#
# Deliberate differences from api.Dockerfile:
#   - ENTRYPOINT is `python -m matchlayer_api.workers.agent_worker` (no HTTP
#     server, Requirement 11.2).
#   - No EXPOSE and no HEALTHCHECK: the worker serves no port to probe. Its
#     liveness signal is the process itself (SIGTERM-aware graceful stop) and
#     queue consumption observable via structured logs and OTel spans.
#   - No Alembic: the worker never runs migrations (the checkpointer schema and
#     agent tables are provisioned by the API image's migration step,
#     Requirement 2.5 — no runtime DDL in either process).
#
# Read-only runtime contract (same as the API image):
#   docker run --read-only --tmpfs /tmp \
#     -e MATCHLAYER_DATABASE_URL=postgresql+asyncpg://... \
#     -e MATCHLAYER_SQS_QUEUE_URL=... <image>
#
# Build context is the repository root, so paths below are repo-relative:
#   docker build -f infra/docker/worker.Dockerfile .

# -----------------------------------------------------------------------------
# Stage 1: builder (identical to api.Dockerfile's builder stage)
# -----------------------------------------------------------------------------
# Tag pin (for human review): docker.io/library/python:3.13-slim
# Debian-trixie based; ships CPython 3.13 — same minor as the distroless final stage.
FROM python@sha256:b04b5d7233d2ad9c379e22ea8927cd1378cd15c60d4ef876c065b25ea8fb3bf3 AS builder

# Tag pin (for human review): ghcr.io/astral-sh/uv:0.11.16
COPY --from=ghcr.io/astral-sh/uv@sha256:440fd6477af86a2f1b38080c539f1672cd22acb1b1a47e321dba5158ab08864d \
     /uv /uvx /usr/local/bin/

# Copy mode keeps the venv self-contained instead of hardlinking into uv's
# cache (the venv ships to a different filesystem in the final stage);
# bytecode precompile avoids first-run .pyc faults.
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Resolve and install runtime dependencies first (no project install) so
# source edits under apps/api/src/ don't bust the dependency cache layer.
COPY apps/api/pyproject.toml apps/api/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Application code, then re-sync to install the project itself. README.md is
# required because pyproject.toml declares `readme = "README.md"` and hatchling
# resolves it at wheel-build time during the project install step.
COPY apps/api/README.md ./
COPY apps/api/src ./src
RUN uv sync --frozen --no-dev

# -----------------------------------------------------------------------------
# Bake the pinned SentenceTransformer Embedding_Model into the image at BUILD
# time (same rationale and file allowlist as api.Dockerfile: the worker's
# startup loads the Phase 2 semantic pipeline for the ATS scorer adapter and
# the JD Skill_Extractor, and the runtime never contacts the Hugging Face hub
# — HF_HUB_OFFLINE=1 is set in the final stage). The name + revision mirror
# the application defaults in matchlayer_api.config; the revision is a full
# commit SHA so the artifact is immutable. A missing/broken artifact degrades
# the worker to the Phase 1 scoring engine (Degraded_Mode) rather than
# triggering a network fetch.
#
# NOTE: this step must run BEFORE the venv re-bind below — after the re-bind,
# /app/.venv/bin/python points at the distroless interpreter path, which does
# not exist in this builder stage.
ARG EMBEDDING_MODEL_NAME="sentence-transformers/all-MiniLM-L6-v2"
ARG EMBEDDING_MODEL_REVISION="c9745ed1d9f207416be6d2e6f8de32d1f16199bf"
ARG EMBEDDING_MODEL_PATH="/app/models/all-MiniLM-L6-v2"
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

# Re-bind the venv to the distroless runtime interpreter (same fixup as
# api.Dockerfile: uv symlinks the venv python to the builder interpreter at
# /usr/local/bin/python3.13; distroless ships it at /usr/bin/python3.13).
RUN set -eux; \
    rm -f /app/.venv/bin/python /app/.venv/bin/python3 /app/.venv/bin/python3.13; \
    ln -s /usr/bin/python3.13 /app/.venv/bin/python3.13; \
    ln -s python3.13          /app/.venv/bin/python3; \
    ln -s python3             /app/.venv/bin/python; \
    sed -i 's|^home = .*|home = /usr/bin|' /app/.venv/pyvenv.cfg

# -----------------------------------------------------------------------------
# Stage 2: final (distroless, non-root)
# -----------------------------------------------------------------------------
# Tag pin (for human review): gcr.io/distroless/python3-debian13:nonroot
# Debian-trixie distroless with CPython 3.13 and the `nonroot` user (UID/GID
# 65532 — satisfies the non-root >= 10000 baseline). No shell, no package
# manager, no setuid binaries.
FROM gcr.io/distroless/python3-debian13@sha256:614040f7f08b3f0dca943ea54eae94ea555ea2b9ca83d1acda1b7e4238ce91fb

WORKDIR /app

COPY --from=builder /app/.venv   /app/.venv
COPY --from=builder /app/src     /app/src
# The baked Embedding_Model snapshot (see the builder-stage note).
COPY --from=builder /app/models  /app/models

# PATH puts the venv's interpreter and console scripts ahead of the system
# path. PYTHONPATH resolves `matchlayer_api` from /app/src directly.
# HF_HUB_OFFLINE=1: the runtime must NEVER contact the Hugging Face hub — the
# model was baked at build time (see above).
ENV PATH="/app/.venv/bin:/usr/bin:${PATH}" \
    PYTHONPATH="/app/src" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HUB_OFFLINE=1

USER nonroot

# No EXPOSE / HEALTHCHECK: this container serves no HTTP endpoint
# (Requirement 11.2 — the worker is a queue consumer, not a server).

ENTRYPOINT ["python", "-m", "matchlayer_api.workers.agent_worker"]
