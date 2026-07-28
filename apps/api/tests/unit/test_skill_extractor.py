"""Unit tests for the spaCy-based Skill_Extractor (task 4.1).

Concrete examples over a small synthetic lexicon and ``spacy.blank("en")``:
alias resolution (Req 4.3), token-boundary and longest-match behavior
(Req 4.11), skill-only output (Req 4.1, 4.9), ordering/tie-break/cap
(Req 4.5), the matched/missing partition (Req 4.4), determinism (Req 4.7),
and the POS gate. The exhaustive properties are covered by the property tests
of tasks 4.2 through 4.6.
"""

from __future__ import annotations

import pytest
import spacy
from spacy.language import Language
from spacy.tokens import Doc

from matchlayer_api.scoring.lexicon import Skill_Lexicon
from matchlayer_api.scoring.skills import Skill_Extractor

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_lexicon() -> Skill_Lexicon:
    return Skill_Lexicon(
        {
            "schema_version": 1,
            "lexicon_version": "test",
            "skills": [
                {
                    "canonical": "python",
                    "display": "Python",
                    "category": "language",
                    "weight": 1.0,
                    "aliases": ["py", "py3"],
                },
                {
                    "canonical": "javascript",
                    "display": "JavaScript",
                    "category": "language",
                    "weight": 0.9,
                    "aliases": ["js"],
                },
                {
                    "canonical": "node.js",
                    "display": "Node.js",
                    "category": "runtime",
                    "weight": 0.9,
                    "aliases": ["node js", "nodejs"],
                },
                {
                    "canonical": "java",
                    "display": "Java",
                    "category": "language",
                    "weight": 0.8,
                    "aliases": [],
                },
                {
                    "canonical": "machine learning",
                    "display": "Machine Learning",
                    "category": "domain",
                    "weight": 0.7,
                    "aliases": ["ml"],
                },
                {
                    "canonical": "go",
                    "display": "Go",
                    "category": "language",
                    "weight": 0.6,
                    "aliases": ["golang"],
                },
            ],
        }
    )


@pytest.fixture(scope="module")
def lexicon() -> Skill_Lexicon:
    return _make_lexicon()


@pytest.fixture(scope="module")
def extractor(lexicon: Skill_Lexicon) -> Skill_Extractor:
    return Skill_Extractor(spacy.blank("en"), lexicon, max_keywords=50)


# A minimal "tagger" so the POS gate is exercisable without en_core_web_sm
# (which is installed by task 8.3): tags every token VERB except a fixed
# noun list.
_NOUN_WORDS = frozenset({"python", "team"})


@Language.component("test_skills_pos_tagger")
def _pos_tagger(doc: Doc) -> Doc:
    for token in doc:
        token.pos_ = "NOUN" if token.lower_ in _NOUN_WORDS else "VERB"
    return doc


# ---------------------------------------------------------------------------
# extract(): matching, alias resolution, boundaries, ordering
# ---------------------------------------------------------------------------


class TestExtract:
    def test_canonical_terms_found_case_insensitively(self, extractor: Skill_Extractor) -> None:
        terms = [k.term for k in extractor.extract("Python and JAVA and JavaScript")]
        assert terms == ["python", "javascript", "java"]

    def test_alias_resolves_to_canonical(self, extractor: Skill_Extractor) -> None:
        terms = [k.term for k in extractor.extract("experience with py required")]
        assert terms == ["python"]

    def test_multi_word_alias_resolves_to_canonical(self, extractor: Skill_Extractor) -> None:
        terms = [k.term for k in extractor.extract("we do ML here")]
        assert terms == ["machine learning"]

    def test_java_does_not_match_inside_javascript(self, extractor: Skill_Extractor) -> None:
        terms = [k.term for k in extractor.extract("javascript developer wanted")]
        assert terms == ["javascript"]

    def test_longest_match_wins_over_inner_alias(self, extractor: Skill_Extractor) -> None:
        # "node js" (alias of node.js, 2 tokens) overlaps the inner alias
        # "js" (alias of javascript); the longer span wins, so the result is
        # node.js and javascript never appears (Req 4.11).
        terms = [k.term for k in extractor.extract("we run node js in production")]
        assert terms == ["node.js"]

    def test_node_dot_js_resolves_to_its_own_canonical(self, extractor: Skill_Extractor) -> None:
        terms = [k.term for k in extractor.extract("services built on node.js")]
        assert terms == ["node.js"]

    def test_generic_terms_never_appear(self, extractor: Skill_Extractor) -> None:
        text = "check our selection process; experience and team work required"
        assert extractor.extract(text) == []

    def test_output_is_deduplicated(self, extractor: Skill_Extractor) -> None:
        terms = [k.term for k in extractor.extract("python python py py3 Python")]
        assert terms == ["python"]

    def test_ordering_descending_weight_then_lexicographic(
        self, extractor: Skill_Extractor
    ) -> None:
        # javascript and node.js share weight 0.9; "javascript" < "node.js"
        # lexicographically, so it comes first (Req 4.5).
        keywords = extractor.extract("java, node.js, javascript and python")
        assert [k.term for k in keywords] == ["python", "javascript", "node.js", "java"]
        assert [k.weight for k in keywords] == [1.0, 0.9, 0.9, 0.8]

    def test_empty_text_yields_empty_list(self, extractor: Skill_Extractor) -> None:
        assert extractor.extract("") == []

    def test_extract_is_deterministic(self, extractor: Skill_Extractor) -> None:
        text = "python, go, node js, machine learning and javascript"
        assert extractor.extract(text) == extractor.extract(text)


