# Branch protection rules and the required-checks aggregator

## Introduction

This document explains how a code-hosting platform can stop unreviewed or broken changes from reaching an important branch, and how a single summarizing check keeps that guarantee stable as the set of automated checks grows. Branch protection is a set of rules a repository host enforces on a named branch — for example, refusing a direct push, requiring a review, or requiring that automated checks pass — before any change is allowed in. Those automated checks run in continuous integration (CI), the practice of merging every change into a shared branch and building and testing it on a server rather than only on a contributor's laptop. A status check is the pass-or-fail result that one CI job (a named, independently runnable unit of work in the pipeline) reports back to the host for a specific change. A required status check is one the host insists must report success before a merge is allowed. The required-checks aggregator is one extra job that waits for all the others and reports a single pass-or-fail result, so branch protection can require that one job instead of naming every individual job. This topic sits in the Hosting and deploy track because branch protection is the gate that turns a green pipeline into an actual merge guarantee.

**Learning outcomes** — after reading this document you will be able to:

- Explain what a branch protection rule is and which actions on a branch it can gate.
- Describe how a required status check blocks a merge until a named job reports success against the latest change.
- Explain why requiring a single aggregator job is more stable than requiring a long list of individual job names.
- Recognise the common mistakes that defeat the aggregator pattern and recover from them.

Prerequisites: No prerequisites. This document defines continuous integration, status checks, jobs, and the aggregator pattern from first principles as it goes.

## Problem it solves

The concrete problem is that a shared branch everyone deploys from can be corrupted in two easy ways: a contributor pushes straight to it without review, or a change merges while its tests are red. Either one leaves the branch in a state nobody verified, and the next person to build from it inherits the breakage.

The prior approach was an honor system. The team agreed, by convention, never to push directly to the main branch and never to merge a change whose checks had failed. That agreement is fragile: a tired contributor force-pushes a quick fix, a reviewer approves before the pipeline finishes, or a flaky check is waved through. Nothing technical prevents any of it, so the branch's health depends entirely on memory and goodwill.

A first improvement is to have the host enforce the rule: mark the branch protected and require that specific checks pass before merging. That removes the honor system, but it introduces a subtler problem. The protection rule has to name the checks it requires, and that list is maintained by hand. When a pipeline has many jobs, the maintainer must enumerate every one of them in the branch settings. If a job is renamed, the old name in the required list is silently never satisfied — or worse, a check that never runs is treated as absent rather than failing, and a merge slips through a gap the maintainer did not notice. Keeping a hand-maintained list of check names in sync with the real pipeline is the same kind of drift problem that bites every duplicated source of truth.

The required-checks aggregator removes that second fragility. Instead of requiring each job by name, the pipeline adds one job that depends on all the others and computes a single verdict from their results. Branch protection requires only that one check. The list of real jobs can change freely; the only name the protection rule ever references is the aggregator's, and the aggregator is the single place that knows which jobs must succeed.

## Mental model

Think of a building with a security turnstile. Many separate inspections happen inside — a badge scan, a bag check, a temperature reading — but the turnstile at the exit only opens when one final attendant confirms that every inspection passed. Visitors and the turnstile never need to know the full list of inspections; they trust the attendant's single thumbs-up. If the building adds a new inspection next month, only the attendant's checklist changes. The turnstile's rule stays exactly the same: wait for the attendant.

Branch protection is the turnstile, the individual checks are the inspections, and the aggregator job is the attendant who gives the single thumbs-up.

A protected merge proceeds through these steps:

1. A contributor opens a proposed change against the protected branch, which triggers the pipeline to run every check against that change's latest commit.
2. Each check runs independently and reports its own pass-or-fail result back to the host, keyed to that commit.
3. The aggregator job waits until every other job has finished, inspects all of their results, and reports success only if all of them succeeded.
4. The host compares the aggregator's result against the branch's required-check list; because the aggregator is the one required check, a single success unlocks the merge button and any failure keeps it locked.
5. If a contributor adds a new commit, the results from the old commit no longer count, the checks re-run, and the gate re-evaluates against the newest commit.

Because the gate keys everything to the latest commit, a change that was green an hour ago cannot merge after a new commit unless the checks pass again.

## How it works

A protected branch is a branch the host treats specially: it intercepts pushes and merges and applies a configured rule set before allowing them. Typical rules include forbidding direct pushes so every change arrives through a reviewed proposal, requiring one or more approving reviews, forbidding force-pushes and deletion, and requiring that a named set of status checks report success. The rules are enforced server-side, so they hold regardless of what any individual contributor does locally.

A status check is a small record the host stores against a specific commit: a name, a state (pending, success, or failure), and usually a link to the run that produced it. An automated pipeline reports one such record per job. The host ties these records to the commit hash, which is why adding a new commit invalidates the previous results — the new commit has no checks yet, so the gate returns to pending until the pipeline re-runs. Requiring a check to pass against the latest commit is what guarantees the verified state and the merged state are the same state.

The required-checks list is the bridge between the pipeline and the protection rule. The host can only require a check by the exact name the pipeline reports. This creates two hazards. First, names must be unique and stable: if two workflows report a check with the same name, the result is ambiguous; if a job is renamed, the old required name is never reported and the merge waits forever or, depending on the host's settings, the missing check is ignored. Second, a check that never starts is not the same as a check that failed — a required check that is never reported can leave a merge either permanently blocked or, in a misconfiguration, silently unblocked.

