# Docker Compose, healthchecks, and the --wait flag

## Introduction

This document explains Docker Compose, a tool that runs a multi-service
application from a single declarative file, along with healthchecks — periodic
probes that report whether a service is actually ready — and the `--wait` flag
that blocks startup until those probes pass. Docker Compose lets you describe
several containers (an isolated running process started from an image), the
configuration each one needs, and how they fit together, then bring them all up
with one command. Healthchecks and `--wait` matter because "the container
started" is not the same as "the service inside it is ready to accept work," and
the gap between those two is a frequent source of flaky local development and
test runs.

**Learning outcomes** — after reading this document you will be able to:

- Explain what Docker Compose does and why a single file describes a whole local stack.
- Describe what a healthcheck is and how a container reports healthy versus unhealthy.
- Explain what the `--wait` flag guarantees that a plain start command does not.
- Recognise the common mistakes around readiness and recover from them.

Prerequisites:

- [Containers versus virtual machines](10-containers-01-containers-vs-vms.md) introduces the container model Compose orchestrates.
- [Named Docker volumes and data persistence](07-database-04-named-docker-volumes.md) covers the volumes that Compose services mount for durable data.

## Problem it solves

The concrete problem is running several cooperating services together during
local development without a fragile pile of manual commands. A typical
application needs a database, perhaps a cache, perhaps an object store, plus the
application itself — each a separate container with its own image, ports,
environment variables, and storage. Starting these by hand, in the right order,
with the right flags, every time you sit down to work, is tedious and
error-prone.

A prior approach was a shell script full of individual container-run commands.
That approach has real costs:

- The script encodes startup order and flags imperatively, so it is hard to read as a description of the system and easy to break.
- There is no shared, declarative record of what the stack _is_ — only a sequence of commands that happen to produce it.
- The script usually starts a container and moves on immediately, with no notion of whether the service inside is actually ready, so the next step races against a service that has not finished starting.

Docker Compose solves the first two by replacing the script with one declarative
file that names every service and its configuration. Healthchecks and the
`--wait` flag solve the third by defining what "ready" means per service and
making startup block until every service reports ready.

## Mental model

Think of opening a shopping centre for the day: unlocking the doors is not the
same as the shops being ready to serve customers.

1. The centre manager unlocks every shop's shutter at once (Compose starts every service container).
2. Each shop still has to switch on its tills, load its register, and put staff in place before it can serve anyone (each service initialises inside its container).
3. A supervisor walks the floor and asks each shop "are you ready?" on a regular beat (the healthcheck probe runs every few seconds).
4. A shop answers "ready" only when its tills are live (the probe succeeds), and "not yet" otherwise (the probe fails).
5. The centre is declared open to customers only once every shop has answered "ready" (the `--wait` flag holds startup until all healthchecks pass).

Unlocking the shutters is instant; being ready to serve takes longer and differs
per shop. The healthcheck is the supervisor's question, and `--wait` is the rule
that the doors stay closed to customers until every shop has said yes.

## How it works

A Compose file is a declarative description of a multi-service application. Each
service entry names the image to run and the configuration that service needs —
environment variables, published ports, mounted storage, and so on. One command
reads the file, creates a shared network, and starts every service on it, so the
services can reach each other by name. Because the file is declarative, it is
both the thing you run and the documentation of what the stack contains.

A healthcheck is a command the engine runs inside a container on a fixed
interval to decide whether the service is healthy. The check defines the probe
command, how often to run it, how long to wait before giving up on a single run,
and how many consecutive failures mark the container unhealthy. A container with
a healthcheck moves through states: starting, then healthy once the probe
succeeds, or unhealthy after enough consecutive failures. This turns the binary
"running or not" into the more useful "running and actually ready," because the
probe tests the real service — connecting to the database, pinging the cache,
fetching a health endpoint — rather than merely checking that the process exists.

The readiness gap is the reason `--wait` exists. Starting a container returns as
soon as the process launches, long before the service inside has finished
initialising — a database has to prepare its data directory, a cache has to load,
a web server has to bind its port. A start command that returns immediately lets
the next step run while services are still warming up, which is the classic cause
of "connection refused" races in scripts and tests. The `--wait` flag changes the
contract: the start command does not return until every service that defines a
healthcheck reports healthy (or until a service is judged to have failed). After
it returns successfully, the whole stack is genuinely ready, so the next step can
safely connect.

## MatchLayer Phase 1 usage

The local stack is described in `docker-compose.yml`, which declares three
services — a database, a cache, and an object store — on one network. The
database service defines a healthcheck whose probe asks the database itself
whether it is ready to accept connections, on a tight interval with a generous
retry budget for first boot:

Source: `docker-compose.yml`

```yaml
healthcheck:
  test: ["CMD-SHELL", "pg_isready -U matchlayer -d matchlayer"]
  interval: 2s
  timeout: 3s
  retries: 30
```

The probe command is the real readiness test for that service: it succeeds only
when the database is actually accepting connections, not merely when the
container process exists. The file's own header comment documents the intended
way to bring the stack up, which pairs the start command with the readiness flag:

Source: `docker-compose.yml`

```yaml
#   docker compose up -d --wait
```

Running with `--wait` means the command returns only after the database, cache,
and object store have all reported healthy, so a test suite or a back-end process
that starts next can connect immediately instead of racing a half-started
database. Each of the three services defines its own healthcheck, so "ready"
is defined per service and the wait covers the whole stack.

## Common pitfalls

- **Mistake:** Treating "the container started" as "the service is ready" and connecting to a database the instant the start command returns.
  **Symptom:** The first connection fails with a connection-refused or not-ready error because the service inside was still initialising.
  **Recovery:** Define a healthcheck that probes real readiness and start the stack with the `--wait` flag so startup blocks until every service reports healthy.

- **Mistake:** Writing a healthcheck that only confirms the process is alive (for example, checking a process exists) rather than that the service can do its job.
  **Symptom:** The container reports healthy while clients still get errors, because the probe never tested the actual service path.
  **Recovery:** Probe the real interface — connect to the database, ping the cache, fetch a health endpoint — so "healthy" means "able to serve."

- **Mistake:** Setting the retry budget or start period too low for a service that is slow on first boot.
  **Symptom:** A freshly created stack is marked unhealthy and `--wait` reports failure even though the service would have become ready a few seconds later.
  **Recovery:** Give first boot enough headroom with a sufficient retry count or start period so initial initialisation is not mistaken for failure.

- **Mistake:** Assuming `--wait` orders service startup so dependencies are fully ready before dependents start.
  **Symptom:** A dependent service still starts alongside its dependency and errors during the warm-up window, because `--wait` gates the command's return, not inter-service ordering.
  **Recovery:** Declare dependency-with-health conditions between services so a dependent waits for its dependency's healthcheck, in addition to using `--wait` on the overall startup.

## External reading

- [Docker Compose: overview](https://docs.docker.com/compose/)
- [Docker Compose: services top-level element and healthcheck](https://docs.docker.com/reference/compose-file/services/)
- [Docker Compose: up command and the --wait flag](https://docs.docker.com/reference/cli/docker/compose/up/)
- [Docker: Dockerfile health-probe instruction](https://docs.docker.com/reference/dockerfile/)
