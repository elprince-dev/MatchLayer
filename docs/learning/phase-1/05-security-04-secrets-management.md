# Secrets management and keeping them out of git

## Introduction

This document explains how a project keeps its secrets out of source control and
what catches them when a mistake slips through. A secret is any value that grants
access or proves identity and must stay confidential — a database password, a
token-signing key, or a cloud access key. Source control here is git, the version
system that records every change forever, which is exactly why a secret committed
once is hard to truly erase. The defenses are layered: a discipline around
environment files, a scanner that runs before each commit, and a platform service
that scans what reaches the hosting provider. This topic sits in the Security
track because a single leaked secret can compromise an entire system, and version
history makes leaks unusually durable.

**Learning outcomes** — after reading this document you will be able to:

- Explain why a committed secret is dangerous long after it is deleted, given how version history works. History keeps the old content.
- Describe the role of an environment file and its committed example counterpart. One holds real values locally; the other documents the shape.
- Explain how a pre-commit secret scanner and a hosting-platform scanner form two independent safety nets. Each catches what the other might miss.
- Recognise the common mistakes around secrets and recover from them safely. A leaked secret must be rotated, not merely deleted.

Prerequisites:

- [Pre-commit hooks](01-foundations-09-pre-commit-hooks.md) — introduces the framework that runs checks automatically before each commit, which is where the secret scanner lives.
- [Environment files and drift detection](01-foundations-08-env-files-and-drift-detection.md) — introduces the environment-file pattern and the committed example file this document builds on.

## Problem it solves

An application needs secrets to run — it must connect to its database, sign its
tokens, and reach its cloud storage. Those values have to live somewhere the code
can read them, but they must never live in source control. The concrete problem
is twofold: where do secrets go so the application can use them, and how does a
team stop a secret from being committed by accident?

The naive approach hardcodes secrets directly in the code or a checked-in
configuration file. This fails badly because of how version control works. Git
records the full history of every file; deleting a secret in a later commit does
not remove it from history, where anyone with repository access — or anyone who
ever cloned it — can still read it. A secret pushed to a shared host even briefly
must be treated as compromised. Worse, secrets in code tend to be the same across
every environment, so one leak exposes production as well as a developer's laptop.

The layered solution keeps real secrets in a local file that is never committed,
documents the required variables in a separate committed example file, and adds
automated scanners that refuse to let a secret-shaped value into a commit or
silently catch one if it reaches the hosting platform. No single layer is
trusted alone; together they make an accidental leak unlikely and a deliberate
one obvious.

## Mental model

Think of a building with three independent safeguards against confidential papers
leaving the building. First, staff keep originals in a locked drawer and post only
a blank template on the noticeboard, so the public board shows the shape of a form
but never a filled-in one. Second, a guard at the door checks every bag on the way
out and stops anything that looks like a confidential original. Third, the courier
company runs its own inspection when a package arrives at the depot, as a backup in
case the door guard was distracted. A leak now requires all three to fail at once.

When a developer commits a change, the flow is:

1. Real secret values live only in a local environment file that source control is configured to ignore, so it is never staged.
2. A committed example file lists every variable name with a placeholder value, so a teammate knows what is required without learning any real value.
3. As the commit is created, a secret scanner inspects the staged content and aborts the commit if it finds a value that matches a known secret pattern.
4. If a secret somehow reaches the hosting platform, the platform's own secret scanner inspects the pushed content and raises an alert.
5. A confirmed leak is rotated — the exposed secret is replaced with a new one — because deletion alone leaves the old value in history.

Step 5 is the part newcomers miss: once a real secret has been committed, removing
it is not enough, because the history still holds it.

## How it works

The foundation is separating secret values from the code that uses them. Real
values are placed in an environment file — a simple list of name-and-value pairs
the application reads at startup — and that file is added to the version system's
ignore list so it is never committed. Alongside it sits a committed example file
that lists every required variable name with a harmless placeholder value. The
example documents the contract (what must be set) without disclosing anything;
a new contributor copies it to a real environment file and fills in actual values
locally.

