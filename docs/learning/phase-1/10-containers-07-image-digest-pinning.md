# Image digest pinning

## Introduction

This document explains image digest pinning: referring to a container base image
by its content digest — a cryptographic fingerprint of the image's exact bytes —
instead of, or alongside, a human-friendly tag like `16-alpine`. A container
image is the read-only template a container runs from, and it is normally fetched
from a registry, a server that stores and serves images. A tag is a movable
label a registry owner can repoint at different image contents over time; a
digest names one exact, unchangeable image. Pinning by digest matters because it
makes builds reproducible and closes a supply-chain hole where the contents
behind a familiar tag change underneath you.

**Learning outcomes** — after reading this document you will be able to:

- Explain the difference between a mutable tag and an immutable content digest.
- Describe what an image digest is and why it cannot point at different contents over time.
- Explain how digest pinning makes builds reproducible and resists supply-chain tampering.
- Recognise the common mistakes around digest pinning and recover from them.

Prerequisites:

- [Containers versus virtual machines](10-containers-01-containers-vs-vms.md) introduces the container and image model.
- [Docker images, layers, and the build cache](10-containers-02-docker-images-layers-and-cache.md) explains the content-addressing that digests build on.

## Problem it solves

The concrete problem is that a tag is a promise that can be broken. When a build
file says it starts from an image at a given tag, it trusts whoever controls that
tag to keep it pointing at the same contents. But tags are deliberately movable:
the owner of an image repository can republish the same tag pointing at new bytes
at any time — for a routine update, or maliciously. So two builds of the
"identical" file, run a week apart, can pull different base images and produce
different results, and a compromised registry can swap trusted-looking contents
behind a trusted-looking tag.

A prior approach was to reference base images only by tag and trust that the tag
was stable enough. That approach has real costs:

- Builds are not reproducible: the same Dockerfile yields different images as the tag moves, so a problem you cannot reproduce may turn out to be a changed base.
- A registry compromise or a hijacked tag silently injects altered contents into your image, because nothing verifies what you received matches what you expected.
- There is no record in the build file of exactly which bytes were used, so an audit cannot answer "what was actually in this image?"

Digest pinning solves this by naming the exact image by its content fingerprint.
The registry must serve bytes whose digest matches, or the pull is rejected, so
the build always gets precisely the image the file names — and can prove it did.

## Mental model

Think of the difference between a nickname and a tamper-evident sealed package
with a printed checksum:

1. A tag is a nickname like "the latest stable one" written on a sticky note: convenient, but someone can move the note to a different box whenever they like.
2. A digest is a checksum printed on a tamper-evident seal: it is computed from the exact contents of the box, so it identifies one specific box and no other.
3. When you ask for the box by nickname, you get whatever box the note is currently stuck to — possibly a different one than yesterday.
4. When you ask for the box by its sealed checksum, the warehouse must hand you a box whose contents produce that exact checksum, or you refuse it.
5. If anyone swaps the contents, the checksum no longer matches and the swap is detected immediately.

The nickname is for humans to read; the sealed checksum is what guarantees you
received the precise contents you asked for. Pinning by digest is insisting on
the checksum while keeping the nickname nearby as a label.

## How it works

A registry identifies image content in two ways. A tag is a mutable,
human-readable label that the repository owner can repoint at any image at any
time — that mutability is a feature for publishing updates, and a liability for
anyone trusting the tag to be stable. A digest is an immutable identifier
computed by hashing the image's content with a cryptographic hash function; the
result is written as the hash algorithm followed by the hash value. Because the
digest is derived from the bytes themselves, it is content-addressed: any change
to the content, however small, produces a different digest, and the same content
always produces the same digest.

Pinning by digest means writing the base-image reference as the name followed by
the digest rather than (or in addition to) a tag. When a build pulls a
digest-pinned reference, the registry returns the matching content and the client
verifies that what it received actually hashes to the requested digest. If the
bytes do not match — because the content was altered in transit or in the
registry — the digest check fails and the pull is rejected. This gives two
guarantees at once: reproducibility, because the reference can only ever resolve
to one exact image, so the same build file produces the same base every time; and
integrity, because tampered content cannot masquerade behind the expected digest.

