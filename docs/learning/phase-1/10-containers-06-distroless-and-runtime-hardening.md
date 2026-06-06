# Distroless images, non-root users, and read-only runtime

## Introduction

This document explains three runtime-hardening practices that make a container —
an isolated running process started from an image — harder to attack: distroless
base images, running as a non-root user with a high user identifier (UID), and
the read-only root filesystem runtime contract. A distroless image is a base
image stripped down to only a language runtime and its libraries, with no shell
and no package manager. Running as a non-root user means the container's process
is not the all-powerful administrative account. A read-only root filesystem
means the running container cannot write anywhere except explicitly allowed
scratch space. Together these shrink what an attacker who reaches the container
can see, change, or escalate.

**Learning outcomes** — after reading this document you will be able to:

- Explain what a distroless image contains and what it deliberately leaves out.
- Explain why running as a non-root user with a high user identifier reduces risk.
- Describe what a read-only root filesystem enforces and why a small writable scratch area is still needed.
- Recognise the common mistakes that defeat these protections and recover from them.

Prerequisites:

- [Containers versus virtual machines](10-containers-01-containers-vs-vms.md) introduces the container model these practices harden.
- [Dockerfiles and multi-stage builds](10-containers-03-dockerfiles-and-multi-stage-builds.md) explains the final-stage base image and the user declaration these practices rely on.

## Problem it solves

The concrete problem is limiting the damage when something goes wrong inside a
container. If an attacker exploits a flaw in the application, the harm they can do
is bounded by what the container environment makes available: the tools present,
the privileges of the process, and the parts of the filesystem they can modify. A
container that ships a full operating system, runs as the administrative account,
and lets the process write anywhere hands an intruder a fully stocked workshop.

A prior approach was to base images on a complete general-purpose operating
system, run the process as the default administrative account, and leave the
filesystem writable — because that is the path of least resistance. That approach
has real costs:

- A full operating system includes a shell, package managers, and dozens of utilities an attacker can use to explore, download tools, and pivot, none of which the application needs.
- Running as the administrative account means any code execution starts with maximum privilege inside the container, easing attempts to escalate further.
- A writable root filesystem lets an intruder drop files, modify binaries, or persist changes, and hides the fact that the application never needed to write there.

Distroless images, non-root high-UID users, and a read-only root filesystem each
remove one of those gifts: no extra tools, no built-in privilege, and no writable
surface beyond a tiny declared scratch area.

## Mental model

Think of three independent locks on the same door, each closing a different way in:

1. The distroless image is an empty room: there is no shell, no toolbox, no spare parts lying around — only the one machine the room exists to run. An intruder who gets in finds nothing to work with.
2. The non-root user is a visitor's badge instead of a master key: the process can do its job but cannot open the building's administrative doors, so a foothold does not start with full control.
3. The high user identifier is a badge number chosen far outside the range the host hands out to its own staff, so even if the badge number somehow lined up with an account on the host, it would not collide with a privileged one.
4. The read-only root filesystem is a room where the furniture is bolted down: nothing can be added, removed, or rewritten, except one small unlocked drawer set aside for scratch notes.
5. Each lock works on its own; together they mean an intruder finds an empty, bolted-down room and only a visitor's badge.

The point is layering: defeating one lock still leaves the others. An empty room
with bolted furniture and a visitor-only badge is a poor place to mount an attack.

## How it works

A distroless base image contains only what a program needs to run — the language
runtime and its shared libraries — and deliberately omits the shell, the package
manager, and the general-purpose utilities a normal operating-system image bundles.
Removing them shrinks the image and, more importantly, removes the tools an
attacker would reach for after gaining code execution: with no shell there is no
easy interactive foothold, and with no package manager there is no simple way to
fetch more tooling. The trade-off is that you cannot open an interactive shell in
the container for debugging, because there is no shell to open; you debug by other
means.

