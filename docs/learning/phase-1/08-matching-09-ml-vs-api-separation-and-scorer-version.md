# Separating model code from API code and the Scorer_Version identifier

## Introduction

This document explains a structural rule that runs through the whole scoring
feature: the code that _builds_ a model and its data lives in a different
top-level area from the code that _serves_ requests, and the two areas are
allowed to depend on each other in only one direction. It also explains the
Scorer*Version, a short identifier (a string that combines an algorithm version
with a data version) that every score is stamped with so a stored result can be
reproduced and audited later. The deterministic scorer that produces those
scores uses no Large Language Model (LLM); here the focus is on \_where its code lives*
and _how its outputs stay reproducible_, not on the scoring math itself.

**Learning outcomes** — after reading this document you will be able to:

- Explain why model-building code and request-serving code are kept in separate top-level areas that depend in one direction only.
- Describe what a thin adapter (a small marshalling layer that forwards calls and holds no business logic of its own) does at that boundary, and why it carries no scoring arithmetic.
- Define a reproducibility version identifier and explain how composing an algorithm version with a data-artifact version makes a persisted result auditable.
- Locate, in this project, the serving-side scorer modules, the thin client, and the exact place the Scorer_Version is composed and stamped onto a result.

Prerequisites: this document builds on the apps-versus-packages idea, so read
[Monorepo layout and the apps-vs-packages split](01-foundations-01-monorepo-layout.md) first. The
settings-injection detail also touches configuration, covered in
[Pydantic and pydantic-settings](03-backend-02-pydantic-and-pydantic-settings.md); that one is
related rather than required.

## Problem it solves

A scoring feature is two very different kinds of work wearing one name. One kind
is _deriving the model and its data_: curating a vocabulary, running a build
script, experimenting with weights, pulling in heavy data-science libraries. The
other kind is _serving a score on request_: taking two pieces of text inside a
web request and returning a number quickly and predictably. These two kinds of
work have different dependencies, different change rhythms, and different risk
profiles.

The common prior approach is to put all of it in one place — the request-serving
application imports the training and derivation scripts directly, and the data
artifact is edited by hand wherever it happens to sit. That arrangement creates
several concrete problems:

- The serving container has to ship the training dependencies it never runs at request time, so the image is larger and its security surface wider.
- A change to an experimentation script can accidentally break the request path, because the two share modules and import each other freely.
- When a stored score is questioned months later, there is no record of _which_ version of the algorithm and _which_ version of the data produced it, so the number cannot be reproduced or defended.

Separating the two areas and stamping every result with a composite version
identifier removes all three problems at once: the serving side stays lean, the
dependency direction is one-way and enforceable, and every saved score carries
the exact recipe that produced it.

## Mental model

Think of a restaurant. There is a **test kitchen** where chefs develop recipes:
they experiment, weigh ingredients, taste, and finally write a recipe card. Then
there is the **service line** where cooks plate dishes to order during service:
they follow the finished recipe card exactly, fast, the same way every time. The
test kitchen and the service line are different rooms with different tools. The
service line never reaches back into the test kitchen mid-service; it only reads
the finished recipe card. And every plate that goes out is tagged with the
recipe-card version, so if a diner complains, the kitchen knows precisely which
recipe was on the plate.

Mapping that back: the test kitchen is the model-building area, the recipe card
is the committed data artifact, the service line is the request-serving code,
and the version tag on every plate is the reproducibility identifier.

A scoring request walks the boundary in these steps:

1. A request arrives carrying two texts and needs a score.
2. The serving code calls a thin adapter — a small forwarding layer — rather than reaching into the scoring engine directly.
3. The adapter reads configuration once, hands the engine its finished data artifact and the configured knobs, and forwards the two texts.
4. The engine computes a result and stamps it with the version identifier composed from its algorithm version plus the artifact's data version.
5. The serving code stores the result together with that identifier, so the number can be reproduced later by rebuilding the same algorithm-plus-data combination.

That five-step walk is the whole flow. The rest of this document fills in what
each layer is allowed to do.

## How it works

The pattern has two halves: a one-way code boundary, and a composite version
identifier.

