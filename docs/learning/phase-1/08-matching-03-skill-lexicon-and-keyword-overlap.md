# Skill lexicon and keyword overlap

## Introduction

This document explains how a program decides which concrete skills a job
description asks for, and then checks which of those skills a résumé actually
mentions. The mechanism has two parts. The first is a skill lexicon: a curated,
versioned dictionary of canonical skill names (such as "python" or "postgresql"),
the alternative spellings or surface forms that mean the same thing (its
aliases, such as "py" and "postgres"), and a numeric weight saying how
important each skill is. The second is keyword-overlap analysis: a procedure
that pulls a set of analyzed keywords out of the job description, then splits
that set into the terms the résumé covers (matched) and the terms it does not
(missing). This topic sits in the Matching and scoring track because the matched
fraction is one of the two halves of the Phase 1 match score, and the missing
list is what later turns into improvement suggestions.

**Learning outcomes** — after reading this document you will be able to:

- Explain what a skill lexicon stores and why a controlled vocabulary of canonical terms plus aliases is more reliable than free-text word matching.
- Describe how text is normalized and how alias substitution rewrites every surface form of a skill to one canonical term before any comparison happens.
- Explain how an analyzed keyword set is partitioned into a matched list and a missing list, and why those two lists are disjoint.
- Recognise the common mistakes in building keyword-overlap analysis and recover from them.

Prerequisites:

- [Term Frequency–Inverse Document Frequency and cosine similarity](08-matching-02-tf-idf-and-cosine-similarity.md) — this sibling Topic_Doc introduces Term Frequency (TF) and Inverse Document Frequency (IDF), the term-weighting scheme that supplies the analyzed keywords that are not already known skills.

## Problem it solves

A résumé and a job description rarely use the exact same words for the same
thing. One says "JavaScript", the other writes its abbreviation JavaScript (JS);
one says "PostgreSQL", the other says "postgres". The concrete problem is
deciding, reliably and repeatably, which required skills a candidate already
demonstrates and which they are missing — without being fooled by spelling
differences and without treating every word in the posting as a skill.

The naïve prior approach is to compare the two documents word by word: lowercase
both, split on spaces, and report the shared words. That approach fails in two
predictable ways. First, it has no notion of synonyms, so "py" and "python"
count as two unrelated tokens and a real match is silently lost. Second, it has
no notion of importance, so boilerplate filler ("responsibilities",
"experience", "team") is treated exactly like a genuine technical skill, and the
result is dominated by words that mean nothing for hiring.

A skill lexicon plus overlap analysis fixes both. A controlled vocabulary maps
every known surface form to a single canonical term, so synonyms collapse
together and a match is found wherever it genuinely exists. Per-term weights and
a filter for generic filler keep the analyzed set focused on real skills. The
output is a stable, explainable split — these skills are present, those are
absent — that does not depend on the two documents happening to choose the same
wording.

## Mental model

Think of a recruiter working with a printed checklist of skills. The checklist
is the lexicon: each row names one skill in a standard way and lists the
nicknames people use for it ("JS" and "ECMAScript" both mean JavaScript), and
each row has a star rating for how much that skill matters. The recruiter reads
the job posting, ticks every checklist row the posting calls for, and ignores
filler words like "motivated" that are not on the checklist at all. Then they
read the résumé and, for each ticked row, mark it green if the résumé shows that
skill and red if it does not. The greens are the matched skills; the reds are
the gaps.

Walked through step by step, analyzing one résumé against one job description
looks like this:

1. Normalize both texts the same way: lowercase them, squeeze repeated spaces down to one, and rewrite every known nickname to its standard canonical name so "py" becomes "python".
2. Build the analyzed set of keywords from the job description: take the canonical skills that appear in it, add the most distinctive remaining words, remove generic filler, and cap the set at a fixed size.
3. Attach a weight to each analyzed keyword — the lexicon's curated weight when the term is a known skill, otherwise a score derived from how distinctive the word is.
4. Walk the analyzed set and, for each keyword, mark it matched if it appears in the normalized résumé and missing if it does not.
5. Keep the two resulting lists ordered by weight so the most important matched skills and the most important gaps come first.

## How it works

