# The codegen orchestrator script and execa

## Introduction

This document explains the small program that runs several code-generation
tools in a fixed order, and the helper library it leans on to launch those
tools safely. The shorthand for producing source files from another source of
truth is code generation (codegen), and an orchestrator script is a single
program whose only job is to call other programs in sequence, hand the output
of one to the next, and stop the whole run the moment any step fails. The other
source of truth here is an OpenAPI document, where OpenAPI is an open
specification for describing a web Application Programming Interface (API) — the
set of endpoints one program exposes for another to call — written as
JavaScript Object Notation (JSON), a plain-text format that stores data as
key/value pairs. The library the orchestrator uses to launch each tool is
execa, a Node.js package for running a child process (a separate program the
script starts and waits on) with safer defaults than the language's built-in
process tools. This topic sits in the Contracts and codegen track because the
orchestrator is the engine that turns the API contract into matching
TypeScript and runtime-validation code.

**Learning outcomes** — after reading this document you will be able to:

- Explain what an orchestrator script is and why running a fixed sequence of tools belongs in one program rather than in scattered manual commands.
- Describe what execa does and why launching a child process with an explicit argument list is safer than building a single shell command string.
- Explain how an orchestrator captures one tool's standard output, feeds it to the next tool, and stops the run when any step exits non-zero.
- Recognise the common mistakes when writing a codegen orchestrator and recover from them.

Prerequisites: this document builds on
[The OpenAPI dump command-line interface](03-backend-10-openapi-dump-cli.md), which explains
the command whose output the orchestrator captures as its first step, and on
[pnpm and pnpm workspaces](01-foundations-02-pnpm-and-workspaces.md), which explains the package
manager the orchestrator uses to locate and run the generator tools.

## Problem it solves

Turning an API contract into matching client code is never a single command. It
takes a fixed pipeline: first print the current contract, then run one tool to
emit type definitions, run a second tool to emit runtime validators, and finally
clean up the temporary file in between. Each step depends on the previous one
succeeding, and the second and third steps both read a file the first step
produced. The concrete problem is running that pipeline the same way on every
machine — a laptop, a teammate's laptop, a continuous-integration runner — with
no step silently skipped and no broken output left behind when something fails.

The prior approach was a hand-written shell script that strung the tools
together with pipes and output redirection, or, worse, a list of commands a
developer was expected to run by hand in the right order. That approach is
fragile for several reasons:

- A shell script written for a Unix-like shell does not run the same way on a Windows shell, so the pipeline breaks for some contributors.
- Redirection and quoting rules differ between shells, and a value interpolated into a command string can be misread as extra commands — a real injection risk when any argument is not a fixed literal.
- Stopping on the first failure requires careful, easy-to-forget shell options, so a broken early step can let later steps run against stale or empty input.

Moving the pipeline into one orchestrator script written in a general-purpose
language removes that fragility: the sequence, the failure handling, and the
cleanup all live in code that behaves the same everywhere, and a single command
runs the whole thing.

## Mental model

Think of a kitchen expediter who calls each station in turn during a dinner
service. The expediter tells the grill to start, waits until the plate is ready,
carries it to the next station, and only then calls that station to begin. If
any station reports a problem, the expediter halts the order rather than sending
out a half-finished dish. Nobody station talks to another directly; the
expediter owns the order and the timing.

When the orchestrator runs, it performs these steps in order:

1. Launch the contract-printing command as a child process and capture everything it writes to its standard output (the default text stream a program writes to).
2. Save that captured text to a temporary file so the next tools can each open it as an ordinary file.
3. Launch the first generator as a child process, pointing it at the temporary file, and wait for it to finish successfully.
4. Launch the second generator the same way, again waiting for a successful finish.
5. Delete the temporary file only after every previous step has succeeded; if any step exits non-zero, stop immediately and leave the file in place for inspection.

Because each step waits for the one before it and any failure halts the run, the
generated files are either fully refreshed together or not touched at all.

## How it works

An orchestrator script is an ordinary program in a general-purpose language whose
body is a list of "run this other program, then run the next" instructions.
Each external program it launches is a child process: the script starts it,
passes it arguments, waits for it to exit, and inspects how it exited. The
language's standard library already offers a way to start child processes, but
its lowest-level interface is verbose and its defaults are awkward, so most
scripts reach for a small wrapper library that adds a friendlier, promise-based
interface on top.

That wrapper is the key idea. A process-runner library lets the script start a
program and `await` its completion as if it were any other asynchronous step.
The single most important safety property is how the command and its arguments
are passed. Instead of building one long command string and handing it to a
shell to re-parse, the script passes the program name as one value and its
arguments as a separate list of values. No shell is involved by default, so
there is nothing to re-interpret special characters, and an argument that
happens to contain spaces, quotes, or other punctuation cannot be misread as a
second command. Removing the shell removes the whole class of command-injection
bugs that string-built commands invite.

The library's defaults also make failure handling automatic. When a launched
program exits with a non-zero status — the conventional signal that something
went wrong — the `await` throws an error instead of returning quietly. An
unhandled error stops the script, which means a failed early step cannot let a
later step run against missing or stale input. The script does not have to check
each exit code by hand.

