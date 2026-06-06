# AWS S3 as the Phase 1 file-storage backend

## Introduction

This document explains the file-storage backend that holds uploaded resume
files in Phase 1: Amazon Simple Storage Service (S3), a cloud service from
Amazon Web Services (AWS) that stores whole files as "objects" under string
keys inside flat containers called buckets. Phase 1 keeps small structured
records in a relational database, but a resume is a whole file you save and
hand back unchanged, so it belongs in an object store rather than a database
column. S3 is that object store, and this document shows why the project picks
it, how the S3 service is shaped, and how the same storage code talks to a
local stand-in during development and to the real AWS service in production.

**Learning outcomes** — after reading this document you will be able to:

- Describe what an object store is and what S3 stores: objects addressed by a key inside a bucket.
- Explain how one S3-compatible client can target a local emulator in development and the real AWS service in production by changing only an endpoint setting.
- Explain why object keys are random identifiers and why uploaded objects are kept private rather than publicly readable.

Prerequisites:

- [Postgres versus MinIO: two stores, two jobs](07-database-02-postgres-vs-minio.md) introduces the object-store concept and the local S3-compatible service this document maps onto real AWS S3.
- [Secrets management and `.env` discipline](05-security-04-secrets-management.md) covers how the access credentials this document reads are kept out of source control.

## Problem it solves

An application that accepts uploaded files has to put those file bytes
somewhere durable, where they survive a server restart and can be fetched back
later. The concrete problem is storing potentially large binary files — a
resume in Portable Document Format (PDF) or a Word document — reliably and
cheaply, while keeping them private to the person who uploaded them.

A common prior approach is to write uploaded files onto the server's own local
disk, into a folder beside the application. That approach breaks down quickly:

- The files vanish when the server is replaced, redeployed, or scaled to a second machine, because each machine has its own separate disk.
- A single local disk has a fixed size and no built-in redundancy, so it fills up and a disk failure loses every file at once.
- Two or more application instances cannot share the same local folder, so a file written by one instance is invisible to the others.

A managed object store solves all three: it is a separate, durable service that
any number of application instances reach over the network, it scales to vast
numbers of large files, and it replicates data so a single hardware failure
does not lose anything. The application keeps only a short key string in its
database and fetches the bytes from the object store on demand.

## Mental model

Think of a coat check at a large venue. You do not stuff your coat into your
pocket; you hand it over and get a numbered ticket back:

1. You arrive with a coat (the file bytes) you want stored safely while you are inside.
2. The attendant hangs it on a rack and gives you a ticket with a unique number (the object key).
3. The rack lives in a back room run by the venue (the object-store service), not in your seat, so it is safe even if you move seats or leave and come back.
4. To get the coat back you present the ticket number; the attendant fetches exactly that coat. You never describe the coat — the number alone is enough.
5. Your seat stub (the small database row) only needs to remember the ticket number, not the coat itself.

The venue's back room is the object store; the ticket number is the key; the
coat is the object. The store is shaped to hold bulky items and return them by
ticket, which is a different job from the seating chart that tracks who sits
where.

## How it works

An object store keeps each file as an _object_: the raw bytes plus a little
metadata (such as a content type), addressed by a unique _key_ string inside a
named _bucket_. A bucket is a flat namespace — there are no real folders, only
keys that may contain slashes that look like folders. The core operations are
deliberately small: put an object under a key, get an object by its key, delete
it by its key, and list keys. There is no query-by-contents and no joining; the
store hands back whole objects by key and nothing more.

The Amazon Simple Storage Service exposes exactly that interface over the
network as a well-known protocol. Because the protocol is public and stable,
other products implement the same protocol — including local, self-hosted
servers you can run on your own machine for development. Any client written
against the protocol works the same way against any implementation of it; the
client only needs to know four things to send a request:

- an **endpoint** — the network address of the service. When the endpoint is left unset, a client library defaults to the real Amazon service; when it is set to a local address, the same client talks to a local emulator instead.
- a **region** — the geographic location label the service uses to route and place data.
- an **access key identifier** and a **secret access key** — the credential pair that authenticates the caller. The secret is the sensitive half and must be protected like a password.

A client library — a Software Development Kit (SDK) — wraps these
into convenient calls. Constructing the client once with the endpoint, region,
and credentials yields an object you can call `put` and `get` on; swapping only
the endpoint value redirects every one of those calls from a local emulator to
the production cloud service without changing any of the calling code.

