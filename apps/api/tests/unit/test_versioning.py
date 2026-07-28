"""Unit tests for Scorer_Version v2 composition and parsing (task 3.1).

Concrete examples for the documented format (design D5), the ``+``/``@``
component rejection (Requirement 6.3), and Phase 1 string parsing
(Requirements 6.1, 6.2). The exhaustive round-trip and injectivity properties
are covered by the property tests of tasks 3.2 and 3.3.
"""

from __future__ import annotations

import pytest

from matchlayer_api.scoring.lexicon import scorer_version as phase1_scorer_version
from matchlayer_api.scoring.versioning import (
    SEMANTIC_ALGORITHM_VERSION,
    ScorerVersionError,
    ScorerVersionParts,
    parse_scorer_version,
    semantic_scorer_version,
)

_EXAMPLE_ARGS = {
    "lexicon_version": "v2",
    "model_name": "sentence-transformers/all-MiniLM-L6-v2",
    "model_revision": "c9745ed1d9f207416be6d2e6f8de32d1f16199bf",
    "spacy_name": "en_core_web_sm",
    "spacy_version": "3.8.0",
}


class TestSemanticScorerVersion:
    def test_documented_format(self) -> None:
        value = semantic_scorer_version(**_EXAMPLE_ARGS)
        assert value == (
            "2.0.0+lex.v2"
            "+emb.sentence-transformers/all-MiniLM-L6-v2"
            "@c9745ed1d9f207416be6d2e6f8de32d1f16199bf"
            "+spacy.en_core_web_sm@3.8.0"
        )

    def test_algorithm_version_constant(self) -> None:
        assert SEMANTIC_ALGORITHM_VERSION == "2.0.0"
        assert semantic_scorer_version(**_EXAMPLE_ARGS).startswith("2.0.0+")

    @pytest.mark.parametrize(
        "field",
        ["lexicon_version", "model_name", "model_revision", "spacy_name", "spacy_version"],
    )
    def test_rejects_plus_in_any_component(self, field: str) -> None:
        args = {**_EXAMPLE_ARGS, field: "bad+value"}
        with pytest.raises(ScorerVersionError):
            semantic_scorer_version(**args)

    @pytest.mark.parametrize(
        "field",
        ["model_name", "model_revision", "spacy_name", "spacy_version"],
    )
    def test_rejects_at_sign_in_pair_components(self, field: str) -> None:
        args = {**_EXAMPLE_ARGS, field: "bad@value"}
        with pytest.raises(ScorerVersionError):
            semantic_scorer_version(**args)


class TestParseScorerVersion:
    def test_round_trips_v2_value(self) -> None:
        value = semantic_scorer_version(**_EXAMPLE_ARGS)
        parts = parse_scorer_version(value)
        assert parts == ScorerVersionParts(
            algorithm_version="2.0.0",
            lexicon_version="v2",
            embedding_model_name="sentence-transformers/all-MiniLM-L6-v2",
            embedding_model_revision="c9745ed1d9f207416be6d2e6f8de32d1f16199bf",
            spacy_pipeline_name="en_core_web_sm",
            spacy_pipeline_version="3.8.0",
        )

    def test_parses_phase1_string_as_algorithm_and_lexicon_only(self) -> None:
        parts = parse_scorer_version(phase1_scorer_version("v1"))
        assert parts == ScorerVersionParts(
            algorithm_version="1.0.0",
            lexicon_version="v1",
        )
        assert parts.embedding_model_name is None
        assert parts.embedding_model_revision is None
        assert parts.spacy_pipeline_name is None
        assert parts.spacy_pipeline_version is None

    @pytest.mark.parametrize(
        "malformed",
        [
            "",
            "2.0.0",
            "2.0.0+v2",  # missing lex. prefix
            "+lex.v2",  # empty algorithm segment
            "2.0.0+lex.v2+emb.name@rev",  # 3 segments
            "2.0.0+lex.v2+emb.namerev+spacy.en@3.8.0",  # emb pair missing @
            "2.0.0+lex.v2+emb.name@rev@extra+spacy.en@3.8.0",  # emb pair with two @
            "2.0.0+lex.v2+spacy.en@3.8.0+emb.name@rev",  # segments out of order
            "2.0.0+lex.v2+emb.name@rev+spacy.en@3.8.0+extra",  # 5 segments
        ],
    )
    def test_rejects_malformed_strings(self, malformed: str) -> None:
        with pytest.raises(ScorerVersionError):
            parse_scorer_version(malformed)
