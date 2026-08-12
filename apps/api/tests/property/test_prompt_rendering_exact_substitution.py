"""Feature: phase-3-llm-layer — Property 5.

# Feature: phase-3-llm-layer, Property 5: Prompt rendering is exact substitution

Property 5: Prompt rendering is exact substitution.

    *For any* prompt template with named placeholders and any complete
    value map, the rendered prompt equals the template with each
    placeholder replaced by its value and no other change (no added
    instruction text); and *for any* value map missing at least one
    placeholder, rendering raises an error, nothing is transmitted to the
    provider, and the request takes the fallback path.

**Validates: Requirements 2.6, 2.7**

Two properties pin the ``services/llm/prompting.render`` contract:

* **Complete map → exact substitution** — templates are generated as an
  explicit interleaving of literal segments and named ``{placeholder}``
  slots, so the expected rendering can be computed independently by
  concatenation. Asserting ``render(...) == expected`` proves every
  placeholder occurrence is replaced with its value verbatim, every other
  character of the template passes through verbatim, and no text is added
  or removed (Requirement 2.6). Values may themselves contain braces and
  placeholder-shaped text (e.g. ``{name}``), pinning single-pass
  substitution: inserted values are never re-substituted.
* **Missing value → PromptRenderError, nothing rendered** — dropping a
  non-empty subset of placeholder values makes ``render`` raise
  :class:`PromptRenderError` (so no partially rendered prompt can ever be
  returned, let alone transmitted), and the exception names the first
  missing placeholder in template order plus the template identity —
  identifiers only, never runtime values (Requirement 2.7).

Literal template segments exclude ``{`` so a placeholder slot can never
form accidentally across segment boundaries; everything else (including
``}``, newlines, and non-ASCII text) is fair game and must survive
verbatim.
"""

from __future__ import annotations

import string

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from matchlayer_api.ml.prompts.registry import LLMFeature
from matchlayer_api.services.llm.prompting import (
    PromptRenderError,
    PromptTemplate,
    render,
)

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_IDENT_FIRST = string.ascii_letters + "_"
_IDENT_REST = string.ascii_letters + string.digits + "_"

_placeholder_name = st.builds(
    lambda first, rest: first + rest,
    st.sampled_from(_IDENT_FIRST),
    st.text(alphabet=_IDENT_REST, max_size=8),
)

# Literal template text: any characters except "{" so no accidental
# placeholder slot can form; "}" alone is inert and stays allowed.
_literal_segment = st.text(
    alphabet=st.characters(exclude_characters="{"),
    max_size=20,
)

# Runtime values: fully arbitrary, including "{", "}", and text shaped
# exactly like a placeholder — inserted values must pass through verbatim.
_value_text = st.text(max_size=20)


@st.composite
def _template_cases(
    draw: st.DrawFn,
    *,
    min_occurrences: int,
) -> tuple[PromptTemplate, dict[str, str], str, list[str]]:
    """Build (template, complete value map, expected rendering, occurrence order).

    The template is an interleaving ``literal + {name} + literal + ...``
    with ``min_occurrences``..8 placeholder occurrences drawn (with
    repetition) from up to 5 distinct names, so repeated slots are covered.
    """
    names = draw(
        st.lists(_placeholder_name, min_size=min(min_occurrences, 1), max_size=5, unique=True)
    )
    occurrences = (
        draw(st.lists(st.sampled_from(names), min_size=min_occurrences, max_size=8))
        if names
        else []
    )
    literals = draw(
        st.lists(
            _literal_segment,
            min_size=len(occurrences) + 1,
            max_size=len(occurrences) + 1,
        )
    )
    values = {name: draw(_value_text) for name in names}

    template_text = literals[0]
    expected = literals[0]
    for occurrence, literal in zip(occurrences, literals[1:], strict=True):
        template_text += "{" + occurrence + "}" + literal
        expected += values[occurrence] + literal

    template = PromptTemplate(
        feature=LLMFeature.RESUME_COACH,
        version=1,
        name="resume_coach.v1.txt",
        text=template_text,
    )
    return template, values, expected, occurrences


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=None)
@given(case=_template_cases(min_occurrences=0))
def test_complete_value_map_renders_exact_substitution(
    case: tuple[PromptTemplate, dict[str, str], str, list[str]],
) -> None:
    """Exactness (Requirement 2.6): with a value for every placeholder, the
    rendered prompt is byte-for-byte the template with each ``{name}`` slot
    replaced by its value — nothing else changed, added, or re-substituted."""
    template, values, expected, _occurrences = case

    assert render(template, values) == expected


@settings(max_examples=100, deadline=None)
@given(case=_template_cases(min_occurrences=1), data=st.data())
def test_missing_value_raises_without_partial_render(
    case: tuple[PromptTemplate, dict[str, str], str, list[str]],
    data: st.DataObject,
) -> None:
    """Failure path (Requirement 2.7): a value map missing at least one
    placeholder makes ``render`` raise ``PromptRenderError`` — so no partial
    rendering is ever returned for transmission — and the exception carries
    the template identity plus the first missing placeholder's *name*, with
    no runtime value in the message."""
    template, values, _expected, occurrences = case

    # Draw the dropped names from placeholders that actually occur in the
    # template text — a name with no occurrence would render successfully.
    missing = set(
        data.draw(
            st.lists(st.sampled_from(sorted(set(occurrences))), min_size=1, unique=True),
            label="missing placeholders",
        )
    )
    partial_values = {name: value for name, value in values.items() if name not in missing}
    first_missing = next(name for name in occurrences if name in missing)

    with pytest.raises(PromptRenderError) as excinfo:
        render(template, partial_values)

    error = excinfo.value
    assert error.placeholder == first_missing
    assert error.template_name == template.name
    assert error.version == template.version
    # Identifier discipline: the message is exactly the fixed string built
    # from template/version/placeholder identifiers. Equality with a message
    # computed from test-known identifiers proves no supplied runtime value
    # can leak into it — without the coincidental-substring false positives
    # a `value not in str(error)` loop suffers for short values (e.g. "d"
    # appearing inside "prompt render failed").
    assert str(error) == (
        f"prompt render failed: template={template.name} "
        f"version={template.version} placeholder={first_missing}"
    )
