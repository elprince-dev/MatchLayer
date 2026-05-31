"""Keyword_Analyzer: the keyword/skill overlap core (phase-1-matching, task 4.1).

Given a job-description text and a resume text, the :class:`Keyword_Analyzer`
derives the *analyzed keyword set* and partitions it into the terms the resume
already **covers** (``matched``) and the terms it is **missing**. This is the
keyword-coverage half of the deterministic, non-LLM ``Match_Scorer``.

Algorithm (Requirement 6.1 through 6.6):

1. **Normalize** both texts the same way: case-fold, collapse runs of
   whitespace to a single space, and apply the ``Skill_Lexicon`` alias rules so
   every surface form of a skill (``"py"``, ``"py3"``, ``"node js"``) is
   rewritten to its canonical term (``"python"``, ``"node.js"``). Alias
   substitution is longest-match-first so a multi-word canonical such as
   ``"node.js"`` is never clobbered by a shorter inner alias like ``"js"``
   (Requirement 6.2).
2. **Analyzed set** is the union of
   (a) ``Skill_Lexicon`` canonical terms that appear in the normalized JD, and
   (b) the highest-weighted TF-IDF terms of the JD (scikit-learn
   :class:`~sklearn.feature_extraction.text.TfidfVectorizer`),
   de-duplicated by canonical term and **capped at ``max_keywords``**
   (Requirement 6.1). Each analyzed term carries a weight: the lexicon weight
   when the term is a known skill, otherwise the term's TF-IDF score.
3. **Partition** the analyzed set into ``matched`` (term present in the
   normalized resume text) and ``missing`` (absent). The two lists are disjoint
   and their union is the analyzed set (Requirements 6.3, 6.4); every matched
   term is verifiably present in the normalized resume text (Requirement 6.5);
   and because they are carved out of the already-sorted analyzed list, both
   are ordered by descending weight (Requirement 6.6).

Import boundary (Requirement 10.1): this module imports **only** scikit-learn
and the Python standard library, never FastAPI, SQLAlchemy,
``matchlayer_api.config``, or any storage/web module. The ``max_keywords`` cap
(``MATCHLAYER_MATCH_MAX_KEYWORDS``) is **injected** by the ``ml/`` adapter via
the constructor rather than read from settings here, keeping the scoring core
framework-free.

Design reference: "Keyword_Analyzer". Requirements covered: 6.1 through 6.6.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from sklearn.feature_extraction.text import (  # type: ignore[import-untyped]
    TfidfVectorizer,  # scikit-learn ships no py.typed / stubs
)

from matchlayer_api.scoring.lexicon import Skill_Lexicon

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

# Domain-specific stopwords: generic job-posting filler that TF-IDF may score
# highly but that are never actionable skills or meaningful keywords. These
# supplement scikit-learn's built-in English stopwords (which cover function
# words like "the", "and", "is"). This set targets:
#   - HR/recruiting boilerplate ("qualifications", "responsibilities", etc.)
#   - Generic verbs/nouns that appear in every JD ("experience", "team", etc.)
#   - Legal/compliance filler ("equal", "privacy", "employer", etc.)
#   - Lorem-ipsum fragments that leak from template JDs ("magna", "lorem")
_JOB_POSTING_STOPWORDS: Final[frozenset[str]] = frozenset(
    {
        # HR / recruiting boilerplate
        "qualifications",
        "responsibilities",
        "requirements",
        "preferred",
        "required",
        "minimum",
        "ability",
        "skills",
        "duties",
        "description",
        "position",
        "role",
        "candidate",
        "candidates",
        "applicant",
        "applicants",
        "application",
        "applications",
        "apply",
        "resume",
        "cover",
        "letter",
        "hire",
        "hiring",
        "employment",
        "employer",
        "employee",
        "employees",
        "job",
        "jobs",
        "career",
        "careers",
        "opportunity",
        "opportunities",
        "offer",
        "offers",
        "compensation",
        "salary",
        "benefits",
        "bonus",
        "package",
        # Generic verbs / adjectives
        "experience",
        "experienced",
        "proficient",
        "proficiency",
        "strong",
        "excellent",
        "good",
        "great",
        "proven",
        "demonstrated",
        "able",
        "capable",
        "knowledge",
        "understanding",
        "familiar",
        "familiarity",
        "comfortable",
        "passion",
        "passionate",
        "motivated",
        "self",
        "driven",
        "detail",
        "oriented",
        "work",
        "working",
        "worked",
        "develop",
        "developing",
        "developed",
        "development",
        "build",
        "building",
        "create",
        "creating",
        "manage",
        "managing",
        "support",
        "supporting",
        "provide",
        "providing",
        "ensure",
        "ensuring",
        "maintain",
        "maintaining",
        "implement",
        "implementing",
        "collaborate",
        "collaborating",
        "collaboration",
        "communicate",
        "communication",
        "help",
        "helping",
        "believe",
        "expect",
        "expected",
        "looking",
        "seeking",
        "join",
        "joining",
        "engaging",
        "engaged",
        # Team / org filler
        "team",
        "teams",
        "company",
        "organization",
        "department",
        "group",
        "member",
        "members",
        "environment",
        "culture",
        "industry",
        "global",
        "world",
        "leading",
        "leader",
        # Legal / compliance / EEO
        "equal",
        "diversity",
        "inclusive",
        "inclusion",
        "discrimination",
        "privacy",
        "protected",
        "status",
        "race",
        "gender",
        "religion",
        "disability",
        "veteran",
        "accommodation",
        "reasonable",
        "information",
        # Education filler
        "degree",
        "bachelor",
        "master",
        "phd",
        "university",
        "college",
        "education",
        "equivalent",
        # Time / quantity filler
        "years",
        "year",
        "months",
        "month",
        "days",
        "hours",
        "time",
        "full",
        "part",
        # Lorem ipsum fragments
        "magna",
        "lorem",
        "ipsum",
        "dolor",
        "amet",
        "consectetur",
        # Misc filler
        "including",
        "include",
        "includes",
        "related",
        "relevant",
        "additional",
        "plus",
        "etc",
        "based",
        "level",
        "senior",
        "junior",
        "mid",
        "entry",
        "cases",
        "case",
        "process",
        "processes",
        "project",
        "projects",
        "technologies",
        "technology",
        "tools",
        "tool",
        "solutions",
        "solution",
        "services",
        "service",
        "systems",
        "system",
        "data",
        "testing",
        "engineering",
        "validation",
        "design",
        "new",
        "best",
        "practices",
        "high",
        "quality",
        "performance",
        "results",
        # More generic filler
        "ideal",
        "range",
        "grow",
        "growth",
        "applies",
        "cum",
        "laude",
        "production",
        "products",
        "product",
        "business",
        "client",
        "clients",
        "customer",
        "customers",
        "stakeholders",
        "stakeholder",
        "report",
        "reporting",
        "responsible",
        "focus",
        "focused",
        "track",
        "record",
        "success",
        "successful",
        "effectively",
        "efficiently",
        "multiple",
        "various",
        "across",
        "within",
        "well",
        "like",
        "use",
        "using",
        "used",
        "need",
        "needs",
        "needed",
        "want",
        "make",
        "making",
    }
)


@dataclass(frozen=True, slots=True)
class Keyword:
    """One analyzed keyword and its weight.

    ``term`` is always a normalized term (case-folded, whitespace-collapsed,
    and, for known skills, the lexicon's canonical form). ``weight`` is the
    lexicon weight for a known skill, or the term's TF-IDF score otherwise.
    Serialized to the ``{term, weight}`` JSONB shape on the Match_Result.
    """

    term: str
    weight: float


@dataclass(frozen=True, slots=True)
class KeywordAnalysis:
    """The analyzed keyword set and its matched/missing partition.

    Invariants (by construction, see :meth:`Keyword_Analyzer.analyze`):

    * ``matched`` and ``missing`` are disjoint and their union is ``analyzed``
      (Requirements 6.3, 6.4);
    * every ``matched`` term is present in the normalized resume text
      (Requirement 6.5);
    * all three lists are ordered by descending weight (Requirement 6.6).
    """

    analyzed: list[Keyword]
    matched: list[Keyword]
    missing: list[Keyword]


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------

# A term is "present" only when flanked by non-alphanumeric characters (or the
# string edges), so ``java`` matches in ``"java developer"`` but not inside
# ``"javascript"``. We deliberately use ``[a-z0-9]`` (not ``\b``) for the
# boundary because lexicon terms themselves contain non-word characters
# (``c++``, ``c#``, ``ci/cd``, ``.net``, ``node.js``) for which ``\b`` behaves
# inconsistently. Text is case-folded before matching, so the class is lower
# case only.
_BOUNDARY_BEFORE: Final[str] = r"(?<![a-z0-9])"
_BOUNDARY_AFTER: Final[str] = r"(?![a-z0-9])"


def _collapse(text: str) -> str:
    """Case-fold ``text`` and collapse all whitespace runs to single spaces."""
    return " ".join(text.casefold().split())


def _presence_pattern(term: str) -> re.Pattern[str]:
    """A compiled regex matching ``term`` as a boundary-delimited occurrence."""
    return re.compile(_BOUNDARY_BEFORE + re.escape(term) + _BOUNDARY_AFTER)


# ---------------------------------------------------------------------------
# Keyword_Analyzer
# ---------------------------------------------------------------------------


class Keyword_Analyzer:  # noqa: N801 -- design uses the underscored component name.
    """Derive and partition a job description's analyzed keyword set.

    Construct once per ``(lexicon, max_keywords)`` pair and reuse across
    requests; instances hold only the lexicon, the cap, and precompiled regexes
    and are safe to share. ``max_keywords`` is injected (typically
    ``MATCHLAYER_MATCH_MAX_KEYWORDS``) so the scoring core never reads settings.
    """

    def __init__(self, lexicon: Skill_Lexicon, *, max_keywords: int) -> None:
        self._lexicon: Final[Skill_Lexicon] = lexicon
        # A negative cap would slice from the end of the ordered list; clamp to
        # a floor of 0 so the cap can only ever *shrink* the analyzed set.
        self._max_keywords: Final[int] = max(0, max_keywords)

        # Precompile a presence pattern per canonical term (used to test the JD
        # and resume). TF-IDF-derived terms are compiled on demand in analyze().
        self._canonical_patterns: Final[dict[str, re.Pattern[str]]] = {
            term: _presence_pattern(term) for term in lexicon.canonical_terms
        }

        # Build one longest-match-first alias-substitution regex. Both canonical
        # terms and aliases are alternatives so a long canonical (``node.js``)
        # is consumed before a short inner alias (``js``) can match it; each
        # surface form maps to its canonical replacement. Canonicals map to
        # themselves, which both protects them and is a harmless no-op rewrite.
        surface_to_canonical: dict[str, str] = {}
        for entry in lexicon.entries:
            surface_to_canonical[entry.canonical] = entry.canonical
            for alias in entry.aliases:
                surface_to_canonical.setdefault(alias, entry.canonical)

        self._surface_to_canonical: Final[dict[str, str]] = surface_to_canonical
        self._alias_pattern: Final[re.Pattern[str] | None] = self._build_alias_pattern(
            surface_to_canonical
        )

    # -- public API --------------------------------------------------------

    def analyze(self, resume_text: str, job_description: str) -> KeywordAnalysis:
        """Analyze ``job_description`` against ``resume_text``.

        Returns the analyzed keyword set (capped, ordered by descending weight)
        partitioned into ``matched`` and ``missing``. An empty or
        whitespace-only job description yields an empty analysis without error,
        which lets the Match_Scorer treat keyword coverage as ``0`` rather than
        raising (Requirement 5.6).
        """
        jd_norm = self._normalize(job_description)
        if not jd_norm:
            return KeywordAnalysis(analyzed=[], matched=[], missing=[])

        resume_norm = self._normalize(resume_text)

        analyzed = self._analyzed_set(jd_norm)

        matched: list[Keyword] = []
        missing: list[Keyword] = []
        for keyword in analyzed:
            if self._present(resume_norm, keyword.term):
                matched.append(keyword)
            else:
                missing.append(keyword)

        return KeywordAnalysis(analyzed=analyzed, matched=matched, missing=missing)

    # -- normalization -----------------------------------------------------

    def _normalize(self, text: str) -> str:
        """Case-fold, collapse whitespace, then substitute aliases to canonical."""
        collapsed = _collapse(text)
        if not collapsed or self._alias_pattern is None:
            return collapsed
        return self._alias_pattern.sub(lambda m: self._surface_to_canonical[m.group(0)], collapsed)

    @staticmethod
    def _build_alias_pattern(
        surface_to_canonical: dict[str, str],
    ) -> re.Pattern[str] | None:
        """Compile the boundary-delimited, longest-match-first surface regex."""
        if not surface_to_canonical:
            return None
        # Longest surface forms first so the alternation prefers e.g. the whole
        # ``node.js`` over the inner ``js``; ``-len`` then the term itself keeps
        # the ordering deterministic.
        surfaces = sorted(surface_to_canonical, key=lambda s: (-len(s), s))
        alternation = "|".join(re.escape(s) for s in surfaces)
        return re.compile(_BOUNDARY_BEFORE + "(?:" + alternation + ")" + _BOUNDARY_AFTER)

    # -- analyzed-set derivation ------------------------------------------

    def _analyzed_set(self, jd_norm: str) -> list[Keyword]:
        """Build the capped, descending-weight analyzed set from a normalized JD."""
        # Source (a): canonical lexicon terms that appear in the JD. Aliases
        # were already rewritten to canonical during normalization, so a
        # canonical-presence test suffices.
        weights: dict[str, float] = {}
        for entry in self._lexicon.entries:
            if self._canonical_patterns[entry.canonical].search(jd_norm) is not None:
                weights[entry.canonical] = entry.weight

        # Source (b): the highest-weighted TF-IDF terms of the JD. A term that
        # the lexicon knows keeps its (higher, curated) lexicon weight; an
        # unknown term takes its TF-IDF score as its weight.
        for term, score in self._tfidf_terms(jd_norm):
            canonical = self._lexicon.normalize(term)
            if canonical in weights:
                continue
            lexicon_weight = self._lexicon.weight(canonical)
            weights[canonical] = lexicon_weight if lexicon_weight is not None else score

        # Order by descending weight, breaking ties by term for determinism,
        # then apply the cap (Requirements 6.1, 6.6).
        ordered = sorted(weights.items(), key=lambda kv: (-kv[1], kv[0]))
        capped = ordered[: self._max_keywords]
        return [Keyword(term=term, weight=weight) for term, weight in capped]

    def _tfidf_terms(self, jd_norm: str) -> list[tuple[str, float]]:
        """Top ``max_keywords`` TF-IDF terms of the JD, descending by score.

        Uses scikit-learn's deterministic :class:`TfidfVectorizer` over the
        single normalized JD document. English stop words are removed so the
        analyzed set is well-formed rather than dominated by ``the``/``and``.
        Additionally, domain-specific job-posting stopwords and short/numeric
        tokens are filtered out so the analyzed set contains only actionable
        terms. A JD that contains only stop words or punctuation yields an
        empty vocabulary, which the vectorizer signals by raising
        ``ValueError``; we treat that as "no TF-IDF terms" so source (a) can
        still contribute.
        """
        if self._max_keywords == 0:
            return []
        vectorizer = TfidfVectorizer(stop_words="english")
        try:
            matrix = vectorizer.fit_transform([jd_norm])
        except ValueError:
            return []

        features: list[str] = [str(name) for name in vectorizer.get_feature_names_out()]
        scores: list[float] = [float(value) for value in matrix.toarray()[0]]
        pairs = sorted(zip(features, scores, strict=True), key=lambda fs: (-fs[1], fs[0]))

        # Filter out domain-specific stopwords, short tokens (< 3 chars),
        # and purely numeric tokens that are never meaningful keywords.
        filtered: list[tuple[str, float]] = []
        for term, score in pairs:
            if len(term) < 3:
                continue
            if term.isdigit():
                continue
            if term in _JOB_POSTING_STOPWORDS:
                continue
            filtered.append((term, score))
            if len(filtered) >= self._max_keywords:
                break

        return filtered

    # -- partition helper --------------------------------------------------

    def _present(self, resume_norm: str, term: str) -> bool:
        """True if ``term`` occurs (boundary-delimited) in the normalized resume.

        Aliases in the resume were rewritten to canonical during normalization,
        so testing the canonical term is sufficient and makes alias surface
        forms interchangeable for matching (Requirement 6.2).
        """
        pattern = self._canonical_patterns.get(term)
        if pattern is None:
            pattern = _presence_pattern(term)
        return pattern.search(resume_norm) is not None