A skill lexicon is a controlled vocabulary: a fixed dictionary, versioned and
reviewed like code, where each entry has a canonical term (the one official
spelling of a skill), a list of aliases (other surface forms that mean the same
skill), and a weight (a number expressing the skill's relative importance). The
canonical term is the key everything else collapses onto. Because the dictionary
is explicit and stored as data rather than computed on the fly, the same input
always produces the same vocabulary, and changing the vocabulary is a deliberate,
trackable edit.

Before any comparison, both texts are put through the same normalization so they
are directly comparable. Normalization case-folds the text, collapses runs of
whitespace to a single space, and then applies alias substitution: every known
surface form is rewritten to its canonical term, so "py", "py3", and "python"
all become the single token "python". Alias substitution is done
longest-match-first, meaning a long multi-word canonical such as "node.js" is
consumed before a shorter alias hidden inside it (like "js") can match, which
prevents a long term from being chewed apart by a shorter one.

With both texts normalized, the analyzed keyword set is derived from the job
description. It is the union of two sources: the canonical skills from the
lexicon that appear in the description, and the most distinctive remaining words
of the description as measured by Term Frequency–Inverse Document Frequency
(TF-IDF), a weighting that favours words frequent in this text but rare in
general. Generic job-posting filler and very short or purely numeric tokens are
discarded, the set is de-duplicated by canonical term, and it is capped at a
fixed maximum so the analysis stays bounded. Each surviving keyword carries a
weight: the lexicon's curated weight when the term is a known skill, otherwise
its distinctiveness score.

Matching a single term uses a boundary-aware test rather than a plain substring
search. A term counts as present only when it is flanked by non-alphanumeric
characters or the edges of the text, so "java" is found in "java developer" but
not inside "javascript". This matters because skill names often contain
punctuation (such as "c++" or "ci/cd"), for which ordinary word-boundary rules
behave inconsistently.

The final step partitions the analyzed set. The procedure walks the analyzed
keywords in order and, for each one, asks whether the normalized résumé contains
that canonical term: if so the keyword joins the matched list, otherwise it
joins the missing list. The two lists are disjoint by construction — every
analyzed keyword lands in exactly one of them — and their union is the whole
analyzed set, so nothing is double-counted and nothing is dropped. Because both
lists are carved out of an already weight-sorted analyzed set, the most important
matches and the most important gaps appear first. A matched term is guaranteed to
be genuinely present in the résumé, which is what makes the result trustworthy as
the basis for a coverage fraction and for gap-based suggestions.

## MatchLayer Phase 1 usage

The committed skill lexicon is the JavaScript Object Notation (JSON) artifact at
`ml/lexicon/skill_lexicon.v1.json`. It is the source of truth, regenerated by
`ml/pipelines/build_skill_lexicon.py`, and a drift check
(`tools/check_lexicon_drift.py`) keeps it byte-identical to the runtime copy the
backend ships as package data at
`apps/api/src/matchlayer_api/scoring/data/skill_lexicon.v1.json`. Each entry
records a canonical term, its aliases, a category, and a weight — here is the
FastAPI entry:

Source: `ml/lexicon/skill_lexicon.v1.json`

```json
    {
      "aliases": [
        "fast api"
      ],
      "canonical": "fastapi",
      "category": "framework",
      "display": "FastAPI",
      "weight": 0.9
    },
```

The loader at `apps/api/src/matchlayer_api/scoring/lexicon.py` reads that
artifact and exposes alias normalization. Its `normalize` method resolves any
surface form to its canonical term: a known canonical passes through, a known
alias is rewritten to its canonical, and any other word (such as a free-text
TF-IDF term) passes through unchanged so the caller can still use it:

Source: `apps/api/src/matchlayer_api/scoring/lexicon.py`

```python
    def normalize(self, term: str) -> str:
        normalized = _normalize_term(term)
        if normalized in self._entries:
            return normalized
        return self._alias_to_canonical.get(normalized, normalized)
```

The overlap analysis itself lives in
`apps/api/src/matchlayer_api/scoring/keyword_analyzer.py`. After the analyzed set
is built and ordered by weight, the analyzer walks it once and partitions each
keyword into the matched list (present in the normalized résumé) or the missing
list (absent), then returns both alongside the full analyzed set:

Source: `apps/api/src/matchlayer_api/scoring/keyword_analyzer.py`

```python
        matched: list[Keyword] = []
        missing: list[Keyword] = []
        for keyword in analyzed:
            if self._present(resume_norm, keyword.term):
                matched.append(keyword)
            else:
                missing.append(keyword)

        return KeywordAnalysis(analyzed=analyzed, matched=matched, missing=missing)
```

The analyzer imports only scikit-learn and the standard library, never the web
framework or the database, so the scoring core stays framework-free; the
`max_keywords` cap is injected through its constructor rather than read from
settings. The matched list feeds the keyword-coverage half of the score, and the
missing list is what the rule-based suggestion step turns into "add this skill"
advice.

## Common pitfalls

- **Mistake:** Comparing the résumé and job description directly without a canonical vocabulary, so synonyms and spelling variants ("py" vs "python", "postgres" vs "postgresql") are treated as different terms.
  **Symptom:** Real skills the candidate genuinely has are reported as missing, and the match score is unfairly low whenever the two documents use different wording for the same skill.
  **Recovery:** Route every surface form through the lexicon's alias normalization so all variants collapse to one canonical term before matching, and add missing aliases to the lexicon when a known synonym slips through.

- **Mistake:** Testing presence with a plain substring search instead of a boundary-aware match.
  **Symptom:** Short skill names produce false positives — "java" appears to match inside "javascript", or "go" matches inside "google" — inflating the matched list with skills the résumé never mentioned.
  **Recovery:** Require the term to be flanked by non-alphanumeric characters or the text edges, using a character-class boundary that also works for punctuated names like "c++" and "ci/cd".

- **Mistake:** Treating every frequent word in the job description as a keyword, with no filtering of generic filler or cap on the set.
  **Symptom:** The analyzed set fills up with boilerplate like "responsibilities", "experience", and "team", crowding out real skills and making coverage meaningless.
  **Recovery:** Filter domain-specific stopwords and short or purely numeric tokens, prefer curated lexicon weights, and cap the analyzed set at a fixed maximum so it stays focused and bounded.

- **Mistake:** Editing the runtime copy of the lexicon the backend loads instead of the committed source-of-truth artifact, or editing one and not the other.
  **Symptom:** The drift check fails in continuous integration, or scores change in a way no source change explains because the two lexicon copies have diverged.
  **Recovery:** Edit only the source artifact, regenerate with the build pipeline, and let the drift check confirm the runtime copy matches before merging.

## External reading

- [scikit-learn: TfidfVectorizer reference](https://scikit-learn.org/stable/modules/generated/sklearn.feature_extraction.text.TfidfVectorizer.html)
- [scikit-learn: text feature extraction](https://scikit-learn.org/stable/modules/feature_extraction.html#text-feature-extraction)
- [Python documentation: the `re` module](https://docs.python.org/3/library/re.html)
- [Python documentation: `importlib.resources`](https://docs.python.org/3/library/importlib.resources.html)
