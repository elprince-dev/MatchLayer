# Contributing flow — branch, commit, PR, merge

> Day-to-day flow for landing a change on `main`. Mirrors the conventions in
> `.kiro/steering/conventions.md` ("Git & commits") and the active `Protect main`
> ruleset documented in `docs/runbooks/repo-setup.md` §1.
>
> Anchors:
>
> - `.kiro/steering/conventions.md` → "Git & commits"
> - `docs/runbooks/repo-setup.md` → §1 (the `Protect main` ruleset that gates merges)
> - `.kiro/steering/security.md` → "Secrets management" (gitleaks pre-commit hook)

## TL;DR

1. Branch off `main`.
2. Edit, run local checks.
3. `git add` → `git commit` (Conventional Commits).
4. `git push -u origin <branch>` on first push.
5. `gh pr create --base main`.
6. `gh pr checks --watch` until green.
7. `gh pr merge --squash --delete-branch`.
8. Sync local `main`.

## The flow in detail

### 0. Start from a clean, up-to-date `main`

```bash
cd /home/hhumoham/Development/code/MatchLayer
git checkout main
git pull --ff-only origin main
git status --porcelain   # must be empty before you start
```

### 1. Branch off `main` using the convention

```bash
git checkout -b phase-1/your-short-description
# or for bug fixes:
# git checkout -b fix/your-short-description
```

Branch naming (per `conventions.md`):

- Feature work: `phase-N/short-description` (kebab-case).
- Bug fixes: `fix/short-description`.
- Chores/docs/CI: any of `chore/`, `docs/`, `ci/` followed by a short kebab description.

### 2. Make your changes and run local checks

Useful gates before committing — these are the same checks CI runs, so failing
them locally saves a CI round-trip.

```bash
# Backend
cd apps/api
uv run ruff format --check
uv run ruff check
uv run mypy src
uv run pytest
cd -

# Frontend
pnpm --filter @matchlayer/web lint
pnpm --filter @matchlayer/web typecheck
pnpm --filter @matchlayer/web test       # requires the web app to be running locally

# Shared types — run only if you touched anything affecting the FastAPI surface
pnpm codegen
git diff packages/shared-types/src/   # if non-empty, commit the regenerated files
```

### 3. Stage

```bash
git add <specific files>          # preferred — pick what you want
# or
git add -A                        # everything tracked + new files

git status                        # always inspect what's staged
```

### 4. Commit (Conventional Commits)

```bash
git commit -m "feat(scope): short summary in lowercase"
```

Format: `type(scope): summary`

- **type** — one of: `feat`, `fix`, `chore`, `docs`, `refactor`, `test`, `ci`,
  `build`, `perf`, `style`, `revert`.
- **scope** — pick the most specific area: `api`, `web`, `shared-types`,
  `infra`, `docs`, `runbook`, `ci`, `deps`. Optional but encouraged.
- **summary** — imperative mood ("add", "fix", "rename"), no trailing period,
  ≤ 72 chars.

Multi-line message when you need a body:

```bash
git commit -m "feat(api): add /matches endpoint" -m "" -m "Implements semantic-similarity scoring against the resume + JD pair." -m "Refs requirements 4.x in phase-1-matching."
```

**Pre-commit hooks run here** — gitleaks, ruff format, prettier, file hygiene.
If a hook auto-fixes something the commit aborts so you can review. Stage the
fixes and re-run `git commit`.

If you genuinely need to skip hooks (rare — e.g., deliberate-fail smoke test):

```bash
git commit --no-verify -m "..."
```

### 5. Push to a new remote branch

```bash
git push -u origin phase-1/your-short-description
```

The `-u` is only needed on the **first** push from a new branch — it sets the
upstream tracking ref so subsequent pushes/pulls don't need arguments.

### 6. Open the PR via `gh`

```bash
gh pr create --base main --head phase-1/your-short-description \
  --title "feat(scope): short summary" \
  --body "$(cat <<'EOF'
## What
Short description of the change.

## Why
Motivation: bug, requirement, refactor goal.

## Testing
- ran X
- verified Y
EOF
)"
```

PR title follows the same Conventional Commits format as the commit (the squash
merge message defaults to the PR title).

### 7. Watch CI

```bash
gh pr checks --watch --interval 10
# or open in browser:
gh pr view --web
```

The six required checks the `Protect main` ruleset gates on are: `backend`,
`frontend`, `shared-types`, `security`, `openapi-drift`, and the
`required-checks` aggregator. All six must be green.

If a check fails, fix locally, `git add` → `git commit` → `git push` to the
**same branch**. CI re-runs automatically.

### 8. Merge

Squash-merge to keep `main`'s history linear (the ruleset rejects merge
commits):

```bash
gh pr merge --squash --delete-branch
```

If `gh pr merge` reports a non-`CLEAN` `mergeStateStatus`, do not force the
merge — diagnose what's blocking it (failing check, base out of date, etc.).

### 9. Sync local `main`

