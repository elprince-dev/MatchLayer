# The five Phase 1 CI jobs

## Introduction

This document explains the five checks that run automatically every time someone
proposes or pushes a change to this project, and exactly what each one proves
before the change is allowed in. These checks run inside Continuous Integration (CI)
— the practice of merging every change into a shared branch and having a
server automatically build and test it, rather than trusting that each developer
ran the checks on their own machine. Each check is packaged as a CI job, which is
a named, independently runnable unit of work that the build system can start on
its own fresh machine. The five jobs each guard a different slice of the
codebase: one for the Python backend, one for the web frontend, one for the
shared type definitions, one for security scanning, and one that re-derives the
generated client code and asserts it has not drifted. Reading this document
teaches you to recognise each job by name, predict why it failed from its name
alone, and reproduce its checks locally.

**Learning outcomes** — after reading this document you will be able to:

- Name the five Phase 1 CI jobs and state which part of the codebase each one guards.
- Explain what each job verifies and which local command reproduces that verification.
- Describe why the jobs run in parallel and how a separate aggregator job turns five results into one pass-or-fail gate.
- Diagnose a red build from the failing job's name and recover without guessing.

Prerequisites: this document assumes you have read the following Topic_Docs,
each of which explains a mechanism that one of these jobs enforces:

- [Lockfiles and frozen installs](01-foundations-07-lockfiles-and-frozen-installs.md)
- [Env files and drift detection](01-foundations-08-env-files-and-drift-detection.md)
- [Dependency and supply-chain scanning](05-security-05-dependency-and-supply-chain-scanning.md)
- [The OpenAPI drift check in continuous integration](11-contracts-06-openapi-drift-check.md)

## Problem it solves

The concrete problem is a broken shared branch. When many changes flow into one
branch that everyone builds on, a single change that fails to compile, breaks a
test, introduces a known-vulnerable dependency, or leaks a secret can block every
other developer until someone notices and reverts it. The cost of catching that
mistake rises the later it is found: cheap on the author's laptop, expensive once
it has merged and a teammate has built on top of it.

The prior approach was discipline and reviewer trust. Each contributor was
expected to run the formatter, the linter, the type checker, the test suite, and
the security scanners locally before opening a change, and the reviewer was
expected to notice when they had not. That expectation is fragile. A developer in
a hurry skips a step; a scanner is installed on one machine but not another; a
test passes locally because of stale state that does not exist on a clean
checkout. Nothing forced the checks to actually run, and a reviewer reading a
change cannot see which checks were skipped.

Splitting the verification into named jobs that run automatically on a clean
machine removes the reliance on memory and on the reviewer's vigilance. Every
change is built and tested the same way, from the committed state, every time. A
failure is attributed to a specific job whose name points at the kind of problem,
so the author knows immediately whether they broke the types, the tests, the
dependencies, or the generated contract.

## Mental model

Picture a factory assembly line with five inspection stations, plus a final
shipping clerk. Each station inspects one aspect of the product — its shape, its
wiring, its labelling, its safety, its paperwork — and stamps a pass or a fail.
The stations work side by side on copies of the same product, so the slowest one
sets the pace rather than all of them running one after another. The shipping
clerk at the end refuses to release the product unless all five stamps say pass.
A single failed stamp stops the shipment, and the clerk's report names the
station that rejected it, so you know where to look.

When a change arrives, the pipeline reasons like this:

1. Spin up several fresh, identical machines, one per job, each starting from the exact committed state of the change.
2. On each machine, install dependencies from locked versions so the result depends only on the change, not on whatever was lying around.
3. Run that job's slice of checks — formatting, linting, types, tests, audits, or contract regeneration — and record a single pass or fail for the job.
4. Let all the jobs run in parallel, so total wait time is roughly the slowest job rather than the sum of all jobs.
5. Feed every job's result into one aggregator job that passes only when all of them passed, and have branch protection require that single aggregator result.

Because each machine starts clean and installs pinned versions, a job that passes
in the pipeline passes for the same reasons on any clean checkout, which is what
makes the green build trustworthy.

## How it works

