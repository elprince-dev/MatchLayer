# Dependabot configuration

## Introduction

Dependabot is a service built into GitHub that watches a project's declared dependencies and opens automated proposals to change them. A dependency is an outside package the project installs and builds on instead of writing that code itself. This document explains how Dependabot is steered by a single committed configuration file — a declarative settings file that tells the service what to watch rather than any running code — and how a team narrows that automation down to security-relevant updates so it stays useful instead of noisy.

The Reader needs no prior experience with GitHub automation; every term is introduced where it first appears. The focus is the configuration file itself: what it lists, what each setting controls, and how the settings combine to express a "security updates only" policy.

Learning outcomes — after reading this document you will be able to:

- Describe what Dependabot watches and what action it takes when it finds an out-of-date or vulnerable dependency. It reads a declared dependency set and opens a proposed change.
- Explain how one configuration file enumerates several independent dependency ecosystems and sets a schedule for each. There is one block per ecosystem, each with its own directory and cadence.
- Explain how setting the open-pull-request limit to zero turns routine version bumps off while leaving security-advisory updates flowing. The limit gates ordinary updates but not advisory-driven ones.
- Recognise the common configuration mistakes and recover from them. A misplaced directory or a missing ecosystem silently disables coverage.

Prerequisites:

- [Lockfiles and frozen installs](01-foundations-07-lockfiles-and-frozen-installs.md) — introduces the pinned dependency list that update tooling reads.
- [Dependency and supply-chain scanning](05-security-05-dependency-and-supply-chain-scanning.md) — introduces Dependabot security updates alongside the other supply-chain scanners.

## Problem it solves

Dependencies do not stay safe forever. A package that was sound when it was added can have a flaw disclosed against it months later, with no change on the team's side. A security advisory — a published notice that a specific package version contains a known vulnerability — can appear at any time. The concrete problem is keeping a project's many dependencies current with those advisories without a human having to remember to look.

The approach many teams start with is manual: a developer occasionally bumps versions by hand, or the team does nothing until a build breaks or an audit flags something. That leaves a long, invisible window during which a known-vulnerable package keeps running in the project. It also scales badly, because a real project pulls in dependencies from more than one package manager, each with its own update feed to track.

An automated update service closes that window by proposing the fix as soon as it exists. But turning such a service on naively creates a second problem: it opens a proposed change for every routine version bump across every dependency, producing a steady stream of low-value notifications. People learn to ignore the stream, and the important security proposals get lost in it. The configuration file is what resolves that tension — it lets a team keep the security proposals while switching the routine-bump noise off.

## Mental model

Think of a building that subscribes to a maintenance service for three different systems: the elevators, the fire alarms, and the plumbing. Each system has its own specialist vendor, its own inspection schedule, and its own location in the building. The building manager files one standing instruction sheet that lists all three subscriptions on a single page. For each system the sheet says which vendor handles it, where in the building it lives, and how often to inspect. The manager adds one more rule: "do not send me routine upgrade quotes — but always call me immediately if you find a safety recall." Safety recalls are urgent and rare; upgrade quotes are frequent and mostly ignorable. One sheet, three subscriptions, and a noise filter that never blocks the urgent calls.

The configuration file plays the role of that instruction sheet. Walking through how the service reads it:

1. The service opens the single configuration file and finds a list of entries, one per dependency ecosystem it should manage.
2. For each entry it reads which ecosystem to use, which directory holds that ecosystem's dependency manifest, and how often to check.
3. On each scheduled check it compares the installed versions against what is available and decides which dependencies are out of date.
4. It would open one proposed change per out-of-date dependency — but it first consults the per-entry limit on how many routine proposals may be open at once.
5. Security-advisory updates are handled on a separate track: they fire when an advisory matches a dependency, and they are not counted against that routine limit.

Step 4 and step 5 together are the noise filter: drive the routine limit to zero and the routine quotes stop, while the safety recalls keep coming.

## How it works

Dependabot is driven by one declarative configuration file committed to the repository in a fixed, well-known location that the service looks for automatically. Because the file is committed alongside the code, its history is reviewed and versioned like any other change, and there is a single source of truth for the update policy.

The file is organised as a list of update entries. Each entry describes one **package ecosystem** — a family of dependencies governed by a single package manager, such as a language's package registry or the platform's own workflow actions. A real project commonly mixes several ecosystems at once, so the file usually holds several entries. For each entry, three settings carry most of the meaning:

1. The ecosystem identifier, which tells the service which package manager's manifest and rules to apply.
2. The directory, which points at the location within the repository where that ecosystem's dependency manifest and **lockfile** live — the lockfile being the file that pins every dependency to an exact resolved version. The directory is relative to the repository root, so a value of the root means "look at the top of the repository."
3. The schedule, which sets the cadence at which the service checks for updates, for example a weekly interval.