The aggregator pattern resolves both hazards by collapsing many results into one. The pipeline declares one final job that depends on all the others, so the pipeline scheduler will not start it until every dependency has reached a terminal state. Two details make this job correct:

- It must run even when a dependency failed. By default a dependent job is skipped if any job it depends on fails, and a skipped job reports neither success nor failure — exactly the silent gap that lets a merge slip through. Forcing the job to run regardless of upstream outcome means it always reports a definite result.
- Because it now runs unconditionally, it must inspect each dependency's result and fail itself when any dependency did not succeed. A dependency can finish as success, failure, cancelled, or skipped; the aggregator treats anything other than success as a reason to fail. It reads each upstream result, and if any is not success, it exits with a failure status that the host records as a failed check.

The payoff is a single, deterministic check name that means "every required job succeeded against this commit." Branch protection requires only that name. The set of real jobs can grow, shrink, or get renamed, and the protection rule never has to change, because the only contract it depends on is the aggregator's name and the aggregator's promise to fail whenever anything it watches did not succeed.

## MatchLayer Phase 1 usage

MatchLayer runs its pipeline from `.github/workflows/ci.yml`, which defines five working jobs — `backend`, `frontend`, `shared-types`, `security`, and `openapi-drift` — plus one aggregator job named `required-checks`. The intent is that the branch protection rule on the `main` branch requires the single `required-checks` status check rather than all five job names, so the required list never drifts as jobs are added or renamed.

The aggregator declares its dependencies with `needs` (which makes the scheduler wait for those jobs) and forces itself to run even after a failure with `if: always()`:

Source: `.github/workflows/ci.yml`

```yaml
needs: [backend, frontend, shared-types, security, openapi-drift]
if: always()
```

Without `if: always()`, a failure in any of the five jobs would cause `required-checks` to be skipped, and a skipped check reports no result — which is the silent gap described above. Running it unconditionally guarantees it always reports a definite success or failure. The full job then translates the upstream results into its own verdict:

Source: `.github/workflows/ci.yml`

```yaml
required-checks:
  name: required-checks
  runs-on: ubuntu-latest
  needs: [backend, frontend, shared-types, security, openapi-drift]
  if: always()
  steps:
    - name: Check required job results
      run: |
        if [[ "${{ needs.backend.result }}" != "success" || \
              "${{ needs.frontend.result }}" != "success" || \
              "${{ needs.shared-types.result }}" != "success" || \
              "${{ needs.security.result }}" != "success" || \
              "${{ needs.openapi-drift.result }}" != "success" ]]; then
          echo "::error::One or more required checks failed."
          echo "  backend       = ${{ needs.backend.result }}"
          echo "  frontend      = ${{ needs.frontend.result }}"
          echo "  shared-types  = ${{ needs.shared-types.result }}"
          echo "  security      = ${{ needs.security.result }}"
          echo "  openapi-drift = ${{ needs.openapi-drift.result }}"
          exit 1
        fi
```

The `result` value for each dependency is the terminal outcome the scheduler recorded for that job: `success`, `failure`, `cancelled`, or `skipped`. The script fails the aggregator unless every one of the five is exactly `success`, and the echo lines print which job was not green so a contributor reading the failed run sees the cause without opening five separate logs. Because the aggregator reports under the stable name `required-checks`, the branch protection rule references only that name; the five underlying jobs can change without anyone editing the branch settings.

## Common pitfalls

- **Mistake:** Omitting `if: always()` from the aggregator job so it inherits the default skip-on-upstream-failure behavior.
  **Symptom:** When any of the five jobs fails, the `required-checks` check shows as skipped rather than failed, and depending on the host's handling of missing checks the merge button can become available even though the pipeline did not pass.
  **Recovery:** Add `if: always()` so the aggregator runs regardless of upstream outcome, then confirm a deliberately failing job now turns `required-checks` red rather than leaving it skipped.

- **Mistake:** Running the aggregator unconditionally but forgetting to inspect the dependencies' results, so the job succeeds as long as it starts.
  **Symptom:** The pipeline shows a failed job, yet `required-checks` reports success and the change merges with a broken job in its history.
  **Recovery:** Read each dependency's recorded result and exit with a failure status whenever any result is not `success`, so the aggregator's verdict reflects the jobs it watches.

- **Mistake:** Configuring branch protection to require each individual job by name instead of requiring the aggregator.
  **Symptom:** Renaming or removing a job leaves a required name that is never reported, and merges either block forever waiting on the missing check or slip through when the stale name is ignored.
  **Recovery:** Require only the single aggregator check in the branch rule and let the aggregator own the list of jobs, so the protection rule never references a name that can drift.

- **Mistake:** Reusing the same job name across more than one workflow file while branch protection requires that name.
  **Symptom:** The required check resolves ambiguously, and pull requests are blocked from merging because the host cannot decide which run satisfies the requirement.
  **Recovery:** Give every job a name that is unique across all workflows, update the required-check name to match, and re-run the pipeline so a single unambiguous result is reported.

## External reading

- [GitHub Docs: About protected branches](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/defining-the-mergeability-of-pull-requests/about-protected-branches)
- [GitHub Docs: Troubleshooting required status checks](https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/collaborating-on-repositories-with-code-quality-features/troubleshooting-required-status-checks)
- [GitHub Docs: Workflow syntax for GitHub Actions](https://docs.github.com/en/actions/using-workflows/workflow-syntax-for-github-actions)
- [GitHub Docs: Contexts (the needs context and job results)](https://docs.github.com/en/actions/learn-github-actions/contexts)
