#!/bin/bash
# Helper to run pytest in apps/api with the right CWD.
set -euo pipefail
cd "$(dirname "$0")/../apps/api"
exec uv run pytest "$@"
