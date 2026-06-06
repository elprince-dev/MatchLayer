# The OpenAPI drift check in continuous integration

## Introduction

This document explains the automated guard that fails the build whenever the
project's committed client code no longer matches the live description of its
Application Programming Interface (API), where an API is the set of endpoints
one program exposes for another to call. The description format is OpenAPI — an
open, language-neutral specification for describing an API: every path, the
shape of each request and response, and the data types involved. That
description is produced from the running code and then used to generate matching
client code; the generated code is committed to the repository so the front end
can build against a known version. The check this document covers runs inside
Continuous Integration (CI) — the practice of merging every change into a shared
branch and automatically building and testing it on a server rather than only on
a developer's laptop. The check re-runs the generation pipeline and fails if the
freshly generated files differ from the committed ones. This topic sits in the
Contracts and codegen track because it is the safety net that keeps generated
artifacts honest.

**Learning outcomes** — after reading this document you will be able to:

- Explain what "drift" means between a generated API contract and the client code derived from it.
- Describe how a drift check regenerates artifacts in a clean environment and compares them against the committed copies.
- Explain why this check exists and what class of mistake it catches before it reaches a reviewer.
- Recognise the common mistakes that break or defeat a drift check and recover from them.

Prerequisites: this document builds on
[the OpenAPI dump command-line interface](03-backend-10-openapi-dump-cli.md), which explains
how the framework produces the contract that this check regenerates and
compares.

## Problem it solves

When a project generates client code from a machine-readable description of its
own API, that generated code is stored in the repository so other tools can
build against it without re-running the generator every time. The concrete
problem is keeping the stored copy honest. A developer changes an endpoint on
the server — adds a field, renames a parameter, changes a type — and forgets to
regenerate and commit the matching client code. The repository now holds two
descriptions of the same API that disagree, and nothing flags it. The mismatch
surfaces later as a confusing runtime failure in a caller that was built against
the stale copy.

The prior approach was discipline: every contributor was expected to remember to
regenerate the client code after touching the server and to include those
regenerated files in the same change. That expectation is fragile. Under
deadlines, large changes, or many contributors, the regenerate-and-commit step
is skipped, and code review rarely catches a missing regeneration because the
reviewer sees only what was committed, not what should have been.

A drift check removes the reliance on memory. It regenerates the client code
from the current server in an automated environment and compares the result
against what is committed. If they differ, the build fails with a message that
names the fix. The contract and its derived code are forced back into agreement
before the change can merge, so the stale-copy failure mode is caught at the
earliest possible point.

## Mental model

Think of an original document and its official translation that must always say
the same thing. The original is the API description generated from the running
code; the translation is the client code generated from that description. The
drift check is an auditor who ignores the translation on file, re-translates the
current original from scratch, and lays the two side by side. If a single line
differs, someone edited the original without updating the translation, and the
auditor refuses to sign off.

When the check runs, it performs these steps:

1. Rebuild the API description from the current server code in a fresh, isolated environment, with no leftover state from a developer's machine.
2. Re-run the generators against that description to produce the client code as it should look right now.
3. Compare the freshly generated files against the copies committed in the repository, line by line.
4. If every file is identical, the check passes; if any file differs, the check fails and prints the exact command the developer must run to fix it.

Because the description is rebuilt from the current code every run, the
comparison always reflects the real state of the server rather than a cached
snapshot.

## How it works

A drift check is built from three ingredients: a single source of truth, a
deterministic generation step, and an exact comparison. The source of truth is
the description generated from the running code, not a hand-maintained file. The
generation step turns that description into derived artifacts — typically typed
client code and validation schemas — and writes them to a known location. The
comparison asserts that re-running the generation step produces output identical
to what is already stored.

The check runs in an isolated environment that starts from the committed state
of the repository and installs dependencies from locked versions, so the result
depends only on the inputs and not on whatever happened to be on a contributor's
machine. It then regenerates the derived artifacts and asks a simple question:
did anything change? A version-control system answers this precisely. The
command that compares the working tree against the committed files returns a
success status when there is no difference and a failure status when there is,
and a build step that propagates that status turns "files changed" into "build
failed".

