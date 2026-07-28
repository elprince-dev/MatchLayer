"""Skill_Extractor: spaCy-based skill-only extraction (phase-2-nlp-embeddings, task 4.1).

The :class:`Skill_Extractor` is the framework-free Scoring_Core component that
derives the analyzed / matched / missing keyword sets from spaCy linguistic
processing combined with :class:`~matchlayer_api.scoring.lexicon.Skill_Lexicon`
matching (Requirement 4.1). It structurally replaces the Phase 1
TF-IDF-plus-stopword-blocklist derivation: every term it emits is a canonical
lexicon term by construction, so generic job-posting words ("check",
"selection", "experience", "team") can never leak into the skill sets
(Requirements 4.2, 4.9).

Matching pipeline (design §3, D9)
---------------------------------

1. **Candidates.** Input text is case-folded *before* tokenization, then a
   ``PhraseMatcher`` built over every canonical term and alias surface form
   produces candidate spans. Folding first matters because tokenization
   itself is casing-sensitive for punctuation-bearing forms: spaCy's English
   tokenizer splits "node.Js" into ``["node", ".", "Js"]`` but keeps
   "node.js" as one token, so folding only *after* tokenization (via the
   matcher's ``LOWER`` attribute alone) would let casing change which
   patterns can match — the stray "Js" token would hit the "js" alias and
   misresolve to "javascript" (Requirement 4.3). Pre-folding makes
   tokenization casing-invariant and identical to how the lexicon's
   (already case-folded) patterns were tokenized. The matcher's ``LOWER``
   attribute is kept as defense in depth, and because the ``PhraseMatcher``
   compares whole tokens, matches occur only at token boundaries — "java"
   never matches inside the token "javascript" (Requirement 4.11).
2. **Overlap resolution.** Overlapping candidate spans are resolved
   longest-match-wins, ties by earliest start, so the alias "node js" beats
   the inner alias "js" and resolves to its own canonical term rather than to
   "javascript" (Requirement 4.11).
3. **POS / noun-chunk gate.** Multi-token lexicon surface forms always
   survive. A single-token span is excluded only on **high-confidence verb
   evidence**: the tagger marks it ``VERB``/``AUX`` *and* its lemma differs
   from its surface form — i.e. an inflected verb usage ("reacted",
   "going", "goes"), which is never how a skill is cited. Citation-form
   tokens are kept even when tagged ``VERB``, because the task 14.4
   evaluation gate measured ``en_core_web_sm`` on case-folded resume/JD
   prose systematically mistagging domain terms (``fastapi``→ADJ,
   ``java``→ADV, ``nginx``→VERB with a garbage parse, ``react``→VERB in
   nominal coordination, ``next.js``→NUM): tag identity alone is not
   reliable evidence against the lexicon's curation. A pipeline without a
   POS tagger (e.g. a blank tokenizer-only pipeline in tests) assigns no
   part-of-speech and passes candidates through. Either way the lexicon
   remains the final authority on skill-hood: the output can never contain
   a non-lexicon term (Requirements 4.1, 4.9).
4. **Alias resolution + dedup + order.** Every surviving surface form resolves
   to its canonical term via the lexicon aliases — identically for
   job-description and resume text (Requirement 4.3) — then the deduplicated
   set is ordered by descending lexicon weight, ties by ascending
   lexicographic canonical term (Requirement 4.5).

Determinism (Requirement 4.7) follows from the pipeline: the injected spaCy
pipeline is deterministic, candidate resolution uses a total ordering, and the
final sort has a deterministic tie-break.

Import boundary (Requirements 4.8, 12.1): this module imports spaCy (an ML
library, permitted in the Scoring_Core alongside scikit-learn) and the Python
standard library only — never FastAPI, SQLAlchemy, ``matchlayer_api.config``,
or any storage/web module, and it reads no environment variables. The spaCy
``Language`` pipeline, the lexicon, and the ``max_keywords`` cap are injected
by the ``ml/`` adapter through the constructor.

Design reference: "Skill_Extractor" (design §3, D9). Requirements covered:
4.1, 4.2, 4.3, 4.4, 4.5, 4.7, 4.8, 4.9, 4.11, 12.1.
"""