Two access-control facts matter from the start. First, objects are private by
default: a freshly written object is readable only by authenticated callers
with permission, never by the anonymous public, unless someone explicitly opens
it up. Second, the key is the only handle to an object, so choosing keys that
do not leak information — random identifiers rather than user-supplied
filenames — keeps both the contents and the original filename out of any
guessable address.

## MatchLayer Phase 1 usage

MatchLayer talks to S3 through a single thin wrapper so that the rest of the
backend never touches the storage client directly. That wrapper — the storage
abstraction, meaning the one module that hides whether the store is a local
emulator or real AWS S3 — lives at `apps/api/src/matchlayer_api/core/storage.py`
and is the only module that imports the AWS client library `boto3` (declared as
a dependency in `apps/api/pyproject.toml`).

The client is built once from the S3 settings. The key detail is the endpoint:
in production it is left unset so the client targets real AWS S3, while local
development points it at the S3-compatible MinIO container. The same function
serves both:

Source: `apps/api/src/matchlayer_api/core/storage.py`

```python
def _build_s3_client(settings: Settings) -> Any:
    """Construct a ``boto3`` S3 client from the S3 ``Settings`` fields.

    ``s3_endpoint_url`` is ``None`` in production so ``boto3`` targets real
    AWS S3; MinIO supplies a non-AWS URL locally. The secret is read via
    :meth:`SecretStr.get_secret_value` only here, at client-construction
    time, so the plaintext secret never lingers on the storage instance or
    in any ``repr``.
    """
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key_id,
        aws_secret_access_key=settings.s3_secret_access_key.get_secret_value(),
    )
```

Those settings come from environment variables, with the committed sample file
wiring the local defaults to the MinIO container. The endpoint is set to the
local MinIO address for development; production deployments leave it empty so
the client reaches the real AWS service:

Source: `.env.example`

```bash
MATCHLAYER_S3_ENDPOINT_URL=http://localhost:9000
MATCHLAYER_S3_REGION=us-east-1
MATCHLAYER_S3_ACCESS_KEY_ID=matchlayer
MATCHLAYER_S3_SECRET_ACCESS_KEY=dev_only_password
```

The local S3-compatible service is the `minio` container declared in
`docker-compose.yml`, so a fresh checkout has a working object store without any
AWS account. Two project rules ride on top of this client. Objects are written
with no public-read access — the write passes no Access Control List (ACL)
argument, so each object inherits the bucket's default-private visibility — and
object keys are random, filename-free identifiers, so a client-supplied
filename never reaches the key or any path. Choosing AWS S3 as the real backend
from day one, while developing against a local emulator of the same protocol,
is what lets the storage code stay byte-for-byte identical between Phase 1's
Fly.io-hosted backend and the later full AWS deployment.

## Common pitfalls

- **Mistake:** Hard-coding the production endpoint (or leaving a local endpoint set) instead of letting the endpoint setting switch environments.
  **Symptom:** Development uploads land in the real cloud bucket, or production traffic tries to reach a `localhost` address that does not exist on the server and every upload fails to connect.
  **Recovery:** Drive the endpoint from configuration only — set it to the local emulator address in development and leave it unset in production — and never embed an environment-specific address in code.

- **Mistake:** Making the bucket or its objects publicly readable to "make downloads easy".
  **Symptom:** Resume files become reachable by anyone with the object address, and a security or privacy review flags the bucket as world-readable.
  **Recovery:** Keep objects private (write them with no public-read grant), block public access at the bucket level, and serve files through authenticated application requests or short-lived signed links instead.

- **Mistake:** Using the uploader's original filename as the object key.
  **Symptom:** Two users uploading `resume.pdf` collide and overwrite each other, and the key leaks the original filename to anyone who can see the address.
  **Recovery:** Mint a random, unique key (for example a time-ordered identifier) for every object and keep the original filename only as a separate display-only field.

- **Mistake:** Committing the secret access key into source control or a sample environment file.
  **Symptom:** A secret scanner flags the repository, or the leaked credential is used to read and write the bucket from outside the application.
  **Recovery:** Keep real credentials in an ignored local environment file or a managed secret store, commit only placeholder values, and rotate any key that was ever committed.

## External reading

- [Amazon S3 — what is Amazon S3?](https://docs.aws.amazon.com/AmazonS3/latest/userguide/Welcome.html)
- [Amazon S3 — blocking public access to your storage](https://docs.aws.amazon.com/AmazonS3/latest/userguide/access-control-block-public-access.html)
- [boto3 — S3 client reference](https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/s3.html)
- [MinIO object storage documentation](https://min.io/docs/minio/container/index.html)
