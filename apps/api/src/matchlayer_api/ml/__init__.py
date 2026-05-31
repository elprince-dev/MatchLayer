"""The ``ml/`` adapter layer — the API's only bridge into ``scoring/``.

Per ``structure.md`` and the design's import-boundary rule (Requirement 10.1,
10.2), the framework-free ``matchlayer_api.scoring`` package must stay free of
FastAPI, SQLAlchemy, and ``matchlayer_api.config``. This ``ml/`` package is the
boundary layer that is *allowed* to read application settings and bind them to
the scoring core: it reads ``get_settings()`` and constructs a single
``Match_Scorer`` from the loaded ``Skill_Lexicon`` and the configured
weights/caps, then exposes a thin ``score(...)`` entry point that only marshals
plain strings in and a plain ``ScoreResult`` dataclass out.

The dependency direction is strictly one-way: ``ml`` imports from ``scoring``,
never the reverse. See ``scorer_adapter.py`` for the marshalling surface.
"""