Two properties make the comparison trustworthy. First, the generation must be
deterministic: the same description must always produce byte-for-byte identical
output. If the description were serialised to a text format such as JavaScript
Object Notation (JSON) with its keys reordered on each run, the comparison would
fail even when nothing meaningful changed, producing noise that trains everyone
to ignore the check. Preserving a stable ordering keeps regeneration repeatable.
Second, the regeneration must read the live description rather than a stored copy
of it; reading a cached description would let the very drift the check exists to
catch slip through, because the cache could itself be stale.

The payoff is that a missing regeneration can no longer merge silently. The
moment the committed artifacts disagree with what the current code would
produce, the comparison fails, and the contributor is told to regenerate and
commit. The check converts an easy-to-forget manual step into an enforced gate.

## MatchLayer Phase 1 usage

In MatchLayer the drift check is a dedicated CI job named `openapi-drift`,
defined in `.github/workflows/ci.yml`. A CI job is a named, independently
runnable unit of work in the build pipeline. This job re-runs the contract
generation pipeline and asserts that nothing under the committed shared-types
source changed as a result.

The job first seeds a `.env` file from `.env.example`, because building the
application to read its contract validates typed settings at import time. It then
runs `pnpm codegen`, which invokes the orchestrator at
`packages/shared-types/scripts/codegen.mjs`. That orchestrator shells out to the
dump command at `apps/api/src/matchlayer_api/tools/dump_openapi.py` to obtain the
live OpenAPI description, then feeds it to the TypeScript and Zod generators,
rewriting the generated files. Finally the job compares the working tree against
the committed copies and fails if they differ:

Source: `.github/workflows/ci.yml`

```yaml
openapi-drift:
  name: openapi-drift
  runs-on: ubuntu-latest
  steps:
    - name: Seed .env from .env.example
      run: cp .env.example .env

    - name: Regenerate shared-types from live OpenAPI
      run: pnpm codegen

    - name: Assert no codegen drift
      run: |
        git diff --exit-code packages/shared-types/src/ || {
          echo "::error::OpenAPI codegen drift detected. Run 'pnpm codegen' locally and commit the result."
          exit 1
        }
```

The comparison command returns a non-zero status on any difference under the
`packages/shared-types/src/` directory, and the wrapper turns that into a build
failure with a message naming the remediation. The generated outputs include the
curated re-export surface in `packages/shared-types/src/index.ts`, so a change to
any endpoint that is not accompanied by a regeneration of those files is caught
here. Because `app.openapi()` is a pure traversal of the registered routes, this
job needs neither a database nor any other running service — only the populated
`.env` so configuration validates at import time.

## Common pitfalls

- **Mistake:** Editing the generated client files by hand instead of changing the server and regenerating.
  **Symptom:** The drift job fails because the next regeneration overwrites the hand edits, so the committed files never match what the generator produces.
  **Recovery:** Treat the generated files as build output: make the change on the server, run the generation command locally, and commit the regenerated result rather than editing it directly.

- **Mistake:** Changing an endpoint on the server and committing without regenerating the client code.
  **Symptom:** The `openapi-drift` job fails with the "codegen drift detected" error even though the change looks complete locally.
  **Recovery:** Run the project's generation command, confirm the regenerated files are part of the change, and commit them in the same pull request as the server change.

- **Mistake:** Making the generation non-deterministic, for example by serialising the description with its keys reordered each run.
  **Symptom:** The drift job fails intermittently with diffs that reorder lines but change no real content, and re-running it sometimes passes.
  **Recovery:** Pin the generation to a stable ordering so the same description always yields byte-for-byte identical output, then regenerate and commit once to establish the canonical files.

- **Mistake:** Pointing the regeneration at a cached or committed copy of the description instead of rebuilding it from the live code.
  **Symptom:** The job passes even after a real endpoint change, and the stale client code reaches consumers, surfacing as runtime mismatches.
  **Recovery:** Have the generation rebuild the description from the current application on every run, and treat any stored description as a disposable artifact, never as the source the check reads.

## External reading

- [GitHub Actions: about workflows](https://docs.github.com/en/actions/writing-workflows/about-workflows)
- [FastAPI: extending OpenAPI](https://fastapi.tiangolo.com/how-to/extending-openapi/)
- [FastAPI: first steps and the generated docs](https://fastapi.tiangolo.com/tutorial/first-steps/)
- [pnpm: running scripts with `pnpm run`](https://pnpm.io/cli/run)