Output handling is the third piece. A child process writes to two streams:
standard output for its real result and standard error for diagnostics. A
process runner can either capture a stream into a value the script can use, or
let it pass straight through to the terminal the developer is watching. A
common pattern is to capture the first tool's standard output into a variable
and then write that text to a temporary file, rather than using shell
redirection. Capturing in code keeps the behaviour identical on every operating
system and keeps the destination under the script's control. Meanwhile the
script can let standard error pass through so any diagnostics from a failing tool
appear live in the terminal.

Two smaller concerns round out a robust orchestrator. First, it should not
depend on which directory the developer happened to run it from: a script can
locate its own file on disk and resolve every relative path against its own
folder, so it behaves identically whether invoked from the repository root or
from its own subfolder. Second, it should run the exact tool versions the
project pins rather than whatever happens to be installed globally; launching
each generator through the package manager's own runner resolves the pinned,
locally installed version reliably, even when the package layout does not place
tool binaries where a global lookup would expect them.

## MatchLayer Phase 1 usage

In MatchLayer the orchestrator is the script
`packages/shared-types/scripts/codegen.mjs`. It is wired into its package's
manifest, `packages/shared-types/package.json`, as a single named script so a
developer runs the whole pipeline with one command:

Source: `packages/shared-types/package.json`

```json
  "scripts": {
    "codegen": "node ./scripts/codegen.mjs"
  },
```

The same entry is mirrored at the repository root so the pipeline can be run from
anywhere in the workspace (the set of packages the package manager links
together):

Source: `package.json`

```json
    "codegen": "node packages/shared-types/scripts/codegen.mjs",
```

The script depends on execa, declared in the package's dev dependencies:

Source: `packages/shared-types/package.json`

```json
    "execa": "^9.5.2",
```

It imports the runner at the top of the file:

Source: `packages/shared-types/scripts/codegen.mjs`

```javascript
import { execa } from "execa";
```

Step one launches the contract-printing command as a child process. Notice the
program name (`uv`) is one value and every argument is a separate entry in the
list, so no shell parses the command and there is no injection surface. The call
captures standard output by default and inherits standard error so any failure
from the launched process shows live in the terminal:

Source: `packages/shared-types/scripts/codegen.mjs`

```javascript
const dumpResult = await execa(
  "uv",
  [
    "run",
    "--project",
    "../../apps/api",
    "--env-file",
    "../../.env",
    "python",
    "-m",
    "matchlayer_api.tools.dump_openapi",
  ],
  {
    cwd: packageRoot,
    stderr: "inherit",
  },
);
```

The captured text is then written to a temporary file in code rather than with
shell redirection, which keeps the behaviour identical on every operating system:

Source: `packages/shared-types/scripts/codegen.mjs`

```javascript
writeFileSync(openapiJsonPath, dumpResult.stdout, "utf8");
```

Because each `await execa(...)` throws when the launched tool exits non-zero, a
failure in any step stops the script before the cleanup runs, so the temporary
file is left behind for debugging and the generated outputs
`packages/shared-types/src/api-types.ts` and
`packages/shared-types/src/api-schemas.ts` are never overwritten with partial
results. The `cwd: packageRoot` option makes every relative path resolve against
the package folder, so `pnpm codegen` behaves the same whether it is invoked from
the repository root or from inside the package.

## Common pitfalls

- **Mistake:** Building one command string and running it through a shell (for example, enabling a shell option or interpolating a value into the command text) instead of passing a program name and an explicit argument list.
  **Symptom:** The pipeline behaves differently on a Windows shell than on a Unix-like shell, and an argument containing a space or quote is misread, producing a confusing error or running something unintended.
  **Recovery:** Pass the program as one value and its arguments as a separate list so no shell re-parses the command; keep every argument a discrete list entry rather than concatenated text.

- **Mistake:** Swallowing a launched tool's non-zero exit — wrapping the call so the error is caught and ignored, or never awaiting it.
  **Symptom:** The orchestrator reports success while the generated files are stale, empty, or half-written, and the mismatch only surfaces much later as a broken build.
  **Recovery:** Let the error from a non-zero exit propagate so the script stops at the failing step; await every child process and do not catch-and-ignore its failure.

- **Mistake:** Using shell redirection to send a tool's output to a file instead of capturing the output in code and writing the file from the script.
  **Symptom:** The redirect works on one machine and fails or produces a differently encoded file on another, breaking a downstream byte-for-byte comparison.
  **Recovery:** Capture the child process's standard output into a variable and write the file from the script, keeping the destination and encoding under the script's control.

- **Mistake:** Relying on the directory the developer happened to run the script from, so relative paths point at the caller's location.
  **Symptom:** The pipeline works when run from one folder and fails to find its inputs when run from another, such as the repository root versus the package folder.
  **Recovery:** Have the script locate its own file and set each child process's working directory to a fixed, script-relative folder so every relative path resolves the same way regardless of where the command was invoked.

## External reading

- [Node.js documentation: the child_process module](https://nodejs.org/api/child_process.html)
- [Node.js documentation: import.meta and ECMAScript modules](https://nodejs.org/api/esm.html)
- [execa documentation (sindresorhus/execa)](https://github.com/sindresorhus/execa)
- [pnpm documentation: pnpm exec](https://pnpm.io/cli/exec)
- [Mozilla Developer Network: using promises and async/await](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Guide/Using_promises)
