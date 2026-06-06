# Pre-commit hooks and the hook framework

## Introduction

This document explains how a project runs a set of fast, automated checks against
your changes at the moment you commit them, so that formatting problems, leaked
secrets, and broken files are caught on your own machine before they ever reach
shared history. The mechanism is a Git hook (a script Git runs automatically at a
defined point in its workflow, such as immediately before a commit is recorded) managed
by the pre-commit framework (a tool that installs, configures, and runs a curated
list of such checks from one configuration file). This document also walks
through each individual hook the project runs.

**Learning outcomes** — after reading this document you will be able to:

- Explain what a Git hook is and what the pre-commit framework adds on top of raw hooks.
- Describe the role of each hook the project runs: file-hygiene checks, secret scanning, the Python formatter and linter, and the cross-language formatter.
- Read the hook configuration and identify which files each hook applies to and which version it is pinned to.
- Recognise the common pre-commit mistakes and recover from them.

Prerequisites: this document builds on
[The root package.json and shared tsconfig.base.json](01-foundations-05-root-package-and-tsconfig.md)
and [uv, a fast Python package manager](01-foundations-03-uv-python-package-manager.md), which
introduce the formatter and linter tools the hooks invoke, and
[EditorConfig and consistent editor settings](01-foundations-06-editorconfig.md), which covers the
editor-level whitespace rules these hooks also enforce at commit time.

## Problem it solves

A team wants every change to meet a baseline before it is shared: code formatted
consistently, no secret keys committed, no leftover merge-conflict markers, and
no oversized binary files. The concrete problem is _when_ and _how_ to enforce
that baseline. Checking only in the shared automated build is late — the bad
commit already exists in history — and checking by asking people to remember to
run tools by hand is unreliable.