The first automated net is a pre-commit secret scanner. A pre-commit hook is a
check that runs automatically at the moment a commit is created; a secret scanner
hook examines the staged changes for strings that match the shapes of known
secrets — cloud access keys, private-key blocks, high-entropy tokens — and aborts
the commit when it finds one. Because it runs locally before the commit exists, it
stops the secret from ever entering history in the first place. The scanner is
configured as a required hook, not an optional convenience, so it cannot be
quietly skipped.

The second net operates at the hosting platform. A platform secret-scanning
service inspects content pushed to the host and raises an alert (and, for some
providers, can even notify the credential issuer to revoke the key) when it
detects a known secret pattern. This is independent of the local scanner, which
matters because a contributor might not have the local hooks installed, or might
bypass them. Two scanners run by different parties, on different triggers, are far
harder to defeat than one.

A point that trips up many teams is that some test fixtures must legitimately look
like secrets — a signing key of the right length to exercise a validation rule, for
example. The disciplined way to handle these is to mark each such value as a known,
reviewed exception in a scoped, auditable way, never by disabling a scanner or
bypassing the commit check wholesale. That keeps the scanners fully active for
everything else.

Finally, the response to a real leak is rotation, not deletion. Because version
history preserves the old content, the only safe assumption once a secret has been
committed and pushed is that it is compromised; it must be replaced with a fresh
value and the old one invalidated.

## MatchLayer Phase 1 usage

The pre-commit secret scanner is the gitleaks hook configured in
`.pre-commit-config.yaml`. It is pinned to a specific version and runs against the
staged content as a whole:

Source: `.pre-commit-config.yaml`

```yaml
- repo: https://github.com/gitleaks/gitleaks
  rev: v8.30.1
  hooks:
    - id: gitleaks-system
```

The committed example file, `.env.example`, lists every required variable with a
placeholder value and states the never-commit-real-secrets rule directly in its
header comment:

Source: `.env.example`

```text
# The defaults below are wired to the local docker-compose stack
# (Postgres, Redis, MinIO) so a fresh checkout can run both apps with
# no further edits. Real secrets must NEVER be committed here — `.env`
# is gitignored, this file is the committed sample.
```

The platform and exception layers are configured too. The hosting-platform secret
scanner has its reviewed-exceptions list in `.gitguardian.yaml`, which scopes the
allowed synthetic test values to the test tree so a real secret in application code
is still caught:

Source: `.gitguardian.yaml`

```yaml
secret:
  # Scope the exemptions to the test tree so a real secret committed in
  # application code is still caught.
  ignored-paths:
    - "apps/api/tests/**"
```

Together these realise the layered model: real values stay in the git-ignored
environment file, `.env.example` documents the contract, the gitleaks hook guards
each commit, and the platform scanner plus its scoped exception list form the
backstop.

## Common pitfalls

- **Mistake:** Treating a committed secret as fixed once it is deleted in a later commit.
  **Symptom:** The secret no longer appears in the current files, but it is still readable in the repository history and remains usable by anyone who has it.
  **Recovery:** Rotate the secret immediately — issue a new value and invalidate the old one — and treat the exposed value as compromised regardless of how briefly it was committed.

- **Mistake:** Committing real values into the example file instead of placeholders.
  **Symptom:** The example file works without edits because it carries live secrets, and those secrets are now in history for everyone with repository access.
  **Recovery:** Replace the live values with placeholders in the example file, move the real values into the git-ignored environment file, and rotate anything that was exposed.

- **Mistake:** Bypassing the secret scanner to push past a failure, or disabling it for a test fixture that looks like a secret.
  **Symptom:** The commit succeeds, but the scanner is no longer protecting the repository, and a real secret can slip through unnoticed.
  **Recovery:** Keep the scanner enabled and instead mark the specific synthetic value as a scoped, reviewed exception, so every other value is still scanned.

## External reading

- [GitHub Docs: about secret scanning](https://docs.github.com/en/code-security/secret-scanning/introduction/about-secret-scanning)
- [GitHub Docs: ignoring files](https://docs.github.com/en/get-started/git-basics/ignoring-files)
- [Open Worldwide Application Security Project (OWASP) Secrets Management Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html)
- [pre-commit: the framework homepage](https://pre-commit.com/)