A continuous-integration pipeline is described by a workflow file that lists a set
of jobs. A job is an independently scheduled unit of work that runs on its own
freshly provisioned machine; within a job, ordered steps run one after another,
and the job fails the moment any step exits with a non-zero status. Because each
job gets its own machine, the system can run all the jobs at the same time, and
the wall-clock time for the whole pipeline is close to the duration of the
slowest job rather than the total of every job added together.

Splitting verification across several jobs instead of cramming everything into
one buys two things. The first is parallelism: independent slices of work finish
sooner when they run side by side. The second is attribution. When a job is
scoped to a single concern — type checking, or dependency auditing, or contract
regeneration — its name alone tells the author what class of problem broke the
build, without reading a long log. A monolithic single job would still catch the
same problems, but every failure would look the same from the outside.

There is a tension to manage, though. If branch protection — the rule that blocks
a merge until required checks pass — had to name all of the individual jobs, then
adding, renaming, or removing a job would mean editing the protection rule too,
and a forgotten update would silently weaken the gate. The common resolution is a
single aggregator job that depends on all the others and is configured to run even
when one of them has already failed. The aggregator inspects each dependency's
result and fails unless every one of them succeeded. Branch protection then
requires only that one aggregator check. The set of real jobs can change freely;
the gate's name stays put.

Two design choices keep the pipeline honest and fast. Installing dependencies
from a lockfile — a file that records the exact resolved version of every
dependency — means a run is reproducible and is not perturbed by an unrelated
upstream release. And keying a cache on that same lockfile lets a run reuse
downloaded packages from a previous run while still rebuilding from scratch
whenever the locked versions change, so caching speeds the pipeline up without
ever masking a real difference.

## MatchLayer Phase 1 usage

The pipeline is defined in `.github/workflows/ci.yml`. It declares five
verification jobs — `backend`, `frontend`, `shared-types`, `security`, and
`openapi-drift` — plus a sixth `required-checks` job that aggregates their
results into the single status branch protection targets. Every job pins its
machine image to `ubuntu-latest`, and the aggregator lists the five it depends
on:

Source: `.github/workflows/ci.yml`

```yaml
jobs:
  backend:
    name: backend
    runs-on: ubuntu-latest
  frontend:
    name: frontend
    runs-on: ubuntu-latest
  shared-types:
    name: shared-types
    runs-on: ubuntu-latest
  security:
    name: security
    runs-on: ubuntu-latest
  openapi-drift:
    name: openapi-drift
    runs-on: ubuntu-latest
  required-checks:
    name: required-checks
    runs-on: ubuntu-latest
    needs: [backend, frontend, shared-types, security, openapi-drift]
```

### `backend` — Python lint, types, tests, and the drift gates

The `backend` job guards the Python service. It installs the locked Python
dependencies, seeds a configuration file so settings validate at import time, and
then runs the formatter check, the linter, the type checker, and the test suite
in turn. It also runs two repository-specific drift gates: one comparing the
committed environment-variable template against the variables the code actually
reads, and one comparing the committed skill-data artifact against its source of
truth. Any single failing step fails the whole job:

Source: `.github/workflows/ci.yml`

```yaml
backend:
  - name: Ruff format check
    run: uv run ruff format --check .
  - name: Ruff lint
    run: uv run ruff check .
  - name: Mypy
    run: uv run mypy src
  - name: Pytest
    run: uv run pytest
  - name: Check .env.example drift
    run: python3 tools/check_env_drift.py
  - name: Check skill_lexicon drift
    run: python3 tools/check_lexicon_drift.py
```

The two drift steps shell out to `tools/check_env_drift.py` and
`tools/check_lexicon_drift.py`, both of which are dependency-free and run on the
committed files directly.

### `frontend` — Next.js lint, format, types, build, and tests

The `frontend` job guards the web application. It installs the locked JavaScript
dependencies, then runs the linter, the formatting check, and the type checker.
It builds the production bundle, starts the production server in the background,
waits until that server accepts connections, runs the test suite against the live
server, and finally stops the server in an always-run cleanup step. The build is
verified end to end because some tests assert real responses (such as security
headers) from a running production server rather than a mock.

### `shared-types` — the same gates against the generated type package

