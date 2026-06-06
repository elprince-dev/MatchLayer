# Docker images, layers, and the build cache

## Introduction

This document explains what a Docker image is, how it is built out of stacked
layers, and how the build cache uses those layers to make rebuilds fast. A
Docker image is a read-only template that packages an application together with
its dependencies and configuration so a container — an isolated running process
started from that template — has everything it needs. An image is not a single
opaque blob: it is a stack of layers, where each layer is a saved set of
filesystem changes produced by one build instruction. Understanding layers
explains why the order of instructions in a build file dramatically changes how
long rebuilds take and how large the image becomes.

**Learning outcomes** — after reading this document you will be able to:

- Explain what a Docker image is and how it relates to a running container.
- Describe how an image is composed of stacked, content-addressed layers.
- Explain how the build cache reuses unchanged layers to skip repeated work.
- Order build instructions so that frequently changing inputs do not invalidate expensive layers.

Prerequisites:

- [Containers versus virtual machines](10-containers-01-containers-vs-vms.md) introduces the container model that an image is the template for.

## Problem it solves

The concrete problem is rebuilding an application image quickly and storing many
similar images without wasting space. A real image contains a base operating
system, a language runtime, installed dependencies, and your application code.
Resolving and installing dependencies can take minutes; your application code, by
contrast, changes many times an hour. If every rebuild redid all of that work
from scratch, the feedback loop would be intolerable and every stored image would
duplicate the same gigabytes of base files.

A prior approach was to treat a built artifact as one monolithic snapshot: build
it, save the whole thing, and rebuild the whole thing on any change. That
approach has real costs:

- A one-character change to source code forces a full rebuild, including the slow dependency-install step that did not change.
- Ten images that share the same base and runtime each store a private copy of those bytes, multiplying disk usage.
- There is no way to share common pieces between images or between rebuilds, because the artifact has no internal structure to share.

Layered images solve this by splitting the build into ordered steps, saving the
filesystem change from each step as its own reusable layer, and addressing each
layer by the hash of its contents so identical layers are stored once and reused
everywhere.

## Mental model

Think of building an image as making a stack of transparent sheets, where each
sheet records only what changed at that step:

1. Start with a base sheet that already has the operating system and runtime drawn on it (the base image layer).
2. Lay a new transparent sheet on top and draw only the dependencies you install (one layer capturing that filesystem change).
3. Lay another sheet on top and draw only your application code (another layer).
4. Stack the sheets and look down through them: the combined picture is the final filesystem the container sees, with upper sheets overriding lower ones.
5. To rebuild after editing only the top sheet, you reuse every sheet below unchanged and redraw the top one alone.

Each sheet is labelled by a fingerprint of exactly what is drawn on it, so two
stacks that share the same lower sheets physically share those sheets rather than
copying them. Redrawing one sheet only forces the sheets above it to be redrawn,
never the ones below.

## How it works

An image is an ordered stack of read-only layers plus a small piece of metadata
(the configuration: the command to run, environment variables, the working
directory). Each layer is the set of filesystem changes — files added, modified,
or removed — produced by a single build instruction. When a container starts, the
engine stacks these read-only layers using a union filesystem, which presents the
combined set of files as one filesystem, and adds a thin writable layer on top for
the container's own runtime changes. Layers lower in the stack are shared,
read-only, and never altered by a running container.

Every layer is content-addressed: it is identified by a cryptographic hash
(a digest) of its contents. Two images that begin from the same base and run the
same early instructions produce byte-identical early layers with identical
digests, so the engine stores those bytes once and both images reference them.
This is why pulling a second image that shares a base with one you already have
downloads only the new layers.

The build cache builds on the same idea. A build runs instructions in order, and
for each instruction the engine computes a cache key from the instruction text
plus the state of its inputs (for a file-copy instruction, the contents of the
files being copied; for a command instruction, the command string and the layer
beneath it). If a cached layer already exists for that key, the engine reuses it
and skips the work. The moment one instruction misses the cache, every
instruction after it must run again, because each layer depends on the exact
filesystem the previous layer produced. The practical rule follows directly:
order instructions from least-frequently-changing to most-frequently-changing.
Put slow, stable steps — installing dependencies — early, and fast, volatile steps
— copying application source — late, so that an everyday source edit invalidates
only the cheap final layers and reuses the expensive early ones.

## MatchLayer Phase 1 usage

The production image for the back-end interface, defined in
`infra/docker/api.Dockerfile`, is deliberately ordered to protect the expensive
dependency layer. It copies only the dependency manifests first and installs
dependencies before any application source is present:

Source: `infra/docker/api.Dockerfile`

```dockerfile
# Resolve and install runtime dependencies first (no project install). Keeping this layer
# independent of source means edits under apps/api/src/ don't bust the dependency cache.
COPY apps/api/pyproject.toml apps/api/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
```

Because the layer produced by that install step keys only on the two manifest
files, editing application code does not change its cache key, so a rebuild reuses
the installed-dependencies layer and skips the slow resolve-and-install. Only
after that does the build bring in the source code, in a later set of
instructions whose layers are cheap to rebuild:

Source: `infra/docker/api.Dockerfile`

```dockerfile
COPY apps/api/README.md ./
COPY apps/api/src ./src
COPY apps/api/alembic.ini ./
COPY apps/api/alembic ./alembic
RUN uv sync --frozen --no-dev
```

A change under the source directory invalidates these trailing layers only; the
dependency layer above stays cached. That ordering is the difference between a
multi-minute rebuild and a few-second one on an everyday code change.

## Common pitfalls

- **Mistake:** Copying the entire project into the image early (one broad copy of everything) and then installing dependencies.
  **Symptom:** Every source edit changes the early copy layer, so the dependency install reruns on every build and rebuilds are slow.
  **Recovery:** Copy only the dependency manifests first, install dependencies, then copy the rest of the source, so source edits never invalidate the install layer.

- **Mistake:** Assuming combining changes across many small layers has no cost, and chaining unrelated installs and cleanups across separate instructions.
  **Symptom:** Files deleted in a later layer still occupy space because the earlier layer that added them remains in the stack, so the image is larger than expected.
  **Recovery:** Remove temporary files within the same instruction that created them so the addition and deletion collapse into one layer.

- **Mistake:** Expecting the cache to notice a changed remote input (a package index or a fetched script) when the instruction text is unchanged.
  **Symptom:** A rebuild reuses a stale layer and ships outdated content, because the cache key was based on the unchanged command, not the changed remote data.
  **Recovery:** Pin inputs by digest or version so a real change alters the cache key, or force a no-cache rebuild when you must refresh a remote input.

- **Mistake:** Believing that a single image holds a private copy of all its bytes and so pulling related images costs the full size each time.
  **Symptom:** Disk and bandwidth estimates are wildly too high because they ignore that shared base layers are stored and transferred once.
  **Recovery:** Reason about size in terms of unique layers added on top of shared bases, and standardise on common base images so layers are reused.

## External reading

- [Docker: image layers](https://docs.docker.com/get-started/docker-concepts/building-images/understanding-image-layers/)
- [Docker: cache management with the build cache](https://docs.docker.com/build/cache/)
- [Docker: best practices for writing Dockerfiles](https://docs.docker.com/build/building/best-practices/)
- [Docker: what is an image?](https://docs.docker.com/get-started/docker-concepts/the-basics/what-is-an-image/)
