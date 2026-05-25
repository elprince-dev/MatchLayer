# matchlayer-api

FastAPI backend for MatchLayer (Phase 1).

See repo-root `README.md` for setup. Within this directory:

```bash
uv sync                                      # install runtime + dev deps
uv run uvicorn matchlayer_api.main:app --reload   # dev server on :8000
uv run pytest                                # unit tests
uv run ruff format . && uv run ruff check .  # lint + format
uv run mypy src                              # type-check
```