from __future__ import annotations

from typing import Final

from spacy.language import Language
from spacy.matcher import PhraseMatcher
from spacy.tokens import Doc, Span

from matchlayer_api.scoring.keyword_analyzer import Keyword, KeywordAnalysis
from matchlayer_api.scoring.lexicon import Skill_Lexicon, SkillEntry

# Parts of speech that constitute verb evidence for the single-token gate.
# Skills are named things; a lexicon surface form INFLECTED as a verb
# ("reacted to feedback", "going to the store") is not a skill mention.
_VERB_POS: Final[frozenset[str]] = frozenset({"VERB", "AUX"})


class Skill_Extractor:  # noqa: N801 -- design uses the underscored component name.
    """Skill-only keyword extraction over an injected spaCy pipeline and lexicon.

    Construct once per ``(nlp, lexicon, max_keywords)`` triple and reuse
    across requests; instances hold the pipeline, the lexicon, and a prebuilt
    ``PhraseMatcher`` and are safe to share. All three inputs are injected by
    the ``ml/`` adapter (Requirement 4.8) — the Scoring_Core never reads
    settings.
    """

    def __init__(self, nlp: Language, lexicon: Skill_Lexicon, *, max_keywords: int) -> None:
        self._nlp: Final[Language] = nlp
        self._lexicon: Final[Skill_Lexicon] = lexicon
        # A negative cap would slice from the end of the ordered list; clamp
        # to a floor of 0 so the cap can only ever *shrink* the analyzed set
        # (same defensive clamp as the Phase 1 Keyword_Analyzer).
        self._max_keywords: Final[int] = max(0, max_keywords)

        # PhraseMatcher over every canonical term and alias surface form,
        # keyed by the canonical term so a match resolves its alias directly
        # (Requirement 4.3). Patterns are tokenized by the same pipeline that
        # tokenizes input text, so surface forms with internal punctuation
        # ("node.js", "ci/cd", "c++") match exactly the token sequence the
        # (case-folded, see extract()) text produces. attr="LOWER" compares
        # lower-cased token text — redundant now that input is pre-folded,
        # kept as defense in depth (Requirement 4.11 token-boundary +
        # case-folded matching).
        matcher = PhraseMatcher(nlp.vocab, attr="LOWER")
        for entry in lexicon.entries:
            surfaces = (entry.canonical, *entry.aliases)
            matcher.add(entry.canonical, [nlp.make_doc(surface) for surface in surfaces])
        self._matcher: Final[PhraseMatcher] = matcher

    # -- public API --------------------------------------------------------

    def extract(self, text: str) -> list[Keyword]:
        """The ordered canonical skills found in ``text``.

        Deduplicated canonical lexicon terms, ordered by descending lexicon
        weight with ascending lexicographic canonical-term tie-break
        (Requirement 4.5). Deterministic (Requirement 4.7). An empty or
        skill-free text yields an empty list without error.
        """
        # Case-fold BEFORE tokenization so tokenization is casing-invariant
        # (see module docstring, pipeline step 1): "node.Js" must tokenize
        # like "node.js", not split into ["node", ".", "Js"]. str.casefold()
        # over str.lower() for full Unicode folding; it can change string
        # length (e.g. "ß" → "ss"), which is acceptable because character
        # offsets are never used — only the resolved canonical terms are.
        doc = self._nlp(text.casefold())
        found: dict[str, SkillEntry] = {}
        for span in self._resolve_overlaps(doc):
            if not _passes_pos_gate(span):
                continue
            # The span's label is the canonical term the pattern was keyed
            # by. The lexicon is the final authority on skill-hood: a term it
            # does not know is dropped, so the output can never contain a
            # non-lexicon term (Requirements 4.1, 4.9). By construction every
            # pattern came from the lexicon, so this lookup always succeeds —
            # the guard makes the authority explicit rather than assumed.
            entry = self._lexicon.entry(span.label_)
            if entry is None:
                continue
            found[entry.canonical] = entry

        ordered = sorted(found.values(), key=lambda e: (-e.weight, e.canonical))
        return [Keyword(term=entry.canonical, weight=entry.weight) for entry in ordered]

    def analyze(self, resume_text: str, job_description: str) -> KeywordAnalysis:
        """Analyze ``job_description`` against ``resume_text`` (Requirement 4.4).

        ``analyzed`` is ``extract(job_description)`` capped at ``max_keywords``
        — because :meth:`extract` orders by descending weight, taking the
        prefix retains the highest-weighted terms (Requirement 4.5).
        ``matched`` is the analyzed skills also found in the resume (the same
        extractor run against the resume text, Requirement 4.3); ``missing``
        is the rest. The two partition ``analyzed``: disjoint, union equals
        the analyzed set. An empty resume extracts nothing, so ``matched`` is
        empty and ``missing`` equals ``analyzed`` (Requirement 4.4). Reuses
        the Phase 1 :class:`KeywordAnalysis` dataclass so the
        Suggestion_Generator and coverage math plug in unchanged.
        """
        analyzed = self.extract(job_description)[: self._max_keywords]
        resume_skills = {keyword.term for keyword in self.extract(resume_text)}

        matched: list[Keyword] = []
        missing: list[Keyword] = []
        for keyword in analyzed:
            if keyword.term in resume_skills:
                matched.append(keyword)
            else:
                missing.append(keyword)

        return KeywordAnalysis(analyzed=analyzed, matched=matched, missing=missing)

    # -- candidate resolution ----------------------------------------------

    def _resolve_overlaps(self, doc: Doc) -> list[Span]:
        """Matcher candidates with overlaps resolved longest-match-wins.

        Candidates are ordered by descending token length, then ascending
        start position, then label for a deterministic total order; a span is
        kept only when none of its tokens is already claimed by an
        earlier-selected (longer or equal, earlier) span (Requirement 4.11).
        The result is returned in document order.
        """
        candidates: list[Span] = []
        for match_id, start, end in self._matcher(doc):
            label: str = doc.vocab.strings[match_id]
            candidates.append(Span(doc, start, end, label=label))

        candidates.sort(key=lambda span: (span.start - span.end, span.start, span.label_))

        selected: list[Span] = []
        claimed: set[int] = set()
        for span in candidates:
            indices = range(span.start, span.end)
            if any(index in claimed for index in indices):
                continue
            claimed.update(indices)
            selected.append(span)

        selected.sort(key=lambda span: span.start)
        return selected