**The one-way boundary.** Two kinds of code are placed in two separate top-level
areas. One area holds model-building and data-derivation code — scripts that
regenerate a data artifact from curated source material, plus the
experimentation that supports them. The other area holds the request-serving
application. The rule is that the serving application may read the _finished
artifacts_ the building area produces, but it must never import the building
area's code at request time. Stated as a dependency arrow: serving depends on
artifacts, never on training code, and training code never depends on serving
code. This is an import boundary — a deliberately enforced rule about which
modules are allowed to import which other modules.

Inside the serving application the same one-way discipline repeats at a smaller
scale. The scoring engine is written to be _framework-free_: it imports only its
own sibling modules and its numerical library, and it never reads application
configuration or web-framework objects. Sitting between the framework world and
that framework-free engine is a **thin adapter** (also called a thin client): a
small module whose only job is marshalling. It reads configuration, constructs
the engine once, injects the configured knobs, and forwards plain inputs in and
plain results out. It performs no computation of its own. Keeping the adapter
thin means the engine stays testable in isolation and the configuration-reading
lives in exactly one place.

**The composite version identifier.** A deterministic scorer produces the same
output for the same input only as long as nothing about it changes. Two things
can change its output: the _algorithm_ (the math and normalization) and the
_data_ (the vocabulary or weights it consults). To make a saved score
reproducible, the system gives each of these its own version string and then
composes them into one identifier — conceptually `algorithm_version` joined with
`data_version`. Every result the engine emits is stamped with this composite
string. Because the identifier is stored next to the score, anyone can later ask
"which algorithm and which data produced this number?" and get an exact answer.
The discipline that keeps it honest is simple: whenever the algorithm changes in
a way that alters scores, bump the algorithm version; whenever the data artifact
changes, bump the data version. A score whose recipe changed silently — same
identifier, different number — is the failure this guards against.

Putting the two halves together: the build area regenerates a versioned data
artifact; the serving area ships a copy of that artifact and loads it at
startup; the thin adapter binds the artifact and configuration to a
framework-free engine; and the engine stamps every result with the composite
version identifier so the stored number is auditable.

## MatchLayer Phase 1 usage

In MatchLayer the split is physical. The model-building and data-derivation code
lives under `ml/`, and the request-serving application lives under `apps/api/`.
The deterministic scorer's framework-free engine sits in
`apps/api/src/matchlayer_api/scoring/scorer.py` (with its collaborators
`apps/api/src/matchlayer_api/scoring/lexicon.py` and siblings), and the only
sanctioned bridge from the framework world into that engine is the thin client
under `apps/api/src/matchlayer_api/ml/`, the module
`apps/api/src/matchlayer_api/ml/scorer_adapter.py`.

The adapter's import lines make the one-way direction concrete: it imports _from_
the scoring engine and from configuration, and nothing imports back into it from
the engine.

Source: `apps/api/src/matchlayer_api/ml/scorer_adapter.py`

```python
from matchlayer_api.config import get_settings
from matchlayer_api.scoring.lexicon import load_lexicon
from matchlayer_api.scoring.scorer import Match_Scorer, ScoreResult
```

The adapter constructs the engine once (cached process-wide), reading the
configured weights and caps and injecting them into the engine's constructor.
This is the one place that touches application settings — the engine itself
never imports configuration.

Source: `apps/api/src/matchlayer_api/ml/scorer_adapter.py`

```python
@lru_cache(maxsize=1)
def get_scorer() -> Match_Scorer:
    settings = get_settings()
    return Match_Scorer(
        load_lexicon(),
        w_similarity=settings.score_weight_similarity,
        w_keyword=settings.score_weight_keyword,
        max_keywords=settings.match_max_keywords,
        max_suggestions=settings.match_max_suggestions,
    )
```

The adapter's `score` entry point holds no arithmetic. It takes two plain
strings, delegates to the cached engine, and returns the engine's result
unchanged — pure marshalling.

Source: `apps/api/src/matchlayer_api/ml/scorer_adapter.py`

```python
def score(resume_text: str, job_description: str) -> ScoreResult:
    return get_scorer().score(resume_text, job_description)
```

The reproducibility identifier is the Scorer_Version. It is composed in
`apps/api/src/matchlayer_api/scoring/lexicon.py` by joining a hand-maintained
algorithm version with the data artifact's content version. The algorithm
version is bumped when the scoring math changes; the data (lexicon) version
travels in from the artifact.

