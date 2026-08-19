"""LangGraph agent layer (Phase 4, phase-4-agentic).

This package holds the multi-agent analysis workflow: the typed
``AgentState`` schema every node consumes and produces (``state.py``), the
deterministic rules, the agent class hierarchy, and the graph wiring. The
whole package passes ``mypy --strict`` (Requirement 1.2).

Privacy invariant (Requirement 1.3): nothing in this package ever carries
raw unredacted Resume ``extracted_text``. State is constructed by the
Agent_Worker from identifiers plus PII_Redactor-transformed / derived
content only, so checkpoints, ``agent_runs`` rows, and spans inherit the
Internal classification by construction.
"""