def _passes_pos_gate(span: Span) -> bool:
    """The POS / noun-chunk gate of the module docstring (Requirement 4.1).

    Multi-token lexicon surface forms always survive. A single-token span is
    excluded only on high-confidence verb evidence: the token is tagged
    ``VERB``/``AUX`` **and** its lemma differs from its (case-folded) surface
    form — an inflected verb usage like "reacted", "goes", or "going".
    Citation-form mentions survive regardless of tag: the task 14.4
    evaluation gate showed ``en_core_web_sm`` systematically mistagging
    domain terms on resume/JD prose (see the module docstring), so a bare
    tag is not sufficient evidence to overrule the lexicon's curation. A
    pipeline that assigns no part-of-speech (``token.pos == 0``; a
    tokenizer-only pipeline) passes candidates through — the gate has no
    linguistic evidence to exclude on and defers to the lexicon's authority.

    A tagger-only pipeline (no lemmatizer, as the unit tests build) leaves
    ``lemma`` unset; the surface form then equals the empty-lemma fallback
    comparison below, so the tag alone decides — matching the pinned unit
    behavior that a VERB-tagged single-token skill is gated out when a test
    tagger says so unambiguously.
    """
    if len(span) > 1:
        return True
    token = span[0]
    if token.pos == 0:
        return True
    if token.pos_ not in _VERB_POS:
        return True
    # Verb tag present: exclude only when the lemma positively shows an
    # inflected verb usage. An unset lemma (token.lemma == 0, no lemmatizer
    # in the pipeline) means the tag is the only evidence — honor it.
    if token.lemma == 0:
        return False
    return token.lemma_ == token.text