Source: `apps/api/src/matchlayer_api/scoring/lexicon.py`

```python
ALGORITHM_VERSION: Final[str] = "1.0.0"
def scorer_version(lexicon_version: str) -> str:
    return f"{ALGORITHM_VERSION}+lex.{lexicon_version}"
```

The data version comes from the committed artifact. There are two copies of that
artifact, and which is which is the heart of the source-of-truth rule. The
canonical source of truth lives under `ml/` at
`ml/lexicon/skill_lexicon.v1.json`; the copy the Application Programming Interface (API)
ships and loads as package data lives under `apps/api/` at
`apps/api/src/matchlayer_api/scoring/data/skill_lexicon.v1.json`. Both are
regenerated — never hand-edited — by the build pipeline
`ml/pipelines/build_skill_lexicon.py`, which holds the curated data and the
content version it writes into both files.

Source: `ml/pipelines/build_skill_lexicon.py`

```python
LEXICON_VERSION = "1.0.0"
```

Because the API at request time loads only the shipped copy (via package-data
loading) and never imports `ml/pipelines/build_skill_lexicon.py`, the
training-versus-serving boundary holds. A drift check,
`tools/check_lexicon_drift.py`, fails Continuous Integration (CI) when the two
copies diverge, so the shipped artifact can never silently drift from its source
of truth.

Finally, the serving side persists the stamped identifier. The Scoring_Service
in `apps/api/src/matchlayer_api/services/matching.py` calls the adapter and
writes the result's `scorer_version` onto the stored match row, so every saved
score records the exact algorithm-plus-data recipe that produced it.

Source: `apps/api/src/matchlayer_api/services/matching.py`

```python
            scorer_version=result.scorer_version,
```

## Common pitfalls

- **Mistake:** Reaching into application configuration or web-framework objects from inside the framework-free scoring engine, instead of injecting those values through the thin adapter.
  **Symptom:** The scoring engine can no longer be imported or unit-tested on its own; an import cycle appears, or a test of the engine fails because it now needs settings or a running application to import.
  **Recovery:** Move the configuration read back into the adapter under `apps/api/src/matchlayer_api/ml/` and pass the values into the engine's constructor as explicit arguments, restoring the one-way direction (engine imports nothing from the framework world).

- **Mistake:** Hand-editing the shipped package-data copy of the data artifact instead of editing the curated source and regenerating both copies.
  **Symptom:** The drift check `tools/check_lexicon_drift.py` fails in CI reporting the two artifacts differ, or the shipped copy carries changes that are absent from the source of truth.
  **Recovery:** Revert the manual edit, change the curated data inside `ml/pipelines/build_skill_lexicon.py`, run that pipeline to regenerate both `ml/lexicon/skill_lexicon.v1.json` and the API's package-data copy, and commit both files together.

- **Mistake:** Changing the scoring math or the lexicon contents without bumping the corresponding version string.
  **Symptom:** Two runs that produce different scores share an identical Scorer_Version, so a previously stored score can no longer be reproduced from its recorded identifier.
  **Recovery:** Bump `ALGORITHM_VERSION` in `apps/api/src/matchlayer_api/scoring/lexicon.py` when the algorithm changes, or `LEXICON_VERSION` in `ml/pipelines/build_skill_lexicon.py` when the data changes, so the composite Scorer_Version moves whenever the recipe moves.

- **Mistake:** Letting scoring arithmetic creep into the thin adapter because it is a convenient place to "tweak" a result.
  **Symptom:** Logic is split across two layers, the same calculation appears in both the adapter and the engine, and tests of the engine pass while real scores differ because the adapter changed the number afterward.
  **Recovery:** Keep the adapter as pure marshalling — construct, inject, forward — and push any computation back into the framework-free engine in `apps/api/src/matchlayer_api/scoring/scorer.py`, where it is covered by the engine's own tests.

## External reading

- [Python `functools.lru_cache`](https://docs.python.org/3/library/functools.html#functools.lru_cache)
- [Python `importlib.resources` (loading package data)](https://docs.python.org/3/library/importlib.resources.html)
- [scikit-learn: text feature extraction](https://scikit-learn.org/stable/modules/feature_extraction.html)
- [Semantic Versioning specification](https://semver.org/)
