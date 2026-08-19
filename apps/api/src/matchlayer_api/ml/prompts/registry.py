"""Active prompt-version registry — the single designated source (Req 2.4).

``ACTIVE_PROMPT_VERSIONS`` maps each LLM feature to the version number of
its active prompt template file (``<feature_name>.v<N>.txt`` in this
package). The version resolved here is the version recorded in every
``LLM_Invocation_Log`` row for calls that used it, and a prompt rollback
changes exactly one value in this dict — no other code change.

``LLMFeature`` is defined here because the registry is the first module in
the dependency chain that needs it; ``services/llm/`` imports it from this
module rather than redefining it.

Phase 4 agent features (phase-4-agentic): the LLM agents run through the
same Phase 3 pipeline under their **own** feature values, so their
persisted results, invocation-log rows, and cache entries live in
agent-specific namespaces and never mix with the Phase 3 endpoints'
(phase-4 Requirement 9.7; Phase 3 contract stays untouched per phase-4
Requirement 16.5). A feature may *share* another feature's template
lineage via ``PROMPT_TEMPLATE_LINEAGE`` instead of shipping a duplicate
file — the Improvement_Agent reuses the resume-coach Prompt_Template
lineage this way (phase-4 Requirement 6.1), still resolved exclusively
through this registry, never from string literals in agent code.
"""

from enum import StrEnum
from typing import Final


class LLMFeature(StrEnum):
    """The Phase 3 generative features plus the Phase 4 agent features."""

    RESUME_COACH = "resume_coach"
    BULLET_REWRITE = "bullet_rewrite"
    INTERVIEW_QUESTIONS = "interview_questions"
    # Phase 4 (phase-4-agentic): the Resume_Analysis_Agent's namespace.
    # Owns its template lineage (agent_resume_analysis.vN.txt files).
    AGENT_RESUME_ANALYSIS = "agent_resume_analysis"
    # Phase 4 (phase-4-agentic): the Improvement_Agent's namespace. Shares
    # the resume-coach template lineage (see PROMPT_TEMPLATE_LINEAGE).
    AGENT_IMPROVEMENT = "agent_improvement"


ACTIVE_PROMPT_VERSIONS: Final[dict[LLMFeature, int]] = {
    LLMFeature.RESUME_COACH: 1,
    LLMFeature.BULLET_REWRITE: 1,
    LLMFeature.INTERVIEW_QUESTIONS: 1,
    # Owns its lineage: version N here names agent_resume_analysis.vN.txt
    # (phase-4 Requirement 3.3).
    LLMFeature.AGENT_RESUME_ANALYSIS: 1,
    # The active version *within the resume-coach lineage* (phase-4
    # Requirement 6.1): version N here names resume_coach.vN.txt.
    LLMFeature.AGENT_IMPROVEMENT: 1,
}


PROMPT_TEMPLATE_LINEAGE: Final[dict[LLMFeature, LLMFeature]] = {
    LLMFeature.AGENT_IMPROVEMENT: LLMFeature.RESUME_COACH,
}
"""Features that reuse another feature's Prompt_Template files.

A feature absent from this map owns its lineage (its enum value is the
``<feature_name>`` filename segment). A mapped feature resolves its
template files from the mapped-to feature's lineage while keeping its own
active-version entry, cache namespace, and persistence/invocation-log
feature key — prompt content is reused, cost/privacy accounting is not
(phase-4 Requirements 6.1, 9.7)."""


def template_lineage(feature: LLMFeature) -> LLMFeature:
    """Return the feature whose template files ``feature`` resolves to."""
    return PROMPT_TEMPLATE_LINEAGE.get(feature, feature)


def template_filename(feature: LLMFeature, version: int) -> str:
    """Return the canonical template filename for a feature at a version.

    Filenames follow the ``<feature_name>.v<N>.txt`` pattern of
    Requirement 2.1. The ``<feature_name>`` segment is the feature's
    template lineage (its own enum value unless remapped via
    ``PROMPT_TEMPLATE_LINEAGE``).
    """
    return f"{template_lineage(feature).value}.v{version}.txt"


def active_template_filename(feature: LLMFeature) -> str:
    """Return the filename of the feature's active template version."""
    return template_filename(feature, ACTIVE_PROMPT_VERSIONS[feature])
