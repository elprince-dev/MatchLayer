# Fly.io as the Phase 1 backend host

## Introduction

Fly.io is a hosting platform that takes a container image — a self-contained,
runnable bundle of an application together with the operating-system pieces it
needs — and runs it on managed servers close to your users, without you renting
or patching a server yourself. This document explains why the Phase 1 back-end
interface is hosted on Fly.io, how a platform like it turns a built image into a
running service, and where the Phase 1 deploy is grounded in real files in this
repository. It is written for a reader who has never deployed anything to a cloud
host before, so every platform-specific term is defined as it appears.

**Learning outcomes** — after reading this document you will be able to:

- Explain what a hosting platform does with a container image and why that is simpler than running your own server.
- Describe the deploy lifecycle of a managed platform: build, push, release, and run with health checks.
- Identify which committed file defines the image Fly.io runs for the Phase 1 back-end, and read the runtime instructions that govern the container once it is live.

Prerequisites:

- [Containers versus virtual machines](10-containers-01-containers-vs-vms.md) — explains what a container image is, which this document assumes you can picture.
- [The production Dockerfiles, instruction by instruction](10-containers-05-production-dockerfiles.md) — walks through the recipe that builds the image the platform runs.

## Problem it solves

The concrete problem is getting a back-end service reachable on the public
internet, over an encrypted connection, restarting it when it crashes, and doing
all of that on a near-zero budget — without becoming a part-time systems
administrator. A solo developer who wants the Phase 1 application reachable needs
a public address, a process that stays up, automatic restarts on failure, and a
place to store secrets, and they need it to cost almost nothing while the product
has no users.

A prior approach was to rent a bare virtual machine — a software-emulated
computer you get full control over — and do everything by hand: install a
language runtime, copy the code up, configure a process supervisor so the app
restarts after a crash, obtain and renew a Transport Layer Security (TLS)
certificate for encryption, and set up a reverse proxy. That works, but it has
real costs:

- Every one of those steps is manual, easy to get subtly wrong, and lives only in the operator's memory.
- Security patching the operating system is now your ongoing job.
- There is no built-in notion of "is this process healthy?"; you build that yourself.

A managed platform such as Fly.io removes that whole layer of work. You hand it a
container image and a small amount of configuration; it provisions the machine,
gives the service a public encrypted address, restarts it on failure, and watches
a health check you define. The operator's job shrinks to "produce a good image and
describe how to run it."

## Mental model

Think of a managed hosting platform as a **valet parking service for your
application**. You do not hand the valet your car's blueprints and ask them to
build a car; you hand them a finished, drivable car (the built container image)
and a small instruction card (which door it listens at, how to tell if it is
running well). The valet finds a parking spot (a server), keeps the engine
running, and fetches help if it stalls. You never see the parking garage.

A second handhold is the deploy lifecycle as a numbered sequence. Every time you
ship a new version, the platform walks these steps:

1. **Build** — turn the source and its recipe into a container image, either on your machine or on the platform's builders.
2. **Push** — upload that image to a registry, which is a versioned storage service for images.
3. **Release** — the platform records the new image as the current version and prepares to swap traffic to it.
4. **Run** — the platform starts one or more _machines_ (its name for a small, fast-booting virtual machine that runs your container) from the image.
5. **Health-gate** — before sending real traffic, the platform runs the health check you defined; only a passing machine receives requests.

If a machine later fails its health check or the process exits, the platform
restarts it from the same image. Picturing these five steps is enough to follow
everything below.

## How it works

A managed application platform sits between a built artifact and the public
internet. The unit it accepts is a container image: a read-only bundle containing
the application and the runtime libraries it depends on, addressed by a unique
identifier. Because the image already carries everything the program needs to
run, the platform does not care which language or framework is inside — it only
needs to start the image and route traffic to it.

The deploy begins with a build. The platform reads a build recipe (a Dockerfile,
the plain-text list of steps that assembles an image) and produces an image,
then uploads it to a registry keyed by version. A release step marks that image
as current. The platform then boots one or more lightweight virtual machines from
the image. Each runs the image's start command — the single foreground process
the container launches — and listens on the network port the image declares.

