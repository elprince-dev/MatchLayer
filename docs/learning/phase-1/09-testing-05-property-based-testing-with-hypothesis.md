# Property-based testing with Hypothesis

## Introduction

This document explains a style of automated testing in which you describe a
rule that your code must always obey and let a tool invent hundreds of inputs
to try to break that rule. The rule is called a **property** — a general
assertion that should hold for every valid input, rather than a claim about one
specific input you picked by hand. The tool used here is **Hypothesis**, a
Python library that generates many varied inputs from a description you write,
feeds each one to your test, and — when it finds an input that breaks the
property — automatically searches for the smallest, simplest version of that
failing input so the bug is easy to read. This belongs in the Testing and
quality track because property-based testing sits alongside hand-written
example tests as one of the test layers used across Phase 1.

**Learning outcomes** — after reading this document you will be able to:

- Explain what a property is and how it differs from a single hand-written example.
- Describe how a generator (Hypothesis calls it a strategy) produces many inputs and how shrinking reduces a failure to its simplest form.
- Identify which kinds of assertions make good properties and which do not.
- Recognise the common mistakes when writing property-based tests and recover from them.

Prerequisites: No prerequisites. Every term used here — including property,
generator, strategy, and shrinking — is defined inline as it first appears.

## Problem it solves

The concrete problem is that hand-written tests only ever check the inputs the
author thought of. A test author writes a function, imagines a handful of cases
("an empty list", "a list with one item", "a typical list"), writes one
assertion per case, and moves on. The bugs that survive are the ones hiding in
the cases nobody imagined: the surprising character, the boundary value, the
unusual combination of arguments. The test suite is green, yet the untested
corner is exactly where the defect lives.

The common prior approach is the example-based test: a test that pins down one
specific input and asserts one specific expected output. An example-based test
is valuable and easy to read, but it has two structural weaknesses. First, its
coverage is only as good as the author's imagination, so blind spots in the
author's thinking become blind spots in the suite. Second, each new case is
another block of code to write and maintain, so authors naturally stop after a
few, leaving the vast majority of the input space untouched.

Property-based testing attacks both weaknesses at once. Instead of enumerating
inputs, the author states a rule that must hold for _all_ valid inputs and
describes the shape of those inputs once. The tool then manufactures a large,
varied sample of inputs and checks the rule against every one. Coverage no
longer depends on the author imagining the awkward case, because the generator
keeps trying awkward cases on its own — and when it finds one that breaks the
rule, it reports a minimal reproduction instead of a noisy random blob.

## Mental model

Think of the difference between a teacher who grades three pre-chosen homework
problems and an examiner who can pose an endless stream of fresh problems and
keep asking until the student slips. The example-based test is the first
teacher: it checks the three answers it already knows. The property-based test
is the examiner: you tell it the _kind_ of problem to pose and the _rule_ a
correct answer must always satisfy, and it keeps generating new problems trying
to catch a violation.

A useful second image for shrinking: when the examiner finally finds a problem
the student gets wrong, it does not hand you the messy 400-digit number that
triggered the failure. It works backward to the simplest possible version of
the same failing problem — often a tiny value like `0` or a single character —
so the underlying mistake is obvious at a glance.

Walked through step by step, one property-based test run looks like this:

1. You write a strategy — a description of what a valid input looks like (for example, "a piece of text between 12 and 64 characters long").
2. You write a property — a function that takes one generated input and asserts a rule that must hold for every such input.
3. The tool draws many concrete inputs from the strategy and calls your property function once per input.
4. If every input satisfies the assertion, the test passes; the rule held across the whole sample.
5. If some input fails the assertion, the tool shrinks it — repeatedly simplifying the input while the failure persists — and reports the smallest failing example it found.

## How it works

A property-based test has two parts: a description of the input space and an
assertion that must hold across that whole space. The input-space description
is called a strategy (the general term is a generator): a recipe the framework
uses to produce concrete values. A strategy can describe simple values such as
integers or text, and strategies compose, so you can build a strategy for a
list of records out of strategies for each field. Crucially, you describe the
_shape_ of valid inputs once rather than listing the inputs themselves.

