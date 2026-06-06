# The production Dockerfiles, instruction by instruction

## Introduction

This document is a guided reading of the two production Dockerfiles — the
plain-text recipes that build the container images shipped to production — with
each instruction explained in turn. A Dockerfile instruction is one line of the
recipe that either adds content to the image or records metadata about how the
finished container behaves. Where the earlier Containerization documents teach
the concepts, this document walks the actual recipes top to bottom so the Reader
can map each concept to a concrete line and understand why it is there.

**Learning outcomes** — after reading this document you will be able to:

- Name the common Dockerfile instructions and state what each one does.
- Explain the difference between instructions that add image content and instructions that set runtime metadata.
- Read a production Dockerfile end to end and explain the purpose of each stage and line.
- Recognise the mistakes that break a production image build or its runtime behaviour.

Prerequisites:

- [Dockerfiles and multi-stage builds](10-containers-03-dockerfiles-and-multi-stage-builds.md) introduces the Dockerfile and the multi-stage structure these files use.
- [Docker images, layers, and the build cache](10-containers-02-docker-images-layers-and-cache.md) explains the layers the instructions below produce.

## Problem it solves

The concrete problem is turning application source into a runnable, reproducible
production image whose every behaviour — what runs, as whom, on which port, how
health is checked — is explicit and reviewable. A team needs to look at one file
and know exactly how the image is assembled and how the container will behave,
without tribal knowledge or out-of-band steps.

A prior approach was to build images interactively: start a base container, run
commands by hand to install and configure software, and save the result as an
image. That approach has real costs:

- The steps live only in someone's shell history, so the image cannot be rebuilt identically by anyone else.
- There is no review surface: a change to how the image is built leaves no diff to inspect.
- Runtime behaviour — the user, the port, the start command — is set ad hoc and easily forgotten or done inconsistently.

A Dockerfile solves this by making every step an explicit, ordered, version-
controlled instruction. The recipe is the single source of truth: it builds the
same way every time and every line is open to review.

## Mental model

Read a production Dockerfile the way you would read a labelled assembly diagram,
where each step either adds a part or writes a note about how to operate the
finished machine:

1. A base instruction states which pre-built foundation the image starts from.
2. Copy and run instructions add parts: they bring files in and execute build steps, each saved as a layer.
3. An environment instruction writes a setting onto the machine that persists into the running container.
4. A user instruction notes which identity operates the machine once it runs.
5. Port, health-probe, and start instructions are operating notes: which door is used, how to check the machine is well, and what to switch on when it powers up.

Walking the file top to bottom, you alternate between "add a part" steps that
shape the image and "operating note" steps that shape the container's runtime
behaviour. Telling the two kinds apart is the key to reading the recipe.

## How it works

Dockerfile instructions fall into two groups. The first group changes the image's
filesystem and produces layers. A base instruction names the starting image and
opens a build stage. A copy instruction brings files from the build context, or
from an earlier stage, into the image. A run instruction executes a command
during the build and saves the resulting filesystem change. A working-directory
instruction sets the directory that later instructions and the running process
use as their base path.

The second group records metadata that takes effect when a container runs, not
during the build. An environment instruction defines variables present in the
running container's environment. A user instruction sets the account the
container's process runs as, which is central to running as a non-administrative
user. A port instruction documents which network port the service listens on. A
health-probe instruction defines a command the engine runs periodically to decide
whether the container is healthy. A start instruction (in entrypoint form)
declares the program launched when the container starts; it does not run during
the build.

A production-grade file combines these with a multi-stage structure: a builder
stage uses the first group heavily to compile and assemble output, and a minimal
final stage copies only that output forward and then sets the second group of
runtime metadata. Reading such a file is a matter of tracking which stage you are
in and, within it, whether each line is adding image content or declaring runtime
behaviour. The order matters for two reasons covered elsewhere: layer caching
rewards putting stable steps first, and the final stage's metadata is what
actually governs the shipped container.

## MatchLayer Phase 1 usage

The image for the back-end interface is `infra/docker/api.Dockerfile`. Its final
stage sets environment variables that place the application's tools on the
executable search path and tell the interpreter where to find the code:

Source: `infra/docker/api.Dockerfile`

```dockerfile
ENV PATH="/app/.venv/bin:/usr/bin:${PATH}" \
    PYTHONPATH="/app/src" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
```

It then declares the non-administrative user, documents the listening port,
defines a periodic health probe, and finally names the program to launch when the
container starts:

Source: `infra/docker/api.Dockerfile`

```dockerfile
USER nonroot

EXPOSE 8000
```

The start instruction itself launches the web server as the container's single
main process:

Source: `infra/docker/api.Dockerfile`

```dockerfile
ENTRYPOINT ["uvicorn", "matchlayer_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

The image for the web front-end, `infra/docker/web.Dockerfile`, mirrors the same
runtime-metadata block: it sets production environment variables, drops to the
non-administrative user, exposes its port, defines a health probe, and names its
start command. Its environment block is:

Source: `infra/docker/web.Dockerfile`

```dockerfile
ENV NODE_ENV=production \
    PORT=3000 \
    HOSTNAME=0.0.0.0
```

Reading either file, the pattern is the same: the builder stage adds and builds
content, and the final stage copies the result forward and then declares exactly
how the container behaves — as whom, on which port, checked how, running what.

## Common pitfalls

- **Mistake:** Reading a run instruction in the final stage as the program that runs when the container starts.
  **Symptom:** Confusion about why a command "in the Dockerfile" never executes at container start, because run executes at build time while the start command is the entrypoint.
  **Recovery:** Distinguish build-time instructions (run, copy) from the runtime start instruction (entrypoint or command) when reading the file.

- **Mistake:** Editing the builder stage's instructions and expecting the change to appear in the shipped image without it being copied forward.
  **Symptom:** A change made in the builder has no effect on the final image because the final stage only copies specific artifact paths.
  **Recovery:** Trace which builder outputs the final stage copies, and make sure the change lands in one of those copied paths.

- **Mistake:** Setting runtime metadata (environment, user, port) in the builder stage instead of the final stage.
  **Symptom:** The shipped container ignores the settings because each stage has its own metadata and only the final stage's applies to the image.
  **Recovery:** Place all runtime metadata in the final stage so it governs the container that actually ships.

- **Mistake:** Changing an early instruction in a way that has no functional effect but reorders the file, then being surprised by a slow rebuild.
  **Symptom:** A large rebuild runs because moving an early instruction invalidated the cache for every layer after it.
  **Recovery:** Keep stable, cache-friendly instructions early and edit later layers when possible, so routine changes do not bust expensive early layers.

## External reading

- [Docker: Dockerfile reference](https://docs.docker.com/reference/dockerfile/)
- [Docker: multi-stage builds](https://docs.docker.com/build/building/multi-stage/)
- [Docker: best practices for writing Dockerfiles](https://docs.docker.com/build/building/best-practices/)
- [Docker: building images overview](https://docs.docker.com/get-started/docker-concepts/building-images/)