Three platform responsibilities make this production-worthy. First, **encrypted
ingress**: the platform terminates HyperText Transfer Protocol Secure (HTTPS)
traffic at its edge, obtaining and renewing the TLS certificate automatically, so
the application itself can speak plain HyperText Transfer Protocol (HTTP)
internally while the public
connection stays encrypted. Second, **health checking**: the platform
periodically runs a check the image or configuration defines — commonly an HTTP
request to a dedicated endpoint that returns a success status only when the app is
ready — and withholds traffic from, or restarts, any instance that fails. Third,
**configuration and secrets injection**: values the app needs at runtime, such as
a database connection string, are supplied as environment variables (named values
present in the running process's environment) rather than baked into the image,
and sensitive ones are stored encrypted by the platform and revealed only to the
running machine.

Cost control on these platforms usually comes from two levers: small machine
sizes built on a shared Central Processing Unit (CPU) — the chip that executes
instructions, divided across many tenants — and the ability to stop machines when idle and start them
on the next request. A small service with no traffic can therefore cost nothing
while still being reachable. Crucially, because the deploy contract is "a
container image plus runtime configuration," the same image can later run on a
different platform with little change — the image is portable even though the
hosting around it is not.

## MatchLayer Phase 1 usage

In Phase 1 the back-end interface is hosted on Fly.io's free shared-CPU machine
tier, chosen for its closeness to the eventual Amazon Web Services container
runtime so a later migration is mostly a configuration swap rather than a
rewrite. There is no committed Fly.io configuration file (a `fly.toml`) or
standalone deploy script in the repository yet — Phase 1 stops at producing the
deployable image and defers the platform-specific deploy wiring. What Fly.io
actually runs is the production image defined by `infra/docker/api.Dockerfile`,
so that file is the source of truth for the container's runtime behaviour, and
its instructions map directly onto the platform responsibilities described above.

The image declares the network port the service listens on, after dropping to a
non-administrative user. Fly.io routes external traffic to this port:

Source: `infra/docker/api.Dockerfile`

```dockerfile
USER nonroot

EXPOSE 8000
```

The image also defines the health check the platform relies on to decide whether
a machine is ready for traffic. It makes a request to the application's health
endpoint and succeeds only on an HTTP 200 response:

Source: `infra/docker/api.Dockerfile`

```dockerfile
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz').status == 200 else 1)"]
```

Finally, the image names the single foreground process the container launches —
the web server — which is what every Fly.io machine started from this image
runs:

Source: `infra/docker/api.Dockerfile`

```dockerfile
ENTRYPOINT ["uvicorn", "matchlayer_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

Two consequences follow from anchoring the deploy to this image. The database
connection string is never baked into the image; it is supplied to the running
machine as an environment variable, which keeps the secret out of version control
and out of the registry. And because the contract is "run this image, route to
this port, gate on this health check," the same `infra/docker/api.Dockerfile`
artifact is what a later Amazon Web Services container service would run, which is
exactly why this host was picked.

## Common pitfalls

- **Mistake:** Assuming the public address is plain, unencrypted HTTP because the image's start command binds to a non-encrypted port (here, port 8000).
  **Symptom:** Confusion about where encryption happens, or an attempt to add certificate handling inside the application that duplicates what the platform already does.
  **Recovery:** Remember the platform terminates HTTPS at its edge and forwards plain HTTP internally; the application correctly listens on a non-encrypted internal port, and the public connection is still encrypted.

- **Mistake:** Putting the database connection string or other secrets into the image or its build recipe so the app always carries them.
  **Symptom:** Secrets appear in the registry and in image history, and rotating a secret now requires rebuilding and redeploying the image.
  **Recovery:** Supply secrets as runtime environment variables set on the platform; keep the image free of any credential so the same image is safe to store and reuse.

- **Mistake:** Defining a health check that returns success before the application can actually serve requests, or pointing it at the wrong path.
  **Symptom:** The platform routes traffic to a machine that then returns errors, or it endlessly restarts a machine it wrongly believes is unhealthy.
  **Recovery:** Point the health check at an endpoint that returns a success status only when the service is truly ready, and give it a start-period grace window so a slow first boot is not mistaken for a failure.

- **Mistake:** Expecting a machine that has been stopped to save cost to respond instantly on the very first request after idling.
  **Symptom:** The first request after a quiet period is noticeably slow or times out while a stopped machine boots.
  **Recovery:** Account for cold-start latency when scaling to zero is enabled; raise the client timeout, or keep a minimum of one machine running if first-request latency matters.

## External reading

- [Fly.io: official documentation](https://fly.io/docs/)
- [Fly.io: how Fly.io machines work](https://fly.io/docs/machines/)
- [Docker: Dockerfile reference](https://docs.docker.com/reference/dockerfile/)
- [MDN Web Docs: HTTPS](https://developer.mozilla.org/en-US/docs/Glossary/HTTPS)
