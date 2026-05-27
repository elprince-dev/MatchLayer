# GitHub repository setup — `MatchLayer`

> Reproducible checklist for the GitHub-side configuration that **cannot** be enforced from
> code. Re-run this top to bottom after a fresh fork, repository transfer, or admin reset.
> Each numbered section is idempotent: re-applying a step that is already in the desired state
> is a no-op.
>
> Anchors:
>
> - `.kiro/specs/phase-1-foundation/requirements.md` → Requirement 11
> - `.kiro/specs/phase-1-foundation/design.md` → §13
> - `.kiro/steering/security.md` → "Dependency & supply-chain security", "Secrets management"
> - `.kiro/steering/conventions.md` → "Git & commits"
>
> **A note on UI drift.** GitHub has been moving repository-level controls into a unified
> _Settings → Security_ surface and recommends **Rulesets** over the older _Branch protection
> rules_ form. The screenshots, sidebar labels, and sub-section names below reflect the UI as of
> late 2025 / 2026. Where two paths exist for the same outcome, the Rulesets path is the
> recommended one and is documented first; the classic _Branch protection rules_ form is
> documented as an acceptable fallback. If GitHub renames a control again, the controlling
> intent in each step should be enough to find the new label.

## Prerequisites

- Admin (or "Maintain") access to the repository on GitHub.
- The default branch is `main` and CI has run at least once (so the `required-checks` aggregator
  job appears in the list of selectable status checks).

---

## 1. Branch protection on `main` (Ruleset)

> Source: Requirement 11.2; Design §13 step 1.
>
> _Settings → Rules → Rulesets → New ruleset → New branch ruleset_.
>
> Reference: GitHub docs,
> [Creating rulesets for a repository](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/creating-rulesets-for-a-repository)
> and
> [Available rules for rulesets](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets).
>
> The Rulesets UI is structured as four sections — **General** (name + enforcement status),
> **Bypass list**, **Targets** (which branches the ruleset applies to), and **Rules**
> (broken into **Branch rules** and **Restrictions**). There is no numbered list of fields and
> no "0/1 approvals" spinner sitting on a flat form: required approvals appear as a sub-setting
> only after you tick **Require a pull request before merging**. If you cannot find the
> approval count or the status-check picker, that is almost always why — toggle the parent rule
> on first.

1. Open _Settings → Rules → Rulesets_, click **New ruleset → New branch ruleset**.
2. Under **General**:
   1. **Ruleset name**: `Protect main`.
   2. **Enforcement status**: select **Active**. (The other options, _Disabled_ and _Evaluate_,
      are for staging a ruleset before it gates merges.)
3. Leave **Bypass list** empty. Even repository admins should not bypass these rules; that is
   the point of the protection.
4. Under **Targets**, click **Add target → Include default branch**. (If the default branch is
   not `main`, fix that first — _Settings → General → Default branch_ — then come back.)
   Alternatively, pick **Include by pattern** and enter `main`.
