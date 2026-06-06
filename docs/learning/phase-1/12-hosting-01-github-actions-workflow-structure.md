# GitHub Actions workflow structure

## Introduction

GitHub Actions is the automation platform built into the GitHub code-hosting service; it runs scripts you define on GitHub's servers whenever something happens in your repository. The unit of automation is a _workflow_ — a configuration file, written in a human-readable, indentation-based text format (YAML), that lives in your repository and tells the platform what to run and when. This document takes that file apart piece by piece so that, with no prior exposure to the platform, you can read one and understand every part. The most common reason a team writes a workflow is Continuous Integration (CI), the practice of automatically building and testing every proposed change before it is allowed to merge.

A workflow has a small, fixed vocabulary: _triggers_ say when it runs, _jobs_ are the independent units of work, _steps_ are the ordered commands inside a job, _concurrency_ controls how overlapping runs interact, and _caching_ reuses files between runs so they finish faster. Once those five ideas click, every workflow file you meet becomes readable.

**Learning outcomes** — after reading this document you will be able to:

- Name the five structural parts of a workflow file and say what each one controls. Triggers, jobs, steps, concurrency, and caching each answer a different question.
- Explain why jobs run in parallel while the steps inside a job run in sequence. The two levels exist for different reasons.
- Describe what a concurrency group does and when a queued run gets cancelled. Concurrency stops wasted, superseded runs.
- Read a caching step and say what makes a cache hit versus a cache miss. The cache key is the whole story.

**Prerequisites:**

- [Lockfiles and frozen installs](01-foundations-07-lockfiles-and-frozen-installs.md) — the caching examples below key their cache on the hash of a lockfile, so it helps to know what a lockfile is before reading them.

## Problem it solves

The concrete problem is that checks a team agrees on — run the formatter, run the tests, scan for secrets — do not happen reliably when they depend on a person remembering to run them. Someone is in a hurry, skips the test suite, and a broken change lands on the shared branch that everyone else builds on. Multiply that across a team and the main branch drifts into a state where nobody is sure what works.

The state that existed before hosted automation was a mix of "run it on my machine and hope" and bespoke build servers that one engineer set up and only that engineer understood. The build server approach worked, but it lived outside the repository: its configuration was a web form or a file on a server, not something reviewed alongside the code it tested. When the build broke, the person who knew how to fix the server was often on holiday, and the configuration history was invisible to everyone else.

A workflow platform solves this by making the automation _part of the repository_. The instructions live in a file next to the code, they are reviewed in the same pull request, their history is in version control, and the platform — not a person — runs them on every change. Because the platform owns the machines, every contributor gets the same environment instead of "works on my machine" surprises.

## Mental model

Picture a factory assembly line that stays switched off until a specific kind of order arrives at the door. When the right order shows up, the line powers on, several work cells run side by side, and each cell performs its own tasks in a set order before reporting whether its part passed inspection.

A workflow run follows that shape. Walk through it step by step:

1. An _event_ happens in the repository — someone opens a pull request, or pushes a commit to a branch.
2. The platform checks each workflow's _triggers_ and starts only the workflows whose triggers match that event.
3. A started workflow launches its _jobs_. By default the jobs run at the same time, on separate fresh machines, because they do not depend on each other.
4. Inside each job, the _steps_ run one after another, top to bottom. If a step fails, the rest of that job normally stops, because later steps usually depend on earlier ones.
5. _Caching_ steps restore saved files at the start and save them at the end, so the next run can skip slow work like re-downloading dependencies.
6. When every job finishes, the run reports a single pass-or-fail result that other systems (like the rule that guards the shared branch) can read.

The key insight from the analogy: the work cells (jobs) are parallel and independent, but the tasks inside one cell (steps) are sequential and ordered. That two-level split is the heart of the model.

## How it works

A workflow file declares a name and then four kinds of content: when to run, what to run, how runs interact, and how to reuse work.

**Triggers** decide when the platform starts a run. They are listed under an `on` key and name the repository events the workflow cares about — opening or updating a pull request, pushing to a branch, a manual button, or a schedule. A workflow with no matching trigger for an event is skipped entirely, which is how one repository can hold many workflows that each wake up for different reasons. Restricting a trigger to specific branches keeps a workflow from running on changes it does not care about.

**Jobs** are the independent units of work, listed under a `jobs` key. Each job names the kind of machine it wants to run on — a fresh, throwaway virtual machine called a _runner_ that the platform provisions for that job and discards afterward. By default every job in a workflow runs in parallel on its own runner, and because the runners are separate machines they share no files. When one job genuinely must wait for another, it declares that dependency explicitly; the platform then delays the dependent job until its prerequisites succeed. A common pattern is a final aggregator job that depends on all the others and exists only to produce one tidy pass-or-fail signal.

