# Pydantic and typed settings

## Introduction

This document explains how a Python application can describe the shape of its
data with ordinary type hints and then have those hints enforced at runtime,
and how the same idea is used to load and validate configuration. The tool is
Pydantic, a Python library that turns a class of typed fields into a validator:
you declare what each field should be, and Pydantic checks incoming values
against that declaration and converts them when it safely can. A companion
library, `pydantic-settings`, applies the same validation to configuration read
from the environment (the key-value variables the operating system hands a
process) and from a local `.env` file (a plain text file of those same
key-value pairs used during development). This belongs in the Backend track
because every other backend component reads its configuration through one
validated settings object.

**Learning outcomes** — after reading this document you will be able to:

- Explain what a Pydantic model is and how declaring a field's type causes that field to be validated at runtime.
- Describe the difference between a field validator and a whole-model validator and when each runs.
- Explain how typed settings load configuration from environment variables and a `.env` file and fail fast on bad input.
- Recognise the common mistakes around typed configuration and recover from them.

Prerequisites: No prerequisites.

## Problem it solves

A program that reads configuration from the environment receives everything as
plain strings, and data arriving from outside the program (a request body, a
configuration file) is untrusted until something checks it. The naive approach
is to read each value where it is needed and convert it by hand — call the
environment lookup, wrap it in an integer conversion, hope the string was
actually a number. That approach has concrete problems.

The common prior approach — scattered manual reads and ad-hoc conversions — has
real costs:

- A malformed value (a non-numeric string where a number is expected, a missing required variable) is often discovered late, deep inside a request, instead of at startup.
- The same variable gets read and converted in several places, so the conversion rules drift apart and one site disagrees with another.
- There is no single place that documents what configuration the program needs, so a new deployment misses a required value and fails in a confusing way.

Pydantic solves the data half by making the type declaration itself the
validation rule: the model rejects or coerces values according to the declared
types in one place. `pydantic-settings` solves the configuration half by loading
every value through one validated model at startup, so a missing or malformed
value raises an error before the application accepts any traffic.

## Mental model

Think of a Pydantic model as a customs checkpoint at a border. Each field is an
inspector with a rule: "this lane is for integers", "this lane is for valid
URLs". Data that matches passes through, possibly with a small, safe conversion
(a numeric string stamped into an actual number). Data that violates the rule is
turned back at the border with a clear note saying which field failed and why —
it never gets into the country (your program) in a bad state.

When a settings object is constructed, the checkpoint runs in this order:

1. Each declared field is matched against an environment variable (here, with a fixed name prefix) or a line in the `.env` file.
2. Each value is validated and converted to its declared type; a missing required field or a malformed value is recorded as an error.
3. Per-field validators run for fields that need a custom rule beyond the type.
4. A whole-model validator runs last, checking rules that span more than one field.
5. If any check failed, construction raises a single validation error listing every problem; otherwise you hold a fully typed, trustworthy object.

Because this runs at construction time, the program either gets a valid
configuration object or stops immediately — there is no half-valid middle state.

## How it works

A validation model is a class whose attributes are declared with type hints. The
library reads those hints and builds, for each field, a small validator that
accepts a matching value, coerces a near-miss when conversion is unambiguous and
safe (the string `"5"` into the integer `5`), and rejects anything else. When you
construct the model from a dictionary of raw values, every field is checked at
once; if any field fails, the library raises one error that aggregates all the
failures rather than stopping at the first. The result is an object whose
attributes are guaranteed to hold values of the declared types, so the rest of
the program can use them without re-checking.

Two kinds of custom rule extend the type-level checks. A field validator is a
function attached to one field that runs after the type check and can enforce an
extra constraint (a minimum length, an allowed set of values) or transform the
value (split a comma-separated string into a list). A model validator runs after
all fields are populated and can enforce a rule that involves several fields at
once (two numbers that must sum to a fixed total). Field validators see one
value; model validators see the whole object.

Loading configuration reuses the same machinery. A settings model declares its
fields the same way, but the source of values is the process environment and an
optional `.env` file rather than a hand-built dictionary. Each field name maps to
an environment variable, often with a shared prefix so the application's
variables are namespaced away from unrelated ones. Because construction validates
everything up front, a deployment that omits a required variable or supplies a
malformed one fails at startup with a precise message, instead of surfacing a
confusing error later under load. A secret value can be wrapped in a dedicated
secret type so it is kept out of the object's printed representation and out of
accidental log lines.

## MatchLayer Phase 1 usage

In MatchLayer the configuration model is the class `Settings` in
`apps/api/src/matchlayer_api/config.py`. It is configured to read variables with
a fixed prefix from the environment and from the repo-root `.env` file:

Source: `apps/api/src/matchlayer_api/config.py`

```python
    model_config = SettingsConfigDict(
        env_prefix="MATCHLAYER_",
```

The same file shows both kinds of custom rule. A field validator rejects a
signing secret shorter than the required length, failing fast at startup:

Source: `apps/api/src/matchlayer_api/config.py`

```python
    @field_validator("jwt_secret")
    @classmethod
    def _jwt_secret_min_length(cls, v: SecretStr) -> SecretStr:
        """Reject secrets shorter than 32 bytes UTF-8 at startup."""
        byte_len = len(v.get_secret_value().encode("utf-8"))
        if byte_len < 32:
```

A whole-model validator runs after every field is populated and checks a rule
that spans two fields — that two score weights sum to one:

Source: `apps/api/src/matchlayer_api/config.py`

```python
    @model_validator(mode="after")
    def _score_weights_sum_to_one(self) -> Settings:
```

Every other backend module reads configuration through this one object via a
cached accessor, so there is a single validated source of truth and no scattered
environment reads. Fields typed as a database connection string or a list of
allowed origins are validated as those shapes, and the signing secret is wrapped
in a secret type so it never appears in a printed object or a log line.

## Common pitfalls

- **Mistake:** Reading configuration directly from the environment in scattered places instead of through the one settings object.
  **Symptom:** The same variable is interpreted differently in two modules, and a malformed value slips past because only one site bothered to validate it.
  **Recovery:** Route every configuration read through the single settings model, and delete the ad-hoc environment lookups so there is one validation path.

- **Mistake:** Expecting a list or structured value to arrive ready-made from a single environment variable, which can only hold a string.
  **Symptom:** The field fails validation, or arrives as one long string instead of the list you wanted.
  **Recovery:** Add a field validator that parses the string into the structured value (for example, splitting on commas), so the model owns the conversion in one place.

- **Mistake:** Putting a secret in a plain string field and then logging or printing the configuration object.
  **Symptom:** The secret value appears in a log line or console output, which violates the rule that secrets must never be logged.
  **Recovery:** Declare the field with the library's secret type so its value is masked in the object's representation, and read the real value only at the point of use.

- **Mistake:** Treating a missing required variable as something to handle later inside a request.
  **Symptom:** The application starts, accepts a request, and then fails deep in a handler with an unclear error about a missing value.
  **Recovery:** Declare the field as required with no default so construction fails at startup with a precise message, turning a late runtime failure into an immediate, obvious one.

## External reading

- [Pydantic: models and validation](https://docs.pydantic.dev/latest/concepts/models/)
- [Pydantic: validators (field and model)](https://docs.pydantic.dev/latest/concepts/validators/)
- [Pydantic Settings: configuration management](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)
- [Python documentation: type hints (typing)](https://docs.python.org/3/library/typing.html)
