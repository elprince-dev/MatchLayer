"""Versioned prompt templates and the active-version registry (Phase 3).

Every prompt instruction text sent to an LLM provider lives here as a UTF-8
file named ``<feature_name>.v<N>.txt`` (Requirement 2.1) — no prompt
instruction text is ever assembled from string literals in service or router
code. A content change ships as a new file with an incremented ``N``, never
an in-place edit, so the version recorded in every LLM invocation log
identifies immutable prompt content (Requirement 2.3).

``registry.py`` is the single designated source resolving the active
template version per feature (Requirement 2.4): a rollback edits exactly
``ACTIVE_PROMPT_VERSIONS`` and nothing else.
"""
