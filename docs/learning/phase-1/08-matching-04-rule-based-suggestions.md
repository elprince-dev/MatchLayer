# Rule-based suggestions from missing terms

## Introduction

When a resume is scored against a job description, the score on its own is not
actionable. A number tells the user where they stand but not what to do next.
The piece that closes that gap is a suggestion generator: a component that reads
the set of _missing terms_ — the skills or keywords the job asks for that the
resume does not mention — and turns each one into a short, plain-text piece of
advice. This document explains how that generator produces advice **without any
Large Language Model (LLM)** — a machine-learning system that generates free-form
text from a learned statistical model of language — and instead uses fixed
templates and a small set of deterministic rules.

The approach matters because it is cheap, fast, predictable, and safe. There is
no token bill, no network call, and no risk of the system inventing experience
the user never had. A _template_ here means a fixed sentence with a single
labelled slot (for example `{name}`) into which a term is substituted; a rule
selects which template applies. The generator is also _deterministic_, meaning
the same input always produces the same output in the same order, which makes its
behaviour reproducible and testable.

**Learning outcomes** — after reading this document you will be able to:

- Explain why a fixed-template, rule-based generator is preferred over an LLM for producing improvement suggestions in an early, cost-constrained product.
- Describe how a set of missing terms is mapped to advice using per-category templates and a fallback template.
- Explain why ordering suggestions by importance and capping their count keeps the output focused and stable.
- Describe how phrasing advice conditionally prevents the system from fabricating a user's experience.

Prerequisites: No prerequisites. This document defines the missing-term set, the
skill lexicon (the committed, versioned list of known skills, each tagged with a
display name and a category), and the templating mechanics inline as it goes.

## Problem it solves

The concrete problem is the "now what?" moment. A candidate sees that their
resume matched a job at some percentage and immediately wants to know which gaps
to close. Handing them a raw list of missing keywords is barely better than the
score — it names the gaps but offers no direction, and a bare keyword such as
`kubernetes` does not tell a nervous job-seeker what action to take.

One obvious approach is to send the resume, the job description, and the missing
terms to a Large Language Model and ask it to write tailored advice. That
produces fluent prose, but it carries real costs for an early product. Every call
is metered and billed, which collides head-on with a tight monthly budget. The
output is non-deterministic, so the same input can yield different advice on two
runs, which is hard to test and hard to trust. Worst of all, a generative model
will happily hallucinate — it can assert that the candidate "has five years of
Kubernetes experience" when nothing in the resume supports that, which is both
misleading and, on a resume, potentially harmful.

A second approach is to write one giant hand-coded paragraph per job. That does
not scale and cannot adapt to whichever terms happen to be missing.

The rule-based generator solves the problem a third way: it treats each missing
term as a trigger for a small, pre-written, conditional piece of advice. It costs
nothing per request, always returns the same result for the same input, and —
because every template is phrased as _"if you have done X, then mention it"_ —
it can never claim the user did something. It trades the eloquence of a language
model for predictability, zero cost, and safety, which is the right trade for an
early phase whose guiding principle is "infrastructure before intelligence".

## Mental model

Think of the generator as a vending machine for advice. You do not negotiate with
a vending machine or ask it to compose something new; you press a button and a
specific, pre-stocked item drops out. Each missing term is a button press, and
each slot in the machine holds a pre-written sentence with one blank to fill in.
The machine never improvises — it only dispenses what was stocked — and that is
exactly why its behaviour is dependable.

Walking through a single request, the generator does this:

1. **Receive the missing terms.** The input is the list of weighted terms the job wants but the resume lacks. _Weight_ here is a number expressing how important a term is to the match; a higher weight means the term mattered more.
2. **Handle the empty case.** If nothing is missing, the generator returns exactly one affirmative message ("you already cover the key keywords") rather than an empty list, so the user always receives a sentence.
3. **Order by importance.** When terms are missing, sort them from highest weight to lowest using a _stable sort_ — a sort that preserves the original order among items of equal weight — so the most important gaps appear first and ties stay predictable.
4. **Look up a template per term.** For each term, find its category in the skill lexicon and pick the matching template; if the term is unknown, fall back to a generic template.
5. **Fill the blank and cap the list.** Substitute the term's display name into the template's slot, then keep at most a configured maximum number of suggestions.

