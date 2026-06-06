# Environment files and env-drift detection

## Introduction

This document explains how a project supplies its per-environment settings —
database addresses, feature switches, secret keys — without writing them into the
source code, and how an automated check keeps the committed template of those
settings honest. The mechanism is the environment variable (a named value the
operating system hands to a running program, set outside the program's own code),
and the convention for managing many of them is a pair of files: a private,
uncommitted `.env` file that holds the real values for one machine, and a
committed `.env.example` file that lists every variable the project expects with
placeholder values. An env-drift check is a small script that compares the
committed template against what the code actually reads, and fails when the two
disagree.

**Learning outcomes** — after reading this document you will be able to:

- Explain what an environment variable is and why secrets and per-environment settings live outside the source code.
- Describe the roles of the private `.env` file and the committed `.env.example` template, and why only one of them is committed.
- Explain what "drift" between the template and the code means and how an automated check detects it in both directions.
- Recognise the common environment-file mistakes and recover from them.

Prerequisites: this document builds on
[Monorepo layout and the apps-vs-packages split](01-foundations-01-monorepo-layout.md), which
introduces the single-repository layout where these root-level files and the
checking script live. No other prerequisites.

## Problem it solves

A program needs configuration that changes between environments: the address of
the database on a developer's laptop is not the address used in production, and
some values — passwords, signing keys, third-party access keys — are secrets that
must never be written into the code at all. The concrete problem is keeping those
values out of the source code while still making it obvious which values a fresh
checkout needs to supply before the program will run.

The common prior approach was to hard-code settings as constants in the source,
or to scatter direct reads of named operating-system values throughout the code
with no central list of what was needed. That approach has three weaknesses:

- A secret written into the source is committed to version control forever, where anyone with repository access — or anyone who later obtains a leaked copy — can read it.
- Per-environment values baked into the code force a code change (and a rebuild) every time an environment differs, instead of being supplied from outside.
- With no single list of required values, a new contributor cannot tell which settings to provide, and discovers a missing one only as a confusing failure at startup.

The `.env` / `.env.example` pair solves the first two: real values live in a
private file that is never committed, and the program reads them from the
environment at startup. A committed template solves the third by listing every
expected variable. The remaining risk — that the template and the code drift
apart over time — is what the env-drift check addresses.

## Mental model

Think of the committed template as a blank form pinned to the wall that lists
every field the project needs filled in, and the private file as your own filled-
in copy of that form that you keep in your drawer and never post publicly. The
blank form tells every newcomer exactly what to provide; your filled-in copy
holds your actual answers. The env-drift check is the clerk who periodically
compares the blank form against what the office actually uses, and raises a flag
whenever the form lists a field nobody uses anymore, or the office relies on a
field the form forgot to list.

To see how the drift check reasons, walk through what it compares:

1. It reads the committed template and collects the set of variable names the template declares.
2. It scans the source code and collects the set of variable names the code actually reads.
3. It computes the names that appear in the code but are missing from the template — variables the program needs that a newcomer was never told to set.
4. It computes the names that appear in the template but are unused by the code — stale entries the template still advertises.
5. If either set is non-empty, it reports the specific names and fails; if both are empty, the template and the code agree and the check passes.

That two-direction comparison in steps 3 and 4 is the whole idea. Drift in either
direction is a defect: a missing variable breaks a fresh setup, and a stale
variable wastes a newcomer's time setting something the code ignores.

## How it works

An environment variable is a named string value that the operating system makes
available to a running program. Because it is supplied from outside the program,
the same compiled code can behave differently in different environments by being
started with different values — and a secret value never has to appear in the
source at all.

Managing many such variables by exporting them one at a time in a shell is
error-prone, so a common convention groups them into a file. That file holds one
`NAME=value` assignment per line, with comment lines for documentation, and a
loader reads it at startup and makes each entry available to the program as an
environment variable. The convention splits this into two files with opposite
visibility:

- A **private file** holds the real values for one machine — including secrets — and is deliberately excluded from version control so it is never committed. Each contributor and each deployed environment has its own copy.
- A **committed template** lists every variable the project expects, each with a placeholder or safe default value instead of a real secret. Because it is committed, it travels with the repository and tells anyone setting up a fresh checkout exactly which variables to provide. A new contributor copies the template to the private filename and fills in real values.

The weak point of this convention is that the template is maintained by hand. A
developer who adds a new variable to the code can forget to add it to the
template, and a developer who removes a variable from the code can forget to
remove it from the template. Over time the template and the code drift apart.

An env-drift check closes that gap with automation. It is a small program,
usually run as part of the project's automated checks, that builds two sets of
variable names — the ones the template declares and the ones the code references
— and compares them. It typically finds the referenced set by scanning the source
for the patterns through which the code reads configuration: direct reads of a
named environment value, and the fields of any settings object that maps to
environment variables by a known naming rule. A name in the code but not the
template is reported as missing; a name in the template but not the code is
reported as stale. Reporting both directions, with the exact offending names,
turns a class of silent setup failures into an immediate, specific error.

## MatchLayer Phase 1 usage

In MatchLayer the committed template is `.env.example` at the repository root. A
contributor copies it to a private `.env` (which is excluded from version
control) and fills in real values. The template documents how to do exactly that
in its own header:

Source: `.env.example`

```text
# MatchLayer — example environment file
#
# Copy this file to `.env` at the repo root:
#   cp .env.example .env
```

Each variable in the template is documented with a comment and given a
development-safe default. For example, the back-end's runtime-environment switch
and log level are declared like this:

Source: `.env.example`

```text
MATCHLAYER_ENVIRONMENT=development

# Log level for structlog. One of: debug | info | warning | error
MATCHLAYER_LOG_LEVEL=info
```

The env-drift check is `tools/check_env_drift.py`, a script written to depend on
nothing outside the language's standard library so it can run before any project
dependencies are installed. It compares the template's declared variables against
the variables the code actually consumes, and reports both directions of
disagreement. The two failure categories are documented at the top of the script:

Source: `tools/check_env_drift.py`

```python
* **Missing** — referenced in code but absent from ``.env.example``. The
  app will boot with an unset variable; the operator never knew to set it.
* **Stale** — declared in ``.env.example`` but unused. The committed
  contract claims a var the code no longer consumes; operators waste time
  setting it. (Or: the var was added in anticipation of a feature that
  hasn't shipped yet — surface the gap so the team can decide.)
```

The script discovers the variables the code reads in three ways: direct reads of
named operating-system values in the Python back end, the fields of the back
end's settings object (which map to environment variables by a fixed naming
rule), and direct reads of named values in the web front end. It then compares
those against the names parsed out of the template. A clean run prints a one-line
confirmation; any drift exits with an error that names each offending variable,
so a forgotten template update fails the automated checks rather than reaching a
teammate's first setup.