The common prior approach was a written checklist ("run the formatter, scan for
secrets, strip trailing whitespace before you commit") that each contributor was
trusted to follow. That approach fails predictably:

- A contributor forgets a step, and unformatted code or a stray secret lands in a commit, where removing it cleanly is far harder than preventing it.
- Each person runs slightly different tool versions, so one person's "formatted" is another person's diff, and the formatting oscillates back and forth.
- There is no single, version-controlled definition of which checks run, so the checklist drifts and new contributors do not know what is expected.

The pre-commit framework solves this by turning the checklist into a committed
configuration file and running the checks automatically at commit time, with each
check pinned to an exact version so everyone runs the same thing.

## Mental model

Think of pre-commit as a quality-control gate on the conveyor belt right before
your work is boxed up: each item passes a short line of inspectors, and if any
inspector rejects it, the box does not get sealed until you fix what they flagged.
The inspectors are listed on a posted manifest, in order, so everyone's work
passes the same line.

When you make a commit in a repository with pre-commit installed, the steps are:

1. You run the commit command; Git pauses before recording the commit and hands the staged changes to pre-commit.
2. Pre-commit runs each configured hook in order, giving each one only the staged files that match the file types it is scoped to.
3. A hook that only reports a problem (such as secret scanning) fails the commit if it finds one; a hook that fixes files (such as a formatter) rewrites them in place.
4. If every hook passes and none modified a file, the commit is recorded.
5. If any hook failed or modified a file, the commit is aborted; you review or re-stage the changes and commit again.

That gate in steps 3 through 5 is the whole idea: problems are caught and often
fixed at the moment of committing, on your machine, rather than after the change
is shared.

## How it works

A Git hook is a script that the version-control tool runs automatically at a
specific moment in its workflow. The pre-commit moment fires immediately before a commit
is recorded, which makes it the natural place to validate staged changes. Writing
and maintaining those scripts by hand is awkward, so the pre-commit framework
manages them for you: you list the checks you want in one configuration file, the
framework installs the actual hook script into the repository, and from then on
it runs your listed checks at commit time.

Each check is called a hook and is declared by referencing the repository that
publishes it, the exact version (a pinned tag, never a floating reference) to use,
and which hook identifiers from that repository to run. The framework fetches and
caches each hook's tool at the pinned version, so every contributor runs an
identical version — the same discipline a version pin brings to the package
manager, applied to the checks. Pinning matters especially for formatters: if two
people run different formatter versions, they reformat each other's code and the
result oscillates.

Hooks fall into two behavioral kinds, and the distinction matters:

- A **reporting** hook only inspects and reports. If it finds a problem it fails the commit but changes nothing; you fix the problem yourself. Secret scanning is the canonical example, because automatically "fixing" a leaked secret is not meaningful.
- A **fixing** hook rewrites files to satisfy its rule — stripping trailing whitespace, reformatting code. When it changes a file the commit is aborted so you can review and re-stage the now-modified files, then commit again.

Hooks are also **scoped** by file type, so a Python formatter runs only on Python
files and a web formatter runs only on the file types it understands, and they
run **in a defined order**, so cheap file-hygiene checks normalize inputs before
later hooks inspect them. Because the configuration lives in version control,
running pre-commit locally and re-running the same checks in the shared automated
build use one identical definition — a contributor who skipped the local install
is still caught by the build.

## MatchLayer Phase 1 usage

MatchLayer declares its hooks in `.pre-commit-config.yaml` at the repository root.
The file requires a minimum framework version and installs the pre-commit hook
type — the script Git runs automatically right before each commit is recorded:

Source: `.pre-commit-config.yaml`

```yaml
minimum_pre_commit_version: "4.0.0"

default_install_hook_types:
  - pre-commit
```

The hooks run in four ordered groups. First, the standard file-hygiene hooks from
the pre-commit project normalize inputs — stripping trailing whitespace (while
preserving Markdown's two-space line breaks), ensuring a final newline, blocking
leftover merge-conflict markers, validating YAML and JavaScript Object
Notation (JSON), and rejecting files larger than five megabytes:

Source: `.pre-commit-config.yaml`

```yaml
- repo: https://github.com/pre-commit/pre-commit-hooks
  rev: v6.0.0
  hooks:
    - id: trailing-whitespace
      args: [--markdown-linebreak-ext=md]
    - id: end-of-file-fixer
    - id: check-merge-conflict
    - id: check-yaml
      exclude: ^pnpm-lock\.yaml$
    - id: check-json
    - id: check-added-large-files
      args: [--maxkb=5120]
```

Second, gitleaks scans the staged content for secrets. It is a reporting hook —
it fails the commit if it detects a secret — and is pinned to an exact tag:

Source: `.pre-commit-config.yaml`

```yaml
- repo: https://github.com/gitleaks/gitleaks
  rev: v8.30.1
  hooks:
    - id: gitleaks-system
      pass_filenames: false
```

Third, Ruff (a fast Python formatter and linter) runs on Python files only,
formatting first and then applying lint fixes, pinned to match the version locked
for the back-end so local and build runs agree byte-for-byte:

Source: `.pre-commit-config.yaml`

```yaml
- repo: https://github.com/astral-sh/ruff-pre-commit
  rev: v0.15.14
  hooks:
    - id: ruff-format
      types_or: [python, pyi]
    - id: ruff-check
      args: [--fix]
      types_or: [python, pyi]
```

Fourth, Prettier (a formatter for web file types) runs on JavaScript, JSX,
TypeScript, TSX, JSON, Markdown, and YAML files,
excluding generated and lockfile content:

Source: `.pre-commit-config.yaml`

```yaml
- repo: https://github.com/rbubley/mirrors-prettier
  rev: v3.8.3
  hooks:
    - id: prettier
      types_or:
        - javascript
        - jsx
        - ts
        - tsx
        - json
        - markdown
        - yaml
```

The secret-scanning hook implements the project's security baseline that a
secret-scan runs on every commit, and the shared automated build re-runs the full
hook set so a contributor who skipped installing the hooks locally is still
caught.

## Common pitfalls

- **Mistake:** Cloning the repository and committing without running the framework's install step, so no hooks are active locally.
  **Symptom:** Commits succeed locally with unformatted code or other issues, and the problems surface only later in the shared automated build that re-runs the hooks.
  **Recovery:** Run the framework's install command once after cloning so the hooks are wired into the repository, then commit again.

- **Mistake:** Bypassing the hooks with the version-control tool's "no verify" option to force a commit through a failing check.
  **Symptom:** The unformatted code or the very issue the hook flagged — including, in the worst case, a leaked secret — lands in shared history, and the automated build later rejects it anyway.
  **Recovery:** Do not bypass the gate; fix what the hook reported (or re-stage the files a fixing hook rewrote) and commit normally.

- **Mistake:** Pinning a formatter hook to a different version than the one the project's package manager installs for the same formatter.
  **Symptom:** The hook and the directly invoked tool disagree on formatting, so files flip back and forth between the two versions and the commit/build loop never settles.
  **Recovery:** Keep the hook's pinned version identical to the version pinned elsewhere for that tool, and bump both together in one reviewed change.

- **Mistake:** Being surprised when a fixing hook reformats files and the commit is aborted, then assuming the commit failed outright.
  **Symptom:** The commit does not complete and the working tree now shows modifications the contributor did not make by hand.
  **Recovery:** Review the hook's automatic changes, stage them, and commit again — an aborted commit after a fixing hook means "I fixed it for you, please re-stage", not "something broke".

## External reading

- [pre-commit — official site and configuration reference](https://pre-commit.com/)
- [Git: customizing Git with hooks](https://git-scm.com/book/en/v2/Customizing-Git-Git-Hooks)
- [Ruff: using Ruff with pre-commit](https://docs.astral.sh/ruff/integrations/)
- [Gitleaks — secret scanning](https://github.com/gitleaks/gitleaks)