The `shared-types` job runs the linter, the formatting check, the type checker,
and the test suite against the package that holds the type definitions shared
across the frontend. Running these gates against the shared package explicitly —
rather than assuming the frontend job covers it — ensures the package stays
clean on its own terms even though it is consumed by another application.

### `security` — dependency audits, secret scanning, and static analysis

The `security` job bundles the supply-chain and secret-scanning defences. It
audits the production Python dependencies for known-vulnerable packages, audits
the production JavaScript dependencies at the high-and-critical severity
threshold, scans the change for committed secrets, runs static analysis over both
languages, and finally runs the full pre-commit hook set so a contributor who
skipped installing the hooks locally is still caught. Each step's failure fails
the whole job:

Source: `.github/workflows/ci.yml`

```yaml
security:
  - name: pip-audit (Python production deps)
    run: uv tool run pip-audit --strict -r requirements.txt
  - name: pnpm audit (production deps, high+/critical)
    run: pnpm audit --prod --audit-level=high
  - name: gitleaks (PR-diff secret scan)
    uses: gitleaks/gitleaks-action@v2
  - name: CodeQL init (python + javascript-typescript)
  - name: CodeQL analyze
  - name: Run all pre-commit hooks against the full tree
    run: pre-commit run --all-files
```

### `openapi-drift` — the generated client code matches the live contract

The `openapi-drift` job re-runs the contract code generation against the live
description produced by the backend and asserts that nothing under the committed
generated source changed as a result. A change to an endpoint that is not
accompanied by a regeneration is caught here. This job is explained in depth in
its own Topic_Doc, [The OpenAPI drift check in continuous integration](11-contracts-06-openapi-drift-check.md);
the relevant point for this document is that it is one of the five gated jobs.

### `required-checks` — one status the merge gate can target

Branch protection requires the single `required-checks` status rather than naming
all five jobs. That aggregator depends on the five and is configured to run even
when a dependency failed, so it can translate any non-success result into one
failed status. The five real jobs can be added to or renamed without touching the
branch-protection configuration, because the configuration only ever names the
aggregator.

## Common pitfalls

- **Mistake:** Reading the raw build log top to bottom instead of starting from which job is red.
  **Symptom:** You spend minutes scrolling logs trying to understand a failure whose category the job name already told you.
  **Recovery:** Look at the failed job's name first. `backend` means a Python lint, type, test, or drift failure; `frontend` a build or component-test failure; `security` an audit, secret, or static-analysis failure; `openapi-drift` a stale generated contract. Open the log for that job only.

- **Mistake:** Assuming a green local run guarantees a green pipeline even though you skipped the frozen install.
  **Symptom:** The pipeline fails to install dependencies, or behaves differently from your machine, because your local install pulled versions the lockfile does not pin.
  **Recovery:** Reproduce the job locally with the same frozen-install commands the job uses, so your dependency tree matches the locked versions the pipeline installs.

- **Mistake:** Trying to make the merge gate pass by editing the branch-protection rule to drop the failing job instead of fixing the change.
  **Symptom:** The merge becomes possible, but the class of defect the dropped job guarded against starts reaching the shared branch unchecked.
  **Recovery:** Leave the gate alone and fix the underlying failure. The aggregator pattern deliberately keeps one stable check name so the gate cannot be weakened by quietly editing a job list.

- **Mistake:** Treating an `openapi-drift` failure as a test bug and re-running the job hoping it turns green.
  **Symptom:** The job keeps failing with a drift error on every re-run because the committed generated files genuinely disagree with the current backend.
  **Recovery:** Regenerate the client code locally with the project's codegen command (codegen is the code-generation step that re-derives the typed client from the live contract) and commit the regenerated files in the same change as the backend edit that caused the drift.

## External reading

- [GitHub Actions: about workflows](https://docs.github.com/en/actions/writing-workflows/about-workflows)
- [GitHub Actions: using jobs in a workflow](https://docs.github.com/en/actions/using-jobs/using-jobs-in-a-workflow)
- [GitHub Actions: caching dependencies to speed up workflows](https://docs.github.com/en/actions/writing-workflows/choosing-what-your-workflow-does/caching-dependencies-to-speed-up-workflows)
- [Ruff: the Ruff linter](https://docs.astral.sh/ruff/linter/)
