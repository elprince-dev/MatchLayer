# How Phase 1 hosting preserves the AWS migration path

## Introduction

This document explains why the places MatchLayer runs in Phase 1 were chosen
so that moving to a full Amazon Web Services (AWS) deployment later is a small,
low-risk change rather than a rewrite. AWS is Amazon's cloud platform — a
collection of rented computing, storage, and networking services billed by
usage. The recurring idea here is the _migration path_: the sequence of changes
needed to relocate a running system from one set of hosts to another, and how
early decisions either keep that path short or quietly lengthen it. Phase 1
deliberately runs the frontend, the backend, and file storage on inexpensive
hosts while depending only on interfaces that the eventual AWS target also
speaks, so the production move swaps environments instead of rebuilding the
application. This topic sits in the Hosting and deploy track because it ties
together the runtime, the container, and the storage choices made across all of
Phase 1.

**Learning outcomes** — after reading this document you will be able to:

- Explain what a migration path is and what it means to keep it short.
- Describe how depending on a portable interface, rather than a vendor-specific one, preserves the option to move hosts later.
- Identify the three Phase 1 hosting choices that keep the AWS move close to an environment swap.
- Recognise the difference between a portability decision and premature work for a future deployment.

Prerequisites:

- [Containers versus virtual machines](10-containers-01-containers-vs-vms.md) explains the portable runtime unit this document relies on.
- [The production Dockerfiles](10-containers-05-production-dockerfiles.md) explains how the backend is packaged for any container host.
- [Postgres versus MinIO and why both are used](07-database-02-postgres-vs-minio.md) explains the local stand-in for cloud object storage.

## Problem it solves

The problem is vendor lock-in across a migration. _Vendor lock-in_ is the state
where an application leans so heavily on one provider's proprietary features
that leaving means rewriting large parts of it. A team that builds directly
against a single provider's unique tools early often discovers, when it is time
to move or scale, that the move is a months-long porting project instead of a
configuration change.

A common prior approach is to pick whatever host is cheapest or quickest to set
up in the early days and wire the code straight into that host's bespoke
interfaces — its own file-access methods, its own database service, its own
deployment format. That gets an early build running fast, but it couples the application to
that host. When the project later needs the scale, reliability, or compliance of
a larger cloud, every one of those bespoke couplings becomes a separate thing to
unpick and re-test.

Keeping the migration path short solves that. By choosing early hosts that mimic
the shape of the intended production cloud, and by depending only on interfaces
that both the early host and the production cloud implement, the eventual move
becomes mostly a matter of pointing the same code at new addresses and
credentials. The backend runs on a host chosen for its close parity with the
container runtime planned for the Phase 6 AWS production architecture, so
promoting the service later is mostly an environment swap rather than a rewrite.

## Mental model

Think of a touring band that rehearses in a small local hall but plans to play a
large arena later. If the band wires its gear to the small hall's one-of-a-kind
sockets, it cannot plug in anywhere else without rebuilding its rig. If instead
it uses the standard plugs that every venue provides, the same rig rolls into
the arena and works on arrival. The standard plug is the portable interface; the
band's rig is the application; the arena is the production cloud.

A migration-friendly setup follows these steps:

1. Identify the production target's standard interfaces — the "plugs" the big venue offers — before committing to an early host.
2. Pick early hosts that expose those same standard interfaces, even if they are smaller or cheaper, so the rig plugs into both.
3. Write the application against the standard interface only, never against a host's private extensions, so nothing in the code knows which venue it is in.
4. Keep everything that differs between hosts — addresses, credentials, region names — in configuration that is supplied from outside the code.
5. When it is time to move, change only that outside configuration and roll the unchanged rig into the arena.

The payoff of the walkthrough is that the riskiest part of a move — changing the
application itself — never happens, because the application was built to be
indifferent to where it runs.

## How it works

Three transferable techniques keep a migration path short, and they reinforce
one another.

The first is depending on a _de-facto standard interface_ — an interface that
many independent providers implement the same way, so code written against it
runs unchanged against any of them. Object storage is the clearest example: one
widely-copied storage interface is implemented both by the original cloud
service and by numerous self-hosted and competing products. An application that
talks to that interface through a standard client library does not encode which
implementation is on the other end; only an endpoint address and credentials
distinguish them. Swapping a local implementation for the cloud one is then a
configuration change, not a code change.

