"""Prompt assembly: template loading, placeholder substitution, message construction.

The three steps between a redacted input and an ``LLMRequest``'s message
list (design §"Prompt assembly (services/llm/prompting.py)"):

1. :func:`load_template` reads the **active** version of a feature's
   Prompt_Template file, resolved solely through
   ``ml/prompts/registry.py``'s ``ACTIVE_PROMPT_VERSIONS`` (Req 2.4). A
   missing or unreadable file raises :class:`PromptTemplateError` so the
   orchestrator takes the Fallback_Response path and emits a structured
   event naming the feature, template, and version (Req 2.5).
2. :func:`render` substitutes runtime values **only** into the named
   ``{placeholder}`` slots defined in the template text; no instruction
   text is added at runtime, so prompt version + logged input hash fully
   determine the transmitted prompt for Phase 5 replay (Req 2.6). A
   placeholder with no corresponding value raises
   :class:`PromptRenderError` — the partially rendered prompt is never
   transmitted (Req 2.7).
3. :func:`build_messages` produces the message list: the rendered template
   is the **sole** system-role message, and every piece of user-derived
   content travels in the single user-role message wrapped in
   ``<user_content kind=...>`` delimiters (Req 4.1). Before wrapping, the
   literal delimiter sequences are deterministically neutralized inside
   user text (:func:`neutralize_delimiters`, Req 4.6) so user content can
   never close its data region or open an instruction region. Apart from
   the Requirement 3 redaction (applied by the caller *before* this
   module) and that neutralization, adversarial instruction-like text
   passes through unmodified (Req 4.4).

PII discipline: this module never logs, and its exceptions carry only
template/version/placeholder identifiers — never template values, resume
text, or any other Restricted content. Structured failure events are
emitted upstream by the orchestrator from the exception attributes.

Design reference: §"Prompt assembly (services/llm/prompting.py)".
Requirements covered: 2.5, 2.6, 2.7, 4.1, 4.4, 4.6.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from importlib import resources
from typing import Final, Literal

from pydantic import BaseModel

from matchlayer_api.ml.llm.client import LLMMessage
from matchlayer_api.ml.prompts.registry import (
    ACTIVE_PROMPT_VERSIONS,
    LLMFeature,
    active_template_filename,
)

__all__ = [
    "PromptRenderError",
    "PromptTemplate",
    "PromptTemplateError",
    "UserContentSection",
    "build_messages",
    "load_template",
    "neutralize_delimiters",
    "render",
]

_PROMPTS_PACKAGE: Final[str] = "matchlayer_api.ml.prompts"
"""The package holding the versioned Prompt_Template files (conventions.md)."""

_PLACEHOLDER_RE: Final[re.Pattern[str]] = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
"""A named placeholder slot: ``{identifier}``. Only these are ever substituted (Req 2.6)."""

_OPEN_DELIMITER: Final[str] = "<user_content"
_CLOSE_DELIMITER: Final[str] = "</user_content"
"""The literal content-delimiter sequences neutralized inside user text (Req 4.6)."""

_NEUTRALIZED_OPEN: Final[str] = "\u27e8user_content"
_NEUTRALIZED_CLOSE: Final[str] = "\u27e8/user_content"
"""Rewrites with ``<`` → ``⟨`` (U+27E8) for the delimiter sequences only (Req 4.6)."""

UserContentKind = Literal[
    "resume",
    "job_description",
    "matched_skills",
    "missing_skills",
    "bullets",
]
"""The ``kind`` labels the v1 templates document for their delimited data regions."""


class PromptTemplateError(Exception):
    """The active Prompt_Template file is missing or unreadable (Req 2.5).

    Carries the feature, template filename, and version for the
    orchestrator's structured failure event. The message is a fixed,
    PII-free string built only from those identifiers.
    """

    def __init__(self, *, feature: LLMFeature, template_name: str, version: int) -> None:
        self.feature = feature
        self.template_name = template_name
        self.version = version
        super().__init__(
            f"prompt template unavailable: feature={feature.value} "
            f"template={template_name} version={version}"
        )


class PromptRenderError(Exception):
    """A template placeholder has no corresponding runtime value (Req 2.7).

    Carries the template filename, version, and the failing placeholder
    *name* — never any runtime value — for the orchestrator's structured
    failure event. The partially rendered prompt is never transmitted.
    """

    def __init__(self, *, template_name: str, version: int, placeholder: str) -> None:
        self.template_name = template_name
        self.version = version
        self.placeholder = placeholder
        super().__init__(
            f"prompt render failed: template={template_name} version={version} "
            f"placeholder={placeholder}"
        )


class PromptTemplate(BaseModel):
    """A loaded Prompt_Template: identity (feature, version, filename) plus raw text."""

    feature: LLMFeature
    version: int
    name: str
    text: str


class UserContentSection(BaseModel):
    """One delimited data region of the user-role message (Req 4.1).

    ``text`` must already be redacted per Requirement 3 where applicable —
    delimiter neutralization is applied *after* redaction, at wrapping time.
    """

    kind: UserContentKind
    text: str


def load_template(feature: LLMFeature) -> PromptTemplate:
    """Load the active version of ``feature``'s Prompt_Template (Req 2.4, 2.5).

    The active version is resolved exclusively through
    ``ACTIVE_PROMPT_VERSIONS``; a rollback changes only that registry value.
    Raises :class:`PromptTemplateError` if the file is missing or unreadable.
    """
    version = ACTIVE_PROMPT_VERSIONS[feature]
    name = active_template_filename(feature)
    try:
        text = (resources.files(_PROMPTS_PACKAGE) / name).read_text(encoding="utf-8")
    except OSError as error:
        raise PromptTemplateError(feature=feature, template_name=name, version=version) from error
    return PromptTemplate(feature=feature, version=version, name=name, text=text)


def render(template: PromptTemplate, values: Mapping[str, str]) -> str:
    """Substitute ``values`` into the template's named placeholders — nothing else (Req 2.6).

    Exact substitution: only ``{identifier}`` slots are replaced, every other
    character of the template passes through verbatim, and no instruction
    text is added at runtime. Raises :class:`PromptRenderError` naming the
    first placeholder that has no corresponding value (Req 2.7).
    """

    def _substitute(match: re.Match[str]) -> str:
        placeholder = match.group(1)
        if placeholder not in values:
            raise PromptRenderError(
                template_name=template.name,
                version=template.version,
                placeholder=placeholder,
            )
        return values[placeholder]

    return _PLACEHOLDER_RE.sub(_substitute, template.text)


def neutralize_delimiters(text: str) -> str:
    """Deterministically neutralize literal delimiter sequences in user text (Req 4.6).

    Any occurrence of ``<user_content`` or ``</user_content`` has its ``<``
    rewritten to ``⟨`` (U+27E8) — those sequences only; all other text is
    untouched (Req 4.4) — so user-supplied content can never close the
    user-content region or open an instruction region.
    """
    return text.replace(_CLOSE_DELIMITER, _NEUTRALIZED_CLOSE).replace(
        _OPEN_DELIMITER, _NEUTRALIZED_OPEN
    )


def build_messages(
    rendered_system_prompt: str,
    sections: Sequence[UserContentSection],
) -> list[LLMMessage]:
    """Construct the two-message request payload (Req 4.1).

    The rendered template is the sole system-role message. All user-derived
    content is carried in one user-role message, each section wrapped in
    ``<user_content kind="...">`` delimiters with its text neutralized via
    :func:`neutralize_delimiters` first (after redaction, before wrapping).
    """
    wrapped = [
        f'<user_content kind="{section.kind}">\n{neutralize_delimiters(section.text)}\n'
        f"</user_content>"
        for section in sections
    ]
    return [
        LLMMessage(role="system", content=rendered_system_prompt),
        LLMMessage(role="user", content="\n\n".join(wrapped)),
    ]
