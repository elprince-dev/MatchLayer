# The S3 and MinIO storage abstraction

## Introduction

This document explains the storage abstraction — the single, narrow layer of
code that hides which object store is actually holding a file behind one
unchanging set of operations (write a file, read it back, remove it). The store
underneath is a local stand-in during development and Amazon Web Services (AWS)
Simple Storage Service (S3) in production, yet the rest of the application calls
the same `put`, `get`, and `delete`-style methods either way. The point of the
abstraction is that switching the real backend changes one configuration value,
never the calling code, so the same software runs unmodified on a laptop and in
the cloud.

This is the interface-and-wrapper view of object storage. A companion document,
listed under Prerequisites below, covers what S3 is as an AWS service; this one
covers the thin wrapper that presents one identical interface over both a local
emulator and the real cloud service.

**Learning outcomes** — after reading this document you will be able to:

- Explain what a storage abstraction is and why hiding the concrete backend behind a fixed interface keeps the rest of an application unaware of where files live.
- Describe how one client built for the S3 protocol targets a local emulator in development and the real cloud service in production by changing only an endpoint setting.
- Identify the small, stable operation set (write, read, delete) that the wrapper exposes and explain why keeping that surface narrow is what makes the two backends interchangeable.

Prerequisites:

- [AWS S3 as the Phase 1 file-storage backend](12-hosting-07-aws-s3-in-phase-1.md) introduces S3 as a cloud service, its buckets-and-objects model, and the local emulator this abstraction sits on top of.
- [Async Python and the asyncio model](03-backend-03-async-python-and-asyncio.md) explains the cooperative scheduling model this wrapper protects when it moves blocking network calls onto worker threads.

## Problem it solves

An application that stores uploaded files needs to read and write those bytes
from many places in the code: an upload handler saves a file, a download
handler fetches it, a cleanup task removes it. The concrete problem is that the
real storage service differs between environments — a developer's laptop has no
cloud account, while production talks to a managed cloud store — and the code
that reads and writes files should not have to care about that difference.

A common prior approach is to call the storage service's client library
directly from every place that touches a file. That spreads the same setup and
the same backend-specific assumptions across the whole codebase, and it creates
two concrete problems:

- Every call site has to know how to build and configure a client, so a change to credentials, region, or endpoint must be repeated in many files instead of one.
- Swapping the backend, or pointing development at a local emulator, means editing every one of those call sites, because the environment-specific details have leaked out of one place and into all of them.

A storage abstraction solves both by collecting all of that into a single
module. Every other part of the application calls a small, fixed set of methods;
only that one module knows which backend is live and how to reach it. The
backend becomes a configuration detail rather than a code structure, so the same
binary runs against a local emulator or the production cloud with no source
change.

## Mental model

Think of a universal travel power adapter. Your laptop charger has one plug, but
the wall socket is shaped differently in every country:

1. You arrive in a new country with the same charger you always use (the application code that wants to store a file).
2. You plug the charger into the travel adapter (the storage abstraction), which always presents the same socket shape to your charger.
3. The adapter's other side is configured for the local wall socket — a different shape in each country (the concrete backend: a local emulator or the cloud service).
4. You change which country plate the adapter uses (one configuration value), and nothing about your charger changes.
5. Your charger never learns which country it is in; it only ever sees the adapter's fixed socket.

The charger is the rest of the application, the adapter is the abstraction with
its fixed interface, and the country-specific plate is the configurable backend.
Because the charger only ever meets the adapter's fixed face, the same charger
works everywhere, and changing countries is a setting on the adapter rather than
a rewiring of the charger.

## How it works

A storage abstraction is built around the idea of an _interface_: a small,
named set of operations that callers depend on, separated from the concrete
implementation that fulfils them. For object storage the interface stays
deliberately tiny — write some bytes under a key, read the bytes back by that
key, and delete by that key. Callers are written against those operation names
and nothing else, so they never hold a reference to the underlying client or its
configuration.

The reason one implementation can serve two environments is that S3 is a
_protocol_, not only a single company's product. The protocol is a public,
stable network contract — an Application Programming Interface (API), the
defined set of requests one program sends to another — for the put/read/delete
operations, and more than one server implements it: the real cloud service
implements it, and so do local,
self-hosted emulators you can run on your own machine. A client library — a
Software Development Kit (SDK), meaning a packaged set of helper functions that
turn the network protocol into ordinary method calls — only needs four facts to
send a request:

- an **endpoint** — the network address of the service. Left unset, the client defaults to the real cloud service; set to a local address, the same client talks to an emulator instead.
- a **region** — the location label the service uses to place and route data.
- an **access key identifier** and a **secret access key** — the credential pair that authenticates the caller, the secret half being the sensitive one.

Because every implementation of the protocol answers the same requests, a single
client constructed against the protocol works against any of them. This is what
makes the endpoint a switch: construct the client once with the endpoint,
region, and credentials, and every later call goes wherever the endpoint points.
The abstraction wraps that client so the operation names are all a caller sees,
and the construction logic lives in exactly one place.