# ---------------------------------------------------------------------------
# POS gate
# ---------------------------------------------------------------------------


class TestPosGate:
    def test_verb_tagged_single_token_skill_is_gated_out(self, lexicon: Skill_Lexicon) -> None:
        nlp = spacy.blank("en")
        nlp.add_pipe("test_skills_pos_tagger")
        extractor = Skill_Extractor(nlp, lexicon, max_keywords=50)
        # "go" is tagged VERB by the test tagger → excluded; "python" is
        # tagged NOUN → survives.
        terms = [k.term for k in extractor.extract("go work with python")]
        assert terms == ["python"]

    def test_multi_token_surface_survives_regardless_of_pos(self, lexicon: Skill_Lexicon) -> None:
        nlp = spacy.blank("en")
        nlp.add_pipe("test_skills_pos_tagger")
        extractor = Skill_Extractor(nlp, lexicon, max_keywords=50)
        # "machine learning" tokens are tagged VERB, but a multi-token
        # lexicon surface form passes the gate.
        terms = [k.term for k in extractor.extract("machine learning models")]
        assert terms == ["machine learning"]

    def test_untagged_pipeline_passes_candidates_through(self, extractor: Skill_Extractor) -> None:
        # spacy.blank("en") assigns no POS; the gate defers to the lexicon.
        terms = [k.term for k in extractor.extract("go and python")]
        assert terms == ["python", "go"]


# ---------------------------------------------------------------------------
# analyze(): partition, cap, empty inputs
# ---------------------------------------------------------------------------


class TestAnalyze:
    def test_matched_and_missing_partition_analyzed(self, extractor: Skill_Extractor) -> None:
        analysis = extractor.analyze(
            resume_text="I write python and js daily",
            job_description="need python, java and javascript",
        )
        assert [k.term for k in analysis.analyzed] == ["python", "javascript", "java"]
        assert [k.term for k in analysis.matched] == ["python", "javascript"]
        assert [k.term for k in analysis.missing] == ["java"]

    def test_alias_in_resume_counts_as_match(self, extractor: Skill_Extractor) -> None:
        # The resume says "py"; the JD says "python" — alias resolution is
        # applied identically to both roles (Req 4.3).
        analysis = extractor.analyze(resume_text="shipped py tooling", job_description="python")
        assert [k.term for k in analysis.matched] == ["python"]
        assert analysis.missing == []

    def test_empty_resume_makes_missing_equal_analyzed(self, extractor: Skill_Extractor) -> None:
        analysis = extractor.analyze(resume_text="", job_description="python and java")
        assert analysis.matched == []
        assert analysis.missing == analysis.analyzed
        assert [k.term for k in analysis.analyzed] == ["python", "java"]

    def test_empty_job_description_yields_empty_analysis(self, extractor: Skill_Extractor) -> None:
        analysis = extractor.analyze(resume_text="python", job_description="")
        assert analysis.analyzed == []
        assert analysis.matched == []
        assert analysis.missing == []

    def test_cap_retains_highest_weighted_terms(self, lexicon: Skill_Lexicon) -> None:
        capped = Skill_Extractor(spacy.blank("en"), lexicon, max_keywords=2)
        analysis = capped.analyze(
            resume_text="",
            job_description="java, go, python and javascript",
        )
        assert [k.term for k in analysis.analyzed] == ["python", "javascript"]

    def test_zero_cap_yields_empty_analyzed_set(self, lexicon: Skill_Lexicon) -> None:
        capped = Skill_Extractor(spacy.blank("en"), lexicon, max_keywords=0)
        analysis = capped.analyze(resume_text="python", job_description="python and java")
        assert analysis.analyzed == []
