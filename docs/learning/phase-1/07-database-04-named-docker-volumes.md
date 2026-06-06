# Named Docker volumes and data persistence

## Introduction

This document explains how the local stack keeps its data alive when the
containers that use it are thrown away and recreated. The mechanism is a named
Docker volume: a storage area that Docker creates and manages by name,
independent of any single container, so the data inside it survives even after
the container that wrote it is removed. A container is a lightweight, isolated
process bundle that starts from an image and is meant to be disposable; its own
writable layer disappears when it is deleted, which is exactly why a separate
place to keep data matters. This document teaches what a volume is, how it
differs from a container's own filesystem, and what `docker compose down` does
and does not erase. This belongs in the Database and storage track because every
stateful service in the stack depends on a volume to not lose your data.

**Learning outcomes** — after reading this document you will be able to:

- Explain why a container's own filesystem is the wrong place to keep data you want to keep.
- Describe what a named volume is and how Docker manages it independently of containers.
- Explain what `docker compose down` removes and what it deliberately leaves behind.
- Recover local data state safely instead of wiping it by accident.

Prerequisites:

- [Postgres versus MinIO: two stores, two jobs](07-database-02-postgres-vs-minio.md) introduces the two stateful services whose data the volumes in this document preserve.

## Problem it solves

Containers are designed to be disposable: you stop one, delete it, and start a
fresh one from the same image whenever you upgrade or reset. That is a feature
— until the container is also where your database keeps its files, because then
deleting the container deletes your data. The concrete problem is keeping data
durable across the routine create-and-destroy cycle of containers.

A common prior approach is to let the database write into the container's own
writable layer (the throwaway filesystem each container gets on top of its
image). That approach has real costs:

- Removing or recreating the container — something you do constantly during development — silently destroys everything the database wrote.
- The container's writable layer is tied to that one container instance, so two runs of "the same" service do not share any data.
- There is no clean way to back up or inspect the data separately, because it is entangled with a disposable process.

A named volume solves this by moving the data outside the container. Docker owns
the volume, gives it a stable name, and mounts it into whichever container needs
it. Delete and recreate the container as often as you like; the volume — and the
data in it — stays.

## Mental model

Think of a container as a rented hotel room and a named volume as a personal
storage locker in the lobby:

1. You check into a room (start a container) to get work done; the room comes furnished from a standard template (the image).
2. Anything you leave lying around the room is cleared out by housekeeping when you check out (the container's writable layer is discarded on removal).
3. Valuables you want to keep go into your lobby locker (the named volume), which has your name on it and is not part of any room.
4. When you check out and later check into a different room, you collect the same locker and carry on (a new container mounts the same volume and sees the same data).
5. The locker is only emptied when you explicitly ask the front desk to clear it (the volume is only deleted when you explicitly remove it).

The room is disposable; the locker is durable. Keeping data in the locker means
swapping rooms costs you nothing.

## How it works

A container starts from a read-only image and gets a thin writable layer of its
own for any changes it makes while running. That writable layer lives and dies
with the container: remove the container and the layer is gone. This is good for
ephemeral process state and bad for anything you want to keep.

A volume is storage that the container engine manages outside any single
container's writable layer. A named volume is one you refer to by a stable name;
the engine stores its contents in an area it controls and mounts it into a
container at a chosen path. Because the volume's lifecycle is separate from the
container's, the same volume can be mounted into a succession of containers over
time, and each one sees the data the previous one left. You can also list,
back up, and remove volumes as first-class objects, independently of the
containers that use them.

A multi-container tool reads a declaration of which services exist and which
named volumes they mount. Bringing the stack up creates any named volumes that
do not exist yet and mounts them. Tearing the stack down removes the containers
and the network between them, but — and this is the point that surprises people
— it leaves the named volumes in place by default, precisely so a teardown does
not destroy your data. Removing the volumes is a separate, explicit action: the
teardown command takes an extra flag whose only job is to also delete the named
volumes. The asymmetry is deliberate. Recreating containers is routine and safe;
destroying data is rare and must be asked for on purpose.

## MatchLayer Phase 1 usage

The local stack declares its named volumes at the bottom of `docker-compose.yml`.
Listing a volume name here tells the engine to create and manage it:

Source: `docker-compose.yml`

```yaml
volumes:
  matchlayer-postgres-data:
  matchlayer-minio-data:
```

Each stateful service mounts the volume it needs at the path where that service
writes its files. The database service mounts its volume at the directory
Postgres uses for its data files:

Source: `docker-compose.yml`

```yaml
volumes:
  - matchlayer-postgres-data:/var/lib/postgresql/data
```

The header comment in `docker-compose.yml` states the persistence contract
directly: the named volumes are not removed by `docker compose down`, and wiping
local data is the separate, explicit `docker compose down -v`. So a routine
teardown and rebuild of the containers keeps every account, record, and uploaded
object intact, while a deliberate reset is a different command you have to type
on purpose.

## Common pitfalls

- **Mistake:** Running `docker compose down -v` to "restart" the stack without realising the `-v` flag also deletes the named volumes.
  **Symptom:** The database comes back completely empty, and every account, record, and uploaded file from the previous session is gone.
  **Recovery:** Use `docker compose down` (no `-v`) to stop and remove containers while keeping data; reserve `-v` for an intentional wipe.

- **Mistake:** Expecting data to persist when the service writes to a path that has no volume mounted on it.
  **Symptom:** Data disappears every time the container is recreated, even though a volume exists, because the service was writing to its throwaway layer instead.
  **Recovery:** Mount the volume at the exact path the service writes to (for the database, its data directory) so writes land in the volume.

- **Mistake:** Assuming two different volume names hold the same data, or typing a slightly different name in a new mount.
  **Symptom:** A service starts with an unexpectedly empty store because the engine created a brand-new volume for the misspelled name.
  **Recovery:** Refer to the exact named volume declared in the stack, and list the existing volumes to confirm the name before mounting it.

- **Mistake:** Treating a named volume as a place you can browse with your normal file explorer like an ordinary folder.
  **Symptom:** You cannot find the volume's files in your project directory and assume the data is lost.
  **Recovery:** Inspect the volume through the container engine's volume tooling, or read it from inside a container that mounts it, rather than hunting for a local folder.

## External reading

- [Docker: volumes](https://docs.docker.com/engine/storage/volumes/)
- [Docker: storage overview](https://docs.docker.com/engine/storage/)
- [Docker Compose: the volumes top-level element](https://docs.docker.com/reference/compose-file/volumes/)
- [Docker Compose: down command](https://docs.docker.com/reference/cli/docker/compose/down/)
- [PostgreSQL: the data directory](https://www.postgresql.org/docs/16/storage-file-layout.html)
