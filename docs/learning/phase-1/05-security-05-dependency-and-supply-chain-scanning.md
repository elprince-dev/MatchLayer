# Dependency and supply-chain scanning

## Introduction

This document explains how a project guards against danger that arrives through
the code it did not write — its dependencies. A dependency is an external package
the project installs and builds on rather than writing from scratch. The supply
chain is the full path those packages travel, from their authors through public
registries into the build. Two distinct risks ride that chain: a dependency with
a known vulnerability (a publicly catalogued security flaw), and a dependency that
is malicious by design (for example, a look-alike package that impersonates a real
one). The defenses are automated scanners that run in Continuous Integration (CI),
the automated pipeline that checks every proposed change. This topic sits in the
Security track because modern applications are mostly third-party code, so the
supply chain is one of the largest attack surfaces a team owns.

**Learning outcomes** — after reading this document you will be able to:

- Distinguish a known-vulnerability scan from a static-analysis scan and say what each one looks for. One audits packages; the other audits source code.
- Explain why scanning runs automatically in the integration pipeline rather than relying on manual checks. Automation runs on every change without being remembered.
- Describe how automated dependency-update proposals shorten the window a known flaw stays unpatched. A bot opens the fix before a human notices.
- Recognise the common mistakes in supply-chain scanning and recover from them. A muted scanner is worse than none.

Prerequisites:

- [Lockfiles and frozen installs](01-foundations-07-lockfiles-and-frozen-installs.md) — introduces the pinned dependency list the audit tools read.

## Problem it solves

A project's own code is a small fraction of what actually runs; the rest is
dependencies, and their dependencies, several layers deep. Each of those packages
can contain a flaw that was unknown when it was installed and catalogued later, or
can be swapped for a hostile version through a compromised account or a
deceptively named imitation. The concrete problem is that a team cannot manually
read and re-review thousands of transitive packages, yet a single bad one can
expose the whole system.

The approach many teams start with is to install dependencies once and forget
them, checking for problems only when something visibly breaks. That fails because
vulnerabilities are disclosed continuously: a package that was clean when added
becomes a known risk months later, with no change on the team's side. Manual,
occasional checks also miss the moment that matters — the pull request that first
introduces a risky package — because nobody thinks to look right then.

Automated supply-chain scanning solves this by moving the checks into the
integration pipeline, where they run on every proposed change without anyone
having to remember. Vulnerability audits compare the project's pinned packages
against databases of known flaws; static analysis inspects the source for unsafe
patterns; and an update bot proposes patches as soon as fixes are published. The
result is continuous, unattended pressure against the largest and least visible
part of the attack surface.

## Mental model

Think of a factory that assembles products from parts bought from many suppliers.
It runs three standing inspections. An incoming-parts inspector cross-checks every
delivered part against a recall list and rejects any part with a known defect. A
process auditor walks the assembly line itself, looking for unsafe practices in
how the factory works, independent of the parts. And a procurement assistant watches
the suppliers' recall notices and, the moment a safer replacement part is announced,
drafts a purchase order so the swap is ready for a manager to approve. No single
inspector covers everything, but together they keep defective and dangerous parts
out.

When a change is proposed, the pipeline runs these checks:

1. A vulnerability audit reads the project's pinned package list and flags any package that appears in a known-vulnerability database.
2. A second audit does the same for a different language ecosystem, since a project often mixes more than one.
3. A static-analysis scan inspects the project's own source code for risky patterns rather than auditing packages.
4. An update bot, running on its own schedule, opens a proposed change whenever a dependency with a known flaw has a fixed version available.
5. Any check that finds a serious problem fails the pipeline, which blocks the change from merging until the problem is addressed.

Step 5 is what gives the checks teeth: they do not merely report, they gate.

## How it works

The first line of defense is the known-vulnerability audit. Such a tool reads the
project's exact, pinned dependency list — the lockfile — and compares each package
and version against a continuously updated database of disclosed vulnerabilities.
When a match is found, it reports the affected package and, usually, the version
that fixes it. Because each language ecosystem maintains its own packages and its
own advisory data, a project that uses more than one language runs one such audit
per ecosystem. Audits are typically scoped to the packages that actually ship to
production, since development-only tooling is not part of the running attack
surface.