The result is an ordered list of short sentences, one per important missing term,
produced by lookup-and-substitute rather than by generation.

## How it works

Rule-based text generation replaces a learned model with an explicit mapping from
inputs to outputs. The inputs are a set of items — here, terms — each tagged with
metadata, and the output is text assembled by selecting a fixed template and
filling in a small number of slots. Nothing about the wording is learned or
sampled; it is authored once, by a human, and reused verbatim except for the
substituted values.

The central data structure is a dictionary of templates keyed by _category_. A
category is a coarse classification of a term — for example whether a skill is a
programming language, a framework, a database, or a soft skill. Grouping terms by
category lets one template serve many terms while still reading naturally: advice
for a programming language can say "list it in your skills section and point to a
project", while advice for a soft skill can say "back it up with a concrete
example", and each reads correctly for every term in its group. A single fallback
template covers any term whose category is unknown or that is not classified at
all, so there is always something to say.

Three design rules keep the output trustworthy:

- **Determinism.** Given the same inputs and the same templates, the generator
  must produce byte-for-byte identical output, including order. This is what makes
  the component testable with simple equality assertions and what lets the rest of
  the system cache or reproduce a result. Determinism comes from using fixed
  templates and a stable, total ordering rather than any random choice.

- **Bounded, ordered output.** Returning one sentence for every missing term can
  overwhelm the user, so the list is ordered by descending importance and then
  truncated to a maximum length. The user sees the few gaps that matter most,
  not an exhaustive dump.

- **No fabrication.** Each sentence is phrased conditionally — "_if_ you have done
  X, _then_ surface it" — and addresses exactly one term. Conditional phrasing
  shifts the claim from an assertion about the user's history (which the generator
  cannot know) to a suggested action the user can choose to take. This is the
  single most important safety property: a suggestion engine must never put words
  in the user's mouth or invent credentials, employers, or dates.

A useful contrast is with a generative model. A Large Language Model produces
text by sampling from a probability distribution, which is why it is fluent but
also why it is non-deterministic and capable of confident falsehoods. A
rule-based generator gives up fluency and adaptability in exchange for a guarantee:
the only sentences it can ever emit are the ones a human wrote in advance, with
only the term name varying.

## MatchLayer Phase 1 usage

The suggestion generator is implemented at
`apps/api/src/matchlayer_api/scoring/suggestions.py` as a `Suggestion_Generator`
class. It lives inside the framework-free scoring package, which imports only
scikit-learn and the Python standard library and never reaches into the
application's configuration, so the component stays a pure, testable unit. The
maximum number of suggestions is passed in by the caller — wired from the
`MATCHLAYER_MATCH_MAX_SUGGESTIONS` setting in
`apps/api/src/matchlayer_api/config.py` through the adapter in
`apps/api/src/matchlayer_api/ml/scorer_adapter.py` — rather than read directly,
which is what keeps the import boundary clean.

The templates are plain module-level constants. The generic fallback, used for a
term that has no skill-lexicon entry (the lexicon is the committed, versioned list
of known skills, each with a display name and a category), reads:

Source: `apps/api/src/matchlayer_api/scoring/suggestions.py`

```python
_DEFAULT_TEMPLATE: Final[str] = (
    "If you have experience with {name}, consider adding it to your resume "
    "where it's relevant to this role."
)
```

Note the conditional `"If you have experience with ..."` phrasing — this is the
no-fabrication rule expressed in literal text. The slot `{name}` is the only part
that changes per term.

When no terms are missing, the generator must still return something useful. A
single affirmative message is defined for that case:

Source: `apps/api/src/matchlayer_api/scoring/suggestions.py`