## Common pitfalls

- **Mistake:** Committing the private `.env` file with real values, or pasting a real secret into the committed `.env.example` template.
  **Symptom:** A secret ends up in version control, where it lives in the history permanently and is exposed to anyone with repository access.
  **Recovery:** Keep `.env` excluded from version control and put only placeholder or development-safe values in `.env.example`; if a real secret was committed, rotate it (replace it with a new one) and remove the value from the template.

- **Mistake:** Adding a new variable that the code reads without adding it to the committed template.
  **Symptom:** The env-drift check reports the variable as missing, and a fresh checkout boots with the value unset because the newcomer was never told to provide it.
  **Recovery:** Add the variable to `.env.example` with a placeholder or safe default in the same change that adds the code that reads it.

- **Mistake:** Removing a variable from the code but leaving its entry in the committed template.
  **Symptom:** The env-drift check reports the variable as stale, and newcomers waste time setting a value the code no longer reads.
  **Recovery:** Remove the now-unused entry from `.env.example` in the same change that removed the code, or restore the code reference if the variable is still needed.

- **Mistake:** Treating the template as documentation only and never running the drift check.
  **Symptom:** The template silently falls out of sync with the code, accumulating both missing and stale variables that nobody notices until a setup fails.
  **Recovery:** Run the drift check as part of the project's automated checks so any disagreement between the template and the code fails fast with the offending names.

## External reading

- [Python: os.environ and reading environment variables](https://docs.python.org/3/library/os.html#os.environ)
- [Python: the standard-library modules used by a stdlib-only script](https://docs.python.org/3/library/index.html)
- [Next.js: environment variables](https://nextjs.org/docs/app/guides/environment-variables)