A different kind of scan is static analysis, sometimes called Static Application
Security Testing (SAST): a scan of the project's own source code for patterns that
tend to be insecure — unsafe handling of untrusted input, dangerous function calls,
and similar. This is complementary to the dependency audit: the audit asks "are any
of the packages we use known to be flawed?", while static analysis asks "does the
code we wrote contain a risky pattern?". Running both covers two separate failure
modes.

The third element is automated dependency updates. An update service watches for
new releases that fix security advisories affecting the project's dependencies and
automatically opens a proposed change that bumps the affected package to the fixed
version. This shrinks the window between a fix being published and the project
adopting it, which is the window during which the project is knowingly exposed.
Configuring such a bot for security updates specifically keeps the noise low while
still closing real holes quickly.

Two cross-cutting practices make these scans trustworthy. The checks run inside the
integration pipeline so they execute on every proposed change automatically, and a
serious finding fails the pipeline so a vulnerable change cannot merge unnoticed.
And dependencies are pinned with committed lockfiles, so the audit sees exactly the
versions that will run — not a floating range that could resolve to something
different later. A separate habit guards against malicious packages specifically:
an unfamiliar or first-seen package name gets a manual look before it is merged,
because a typo-squatting imitation is caught by a human noticing the odd name, not
by a vulnerability database that has no advisory for brand-new malware.

## MatchLayer Phase 1 usage

The integration pipeline at `.github/workflows/ci.yml` runs the supply-chain
checks in a dedicated security job. The Python vulnerability audit reads the
production dependency list and fails on any unfixed advisory:

Source: `.github/workflows/ci.yml`

```yaml
- name: pip-audit (Python production deps)
  run: uv tool run pip-audit --strict -r requirements.txt
```

The JavaScript and TypeScript audit runs against production dependencies only and
gates on high-and-critical advisories:

Source: `.github/workflows/ci.yml`

```yaml
- name: pnpm audit (production deps, high+/critical)
  run: pnpm audit --prod --audit-level=high
```

Static analysis runs in the same job, covering both languages from a single
initialization:

Source: `.github/workflows/ci.yml`

```yaml
- name: CodeQL init (python + javascript-typescript)
  uses: github/codeql-action/init@v3
  with:
    languages: python, javascript-typescript
```

Automated security-update proposals are configured in `.github/dependabot.yml`.
Setting the routine-update limit to zero keeps ordinary version-bump noise off
while leaving security-advisory updates flowing:

Source: `.github/dependabot.yml`

```yaml
- package-ecosystem: "npm"
  directory: "/"
  schedule:
    interval: "weekly"
  open-pull-requests-limit: 0
```

Each check's failure fails the whole security job, which the pipeline aggregates
into a single required status — so a change that introduces a known-vulnerable or
unsafe dependency cannot merge until the finding is resolved.

## Common pitfalls

- **Mistake:** Auditing every dependency, including development-only tooling, and then ignoring the resulting flood of findings.
  **Symptom:** The audit reports many advisories in test or build tools that never ship, the team tunes out the noise, and a real production advisory is missed in the pile.
  **Recovery:** Scope the production audit to the packages that actually ship, and triage that smaller, relevant set seriously rather than muting the tool.

- **Mistake:** Running the scanners but not letting a serious finding fail the pipeline.
  **Symptom:** The scan output shows vulnerabilities, but changes merge anyway because the check is advisory, so the catalogued flaw stays in the running system.
  **Recovery:** Configure the audit and static-analysis steps to exit non-zero on serious findings, and make that job a required check that blocks merging.

- **Mistake:** Trusting vulnerability databases alone to catch a malicious package.
  **Symptom:** A typo-squatting imitation with a name close to a real package is installed and passes every audit, because no advisory exists for freshly published malware.
  **Recovery:** Add a human review step for unfamiliar or first-seen package names before merging the change that introduces them, and pin versions with committed lockfiles so swaps are visible.

## External reading

- [GitHub Docs: about Dependabot security updates](https://docs.github.com/en/code-security/dependabot/dependabot-security-updates/about-dependabot-security-updates)
- [GitHub Docs: about code scanning with CodeQL](https://docs.github.com/en/code-security/code-scanning/introduction-to-code-scanning/about-code-scanning-with-codeql)
- [Open Worldwide Application Security Project (OWASP): Vulnerable and Outdated Components](https://owasp.org/Top10/A06_2021-Vulnerable_and_Outdated_Components/)
- [pnpm: the audit command](https://pnpm.io/cli/audit)
