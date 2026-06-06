# Language version pinning with .nvmrc and .python-version

## Introduction

This document explains how a project records the exact version of a language
runtime it expects, so that every person and every machine that builds the code
runs it on the same interpreter. A language runtime (the program that actually
executes your code — the Node.js engine for JavaScript and TypeScript, or the
Python interpreter for Python) ships in many versions over time, and small
differences between those versions can change how the same source code behaves.
Version pinning (writing the expected version into a small file that lives next
to the code) is the practice that removes that uncertainty. This document also
introduces the idea of a version manager (a command-line program that installs
several runtime versions side by side and switches between them on demand),
because the pin files are the signal a version manager reads.

**Learning outcomes** — after reading this document you will be able to:

- Explain what version pinning is and why a single recorded version protects a team from "works on my machine" failures.
- Describe how a version manager reads a pin file and selects the matching runtime.
- Recognise the two pin files used here and state what each one controls.
- Decide what to write inside each pin file and how precise that value should be.

Prerequisites: [Monorepo layout](01-foundations-01-monorepo-layout.md). That document explains the
single-repository structure these pin files sit at the top of; this document
assumes you know the repository has one root where shared configuration lives.

## Problem it solves

The concrete problem is drift between environments. A language runtime is not one
fixed thing: Node.js and Python each release new major versions every year, and
those releases add, change, and occasionally remove behaviour. When one
contributor builds on one version and a teammate builds on another, the same
source code can pass on one laptop and fail on the other — the classic "but it
works on my machine" report that wastes hours to diagnose.

The common prior approach was an informal one: a sentence in a setup guide that
said "install Node.js 24 and Python 3.13", or nothing at all. That approach has
three weaknesses:

- A human has to read the instruction, and humans skip setup steps or install whatever version they already had.
- The instruction lives apart from the code, so it drifts out of date when the project moves to a newer runtime and nobody updates the prose.
- Nothing checks the instruction was followed, so a wrong version is discovered only later, as a confusing failure rather than a clear message.

Version pinning replaces the prose with a small machine-readable file committed
beside the code. The recorded version travels with the repository, updates in the
same change that bumps the runtime, and can be read automatically by a version
manager instead of relying on a person to remember.

## Mental model

Think of a pin file as the dress-code card posted on the door of a room. Anyone
walking in reads the card and changes into the right outfit before they start;
nobody has to ask the host, and everyone in the room ends up dressed the same.
The card does not own any clothes — a separate wardrobe does that — it only
states what to wear.

To see how the card is used, walk through what happens when a developer sits down
to work:

1. The developer moves into the project directory on their machine.
2. A version manager notices the pin file sitting in that directory and reads the single version string inside it.
3. The version manager checks whether that exact runtime version is already installed on the machine.
4. If it is missing, the version manager installs that version; if it is present, the version manager selects it for the current shell.
5. From that point on, the language commands in that shell run against the pinned version, so the developer's environment matches everyone else's without any manual choice.

That five-step sequence is the whole idea. The rest of this document explains the
file formats behind step 2 and the limits behind step 4.

## How it works

A version pin file is a plain-text file with a conventional name that holds one
short version string and nothing else. Two long-standing conventions cover the
two runtimes in this stack.

For the Node.js runtime, the conventional file is named `.nvmrc`. The leading dot
marks it as a configuration file, and the name comes from the version manager
that first read it. Its entire contents are a single version identifier — often
a whole major version like `24`, sometimes a more precise `24.3.0`. A version
manager reads that line and resolves it to an installed runtime, treating a bare
major number as "the newest installed release in that major line".

For the Python runtime, the conventional file is named `.python-version`. It
works the same way: one line, one version string, such as `3.13`. Python version
managers read this file when you enter the directory and switch the active
interpreter to the version it names, installing it first if necessary.

The key design choice in both formats is precision versus flexibility. A pin can
be:

- **Coarse** (a major version such as `24` or `3.13`). This guarantees the major line — where the largest behaviour changes happen — while letting each machine use the latest patch release it already has. Patch releases are meant to be backward compatible, so this is a common, low-friction choice.
- **Exact** (a full `24.3.0`). This guarantees byte-for-byte the same runtime everywhere, at the cost of forcing every machine to install that precise build.

Neither file installs anything by itself. Each is only a declaration; a separate
version manager is the program that reads the declaration and does the install or
switch. That separation is deliberate — the same pin file works no matter which
version manager a given developer prefers, because they all agree on the file
name and the one-line format. A build system or a continuous-integration step can
read the very same file to provision the matching runtime, so the recorded
version becomes the single source of truth for every environment at once.

## MatchLayer Phase 1 usage

In MatchLayer the two pin files live at the repository root, next to the other
root-level configuration files, so a version manager picks them up the moment
you enter the project directory. Each file holds exactly one line.

The Node.js pin records the major version the web front end and the
JavaScript-based tooling are built and tested against:

Source: `.nvmrc`

```text
24
```

The bare `24` pins the major line while allowing each machine to use whatever
24.x patch release it already has installed. Reading this file is how a
contributor — or an automated build — knows to provision Node.js 24 rather than
whatever happened to be on the machine.

The Python pin records the interpreter version the back-end service and the
Python pipelines target:

Source: `.python-version`

```text
3.13
```

This `3.13` matches the Python version the backend declares elsewhere in its
package configuration, so the interpreter a developer's shell selects on entry is
the same one the application expects at runtime. Keeping both pin files at the
root means a single, visible place records the runtime expectations for the whole
repository, and both files are updated in the same change whenever the project
moves to a newer runtime.

## Common pitfalls

- **Mistake:** Assuming a pin file installs the runtime by itself, with no version manager present on the machine.
  **Symptom:** You enter the directory, nothing changes, and the language command still runs whatever version was already on your shell; the pinned version is silently ignored.
  **Recovery:** Install a version manager that reads the pin file, then re-enter the directory (or run its "use" command) so it reads the file and selects or installs the named version.

- **Mistake:** Writing extra content into the file — a comment, a label, a leading `v`, or several versions — instead of a single bare version string.
  **Symptom:** The version manager reports that it cannot parse the file, or it resolves to an unexpected version because it read more than the number you intended.
  **Recovery:** Reduce the file to one line containing only the version (for example `24` or `3.13`), with no prefix, comment, or trailing text, and commit that.

- **Mistake:** Bumping the runtime in one place — the pin file or the package configuration — but forgetting the other, so the two disagree.
  **Symptom:** Your shell selects one version while the application or its dependency resolver expects another, producing install errors or behaviour that differs between your machine and the build.
  **Recovery:** Treat the pin file and the package configuration's declared version as a pair, change them together in the same commit, and re-run the install so both agree again.

## External reading

- [Node.js downloads and releases](https://nodejs.org/en/download)
- [Python setup and usage](https://docs.python.org/3/using/index.html)
- [uv: Python versions and the .python-version file](https://docs.astral.sh/uv/concepts/python-versions/)
