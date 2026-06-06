# Containers versus virtual machines

## Introduction

This document explains what a container is and how it differs from a virtual
machine (VM) — a software-simulated computer that runs a complete guest
operating system, the low-level software that manages a computer's hardware and
runs programs, on top of your real machine. A container is a way to package
an application together with everything it needs to run, then run it as an
isolated process that shares the host machine's operating system kernel (the
core of the operating system that talks directly to the hardware) instead of
booting its own. The distinction matters because it explains why containers
start in milliseconds and stay small while virtual machines are heavier and
slower, and it is the foundation for every other topic in the Containerization
track.

**Learning outcomes** — after reading this document you will be able to:

- Explain what a container is and what it shares with the host machine.
- Describe how a virtual machine differs from a container and what each one virtualizes.
- State when a container is the right tool and when a virtual machine is a better fit.
- Recognise the everyday mistakes people make when they treat a container like a small virtual machine.

No prerequisites.

## Problem it solves

The concrete problem is "it works on my machine, but not on yours." An
application depends on a specific language runtime, specific library versions,
specific system packages, and specific configuration. When two developers, or a
developer and a production server, have even slightly different versions of any
of those, the application behaves differently or fails outright. The goal is to
package an application with its exact dependencies so it runs the same way
everywhere.

One prior approach was to write setup documents and installation scripts that
each machine ran to reproduce the right environment. That approach has real
costs:

- Every machine starts from a different baseline, so the same script produces different results.
- Dependencies installed globally on a machine collide: two applications that need different versions of the same library cannot coexist cleanly.
- Reproducing a known-good environment a year later means re-running scripts against software that has since changed underneath them.

A second prior approach was the virtual machine: ship a whole simulated computer,
guest operating system included, so the environment is captured exactly. That
works and isolates strongly, but a virtual machine carries an entire operating
system per application — gigabytes of disk, its own boot sequence, and a slice of
memory reserved up front — which is heavy when all you wanted was to run one
program predictably. Containers keep the reproducibility of a packaged
environment without paying for a whole guest operating system each time.

## Mental model

Think of the difference between an apartment building and a row of detached
houses:

1. A virtual machine is a detached house: it has its own foundation, plumbing, and electrical system (its own guest operating system and simulated hardware), so it is fully self-contained but expensive to build and slow to move into.
2. A container is an apartment in a shared building: it has its own locked front door and private rooms (isolated filesystem, process list, and network view), but it shares the building's foundation and utilities (the host machine's operating system kernel) with every other apartment.
3. Building a new detached house means pouring a new foundation every time (booting a new guest operating system), which takes minutes and a lot of material.
4. Renting another apartment means handing over a key to an already-built unit (starting another isolated process on the running kernel), which takes a moment and very little material.
5. Both give you privacy and a place of your own; they differ in how much they duplicate underneath you.

The shared foundation is the whole point: a container reuses the host kernel
instead of carrying its own, which is why it is small and starts quickly.

## How it works

A normal program runs as a process directly on the operating system, and it can
see every other process, the whole filesystem, and the machine's network. A
container is still an ordinary process, but the kernel wraps it in isolation so
it sees only its own private view: its own filesystem, its own process list, and
its own network interfaces. The kernel provides this with two long-standing
features — namespaces, which give a process an isolated view of system resources
so it cannot see or touch things outside its box, and control groups, which cap
how much processor time and memory the process may consume. Because the isolation
comes from the host kernel, the container does not need a kernel of its own.

A virtual machine isolates at a lower level. A piece of software called a
hypervisor simulates hardware — a processor, memory, disks, network cards — and a
complete guest operating system boots on that simulated hardware, exactly as it
would on a physical computer. Every virtual machine therefore carries its own
full operating system and goes through a real boot sequence when it starts. That
gives very strong isolation, because the guest believes it has a whole machine to
itself, at the cost of size and start-up time.

