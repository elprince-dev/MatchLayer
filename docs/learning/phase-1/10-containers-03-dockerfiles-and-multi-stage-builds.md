# Dockerfiles and multi-stage builds

## Introduction

This document explains the Dockerfile — a plain-text recipe that lists, step by
step, how to build a container image — and the multi-stage build, a technique
where one Dockerfile defines several independent build environments and copies
only the finished output from one into another. A container image is the
read-only template a container runs from; a Dockerfile is how you produce that
template reproducibly instead of by hand. Multi-stage builds matter because they
let you compile and assemble an application using a heavy toolchain, then ship a
final image that contains only the runnable result and none of the build tools.

**Learning outcomes** — after reading this document you will be able to:

- Read a Dockerfile and explain what each common instruction contributes to the image.
- Explain what a build stage is and how one stage copies artifacts from another.
- Describe why a multi-stage build produces a smaller, safer final image than a single-stage build.
- Recognise the common mistakes that bloat images or leak build-time tooling into production.

Prerequisites:

- [Containers versus virtual machines](10-containers-01-containers-vs-vms.md) introduces the container and image model this builds on.
- [Docker images, layers, and the build cache](10-containers-02-docker-images-layers-and-cache.md) explains the layers a Dockerfile's instructions produce.

## Problem it solves

The concrete problem is that the tools you need to _build_ an application are
almost never the tools you need to _run_ it. Building often requires compilers,
package managers, header files, and development libraries; running requires only
the compiled output and a runtime. If the final image carries the entire build
toolchain, it is large, slow to transfer, and exposes a wide surface of software
that must be kept patched even though production never uses it.

A prior approach was to build the application on a build machine and then write a
second, separate recipe for the runtime image, manually copying artifacts between
them. That approach has real costs:

- The build steps and the runtime steps live in different files, so they drift apart and the runtime image is assembled from stale or mismatched artifacts.
- Getting the build output into the runtime image requires extra glue — archives, shared directories, scripted copies — that is easy to get wrong.
- It is tempting to take the shortcut of installing the build tools into the runtime image, producing a bloated image full of software production does not run.

Multi-stage builds solve this by putting every stage in one Dockerfile: a builder
stage with the full toolchain produces the artifacts, and a final stage starts
from a minimal base and copies in only the finished output, discarding the
builder entirely.

## Mental model

Think of building a piece of furniture in a fully equipped workshop and then
delivering only the finished chair:

1. The workshop (the builder stage) holds every saw, clamp, and jig you might need — a heavy, messy environment full of tools.
2. You build the chair in the workshop, using all those tools freely (compiling code, installing dependencies, assembling output).
3. When the chair is done, you carry only the chair out to the delivery van (copy only the finished artifacts into the final stage).
4. The saws and sawdust stay behind in the workshop and never reach the customer (the build toolchain is discarded, not shipped).
5. The customer receives a clean chair with no tools attached (a minimal final image containing only what runs).

The two environments live under one roof (one Dockerfile) but only the finished
product crosses from the workshop into the van. Everything the build needed but
the runtime does not is left behind.

## How it works

A Dockerfile is read top to bottom, and most instructions add a layer to the
image being built. A base instruction names the starting image. A copy
instruction brings files from the build context (the set of files sent to the
builder) into the image. A run instruction executes a command and saves the
resulting filesystem change as a layer. Other instructions set metadata rather
than filesystem content: one sets the working directory, one declares
environment variables, one declares which user the process runs as, one declares
the network port the container listens on, one defines a periodic health probe,
and one sets the command that runs when a container starts. None of these
metadata instructions execute the application at build time; they record how the
container will behave when it later runs.

A multi-stage build places more than one base instruction in the same file. Each
base instruction begins a new stage with its own filesystem, and a stage can be
given a name. Earlier stages act as scratch build environments: they may install
compilers and development dependencies and produce compiled or assembled output.
A later stage starts from its own base — typically a small runtime-only image —
and uses a copy instruction with a "from" reference to pull specific files out of
an earlier named stage. Only the files explicitly copied forward end up in the
final image; everything else in the earlier stages, including the entire
toolchain, is discarded when the build finishes.