The second is packaging the application as a _container_ — a self-contained,
portable bundle of the program plus everything it needs to run, built once and
executed identically on any compliant runtime. Because the same image runs on a
small host and on a large cloud's container service without rebuilding, the unit
that gets deployed is already the unit the production environment will accept.
The runtime becomes interchangeable; what moves between hosts is a fixed
artifact rather than a freshly assembled one.

The third is _configuration from the environment_. Anything that varies between
where the app runs locally and where it runs in production — connection
addresses, secret keys, region identifiers, allowed origins — is read from
environment variables supplied at startup, never hard-coded. The code stays
byte-identical across hosts; only the injected values differ. A move then
reduces to providing a new set of values.

Put together, these three mean the migration path collapses to: build the same
container, point its environment variables at the new cloud's standard
interfaces, and start it. The application logic, the storage calls, and the
request handling are all untouched, which is what makes the move low-risk.
Crucially, none of this requires building the future system early — it only
requires refusing to depend on anything the future system cannot also provide.

## MatchLayer Phase 1 usage

MatchLayer realises all three techniques in Phase 1. The clearest is the
object-storage abstraction in `apps/api/src/matchlayer_api/core/storage.py`,
which builds a single Amazon Simple Storage Service (Amazon S3) client. Amazon
S3 is AWS's object-storage service, and its
application programming interface (API) is the de-facto standard that MinIO —
the S3-compatible store run during local development — also implements. The
client is constructed the same way regardless of which store is behind it:

Source: `apps/api/src/matchlayer_api/core/storage.py`

```python
def _build_s3_client(settings: Settings) -> Any:
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key_id,
        aws_secret_access_key=settings.s3_secret_access_key.get_secret_value(),
    )
```

The only thing that distinguishes local MinIO from real Amazon S3 is the value
of `endpoint_url`, and that value comes from configuration rather than code. The
settings model in `apps/api/src/matchlayer_api/config.py` makes the endpoint
optional precisely so the same code targets either store:

Source: `apps/api/src/matchlayer_api/config.py`

```python
    s3_endpoint_url: str | None = None
    s3_region: str = "us-east-1"
    s3_access_key_id: str
    s3_secret_access_key: SecretStr
    s3_bucket: str
```

Leaving `s3_endpoint_url` unset makes the client talk to real AWS S3; setting it
to MinIO's address keeps everything local. Because resume files already live in
real Amazon S3 rather than a local-only store, the object-storage interface the
Phase 6 migration inherits is the production one from day one, so no storage code
has to change when the rest of the stack follows. The same pattern holds for the
container packaging in `infra/docker/api.Dockerfile` and the service definitions
in `docker-compose.yml`: the backend is shipped as one portable image and every
host-specific value reaches it through environment variables, so the artifact
and its configuration — not the application code — are what change when the host
changes.

## Common pitfalls

- **Mistake:** Calling a host's proprietary storage or deployment feature directly from application code because it is convenient now.
  **Symptom:** A later attempt to run the same code against a different host fails with errors about unknown operations or missing services, and the fix touches many files.
  **Recovery:** Route the access through a standard client and interface that both hosts implement, and confine any host-specific detail to a single, swappable construction point.

- **Mistake:** Hard-coding endpoint addresses, region names, or credentials as literals in the source.
  **Symptom:** Moving to a new host means editing and re-testing code, and secrets end up committed or duplicated across environments.
  **Recovery:** Read every value that varies between hosts from environment variables supplied at startup, so relocating is a configuration change with no code edit.

- **Mistake:** Building the future production cloud's infrastructure early "to be ready" instead of staying portable.
  **Symptom:** Time and money go into managed services that sit unused, and the early build carries operational weight it does not need yet.
  **Recovery:** Keep the cheap early hosts, depend only on interfaces the future cloud also offers, and defer the actual provisioning until the move is genuinely due.

- **Mistake:** Assuming a local stand-in and the real cloud service behave identically in every respect.
  **Symptom:** Code that works locally fails in the cloud on edge behaviours such as access-control defaults or error shapes that the stand-in does not reproduce.
  **Recovery:** Depend only on the well-specified core of the shared interface, test against the real service before the move, and avoid relying on quirks unique to the local stand-in.

## External reading

- [Amazon S3 User Guide — what is Amazon S3](https://docs.aws.amazon.com/AmazonS3/latest/userguide/Welcome.html)
- [Docker — multi-stage builds](https://docs.docker.com/build/building/multi-stage/)
- [Docker — Dockerfile reference](https://docs.docker.com/reference/dockerfile/)
