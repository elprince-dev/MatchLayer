"""Scorer_Version v2 composition and parsing (phase-2-nlp-embeddings, task 3.1).

Phase 2 extends the Phase 1 ``Scorer_Version`` scheme so every stored score
identifies exactly which pipeline composition produced it (Requirement 6.1):

* the scoring **algorithm** version (:data:`SEMANTIC_ALGORITHM_VERSION`),
* the **Skill_Lexicon** version (Requirement 5.3 — a lexicon version change
  always changes the Scorer_Version),
* the **Embedding_Model** name and revision, and
* the **spaCy pipeline** name and version.

Documented format (design decision D5)::

    2.0.0+lex.{lexicon_version}+emb.{model_name}@{model_revision}+spacy.{spacy_name}@{spacy_version}

Each component is individually recoverable from the stored string alone by
splitting on ``+`` and the labeled ``lex.`` / ``emb.`` / ``spacy.`` prefixes
(Requirement 6.1). The scheme is **injective** over pipeline compositions
(Requirement 6.3) because:

* no component value may contain ``+`` (the segment separator), and
* neither half of an ``@``-joined pair (model name/revision, spaCy
  name/version) may contain ``@`` (the pair separator),

so distinct component tuples can never serialize to the same string.
:func:`semantic_scorer_version` rejects violating components with
:class:`ScorerVersionError` instead of producing an ambiguous value.

Fallback results keep the unchanged Phase 1 format
``{ALGORITHM_VERSION}+lex.{lexicon_version}`` (``1.0.0+lex.v1``), composed by
:func:`matchlayer_api.scoring.lexicon.scorer_version`. Phase 1 values are
distinct from every v2 value (Requirement 6.2 — the algorithm segment differs
and v1 strings carry no ``emb.``/``spacy.`` segments) and
:func:`parse_scorer_version` parses them as algorithm + lexicon only.

Import boundary (Requirement 12.1): this module is part of the framework-free
Scoring_Core — standard library only, no FastAPI / SQLAlchemy /
``matchlayer_api.config`` / storage / web imports, and no environment reads.
All component values are passed in by the ML_Adapter.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

# The Phase 2 semantic scoring *algorithm* version. Bump when the semantic
# scoring algorithm itself changes in a way that alters produced scores —
# independent of the lexicon, embedding model, or spaCy pipeline, which carry
# their own segments in the composed Scorer_Version.
SEMANTIC_ALGORITHM_VERSION: Final[str] = "2.0.0"

# Labeled segment prefixes (design D5). A composed v2 value is exactly:
#   {algorithm}+lex.{...}+emb.{name}@{rev}+spacy.{name}@{ver}
_LEX_PREFIX: Final[str] = "lex."
_EMB_PREFIX: Final[str] = "emb."
_SPACY_PREFIX: Final[str] = "spacy."

# Separators that component values must not contain (injectivity, Req 6.3).
_SEGMENT_SEP: Final[str] = "+"
_PAIR_SEP: Final[str] = "@"


class ScorerVersionError(ValueError):
    """A Scorer_Version component or string violates the documented format.

    Raised when composing with a component that would make the value ambiguous
    (Requirement 6.3), or when parsing a string that follows neither the
    Phase 1 nor the Phase 2 documented format (Requirement 6.1). A subclass of
    :class:`ValueError` because both cases are programming/configuration
    errors, not user input faults.
    """


@dataclass(frozen=True, slots=True)
class ScorerVersionParts:
    """The component identifiers recovered from a Scorer_Version string.

    ``algorithm_version`` and ``lexicon_version`` are always present — both
    the Phase 1 and Phase 2 formats carry them. The embedding and spaCy
    fields are ``None`` exactly when the parsed string is a Phase 1 value
    (``1.0.0+lex.{v}``), which carries no ``emb.``/``spacy.`` segments
    (Requirement 6.1, 6.2).
    """

    algorithm_version: str
    lexicon_version: str
    embedding_model_name: str | None = None
    embedding_model_revision: str | None = None
    spacy_pipeline_name: str | None = None
    spacy_pipeline_version: str | None = None


def _require_no_separator(component: str, name: str, *, forbid_pair_sep: bool) -> None:
    """Reject component values that would break injectivity (Requirement 6.3)."""
    if _SEGMENT_SEP in component:
        msg = f"Scorer_Version component {name!r} must not contain {_SEGMENT_SEP!r}: {component!r}"
        raise ScorerVersionError(msg)
    if forbid_pair_sep and _PAIR_SEP in component:
        msg = f"Scorer_Version component {name!r} must not contain {_PAIR_SEP!r}: {component!r}"
        raise ScorerVersionError(msg)


def semantic_scorer_version(
    lexicon_version: str,
    model_name: str,
    model_revision: str,
    spacy_name: str,
    spacy_version: str,
) -> str:
    """Compose the Phase 2 Scorer_Version string (Requirements 6.1, 6.3, 5.3).

    ``f"{SEMANTIC_ALGORITHM_VERSION}+lex.{lexicon_version}"
    f"+emb.{model_name}@{model_revision}+spacy.{spacy_name}@{spacy_version}"``

    Raises :class:`ScorerVersionError` if any component contains ``+``, or if
    a name/revision/version half of an ``@``-joined pair contains ``@`` —
    either would let two distinct compositions serialize identically,
    violating injectivity (Requirement 6.3).
    """
    _require_no_separator(lexicon_version, "lexicon_version", forbid_pair_sep=False)
    _require_no_separator(model_name, "model_name", forbid_pair_sep=True)
    _require_no_separator(model_revision, "model_revision", forbid_pair_sep=True)
    _require_no_separator(spacy_name, "spacy_name", forbid_pair_sep=True)
    _require_no_separator(spacy_version, "spacy_version", forbid_pair_sep=True)
    return (
        f"{SEMANTIC_ALGORITHM_VERSION}"
        f"{_SEGMENT_SEP}{_LEX_PREFIX}{lexicon_version}"
        f"{_SEGMENT_SEP}{_EMB_PREFIX}{model_name}{_PAIR_SEP}{model_revision}"
        f"{_SEGMENT_SEP}{_SPACY_PREFIX}{spacy_name}{_PAIR_SEP}{spacy_version}"
    )


def _split_pair(segment: str, prefix: str, value: str) -> tuple[str, str]:
    """Split a labeled ``{prefix}{name}@{rev}`` segment into its two halves."""
    body = segment[len(prefix) :]
    name, sep, revision = body.partition(_PAIR_SEP)
    if not sep or _PAIR_SEP in revision:
        msg = (
            f"Scorer_Version segment {segment!r} in {value!r} must contain exactly one "
            f"{_PAIR_SEP!r} separating name and revision/version"
        )
        raise ScorerVersionError(msg)
    return name, revision


def parse_scorer_version(value: str) -> ScorerVersionParts:
    """Recover every component identifier from a Scorer_Version string alone.

    Understands both documented formats (Requirement 6.1):

    * **Phase 2**: ``{algo}+lex.{lex}+emb.{name}@{rev}+spacy.{name}@{ver}`` —
      all six fields populated.
    * **Phase 1**: ``{algo}+lex.{lex}`` (for example ``1.0.0+lex.v1``) —
      algorithm + lexicon only; embedding and spaCy fields are ``None``.

    Raises :class:`ScorerVersionError` for any string following neither
    format, rather than guessing at a partial parse.
    """
    segments = value.split(_SEGMENT_SEP)
    if len(segments) not in (2, 4):
        msg = (
            f"Scorer_Version {value!r} has neither the Phase 1 (2-segment) nor the "
            f"Phase 2 (4-segment) documented format"
        )
        raise ScorerVersionError(msg)

    algorithm = segments[0]
    if not algorithm:
        msg = f"Scorer_Version {value!r} has an empty algorithm segment"
        raise ScorerVersionError(msg)

    lex_segment = segments[1]
    if not lex_segment.startswith(_LEX_PREFIX):
        msg = f"Scorer_Version {value!r}: second segment must start with {_LEX_PREFIX!r}"
        raise ScorerVersionError(msg)
    lexicon_version = lex_segment[len(_LEX_PREFIX) :]

    if len(segments) == 2:
        return ScorerVersionParts(
            algorithm_version=algorithm,
            lexicon_version=lexicon_version,
        )

    emb_segment = segments[2]
    if not emb_segment.startswith(_EMB_PREFIX):
        msg = f"Scorer_Version {value!r}: third segment must start with {_EMB_PREFIX!r}"
        raise ScorerVersionError(msg)
    model_name, model_revision = _split_pair(emb_segment, _EMB_PREFIX, value)

    spacy_segment = segments[3]
    if not spacy_segment.startswith(_SPACY_PREFIX):
        msg = f"Scorer_Version {value!r}: fourth segment must start with {_SPACY_PREFIX!r}"
        raise ScorerVersionError(msg)
    spacy_name, spacy_version = _split_pair(spacy_segment, _SPACY_PREFIX, value)

    return ScorerVersionParts(
        algorithm_version=algorithm,
        lexicon_version=lexicon_version,
        embedding_model_name=model_name,
        embedding_model_revision=model_revision,
        spacy_pipeline_name=spacy_name,
        spacy_pipeline_version=spacy_version,
    )
