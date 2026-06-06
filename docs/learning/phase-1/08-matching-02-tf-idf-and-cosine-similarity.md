# TF-IDF and cosine similarity

## Introduction

This document explains how a program can measure how similar two pieces of text
are — for example a résumé and a job description — using nothing but word
counts and a little geometry. It covers two ideas that work together. The first
is Term Frequency (TF) scaled by Inverse Document Frequency (IDF) — together
written Term Frequency–Inverse Document Frequency (TF-IDF) — a way of turning a
document into a list of numbers where common-but-uninformative words count for
little and rare-but-distinctive words count for a lot. The second is cosine
similarity, a measurement that compares two such lists of numbers and returns a
single value between 0 and 1 describing how closely they point in the same
direction. Together they give a deterministic, explainable similarity score with
no machine-learning model to train and no external service to call. This topic
sits in the Matching and scoring track because this similarity value is one of
the two halves of the Phase 1 match score.

**Learning outcomes** — after reading this document you will be able to:

- Explain what Term Frequency (TF) and Inverse Document Frequency (IDF) each measure and why multiplying them downweights filler words.
- Describe how a document becomes a vector of numbers and how cosine similarity compares two such vectors.
- Explain why cosine similarity ignores document length and returns a value in the range 0 to 1.
- Recognise the common mistakes when computing a text-similarity score and recover from them.

Prerequisites: No prerequisites. Every term used here — including TF-IDF,
cosine similarity, and the idea of a vector — is defined inline as it appears.

## Problem it solves

Comparing two documents by similarity sounds easy until you try to make a
computer do it consistently. The concrete problem is producing a single,
repeatable number that says "these two texts overlap a lot" or "these two texts
have little in common", in a way that is fair to documents of different lengths
and is not fooled by words that appear in almost every document.

The naïve prior approach is to count shared words: take the set of words in each
document and report how many they have in common. That approach has two failures
that show up immediately. First, it treats every word as equally important, so a
document full of the words "the", "and", and "with" looks similar to any other
document full of the same filler. Second, raw shared-word counts grow with
document length, so a long document looks more similar to everything merely
because it contains more words — a short, sharply relevant document can score
lower than a long, rambling, off-topic one.

TF-IDF fixes the first failure by weighting words so that ubiquitous words barely
count and distinctive words dominate. Cosine similarity fixes the second by
comparing the _direction_ of two documents' weighted word profiles rather than
their _magnitude_, which removes the length bias. The result is a stable score in
a fixed range that reflects shared meaningful vocabulary rather than shared
filler or sheer size.

## Mental model

Think of each document as a recipe, and the vocabulary across all documents as
the full shelf of possible ingredients. A document becomes a vector — an ordered
list of numbers, one slot per ingredient on the shelf — where each slot holds how
much that ingredient matters in this recipe. A word that appears in every recipe
(salt) gets a tiny weight because it tells you nothing distinctive; a word that
appears in only a few recipes (saffron) gets a large weight because it is a
strong signal of what the dish actually is. That weighting is the TF-IDF step.

Comparing two recipes then becomes comparing the directions their ingredient
vectors point. Cosine similarity measures the angle between the two vectors: if
they emphasise the same distinctive ingredients they point the same way and the
angle is small (similarity near 1); if they share nothing distinctive they point
at right angles (similarity near 0). Doubling a recipe — using twice as much of
everything — does not change the direction it points, which is exactly why this
measurement ignores document length.

Walked through step by step, scoring two documents looks like this:

1. Build the shared vocabulary: collect every distinct word that appears across both documents.
2. For each document, compute a Term Frequency (TF) for every word — how often that word appears in this document.
3. Weight each word by its Inverse Document Frequency (IDF) — a factor that shrinks toward zero for words appearing in many documents and grows for words appearing in few, so distinctive words dominate the vector.
4. The TF-IDF value for a word in a document is its TF multiplied by its IDF; together these values form that document's vector.
5. Compute the cosine of the angle between the two vectors to get a single similarity value between 0 and 1.

## How it works

A document is converted into a vector — an ordered list of numbers — by first
fixing a vocabulary: the set of distinct words (called terms) seen across the
collection of documents being compared. Each position in the vector corresponds
to one term in that vocabulary, so every document is described by a list of the
same length, most of whose entries are zero because any one document uses only a
fraction of the whole vocabulary.

The number placed in each slot is the term's Term Frequency (TF) multiplied by
its Inverse Document Frequency (IDF). Term Frequency is how often the term occurs
in this particular document; a term that appears five times has a higher TF than
one that appears once. Inverse Document Frequency is a factor computed from how
many documents in the collection contain the term at all: a term that shows up in
almost every document gets an IDF near zero, while a term that shows up in only
one or two documents gets a large IDF. The product, Term Frequency–Inverse
Document Frequency (TF-IDF), is therefore large only when a term is both frequent
in this document and rare across the collection — in other words, when it is
distinctive. Filler words that appear everywhere are crushed toward zero no
matter how often they repeat, and a configurable stop-word list can drop the most
common ones (such as "the" and "and") entirely before any counting happens.