The trade-off is that digests are opaque to humans and do not advertise what they
contain, so a common practice keeps the readable tag beside the digest as a
comment or label for review while the digest is what actually resolves. Updating a
pinned image is then a deliberate act: you look up the new digest for the tag you
want and change the reference on purpose, which is exactly the point — base-image
updates become reviewable changes in version control instead of invisible shifts.
Tools that watch for new image versions can automate proposing those digest
updates so pinning does not mean falling behind on patches.

## MatchLayer Phase 1 usage

Every base image in the local stack, declared in `docker-compose.yml`, is pinned
by digest with the readable tag kept inline for human review. The database
service reference is the tag followed by an at-sign and the content digest:

Source: `docker-compose.yml`

```yaml
image: postgres:16-alpine@sha256:16bc17c64a573ef34162af9298258d1aec548232985b33ed7b1eac33ba35c229
```

The file's header comment states the rationale directly — pinning by digest makes
the stack reproducible and protects against a mutable tag being repointed at
altered contents:

Source: `docker-compose.yml`

```yaml
# All images pinned by SHA256 digest so the stack is reproducible across
# machines and protected against supply-chain mutation of mutable tags.
# The human-readable tag is left in place as documentation; Docker
# resolves the @sha256 digest, not the tag.
```

The production Dockerfiles do the same for their base images. In
`infra/docker/api.Dockerfile`, the builder stage's base is pinned by digest while
a comment records the readable tag the digest corresponds to:

Source: `infra/docker/api.Dockerfile`

```dockerfile
# Tag pin (for human review): docker.io/library/python:3.13-slim
# Debian-trixie based; ships CPython 3.13 — same minor as the distroless final stage.
FROM python@sha256:b04b5d7233d2ad9c379e22ea8927cd1378cd15c60d4ef876c065b25ea8fb3bf3 AS builder
```

The web image, `infra/docker/web.Dockerfile`, follows the identical pattern for
its Node.js builder base: a readable tag in a comment and the digest on the base
instruction. Across all three files the rule is consistent — the digest is what
resolves, and the tag is documentation — so a build can only ever pull the exact
images these files name.

## Common pitfalls

- **Mistake:** Pinning only the tag and assuming a tag like a version number is immutable.
  **Symptom:** Builds that were identical start producing different base images over time, because the tag was repointed at new contents.
  **Recovery:** Add the content digest to the reference so it resolves to one exact image, keeping the tag inline only as a readable label.

- **Mistake:** Updating the readable tag comment but forgetting to update the digest it is supposed to describe.
  **Symptom:** The comment and the digest disagree, so reviewers are misled about which image actually ships while the old digest keeps resolving.
  **Recovery:** Treat the tag comment and the digest as one unit; when you change one, look up and change the other in the same edit.

- **Mistake:** Treating digest pinning as a reason to never update base images.
  **Symptom:** The pinned base drifts months behind on security patches because nothing ever revisits the digest.
  **Recovery:** Schedule deliberate digest bumps, ideally via an automated update tool that proposes the new digest as a reviewable change.

- **Mistake:** Copying a digest from an untrusted source without confirming it corresponds to the intended image and tag.
  **Symptom:** The build reproducibly and verifiably pulls the wrong — possibly malicious — image, because the digest was authentic but pointed at attacker-chosen content.
  **Recovery:** Obtain digests from the official registry for the exact image and tag you intend, and verify the tag-to-digest mapping before committing it.

## External reading

- [Docker: image pull by digest and content addressability](https://docs.docker.com/reference/cli/docker/image/pull/)
- [Docker: Dockerfile base-image instruction reference](https://docs.docker.com/reference/dockerfile/)
- [Docker Compose: services image reference](https://docs.docker.com/reference/compose-file/services/)
- [Open Container Initiative (OCI) image specification: descriptors and digests](https://github.com/opencontainers/image-spec/blob/main/descriptor.md)