```bash
git checkout main
git pull --ff-only origin main
git branch -D phase-1/your-short-description 2>/dev/null
git fetch --prune origin
```

## Rules to remember

- **Never push directly to `main`.** The ruleset rejects it. Always go through a
  PR.
- **Always `--squash`** when merging. Linear history is required.
- **Never `git push --force`** to a shared branch. The ruleset blocks it on
  `main`. On your own feature branch use `--force-with-lease` if you must
  rewrite history before merge.
- **Don't `git commit --no-verify`** unless you have a documented reason. The
  hooks exist to catch secrets, formatting drift, and lint regressions before
  they hit CI.
- **Don't merge your own PR without all six required checks green.** The
  ruleset enforces this; trying to merge anyway just wastes a few seconds of
  your time.

## Common gotchas

### "Updates were rejected because the remote contains work that you do not have"

Someone (or a tool) pushed to your branch since your last fetch.

```bash
git pull --rebase origin <your-branch>
# resolve conflicts if any
git push origin <your-branch>
```

### PR shows a red `required-checks` but no failing job in the list

Usually a cancelled superseded run from a force-push or rebase. Push an empty
commit to retrigger:

```bash
git commit --allow-empty -m "chore: retrigger CI"
git push
```

### `pnpm codegen` produces a diff

You changed the FastAPI app's surface. Commit the regenerated
`packages/shared-types/src/*.ts` files in the same PR — the `openapi-drift` job
will fail otherwise.

### `security` job fails on a `pip-audit` advisory you didn't introduce

A new advisory landed against a transitive dep since the last lockfile update
(this has happened with `starlette` already). Fix:

```bash
# 1. Identify the offending package and a fixed version range from the
#    pip-audit output (e.g., starlette >= 1.0.1).
# 2. Bump the parent dep in apps/api/pyproject.toml so its cone allows the
#    fixed version (preferred), or pin the transitive dep directly.
# 3. Regenerate the lockfile:
cd apps/api
uv lock
uv sync --frozen
# 4. Verify locally:
uv export --no-dev --no-emit-project --format requirements-txt > /tmp/req.txt
uv tool run pip-audit --strict -r /tmp/req.txt   # must say "No known vulnerabilities found"
# 5. Commit pyproject.toml AND uv.lock together.
```

### Pre-commit hook keeps "fixing" your file in a loop

Run the hook standalone to see what it's doing:

```bash
pre-commit run --all-files
```

Then `git add` the result and commit. If a hook is fighting you, the formatter
is right and your local edit is wrong — let the formatter win.

### "I forgot to branch off `main` and committed straight to `main`"

If you haven't pushed yet:

```bash
git branch phase-1/oops-recovery   # save your work on a new branch
git reset --hard origin/main       # rewind local main
git checkout phase-1/oops-recovery # back to your work, now on the right branch
```

If you already pushed to `main`, the ruleset should have rejected it. If it
somehow didn't, revert the commit on `main` via a PR — never `git push --force`
on `main`.

## Auth-specific gotchas (Phase 1)

The phase-1-auth landing surfaced two extra traps worth flagging on top of the
generic flow above. Both are documented behavior, not bugs.

### Auth_Router signature changes require a fresh `pnpm codegen`

The general `pnpm codegen` gotcha above applies to every router on the FastAPI
surface, but the auth router is the most-touched one in early phases and the
one whose drift is most user-visible (login/register/refresh contracts feed
directly into `apps/web/src/lib/api.ts` and the React Hook Form + Zod
resolvers). Any time you touch a Pydantic model or path under
`apps/api/src/matchlayer_api/api/auth/`, re-run codegen and inspect the diff:

```bash
pnpm codegen
git diff packages/shared-types/src/   # commit the regenerated TS + Zod output
```

If you forget, the `openapi-drift` CI job fails on the PR. Fix locally, push
the regenerated files in the same branch, and the job retriggers automatically.

### `localhost` and the auth cookies' `Secure`-flag carve-out

`matchlayer_refresh` and `matchlayer_csrf` are emitted with `Secure` set in
every environment **except** `MATCHLAYER_ENVIRONMENT=development`, where the
flag is dropped so the browser keeps the cookies on plain `http://localhost`
(see design §9.2). Two consequences worth knowing about:

- If your local `.env` has `MATCHLAYER_ENVIRONMENT` unset or set to anything
  other than `development`, the browser will silently drop the refresh cookie
  on `http://localhost:3000`, login will appear to succeed, and the silent
  refresh on `lib/api.ts` will then 401. Symptom: "I just signed in but the
  next request says I'm logged out." Fix: `MATCHLAYER_ENVIRONMENT=development`
  in `.env`.
- The carve-out is `development`-only on purpose. Do not extend it to
  `staging`/`production`/CI. The cookie-emission helpers in
  `core/security/cookies.py` are the single source of truth for the attribute
  set; if you find yourself reaching for an additional carve-out, change the
  helper rather than the call site, and update §9.2 of the design doc to keep
  the table honest.