Once both documents are TF-IDF vectors, cosine similarity compares them. Picture
the two vectors as arrows starting from the same origin in a space with one axis
per vocabulary term. Cosine similarity is the cosine of the angle between those
two arrows, computed as their dot product divided by the product of their
lengths. The division by length is the important part: it normalises away how big
each vector is, so only the _direction_ matters. Two documents that emphasise the
same distinctive terms point in nearly the same direction and score close to 1;
two documents with no shared distinctive terms sit at right angles and score 0.

Because TF-IDF values are never negative — a term either appears or it does not,
and frequencies and IDF factors are non-negative — the angle between two such
vectors can never exceed ninety degrees, so the cosine lands in the closed range
0 to 1 and never goes negative. This makes the output easy to reason about: 0
means "no shared distinctive vocabulary", 1 means "identical distinctive
vocabulary profile", and values in between scale smoothly. One edge case is worth
naming up front: if a document pair shares no usable terms at all — for instance,
text made only of punctuation or single characters that the tokenizer discards —
there is no vocabulary to compare, and the sensible answer is a similarity of 0
rather than an error.

## MatchLayer Phase 1 usage

The deterministic scorer lives in
`apps/api/src/matchlayer_api/scoring/scorer.py`. It imports exactly two things
from scikit-learn — `TfidfVectorizer`, which turns text into TF-IDF vectors, and
`cosine_similarity`, which compares them — and nothing from the web framework or
the database, so the scoring core stays framework-free:

Source: `apps/api/src/matchlayer_api/scoring/scorer.py`

```python
from sklearn.feature_extraction.text import (  # type: ignore[import-untyped]
    TfidfVectorizer,  # scikit-learn ships no py.typed / stubs
)
from sklearn.metrics.pairwise import (  # type: ignore[import-untyped]
    cosine_similarity,  # scikit-learn ships no py.typed / stubs
)
```

The similarity itself is computed in a small helper that fits a single
`TfidfVectorizer` over the two normalized documents, takes the cosine similarity
of the resulting two vectors, and clamps the value into the range 0 to 1. The
helper treats scikit-learn's "empty vocabulary" error — raised when neither
document contains a usable term — as "no shared signal" and returns 0 instead of
raising, so scoring never crashes on degenerate input:

Source: `apps/api/src/matchlayer_api/scoring/scorer.py`

```python
def _similarity(resume_norm: str, jd_norm: str) -> float:
    vectorizer = TfidfVectorizer()
    try:
        matrix = vectorizer.fit_transform([resume_norm, jd_norm])
    except ValueError:
        return 0.0
    sim = float(cosine_similarity(matrix[0:1], matrix[1:2])[0][0])
    return max(0.0, min(1.0, sim))
```

The vectorizer is fit on only the two documents being compared (the résumé and
the job description), so the Inverse Document Frequency factor is derived from
that pair rather than from a separate trained corpus — which is what keeps the
score deterministic and reproducible with no model artifact to version. This
similarity value is only one half of the final match score; the other half is a
keyword-coverage fraction, and the two are combined with configured weights. The
scoring core never reads configuration itself: the weights and caps are injected
through the constructor by the thin adapter at
`apps/api/src/matchlayer_api/ml/scorer_adapter.py`, which is the one place that
bridges the framework world into the framework-free scorer.

## Common pitfalls

- **Mistake:** Comparing documents by raw shared-word counts without any weighting, so filler words like "the" and "and" dominate the comparison.
  **Symptom:** Two unrelated documents score as highly similar purely because they both contain a lot of common English words.
  **Recovery:** Weight terms with TF-IDF (and enable a stop-word list) so ubiquitous words are crushed toward zero and only distinctive shared vocabulary drives the score.

- **Mistake:** Using a length-sensitive similarity measure (such as raw dot product or shared-count) instead of cosine similarity.
  **Symptom:** Long documents appear more similar to everything, and a short, sharply relevant document scores lower than a long, off-topic one.
  **Recovery:** Use cosine similarity, which divides by vector length so only direction matters, removing the bias toward longer documents.

- **Mistake:** Assuming the similarity computation always succeeds, and not handling text that contains no usable terms after tokenization.
  **Symptom:** The vectorizer raises an "empty vocabulary" error on input made only of punctuation or single characters, and the request fails with a server error.
  **Recovery:** Catch the empty-vocabulary error and treat it as a similarity of 0 ("no shared signal"), so scoring degrades gracefully instead of crashing.

- **Mistake:** Fitting the term weights on one collection of documents and then scoring a different, unrelated pair, expecting the numbers to be comparable.
  **Symptom:** Scores drift unpredictably between runs or environments because the Inverse Document Frequency factors come from a hidden, changing corpus.
  **Recovery:** Fit the vectorizer on exactly the documents being compared (or a fixed, versioned corpus) so the computation is deterministic and reproducible.

## External reading

- [scikit-learn: TF-IDF term weighting](https://scikit-learn.org/stable/modules/feature_extraction.html#tfidf-term-weighting)
- [scikit-learn: TfidfVectorizer reference](https://scikit-learn.org/stable/modules/generated/sklearn.feature_extraction.text.TfidfVectorizer.html)
- [scikit-learn: cosine_similarity reference](https://scikit-learn.org/stable/modules/generated/sklearn.metrics.pairwise.cosine_similarity.html)
- [scikit-learn: pairwise metrics and cosine similarity](https://scikit-learn.org/stable/modules/metrics.html#cosine-similarity)
