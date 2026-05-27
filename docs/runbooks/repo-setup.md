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

## Prerequisites

- Admin (or "Maintain") access to the repository on GitHub.
- The default branch is `main` and CI has run at least once (so the `required-checks` aggregator
  job appears in the list of selectable status checks).

---

## 1. Branch protection on `main`

> Source: Requirement 11.2; Design §13 step 1.
>
> _Settings → Branches → Branch protection rules → Add branch protection rule_ (or _Edit_ on
> the existing rule for `main`).

1. Set **Branch name pattern** to `main`.
2. Enable **Require a pull request before merging**.
   1. **Required approvals**: `1`.
      (Solo-developer note: GitHub does not allow a PR author to approve their own PR. If you
      operate solo without a second account, lower this to `0` and rely on the rest of this
      rule — required status checks plus linear history — to gate merges. Restore to `1` as
      soon as a second collaborator joins.)
   2. Enable **Dismiss stale pull request approvals when new commits are pushed**.
3. Enable **Require status checks to pass before merging**.
   1. Enable **Require branches to be up to date before merging**.
   2. Add the single required check: **`required-checks`**.
      This is the aggregator job defined at the bottom of `.github/workflows/ci.yml`; it
      `needs:` every other job (`backend`, `frontend`, `shared-types`, `security`,
      `openapi-drift`) and reports a single deterministic status, so branch protection only
      ever needs to track one check name even as individual jobs are added or renamed.
4. Enable **Require conversation resolution before merging**.
5. Enable **Require linear history**.
   (Forces squash- or rebase-merge; rejects merge commits. Keeps `main`'s history reviewable.)
6. Enable **Restrict who can push to matching branches** and leave the actor list empty.
   (Combined with the PR requirement above, this enforces "never push directly to `main`" from
   `conventions.md`.)
7. Under **Rules applied to everyone including administrators**, enable **Do not allow bypassing
   the above settings**.
   (Prevents accidental admin-button merges that skip CI.)
8. Confirm both **Allow force pushes** and **Allow deletions** are **disabled**.
9. Click **Save changes**.

**Verification**: open _Settings → Branches_ and confirm the rule for `main` shows "1
approval", "Require status checks", "`required-checks`" listed, "Require linear history", and
no force-push / deletion toggles.

---

## 2. Secret Scanning and Push Protection

> Source: Requirement 11.3; Design §13 steps 2–3; `security.md` "Secrets management".
>
> _Settings → Code security_ (formerly _Code security and analysis_).

1. Under **Secret scanning**, click **Enable**.
2. Under **Secret scanning → Push protection**, click **Enable**.
   (Push protection blocks `git push` on the server side when GitHub detects a high-confidence
   secret pattern in the pushed commits — defense in depth on top of the local `gitleaks`
   pre-commit hook.)
3. (Optional, recommended) Under **Secret scanning → Validity checks**, enable **Automatically
   verify if a secret is valid by sending it to the relevant partner**.

**Verification**: navigate to _Security → Secret scanning alerts_; the page should load
without a "feature not enabled" banner. Push protection violations surface as a `remote: error`
on `git push` — covered by the smoke test in section 7.

---

## 3. Dependabot security updates

> Source: Requirement 11.3; Design §13 step 4; `security.md` "Dependency & supply-chain
> security".
>
> _Settings → Code security_.

1. Confirm `.github/dependabot.yml` is committed on `main`. The committed file pins
   `open-pull-requests-limit: 0` for `npm`, `pip`, and `github-actions` — this disables routine
   version-bump PRs while leaving security advisory PRs unaffected.
2. Under **Dependabot**:
   1. Enable **Dependabot alerts**.
   2. Enable **Dependabot security updates**.
   3. Leave **Dependabot version updates** governed by the committed
      `.github/dependabot.yml` (no UI toggle change needed — the file is the source of truth).
3. (Optional) Enable **Grouped security updates** under _Dependabot → Grouped updates_ if you
   prefer one PR per ecosystem per week over one PR per advisory.

**Verification**: _Security → Dependabot alerts_ loads without an "enable" banner; any open
advisories show a "Create Dependabot security update" button.

---

## 4. CodeQL default setup (Python and JavaScript/TypeScript)

> Source: Requirement 11.4; Design §13 step 5.
>
> _Settings → Code security → Code scanning → Set up_.
>
> The `security` job in `.github/workflows/ci.yml` already runs CodeQL on every PR via
> `github/codeql-action/init` + `analyze`. Enabling default setup at the repository level adds
> a redundant, GitHub-managed schedule and ensures contributors who fork the repo get scanning
> without having to inspect the workflow.

1. Click **Set up → Default**.
2. In the **Languages** list, confirm both **Python** and **JavaScript/TypeScript** are
   selected. If a language is missing because the default setup detector cannot see it, fall
   through to step 4 (advanced setup).
3. Under **Query suite**, choose **Default**.
4. Click **Enable CodeQL**.

**Fallback when default setup is unavailable** (per Requirement 11.4):

If the **Default** option is greyed out or fails to detect one of the languages — for example
on a fork where the language detector hasn't run yet, or for a language CodeQL no longer
auto-discovers — switch to **Advanced** setup. GitHub generates a `.github/workflows/codeql.yml`
on a branch and opens a PR; review and merge it, keeping the languages set to `python` and
`javascript-typescript`. This advanced workflow runs alongside the in-tree `security` job;
that's intentional — the redundancy is the point.

**Verification**: _Security → Code scanning_ shows the CodeQL configuration as **Active** for
both languages. The next push to `main` (or the next scheduled run) populates an alerts page.

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

> Source: task 10.1; Requirement 11.5 ("re-runnable" implies post-setup validation).
>
> Run this once after completing sections 1–5 and again after any branch-protection or CI
> change. The test deliberately fails CI on a throwaway branch and verifies that the failure
> propagates to the `required-checks` aggregator and that branch protection then refuses to
> merge.

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

   If any of the three is missing, branch protection is misconfigured — return to section 1.

5. Tear down the throwaway branch without merging:

   ```bash
   gh pr close phase-1/smoke-required-checks --delete-branch
   git checkout main
   git branch -D phase-1/smoke-required-checks
   ```

   (Web UI equivalent: close the PR, then delete the remote branch from the PR page and run
   `git branch -D` locally.)

The repository is now configured. Re-run sections 1–5 in order on any future fork or transfer;
re-run section 7 after any change to `ci.yml`'s required jobs or to the `main` branch protection
rule.