Running as a non-root user addresses privilege. On a typical operating system the
administrative account (user identifier 0) can override file permissions and
perform privileged operations. If the container's process runs as that account,
any code execution begins with those powers inside the container. Declaring that
the process runs as an ordinary, unprivileged user means a foothold starts with
limited rights instead. Choosing a high user identifier — a large number well
outside the range a host assigns to its own accounts — is a defence-in-depth
measure: should the container's user identifier ever be interpreted against the
host's account table (for example, through a shared mount), a high, unusual number
is far less likely to coincide with a real, privileged host account than a low one.

A read-only root filesystem enforces immutability at runtime. The container is
started with its root filesystem mounted read-only, so the process cannot create,
modify, or delete files anywhere on it. This blocks an intruder from dropping
files, tampering with binaries, or persisting changes, and it surfaces any place
the application unexpectedly tried to write. Because some programs need a little
scratch space for transient files, the contract pairs the read-only root with a
small, explicitly mounted writable area (commonly an in-memory temporary
directory) that holds only ephemeral data and vanishes when the container stops.
The rule is that the writable area is the _only_ exception; everything else stays
read-only.

## MatchLayer Phase 1 usage

The production image for the back-end interface, `infra/docker/api.Dockerfile`,
uses a distroless final base image and documents its properties in the file's own
comments — no shell, no package manager, and a built-in non-root user with a high
identifier:

Source: `infra/docker/api.Dockerfile`

```dockerfile
# Tag pin (for human review): gcr.io/distroless/python3-debian13:nonroot
# Debian-trixie distroless with CPython 3.13 and the `nonroot` user (UID/GID 65532).
# No shell, no package manager, no setuid binaries.
```

The image then declares that the container runs as that non-root user:

Source: `infra/docker/api.Dockerfile`

```dockerfile
USER nonroot
```

The read-only runtime contract is documented in the file's header, which gives the
exact run command pairing a read-only root filesystem with a single in-memory
scratch directory:

Source: `infra/docker/api.Dockerfile`

```dockerfile
#   docker run --read-only --tmpfs /tmp \
```

The web front-end image, `infra/docker/web.Dockerfile`, applies the same three
practices: a distroless final base with a non-root high-identifier user and the
same read-only run contract. Its header states the contract directly:

Source: `infra/docker/web.Dockerfile`

```dockerfile
#   docker run --read-only --tmpfs /tmp -p 3000:3000 <image>
```

In both images the combination is deliberate: nothing to exploit in the base, no
administrative privilege for the process, and no writable surface beyond one
in-memory scratch directory.

## Common pitfalls

- **Mistake:** Relying on opening an interactive shell inside a distroless container to debug a problem.
  **Symptom:** Attempts to start a shell fail because the distroless image has no shell, leaving the usual debugging path unavailable.
  **Recovery:** Debug through logs, health probes, and a separate non-distroless debug image, rather than expecting a shell in the production image.

- **Mistake:** Declaring a non-root user but having the application try to write into a directory only the administrative account can write.
  **Symptom:** The container starts but the application fails with permission-denied errors when it tries to write outside its allowed paths.
  **Recovery:** Give the non-root user ownership of the specific paths it must write, or redirect those writes to the mounted scratch area.

- **Mistake:** Adding the read-only flag without providing the small writable scratch area the program needs for transient files.
  **Symptom:** The container fails at runtime trying to write a temporary file, because the entire root filesystem is read-only and no scratch area was mounted.
  **Recovery:** Mount a small explicit writable area (an in-memory temporary directory) alongside the read-only root, as the run contract specifies.

- **Mistake:** Quietly granting extra writable paths to make an error go away, instead of treating the unexpected write as a signal.
  **Symptom:** The read-only protection erodes over time as more writable exceptions accumulate, until the root filesystem is effectively writable again.
  **Recovery:** Treat any new write attempt as something to investigate; keep the single scratch area as the only exception and route legitimate persistence to a managed volume.

## External reading

- [GoogleContainerTools/distroless project](https://github.com/GoogleContainerTools/distroless)
- [Docker: run reference (read-only and tmpfs options)](https://docs.docker.com/reference/cli/docker/container/run/)
- [Docker: build best practices (run as a non-root user)](https://docs.docker.com/build/building/best-practices/)
- [Docker: container security overview](https://docs.docker.com/engine/security/)