The assertion is the property: a function, marked so the framework knows to
drive it, that receives one generated value and checks a rule. A good property
is a statement that is true for every valid input, not a comparison against one
precomputed answer. Common property shapes include round-trips ("decoding an
encoded value returns the original"), invariants ("the result is always within
a fixed range"), idempotence ("applying the operation twice gives the same
result as applying it once"), and equivalence with a simpler reference
implementation. When the framework runs the test, it draws many values from the
strategy — typically dozens to hundreds per run — and calls the property
function on each, failing the test if any single value breaks the assertion.

The feature that makes failures usable is shrinking. When the framework finds
an input that violates the property, it does not stop at that often-large,
random-looking value. It systematically tries simpler inputs — smaller numbers,
shorter text, fewer list elements — keeping any simpler input that still
reproduces the failure, until it cannot simplify further. The result reported to
the author is a minimal counterexample, which usually points straight at the
root cause. Many frameworks also record failing examples and replay them on the
next run, so a discovered bug stays covered after it is fixed.

Two limits are worth naming up front. First, a property only tests what it
asserts: a rule that is too weak ("the function returns without raising") can
pass while the output is wrong, so the assertion has to capture something
meaningful. Second, generated inputs must be constrained to the genuinely valid
input space; if the strategy produces values the code was never meant to accept,
the test reports failures that are not real defects. The skill is writing
strategies that are wide enough to surprise you but narrow enough to stay valid.

## MatchLayer Phase 1 usage

In MatchLayer the property-based tests live under `apps/api/tests/property/`,
one file per property, next to the hand-written example tests. The shared
configuration for those tests is set in the test suite's `conftest.py` — a
pytest file (pytest is the test runner Phase 1 uses) whose contents apply to
every test in the directory tree below it. The file
`apps/api/tests/conftest.py` registers and loads a Hypothesis **settings
profile**, a named bundle of run options. Here the profile disables the
per-example time limit (`deadline=None`) because hashing a password is
deliberately slow, and raises the number of generated inputs per run to 200 so
each property is exercised meaningfully:

Source: `apps/api/tests/conftest.py`

```python
hypothesis_settings.register_profile(
    "auth",
    deadline=None,
    max_examples=200,
)
hypothesis_settings.load_profile("auth")
```

A representative property test is the password hash round-trip in
`apps/api/tests/property/test_password_roundtrip.py`. It first defines a
strategy, `_valid_password`, describing the input space — unicode text between
12 and 64 characters — and then states the property: for any such password,
hashing it and then verifying that hash against the same password must report a
match. The `@given` line wires the strategy to the test, and the `@settings`
line tunes this property's run. The function body holds no hand-picked password
at all; Hypothesis supplies them:

Source: `apps/api/tests/property/test_password_roundtrip.py`

```python
_valid_password = st.text(
    alphabet=st.characters(categories=("L", "M", "N", "P", "S", "Z")),
    min_size=12,
    max_size=64,
)
@settings(deadline=None, max_examples=50)
@given(password=_valid_password)
def test_hash_verify_roundtrip(password: str) -> None:
    """For any p with len(p) >= 12, verify(hash(p), p) is True."""
    hashed = hash_password(password)
    matches, _ = verify_password(hashed, password)
    assert matches, f"verify_password failed for password of length {len(password)}"
```

This is a round-trip property: it asserts that `verify` undoes what `hash`
produced, across the whole generated space of valid passwords, rather than for
one example string. Other property files in the same directory follow the same
shape for different rules — for instance, a scoring file asserts the match score
is always an integer in the inclusive range 0 to 100 for any résumé and
job-description text (an invariant property). The compliance validator's own
test suite under `tools/tests/` uses the same Hypothesis library to property-test
the documentation rules, so the technique appears on both the application side
and the tooling side of the repository.

## Common pitfalls

- **Mistake:** Writing a property whose assertion is too weak — for example, only checking that the function returns without raising an exception.
  **Symptom:** The property-based test stays green even when the output values are wrong, giving false confidence; bugs are caught later by an example test or in production instead.
  **Recovery:** Strengthen the assertion to capture a meaningful rule the output must satisfy — a round-trip, a bound, an invariant, or agreement with a simpler reference — so a wrong value actually fails the test.

- **Mistake:** Writing a strategy that generates inputs outside the genuinely valid input space the code is designed to accept.
  **Symptom:** The test reports failing examples that are not real defects, because the code legitimately rejects inputs it was never meant to handle, and the team starts ignoring the failures.
  **Recovery:** Constrain the strategy to the valid domain (set size or value bounds, filter out disallowed values), so every generated input is one the code is actually contracted to handle.

- **Mistake:** Restating the implementation inside the property instead of asserting an independent rule — computing the expected answer with the same logic the code under test uses.
  **Symptom:** The property passes no matter what, because any bug in the implementation is mirrored in the test's own expected-value calculation, so the two always agree.
  **Recovery:** Assert a rule that is independent of the implementation — a structural invariant, a round-trip, or a comparison to a deliberately simpler reference implementation — rather than recomputing the result the same way.

- **Mistake:** Leaving a tight per-example time limit in place for a property that drives deliberately slow work, such as a strong password hash.
  **Symptom:** The test fails intermittently with a deadline-exceeded error on slower machines even though the rule itself holds, making the suite flaky.
  **Recovery:** Raise or disable the deadline for that property (for example, via a settings profile) so the time limit reflects the real cost of the operation being exercised.

## External reading

- [Hypothesis documentation: quick start guide](https://hypothesis.readthedocs.io/en/latest/quickstart.html)
- [Hypothesis documentation: what you can generate and how (strategies)](https://hypothesis.readthedocs.io/en/latest/data.html)
- [Hypothesis documentation: settings and profiles](https://hypothesis.readthedocs.io/en/latest/settings.html)
- [pytest documentation](https://docs.pytest.org/en/stable/)