```python
_AFFIRMATIVE_TEXT: Final[str] = (
    "Your resume already covers the key keywords identified for this job. Keep "
    "tailoring it to the specific role to keep the match strong."
)
```

The public `generate` method ties the rules together: it short-circuits the empty
case to that single affirmative suggestion, otherwise sorts the missing terms by
descending weight, builds one suggestion per term, and truncates to the configured
cap:

Source: `apps/api/src/matchlayer_api/scoring/suggestions.py`

```python
        if not missing:
            return [Suggestion(keyword="", text=_AFFIRMATIVE_TEXT)]
        ordered = sorted(missing, key=lambda kw: kw.weight, reverse=True)
        suggestions = [self._suggestion_for(kw) for kw in ordered]
        return suggestions[: self._max_suggestions]
```

The per-term lookup is where the category rule lives. It reads the term's lexicon
entry, picks the category template (or the fallback when the category is
unrecognized), and falls back to the raw term name when the term is a free-text
statistical keyword — a word drawn straight from the text and weighted by how
distinctive it is, rather than a curated skill — that the lexicon does not know:

Source: `apps/api/src/matchlayer_api/scoring/suggestions.py`

```python
    def _suggestion_for(self, keyword: KeywordLike) -> Suggestion:
        """Build the fixed-template suggestion for a single missing keyword."""
        entry = self._lexicon.entry(keyword.term)
        if entry is not None:
            name = entry.display
            template = _CATEGORY_TEMPLATES.get(entry.category, _DEFAULT_TEMPLATE)
        else:
            # A free-text TF-IDF term not in the lexicon: use the term verbatim
            # (it is already normalized/case-folded) with the generic template.
            name = keyword.term
            template = _DEFAULT_TEMPLATE
        return Suggestion(keyword=keyword.term, text=template.format(name=name))
```

Every branch ends in `template.format(name=name)`, the single substitution step,
and returns a frozen `Suggestion` value carrying the term it addresses and the
finished text. No branch calls a network service or a model. The whole component
is lookup, substitute, order, and cap.

## Common pitfalls

- **Mistake:** Returning an empty list of suggestions when the resume already covers every required term.
  **Symptom:** The results screen shows a score and a "suggestions" area that is blank, leaving the user unsure whether the system ran correctly or merely had nothing to say.
  **Recovery:** Treat the empty-missing case explicitly and return exactly one affirmative message, so the suggestion list is never empty and the user always receives a sentence.

- **Mistake:** Phrasing advice as a statement about the user ("You have experience with {name}") instead of a conditional action.
  **Symptom:** Suggestions assert skills the user may not possess; on a resume-facing product this reads as the system fabricating credentials, and reviewers flag it as misleading.
  **Recovery:** Phrase every template conditionally — "If you have used {name}, mention it where ..." — so the text proposes an action the user can take rather than claiming a fact the generator cannot verify.

- **Mistake:** Emitting one suggestion per missing term with no ordering and no cap.
  **Symptom:** A job with dozens of missing terms produces a wall of advice, the most important gaps are buried, and two runs over the same input return the list in different orders.
  **Recovery:** Sort the terms by descending importance with a stable sort and truncate to a configured maximum, so the output is short, prioritized, and reproducible.

- **Mistake:** Crashing or skipping a term when it has no entry in the skill lexicon.
  **Symptom:** Free-text terms pulled straight from the job description raise a lookup error or silently vanish from the suggestions, so some real gaps are never surfaced.
  **Recovery:** Provide a generic fallback template and use the term's own text as the name when no lexicon entry exists, so every missing term yields advice.

## External reading

- [Python `str.format()` and the Format String Syntax](https://docs.python.org/3/library/string.html#format-string-syntax)
- [Python sorting techniques — sort stability and key functions](https://docs.python.org/3/howto/sorting.html)
- [Python `dataclasses` — frozen value objects](https://docs.python.org/3/library/dataclasses.html)
- [Python `typing.Protocol` — structural subtyping](https://docs.python.org/3/library/typing.html#typing.Protocol)