5. Under **Rules → Branch rules**, enable each of the following and configure as noted. Each
   rule is its own checkbox; the sub-options for a rule appear inline only after the parent
   checkbox is ticked.
   1. **Restrict deletions** — on. (Blocks `git push --delete origin main`.)
   2. **Block force pushes** — on. (Blocks `git push --force` to `main`. Note: this rule's name
      changed from the older "Allow force pushes / Allow deletions" toggle pair on the classic
      branch-protection form to two separate, positively-named "Restrict / Block" rules in
      Rulesets. The intent is identical.)
   3. **Require linear history** — on. (Forces squash- or rebase-merge; rejects merge commits.
      Keeps `main`'s history reviewable.)
   4. **Require a pull request before merging** — on. After enabling this, the **Additional
      settings** sub-section appears. Configure:
      - **Required approvals**: `1`.
        (Solo-developer note: GitHub does not allow a PR author to approve their own PR. If
        you operate solo without a second account, lower this to `0` and rely on the rest of
        this ruleset — required status checks plus linear history — to gate merges. Restore to
        `1` as soon as a second collaborator joins.)
      - **Dismiss stale pull request approvals when new commits are pushed**: on.
      - Leave **Require review from Code Owners**, **Require approval of the most recent
        reviewable push**, and the per-team **Required reviewers** picker untouched.
      - **Require conversation resolution before merging**: on.
   5. **Require status checks to pass** — on. After enabling this, the status-check picker
      appears under **Additional settings**:
      - **Require branches to be up to date before merging**: on.
      - Click **Add checks**. In the picker that opens, type **`required-checks`** in the
        search box.
      - When the entry appears, hover the **Source** column and pick **GitHub Actions** as the
        source app. (The Rulesets picker requires both a name _and_ a source. If you leave the
        source as "any source", merging is still gated, but any actor with `statuses:write`
        could in principle satisfy the check.)
      - Click **Add selected status checks**. Confirm the row now shows
        `required-checks` · GitHub Actions in the table.
      - Why a single check: `required-checks` is the aggregator job at the bottom of
        `.github/workflows/ci.yml`. It `needs:` every other job (`backend`, `frontend`,
        `shared-types`, `security`, `openapi-drift`) and reports a single deterministic status,
        so the ruleset only ever needs to track one check name even as individual jobs are
        added or renamed.
6. Leave **Rules → Restrictions** (the metadata, file-path, file-extension, file-size, and
   file-path-length restrictions) empty. Those are push-ruleset features unrelated to this
   protection.
7. Click **Create**. The ruleset takes effect immediately because the enforcement status is
   **Active**.

**Verification**: open _Settings → Rules → Rulesets_ and click into `Protect main`. Confirm
**Enforcement status** is **Active**, the **Targets** row lists `Default` (or `main`), and the
**Rules** summary lists, at minimum: _Restrict deletions_, _Block force pushes_, _Require
linear history_, _Require a pull request before merging_ (with `1` approval), and _Require
status checks to pass_ (with `required-checks` from GitHub Actions).

### 1a. Alternative — classic _Branch protection rules_ form

The classic _Branch protection rules_ form remains available at _Settings → Branches → Branch
protection rules → Add rule_. It is functionally equivalent for the rules used here. Use it
only if the Rulesets UI is unavailable on your plan or your repo. The classic form maps to the
Rulesets settings above as follows:

| Classic checkbox                                                     | Rulesets equivalent                                                        |
| -------------------------------------------------------------------- | -------------------------------------------------------------------------- |
| _Branch name pattern_ → `main`                                       | **Targets** → Include default branch                                       |
| _Require a pull request before merging_                              | **Branch rules** → _Require a pull request before merging_                 |
| _Required approvals_ → `1`                                           | **Additional settings** → _Required approvals_                             |
| _Dismiss stale pull request approvals when new commits are pushed_   | **Additional settings** → same checkbox                                    |
| _Require status checks to pass before merging_                       | **Branch rules** → _Require status checks to pass_                         |
| _Require branches to be up to date before merging_                   | **Additional settings** → same checkbox                                    |
| add `required-checks` in the search box                              | **Additional settings** → **Add checks** → search `required-checks`        |
| _Require conversation resolution before merging_                     | sub-option of _Require a pull request before merging_                      |
| _Require linear history_                                             | **Branch rules** → _Require linear history_                                |
| _Restrict who can push to matching branches_ (with empty actor list) | implicitly enforced by _Require a pull request before merging_ in Rulesets |
| _Do not allow bypassing the above settings_ (admins included)        | leave **Bypass list** empty                                                |
| _Allow force pushes_ disabled, _Allow deletions_ disabled            | **Branch rules** → _Block force pushes_ on, _Restrict deletions_ on        |

If you configure both a classic branch protection rule and a Rulesets ruleset on `main`, GitHub
applies the **most restrictive** combined result. There is no harm in running both; just don't
use that as a way to fight yourself over the approval count.

---

## 2. Secret Scanning and Push Protection

> Source: Requirement 11.3; Design §13 steps 2–3; `security.md` "Secrets management".
>
> _Settings → Security → Advanced Security_.
>
> Reference: GitHub docs,
> [Managing security and analysis settings for your repository](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/enabling-features-for-your-repository/managing-security-and-analysis-settings-for-your-repository).
>
> The page used to be called _Code security and analysis_ and lived under a top-level
> _Security_ heading. It has been merged into a single _Advanced Security_ surface in the new
> sidebar, with sub-sections labelled **Secret Protection**, **Code Security**, and
> **Dependabot**. The control names below match that layout.

1. In the left sidebar, under the **Security** heading, click **Advanced Security**.
2. Scroll to the **Secret Protection** sub-section.
3. To the right of **Secret scanning**, click **Enable**. (If the button reads **Disable**,
   the feature is already on — leave it.)
4. To the right of **Push protection**, click **Enable**. (Push protection blocks `git push`
   on the server side when GitHub detects a high-confidence secret pattern in the pushed
   commits — defense in depth on top of the local `gitleaks` pre-commit hook.)
5. (Optional, recommended) If a **Validity checks** toggle is shown under **Secret scanning**,
   enable **Automatically verify if a secret is valid by sending it to the relevant partner**.

**Verification**: navigate to the repository's **Security** tab → **Secret scanning alerts**.
The page should load without a "feature not enabled" banner. Push protection violations surface
as a `remote: error` on `git push` — covered by the smoke test in section 7.

---

## 3. Dependabot security updates

> Source: Requirement 11.3; Design §13 step 4; `security.md` "Dependency & supply-chain
> security".
>
> _Settings → Security → Advanced Security_.
>
> Reference: GitHub docs,
> [Configuring Dependabot security updates](https://docs.github.com/en/code-security/dependabot/dependabot-security-updates/configuring-dependabot-security-updates).

1. Confirm `.github/dependabot.yml` is committed on `main`. The committed file pins
   `open-pull-requests-limit: 0` for `npm`, `pip`, and `github-actions` — this disables routine
   version-bump PRs while leaving security advisory PRs unaffected.
2. In the left sidebar, under **Security**, click **Advanced Security**.
3. Scroll to the **Dependabot** sub-section.
4. To the right of **Dependabot alerts**, click **Enable**.
5. To the right of **Dependabot security updates**, click **Enable**.
6. Leave **Dependabot version updates** governed by the committed `.github/dependabot.yml`
   (no UI toggle change needed — the file is the source of truth).
7. (Optional) If a **Grouped security updates** toggle is shown under **Dependabot**, enable it
   if you prefer one PR per ecosystem per week over one PR per advisory.

**Verification**: the repository's **Security** tab → **Dependabot alerts** loads without an
"enable" banner; any open advisories show a "Create Dependabot security update" button.

---

## 4. CodeQL default setup (Python and JavaScript/TypeScript)

> Source: Requirement 11.4; Design §13 step 5.
>
> _Settings → Security → Advanced Security → Code Security → CodeQL analysis_.
>
> Reference: GitHub docs,
> [Configuring default setup for code scanning](https://docs.github.com/en/code-security/code-scanning/enabling-code-scanning/configuring-default-setup-for-code-scanning).

**Do not enable CodeQL default setup on this repository.** The canonical CodeQL scanner
for `MatchLayer` is the **advanced workflow** in `.github/workflows/ci.yml` — the
`security` job runs `github/codeql-action/init@v3` + `analyze@v3` for `python,
javascript-typescript` on every push and PR, and uploads a SARIF result that the
`required-checks` aggregator gates on. Enabling default setup at the repository level is
**mutually exclusive** with that advanced workflow, not redundant: GitHub silently
suppresses SARIF uploads from any in-tree workflow that calls `codeql-action/init`
whenever default setup is configured, and the `security` job fails with an
`HTTP 409: code scanning is not configured for advanced setup` error on the upload step.
The two configurations cannot coexist; pick one, and on this repo it is the in-tree
advanced workflow.

**No action is required in Phase 1 for default setup.** Confirm it is _off_ and move on.

**Verification**:

```bash
gh api /repos/elprince-dev/MatchLayer/code-scanning/default-setup --jq '.state'
# Expected: not-configured
```

If the command returns `configured`, default setup has been enabled and the in-tree
`security` job will be failing on every PR until it is disabled. To disable it: open
_Settings → Security → Advanced Security → Code Security → CodeQL analysis_, click the
**⋯** menu, and choose **Disable CodeQL** (or **Switch to advanced** if that option is
offered — both have the same effect on this repo because the advanced workflow already
exists in `ci.yml`). Then push an empty commit on any open PR to clear the stale failing
`security` runs.

The repository's **Security** tab → **Code scanning** still populates an alerts page
(or "no alerts found") on the next push to `main`; the alerts come from the in-tree
workflow's SARIF upload, not from default setup.

### 4a. Fallback — if you can't run the advanced workflow

If the in-tree advanced workflow cannot run (for example on a fork where Actions are
disabled organization-wide, or on a stripped-down clone with no `.github/workflows/`
directory), default setup is the acceptable fallback. **In that case, and only in that
case**, follow the steps below. Do not run this on the canonical `elprince-dev/MatchLayer`
repository — it will break the in-tree `security` job until it is reverted.

1. In the left sidebar, under **Security**, click **Advanced Security**.
2. Scroll to the **Code Security** sub-section.
3. To the right of **CodeQL analysis**, click **Set up ▾**, then click **Default**.
4. A **CodeQL default configuration** dialog opens, listing the auto-detected configuration.
   Click **Edit** if you need to change anything.
   - In **Languages**, confirm both **Python** and **JavaScript/TypeScript** are selected. If a
     language is missing because the auto-detector cannot see it (for example on a fork where
     the language detector hasn't run yet), close the dialog and switch to **Advanced** —
     see the second fallback below.
   - In **Query suites**, select **Default**.
5. Click **Enable CodeQL**. GitHub triggers a workflow run to test the new configuration.

**Second fallback — Advanced setup as a generated workflow** (per Requirement 11.4):

If the **Default** option is greyed out, fails to detect one of the languages, or the **Set up**
menu only offers **Advanced**, switch to **Advanced** setup. To the right of **CodeQL
analysis**, click **Set up ▾ → Advanced**. GitHub generates a `.github/workflows/codeql.yml`
on a branch and opens a PR; review and merge it, keeping the languages set to `python` and
`javascript-typescript`. On a fork that has no in-tree advanced workflow yet, this is the
preferred path because it lands the workflow file in the repo where it can be evolved.

**Verification (fallback only)**: from the **Code Security** sub-section, click the **⋯** menu next to
**CodeQL analysis** and choose **View CodeQL configuration**. The configuration shows both
languages with the **Default** query suite. The repository's **Security** tab → **Code
scanning** populates an alerts page (or "no alerts found") on the next push to `main` or the
next scheduled run.

---

## 5. Repository topics

> Source: Design §13 step 7; supports project discoverability on GitHub search.
>
> _Repository home page → ⚙ next to "About" → Topics_.

Set the topics to:

- `nextjs`
- `fastapi`
- `ats`
- `ai`
- `monorepo`

Save. Topics are case-insensitive; GitHub renders them lower-case.

---

## 6. Environments (deferred — placeholder)

> Source: Design §13 step 6.
>
> GitHub Environments — with required reviewers, deployment branch policies, environment
> secrets, and wait timers — are not configured in Phase 1. Production deployment lands in
> **Phase 6 (AWS Production Architecture)**. At that point, expect to create at least:
>
> - `staging` — auto-deploy on push to `main`; no required reviewers.
> - `production` — required reviewer (or self-approval bypass for solo dev), deployment
>   restricted to `main`, environment secrets for the AWS OIDC role and Stripe keys.
>
> Until that spec lands, leave _Settings → Environments_ empty. Do not create placeholder
> environments — empty environments without policies are worse than none, because they give a
> false sense of gating.

No action required in Phase 1.

---

## 7. Post-setup smoke test — confirm gates actually block merge

> Source: task 10.1 step (7); Requirement 11.5 ("re-runnable" implies post-setup validation).
>
> Run this once after completing sections 1–5 and again after any branch-protection or CI
> change. The test deliberately fails CI on a throwaway branch and verifies that the failure
> propagates to the `required-checks` aggregator and that the `Protect main` ruleset then
> refuses to merge.

1. From an up-to-date local clone, create a throwaway branch off `main`:

   ```bash
   git checkout main
   git pull --ff-only
   git checkout -b phase-1/smoke-required-checks
   ```

2. Introduce a deliberate, obvious failure that one of the CI jobs will catch. Pick one — do
   not stack failures, since the goal is to confirm a single failure propagates correctly:
   - **Lint failure (fastest):** edit any file under `apps/api/src/matchlayer_api/` and add a
     line like `import os, sys` (multi-import on one line — `ruff` rejects this). The
     `backend` job's `ruff check` step fails.
   - **Test failure:** add `assert False, "smoke test"` to any pytest file under
     `apps/api/tests/`. The `backend` job's `pytest` step fails.
   - **Frontend failure:** add an unused variable in `apps/web/src/app/page.tsx`. The
     `frontend` job's `lint` or `typecheck` step fails.

3. Commit and push, then open a PR targeting `main`:

   ```bash
   git add -A
   git commit -m "chore: deliberate failure for required-checks smoke test"
   git push -u origin phase-1/smoke-required-checks
   gh pr create --base main --head phase-1/smoke-required-checks \
     --title "chore: smoke test (do not merge)" \
     --body "Deliberate failure to verify branch protection. Will be closed without merging."
   ```

   (Without the GitHub CLI, open the PR through the web UI.)

4. On the PR page, confirm **all three** of the following:
   1. The job you sabotaged shows ❌ in the **Checks** tab.
   2. The **`required-checks`** aggregator job shows ❌ with the message
      `One or more required checks failed.` and lists the failing job by name.
   3. The **Merge pull request** button is disabled with the text
      _"Required statuses must pass before merging"_, and the
      _"Required"_ label appears next to `required-checks` in the merge box.

   If any of the three is missing, the ruleset is misconfigured — return to section 1.

5. Tear down the throwaway branch without merging:

   ```bash
   gh pr close phase-1/smoke-required-checks --delete-branch
   git checkout main
   git branch -D phase-1/smoke-required-checks
   ```

   (Web UI equivalent: close the PR, then delete the remote branch from the PR page and run
   `git branch -D` locally.)

The repository is now configured. Re-run sections 1–5 in order on any future fork or transfer;
re-run section 7 after any change to `ci.yml`'s required jobs or to the `Protect main`
ruleset.

---

## 8. Troubleshooting — `ci.yml` doesn't trigger on the smoke PR

> Source: discovered while running section 7. Documenting because the failure mode is
> invisible from the standard _Settings → Actions → General_ surface.

If section 7 stalls because no GitHub Actions run ever appears on the smoke PR — `gh pr checks 1`
shows only the always-on apps (CodeQL default-setup, GitGuardian, etc.) and `gh run list --branch
phase-1/smoke-required-checks` is empty — the most likely cause is that **the `CI` workflow has
been disabled at the per-workflow level**.

GitHub silently puts a workflow into `disabled_manually` state when:

- An admin clicks **Disable workflow** on the workflow's page in the **Actions** tab (the
  three-dot ⋯ menu). This is a per-workflow toggle separate from _Settings → Actions →
  General_'s "Allow all actions and reusable workflows" radio buttons. The repo-level setting
  can be `enabled` while a specific workflow is `disabled_manually`.
- A workflow file is detected as having syntax that GitHub considers structurally invalid (rare).
- A scheduled workflow has not run for 60 days on a public repository (does not apply to
  `pull_request`/`push`-triggered workflows like ours, but worth knowing).

The disable does **not** show up in:

- _Settings → Actions → General_ (which only governs the global enabled/allowed-actions policy,
  not per-workflow state).
- The Actions tab list of workflows in the left sidebar (the workflow still appears, just
  without a "Run" badge).

It is visible at:

- _Actions tab_ → click the workflow name → if disabled, a yellow banner reads "This workflow
  was disabled manually. To re-enable it, click Enable workflow."
- API: `gh api /repos/<owner>/<repo>/actions/workflows/ci.yml --jq '.state'` returns
  `disabled_manually` instead of `active`.

### Diagnosis

Run, in order:

```bash
# 1. Is the workflow file present on the smoke branch?
git show phase-1/smoke-required-checks:.github/workflows/ci.yml | head -50
# Expected: identical (or near-identical) to main's ci.yml.

# 2. Has GitHub recorded any workflow_run for the smoke branch?
gh api "/repos/<owner>/<repo>/actions/runs?branch=phase-1/smoke-required-checks&per_page=20" \
  --jq '.workflow_runs | length'
# Expected: > 0 once CI has triggered. If 0 here but > 0 on main, the gate is repo-wide
# and the next query confirms it.

# 3. What state is the CI workflow in?
gh api "/repos/<owner>/<repo>/actions/workflows/ci.yml" --jq '{name, path, state}'
# Smoking gun: state == "disabled_manually".
```

### Fix

Re-enable the workflow, then push an empty commit on the smoke branch to retrigger it (the
disable does not retroactively trigger missed runs):

```bash
gh api -X PUT "/repos/<owner>/<repo>/actions/workflows/ci.yml/enable"
gh api "/repos/<owner>/<repo>/actions/workflows/ci.yml" --jq '.state'  # should be "active"

git checkout phase-1/smoke-required-checks
git commit --allow-empty -m "chore: retrigger CI for smoke test verification"
git push
```

Within a minute, `gh api "/repos/<owner>/<repo>/actions/runs?branch=phase-1/smoke-required-checks"`
should report a new run, and section 7 step 4 can resume.

The web-UI equivalent is _Actions tab → CI → Enable workflow_ on the yellow banner.

### Why this is documented as a solo-dev gotcha

In a multi-person repo, someone else disabling a workflow leaves a UI breadcrumb (the banner)
that a reviewer would notice on the next push. Solo dev workflows hit this trap when the
disable was done weeks earlier — for example to mute a noisy CI run during heavy local work —
and was forgotten by the time the next PR opens. The smoke test is exactly when that becomes
visible, because branch protection requires a check that the disabled workflow would have
produced.