**Steps** are the ordered commands inside a job, listed under a `steps` key. A step either runs a shell command or invokes a reusable, prepackaged unit of behaviour called an _action_ (for example, an action that checks out the repository's code, or one that installs a language toolchain). Steps run top to bottom on the same runner and share its filesystem, so a later step can use files an earlier step produced. If a step exits with a failure, the job stops there by default, on the assumption that continuing past a failed prerequisite is pointless.

**Concurrency** controls what happens when runs overlap. A _concurrency group_ is a label you compute; the platform allows only one active run per group, and a configurable rule decides whether a newly triggered run cancels an already-running one in the same group or waits for it. The typical use is to cancel superseded work: when a contributor pushes twice in quick succession, the first run is abandoned because its result no longer matters, which saves machine time and returns the relevant answer sooner.

**Caching** reuses files between runs so repeated work is skipped. A cache step computes a _key_ — usually a string that includes the hash of a dependency manifest such as a lockfile — and asks the platform for a saved bundle stored under that exact key. An identical key means the inputs have not changed, so the saved bundle is restored (a cache _hit_) and the slow install is avoided; a different key means something changed, so nothing is restored (a _miss_) and the step saves a fresh bundle under the new key at the end. Optional fallback keys let a near-miss restore a slightly stale bundle rather than starting from nothing. Caching changes only speed, never correctness: a run must produce the same result whether the cache was hit or missed.

Two cross-cutting ideas tie these together. First, least privilege: a workflow can be granted only the permissions it needs, and a single job can be given more where it genuinely requires them. Second, a deterministic final result: by funnelling many parallel jobs into one aggregating job, a workflow exposes a single, stable name that downstream gates can require.

## MatchLayer Phase 1 usage

The Phase 1 pipeline lives in one file, `.github/workflows/ci.yml`, and it uses every structural part described above.

The **triggers** keep the run surface small and intentional — the pipeline runs on pull requests targeting the main branch and on direct pushes to it, and nothing else:

Source: `.github/workflows/ci.yml`

```yaml
on:
  pull_request:
    branches: [main]
  push:
    branches: [main]
```

The **concurrency** block cancels a superseded pull-request run when a new commit arrives, while letting main-branch runs finish. The group is keyed on the branch reference, and the cancel rule is switched on only for pull-request events:

Source: `.github/workflows/ci.yml`

```yaml
concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}
```

A **job** declares the runner it wants and a list of **steps**. The first step of the backend job uses the prepackaged checkout action to pull the repository onto the runner:

Source: `.github/workflows/ci.yml`

```yaml
jobs:
  backend:
    name: backend
    runs-on: ubuntu-latest
    steps:
      - name: Check out repository
        uses: actions/checkout@v4
```

A **caching** step shows the cache-key idea in practice. This step saves and restores the frontend build's incremental cache; its key includes the hash of the dependency manifests, so a dependency change produces a new key (a miss), while an unchanged tree reuses the saved bundle (a hit). The fallback `restore-keys` line lets a near-miss warm the cache from a previous run:

Source: `.github/workflows/ci.yml`

```yaml
- name: Cache Next.js build
  uses: actions/cache@v4
  with:
    path: apps/web/.next/cache
    key: ${{ runner.os }}-nextjs-${{ hashFiles('apps/web/package.json', 'pnpm-lock.yaml') }}
    restore-keys: |
      ${{ runner.os }}-nextjs-
```

Finally, the **job-dependency** pattern produces one deterministic result. A `required-checks` job declares that it `needs` every other job and runs even when one of them failed, so branch protection — the GitHub setting that blocks a merge until named checks pass — can require this single check name instead of listing each job separately:

Source: `.github/workflows/ci.yml`

```yaml
required-checks:
  name: required-checks
  runs-on: ubuntu-latest
  needs: [backend, frontend, shared-types, security, openapi-drift]
  if: always()
```

Together these pieces give the repository a parallel set of independent jobs, a guard against wasted overlapping runs, dependency-aware sequencing where it is needed, and a single status that downstream branch rules can depend on.

## Common pitfalls

- **Mistake:** Expecting a file produced in one job to be available in another job, as if all jobs shared one machine.
  **Symptom:** A later job fails with a missing-file or "command not found" error for something an earlier job plainly created, even though both jobs are green individually.
  **Recovery:** Treat each job as a separate, empty machine: either do the dependent work in the same job (so steps share the filesystem), or pass files between jobs explicitly through an artifact or cache. Reserve the `needs` keyword for ordering, not for sharing files.

- **Mistake:** Treating a cache as a source of truth and assuming a restored bundle is always current.
  **Symptom:** A build keeps using stale dependencies or stale compiled output, and the failure disappears only after someone clears the cache by hand.
  **Recovery:** Put every input that should invalidate the cache into the key — most importantly the hash of the lockfile — so a dependency change forces a fresh key. Keep fallback `restore-keys` as a warm start only, and make sure the build step itself re-derives anything that must be correct rather than trusting the restored files.

- **Mistake:** Pointing the shared-branch protection rule at one job by name and assuming the whole pipeline is gated.
  **Symptom:** A change merges with a red pipeline because the failing job was a different one than the single job the branch rule was watching, or a renamed job silently stops being required.
  **Recovery:** Add one aggregator job that `needs` all the others and runs with an always-run condition, then require that single stable check name on the branch. New jobs are covered automatically as long as they are added to the aggregator's `needs` list.

## External reading

- [GitHub Docs: Workflow syntax for GitHub Actions](https://docs.github.com/en/actions/using-workflows/workflow-syntax-for-github-actions)
- [GitHub Docs: Events that trigger workflows](https://docs.github.com/en/actions/using-workflows/events-that-trigger-workflows)
- [GitHub Docs: Control the concurrency of workflows and jobs](https://docs.github.com/en/actions/using-jobs/using-concurrency)
- [GitHub Docs: Caching dependencies to speed up workflows](https://docs.github.com/en/actions/using-workflows/caching-dependencies-to-speed-up-workflows)