A fourth setting controls volume rather than targeting: a per-entry limit on how many routine update proposals may be open at one time. This is where the security-only policy is expressed. Dependabot distinguishes two kinds of update. **Version updates** are routine: they track newer releases regardless of whether anything is wrong with the current version. **Security updates** are advisory-driven: they fire only when a published vulnerability advisory matches a dependency the project actually uses. The key behaviour is that security-update proposals are not counted against the routine open-proposal limit. Setting that limit to zero therefore disables the routine version-bump proposals for an ecosystem while leaving the advisory-driven security proposals free to open.

One important dependency sits outside the file. The advisory-driven security proposals only open when the repository itself has its security-update capability switched on in its settings; the configuration file tunes the behaviour but does not, on its own, enable the security feed. A correct setup is therefore two parts: the committed file expressing the per-ecosystem policy, and the repository-level toggle that turns the security feed on.

## MatchLayer Phase 1 usage

The whole policy lives in one committed file, `.github/dependabot.yml`. It opens by declaring the configuration schema version and a single `updates` list that will hold one entry per ecosystem:

Source: `.github/dependabot.yml`

```yaml
version: 2

updates:
```

Each entry names an ecosystem, the directory whose manifest it should read, a weekly schedule, and the routine-proposal limit. The Python entry points at the backend service directory and caps routine proposals at zero:

Source: `.github/dependabot.yml`

```yaml
- package-ecosystem: "pip"
  directory: "/apps/api"
  schedule:
    interval: "weekly"
  open-pull-requests-limit: 0
```

Three ecosystems are covered — the JavaScript workspace (`npm`), the Python backend (`pip`), and the workflow actions (`github-actions`) — and every one of them sets the same `open-pull-requests-limit: 0`, so the security-only policy is uniform across the repository:

Source: `.github/dependabot.yml`

```yaml
- package-ecosystem: "npm"
  open-pull-requests-limit: 0
- package-ecosystem: "pip"
  open-pull-requests-limit: 0
- package-ecosystem: "github-actions"
  open-pull-requests-limit: 0
```

The file documents the security-only mechanism inline, including the repository-level toggle that the file depends on:

Source: `.github/dependabot.yml`

```yaml
# How "security-only" is enforced here:
#   open-pull-requests-limit: 0 disables routine version-bump PRs for each
#   ecosystem. Per Dependabot docs, security-update PRs are NOT counted
#   against this limit and are still opened automatically when GitHub
#   security advisories match a vulnerable dependency, provided
#   "Dependabot security updates" is enabled at the repository level
#   (see docs/runbooks/repo-setup.md).
```

This matches the project's stated dependency policy: Dependabot is enabled for security updates only, working alongside the Continuous Integration (CI) audit steps in `.github/workflows/ci.yml` that fail a build on an unfixed advisory. The repository-level enablement step is recorded in `docs/runbooks/repo-setup.md`, so the committed file and the one-time toggle are documented together.

## Common pitfalls

- **Mistake:** Pointing an entry's `directory` at a folder that holds no dependency manifest for that ecosystem.
  **Symptom:** Dependabot runs without error but never opens proposals for that ecosystem, and the security feed for it stays empty because the service found nothing to track.
  **Recovery:** Set the directory to the location that actually contains that ecosystem's manifest and lockfile (the repository root for the workspace, the backend service directory for the Python project), then re-check that proposals or "up to date" results appear for the entry.

- **Mistake:** Omitting an ecosystem entirely — for example, listing the application dependencies but forgetting the workflow actions.
  **Symptom:** A vulnerable dependency in the un-listed ecosystem is never flagged and never gets an automated fix, while the listed ecosystems look healthy and give a false sense of full coverage.
  **Recovery:** Add one entry per package manager the repository uses, confirm each names a distinct ecosystem, and treat the entry list as the checklist of everything that must be watched.

- **Mistake:** Assuming the configuration file alone turns security updates on, so the repository-level toggle is left off.
  **Symptom:** With the routine limit at zero and the toggle off, no proposals open at all — neither routine nor security — and the project appears quiet when it is actually unprotected.
  **Recovery:** Enable "Dependabot security updates" in the repository settings as well as committing the file, following the recorded setup step, then verify a security proposal can open.

- **Mistake:** Reading `open-pull-requests-limit: 0` as "disable Dependabot completely," including its security proposals.
  **Symptom:** A team member proposes deleting the file to "stop the bot," which would also remove the security-update policy and silence advisory-driven fixes.
  **Recovery:** Keep the entries in place; remember that the zero limit gates only routine version bumps, and that security-advisory proposals are exempt from the limit by design.

## External reading

- [GitHub Docs: Configuring Dependabot version updates](https://docs.github.com/en/code-security/dependabot/dependabot-version-updates/configuring-dependabot-version-updates)
- [GitHub Docs: About Dependabot security updates](https://docs.github.com/en/code-security/dependabot/dependabot-security-updates/about-dependabot-security-updates)
- [GitHub Docs: Dependabot options reference](https://docs.github.com/en/code-security/dependabot/working-with-dependabot/dependabot-options-reference)