The payoff is a smaller, safer result with no loss of build capability. The final
image contains only the runtime base plus the copied artifacts, so it is small,
fast to push and pull, and exposes little software to maintain. Because the
build and runtime definitions live in one file and the runtime image is assembled
directly from the builder's verified output, the two cannot silently drift apart.
The build cache still applies per stage, so an unchanged builder stage is reused
even when only the final stage changes. The cost is a little more structure in
the Dockerfile, which pays for itself immediately in image size and clarity.

## MatchLayer Phase 1 usage

Both production Dockerfiles in `infra/docker/` are two-stage builds. The image
for the back-end interface, `infra/docker/api.Dockerfile`, starts a named builder
stage from a full Python base image where dependencies are resolved and installed:

Source: `infra/docker/api.Dockerfile`

```dockerfile
FROM python@sha256:b04b5d7233d2ad9c379e22ea8927cd1378cd15c60d4ef876c065b25ea8fb3bf3 AS builder
```

The final stage then starts from a minimal runtime base and copies only the
finished virtual environment and application code out of the builder, leaving the
build tooling behind:

Source: `infra/docker/api.Dockerfile`

```dockerfile
COPY --from=builder /app/.venv      /app/.venv
COPY --from=builder /app/src        /app/src
COPY --from=builder /app/alembic.ini /app/alembic.ini
COPY --from=builder /app/alembic    /app/alembic
```

The image for the web front-end, `infra/docker/web.Dockerfile`, follows the same
shape: a builder stage installs the full dependency graph and runs the production
build, and the final stage copies only the self-contained server output forward.
The builder stage runs the build like this:

Source: `infra/docker/web.Dockerfile`

```dockerfile
COPY . .
RUN pnpm --filter @matchlayer/web build
```

In both files the heavy toolchain — the package managers, the full dependency
trees, the compilers — exists only in the builder stage and never reaches the
shipped image. That is what keeps the production images small and limits the
software that has to be kept patched.

## Common pitfalls

- **Mistake:** Writing a single-stage Dockerfile that installs build tools and then runs the application from the same image.
  **Symptom:** The final image is hundreds of megabytes larger than needed and contains compilers and package managers that production never invokes.
  **Recovery:** Split the build into a builder stage and a minimal final stage, and copy only the runnable artifacts forward with a from-reference copy.

- **Mistake:** Copying a whole stage's filesystem into the final image instead of the specific output directories.
  **Symptom:** The final image quietly regains the bloat the multi-stage build was meant to remove, because the copy pulled in build caches and tooling too.
  **Recovery:** Copy only the explicit artifact paths the runtime needs, naming each directory rather than the stage root.

- **Mistake:** Putting frequently changing instructions before slow, stable ones within a stage.
  **Symptom:** Routine source edits invalidate the dependency-install layer, so every build redoes the slow install.
  **Recovery:** Order each stage from least- to most-frequently-changing — manifests and installs first, source last — so the cache protects the expensive steps.

- **Mistake:** Assuming a run instruction in the Dockerfile starts the application, and putting the server-start command in a run instruction.
  **Symptom:** The build hangs or the server runs at build time instead of when a container starts, because run executes during the build, not at container start.
  **Recovery:** Use the entrypoint or command instruction to declare the start command; reserve run for build-time steps that produce image content.

## External reading

- [Docker: multi-stage builds](https://docs.docker.com/build/building/multi-stage/)
- [Docker: Dockerfile reference](https://docs.docker.com/reference/dockerfile/)
- [Docker: writing a Dockerfile](https://docs.docker.com/get-started/docker-concepts/building-images/writing-a-dockerfile/)
- [Docker: best practices for writing Dockerfiles](https://docs.docker.com/build/building/best-practices/)
