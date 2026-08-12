"""Active prompt-version registry — the single designated source (Req 2.4).

``ACTIVE_PROMPT_VERSIONS`` maps each LLM feature to the version number of
its active prompt template file (``<feature_name>.v<N>.txt`` in this
package). The version resolved here is the version recorded in every
``LLM_Invocation_Log`` row for calls that used it, and a prompt rollback
changes exactly one value in this dict — no other code change.

``LLMFeature`` is defined here because the registry is the first module in
the dependency chain that needs it; ``services/llm/`` imports it from this
module rather than redefining it.
"""

from enum import StrEnum
from typing import Final


class LLMFeature(StrEnum):
    """The three Phase 3 generative features (see the spec glossary)."""

    RESUME_COACH = "resume_coach"
    BULLET_REWRITE = "bullet_rewrite"
    INTERVIEW_QUESTIONS = "interview_questions"


ACTIVE_PROMPT_VERSIONS: Final[dict[LLMFeature, int]] = {
    LLMFeature.RESUME_COACH: 1,
    LLMFeature.BULLET_REWRITE: 1,
    LLMFeature.INTERVIEW_QUESTIONS: 1,
}


def template_filename(feature: LLMFeature, version: int) -> str:
    """Return the canonical template filename for a feature at a version.

    Filenames follow the ``<feature_name>.v<N>.txt`` pattern of
    Requirement 2.1. The feature's enum value doubles as the
    ``<feature_name>`` segment.
    """
    return f"{feature.value}.v{version}.txt"


def active_template_filename(feature: LLMFeature) -> str:
    """Return the filename of the feature's active template version."""
    return template_filename(feature, ACTIVE_PROMPT_VERSIONS[feature])