The practical consequences fall out of that one difference. A container shares
the host kernel, so it holds only the application and its dependencies: it is
typically tens or hundreds of megabytes, it starts in well under a second, and a
single host can run many of them at once. A virtual machine includes a guest
operating system, so it is typically gigabytes, takes seconds to minutes to boot,
and a host runs comparatively few. The trade-off is isolation depth: a virtual
machine's boundary is the simulated-hardware line, which is harder to cross than a
container's kernel-enforced boundary, so workloads that demand the strongest
separation or that need a different operating system kernel than the host still
reach for virtual machines. Many real systems combine both — containers running
inside virtual machines — to get fast packaging on top of strong tenant
isolation.

## MatchLayer Phase 1 usage

In Phase 1 every backing service in the local development stack is a container,
declared in `docker-compose.yml`. Each service names an image, and the running
container is an isolated process started from that image — not a virtual machine,
and not a program installed onto your host. The database service is one such
container:

Source: `docker-compose.yml`

```yaml
postgres:
  image: postgres:16-alpine@sha256:16bc17c64a573ef34162af9298258d1aec548232985b33ed7b1eac33ba35c229
```

Because these are containers sharing your machine's kernel rather than virtual
machines, the whole stack starts in seconds with `docker compose up -d --wait`
and consumes a fraction of the memory three separate virtual machines would. The
application images themselves follow the same model: the production container
definition for the back-end interface, `infra/docker/api.Dockerfile`, ends by
starting the application as the container's main process rather than booting an
operating system:

Source: `infra/docker/api.Dockerfile`

```dockerfile
ENTRYPOINT ["uvicorn", "matchlayer_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

That single line is the container's whole reason to exist: it runs one process,
the web server, on the shared host kernel. There is no guest operating system to
boot — which is exactly what makes the container lightweight.

## Common pitfalls

- **Mistake:** Treating a container like a tiny virtual machine and trying to run several long-lived programs (a database, a web server, and a cron daemon) inside one container.
  **Symptom:** When the main process exits the container stops, taking the other programs with it, and there is no init system to supervise or restart them, so failures cascade silently.
  **Recovery:** Run one main process per container and compose multiple containers together, letting each service own its own lifecycle.

- **Mistake:** Assuming a container fully isolates you from the host the way a virtual machine does, and running an untrusted image expecting hardware-level separation.
  **Symptom:** A container can reach kernel features and shared resources a virtual machine would have hidden, so a kernel-level vulnerability or a misconfiguration crosses the boundary that a virtual machine would have held.
  **Recovery:** Match isolation to the threat: keep containers for trusted workloads and packaging, and use a virtual machine boundary when you need strong separation between untrusted tenants.

- **Mistake:** Expecting a container built for one operating-system kernel to run unchanged on a different kernel because "containers are portable."
  **Symptom:** A container built for a particular kernel and processor architecture fails to start or behaves oddly on a host with a different one, because the container shares the host kernel rather than carrying its own.
  **Recovery:** Build images for the target kernel and architecture, and use a virtual machine when you genuinely need a different operating system than the host provides.

- **Mistake:** Storing important data inside the container's own writable layer because it feels like a persistent little computer.
  **Symptom:** The data vanishes the moment the container is removed and recreated, because a container's writable layer is disposable by design.
  **Recovery:** Keep durable data outside the container in a managed volume, as covered in [Named Docker volumes and data persistence](07-database-04-named-docker-volumes.md).

## External reading

- [Docker: what is a container?](https://docs.docker.com/get-started/docker-concepts/the-basics/what-is-a-container/)
- [Docker: containers versus virtual machines](https://docs.docker.com/get-started/docker-overview/)
- [Linux kernel: namespaces overview](https://www.kernel.org/doc/html/latest/admin-guide/namespaces/index.html)
- [Linux kernel: control groups (cgroups)](https://docs.kernel.org/admin-guide/cgroup-v2.html)