One more concern shapes the wrapper. The standard client libraries for this
protocol are _synchronous_: each call blocks the calling thread until the
network round-trip finishes. In a server built on a cooperative single-threaded
scheduler — an event loop, which is the component that interleaves many tasks on
one thread by running each until it pauses — a blocking network call would
freeze every other in-flight request. The wrapper avoids that by handing each
blocking call to a background worker thread, so the scheduler stays free to make
progress on other work while the storage round-trip is in flight.

The request path through the abstraction is therefore a short, fixed chain:

1. Caller code asks the storage abstraction to write or read bytes by key, using the fixed put/get interface and nothing else.
2. The abstraction forwards the call to the one client it holds, which was built once against the protocol.
3. The client sends the request to whatever the endpoint setting points at — a local emulator or the cloud service — and the same request shape works against either.
4. The bytes (or an acknowledgement) travel back up the same chain to the caller, which never learns which backend answered.

## MatchLayer Phase 1 usage

MatchLayer routes every read and write of resume bytes through one module so the
rest of the backend never touches the object-storage client directly. That
module — the storage abstraction, meaning the single wrapper that hides whether
the store is a local emulator or the real cloud service — lives at
`apps/api/src/matchlayer_api/core/storage.py` and is the only module that
imports the AWS client library (declared as a dependency in
`apps/api/pyproject.toml`).

The wrapper exposes the storage operations as ordinary methods on one class. The
write path (`put`) and the read path (`get`) each wrap a single synchronous
client call in a small inner function and hand it to a worker thread, so a slow
object store never stalls the event loop. The methods take a key and bytes —
nothing about the backend — which is what keeps the interface identical
regardless of whether a local emulator or the production cloud answers:

Source: `apps/api/src/matchlayer_api/core/storage.py`

```python
    async def put(self, *, key: str, data: bytes, content_type: str) -> None:

        def _put() -> None:
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
            )

        await run_in_threadpool(_put)

    async def get(self, *, key: str) -> bytes:

        def _get() -> bytes:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
            return cast(bytes, response["Body"].read())

        return await run_in_threadpool(_get)
```

The one place that knows which backend is live is the client-construction
function, and the switch is the endpoint: production leaves the endpoint setting
empty so the client targets the real cloud service, while local development
points it at the emulator. Two project rules ride on this interface. A write
passes no Access Control List (ACL) argument, so each stored object inherits the
bucket's default-private visibility rather than becoming publicly readable, and
object keys are random, filename-free identifiers, so a client-supplied filename
never reaches the key. The validated content type stamped on each object — a
Multipurpose Internet Mail Extensions (MIME) type such as the one for a Portable
Document Format (PDF) file or a Word document (DOCX) — is passed in by the
caller, keeping the wrapper itself ignorant of file kinds. Building the wrapper
against the S3 protocol from day one, and developing against a local emulator of
the same protocol, is what lets this code stay byte-for-byte identical between
the Phase 1 hosting and the later full cloud deployment.

## Common pitfalls

- **Mistake:** Calling the storage client directly from upload, download, and cleanup code instead of going through the one wrapper module.
  **Symptom:** Client setup, credentials, and endpoint handling are duplicated across several files, and a change to any of them forces edits in every call site.
  **Recovery:** Route every read and write through the single abstraction module, and let only that module construct and hold the client; callers should import the wrapper, never the client library.

- **Mistake:** Hard-coding an environment-specific endpoint (a production address baked in, or a local address left in place) instead of switching it through configuration.
  **Symptom:** Development writes land in the real cloud bucket, or production calls try to reach a local address that does not exist on the server and every storage call fails to connect.
  **Recovery:** Drive the endpoint from a configuration value only — set it to the emulator address in development and leave it empty in production — so the same code follows the setting to the right backend.

- **Mistake:** Widening the abstraction's interface with backend-specific options (passing raw client parameters or vendor-only features through the wrapper methods).
  **Symptom:** The wrapper's methods grow arguments that one backend understands and the other rejects, and the emulator and the cloud service start behaving differently for the same call.
  **Recovery:** Keep the interface to the small, shared operation set (write, read, delete by key); if a feature exists on only one backend, keep it out of the shared methods so both backends stay interchangeable.

- **Mistake:** Calling the synchronous storage client straight from asynchronous request code without moving the blocking call off the event loop.
  **Symptom:** Under load the whole server becomes unresponsive while a storage round-trip is in flight, because one blocking call has frozen the single scheduling thread.
  **Recovery:** Dispatch each blocking client call to a worker thread (as the wrapper's read and write paths do) so the event loop stays free to serve other requests.

## External reading

- [Amazon S3 API reference](https://docs.aws.amazon.com/AmazonS3/latest/API/Welcome.html)
- [boto3 — S3 client reference](https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/s3.html)
- [MinIO object storage documentation](https://min.io/docs/minio/container/index.html)
- [Python documentation — running blocking calls in a thread pool with asyncio](https://docs.python.org/3/library/asyncio-eventloop.html)
