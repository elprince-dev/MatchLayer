"""Feature: phase-1-matching — Property 20.

Property 20: Keyed mutations are idempotent.

    *For any* valid upload request and *any* valid match-creation request
    carrying an ``Idempotency-Key``, issuing the identical request twice within
    24 hours returns the original response both times and creates exactly one
    resource (one stored object / one ``resumes`` row for upload; one
    ``match_results`` row for match), never two.

**Validates: Requirements 2.8, 8.9**

This module is the universal companion to the example/edge coverage of the
idempotency primitives (``tests/unit/test_user_rate_limit_and_idempotency.py``,
task 9.2) and the end-to-end replay coverage of the resume/match routers (the
integration suites). Where those drive concrete requests, this file asserts the
*store-level* invariant that makes a keyed mutation idempotent holds across a
wide, generated input space using Hypothesis (>=100 examples).

The idempotency layer (``core/dependencies.py``) is the seam that turns a
client-supplied ``Idempotency-Key`` into "exactly one resource". The Resumes /
Matches routers do the same dance on every keyed mutation: look the key up via
:meth:`IdempotencyStore.get`; on a miss, perform the mutation once and memoize
the 201 outcome via :meth:`IdempotencyStore.put` (which issues ``SET ... NX EX
86400``); on a hit, replay the stored response without re-running the mutation.
That contract reduces to four store-level guarantees this property exercises:

* **(a) Round-trip.** A :meth:`get` after a :meth:`put` returns a record *equal*
  to the one stored — so a replay sees the original 201 body and status, byte
  for byte, not a re-derived one.
* **(b) First-writer-wins.** A *second* :meth:`put` under the same
  ``(user_id, route, key)`` with a *different* record never overwrites the
  first; every later :meth:`get` still returns the FIRST record. This is the
  ``SET ... NX`` semantics, and it is precisely why replaying a keyed mutation
  N times yields one stored outcome (one resource), never two.
* **(c) Namespacing.** The same client key under a *different* ``user_id`` or a
  *different* ``route`` is an independent slot — one account's (or route's) key
  can never replay another's stored response (no cross-tenant / cross-route
  leak; ``security.md`` "Cross-tenant leakage").
* **(d) Key shape + TTL.** The stored Redis key is exactly
  ``idem:{user_id}:{route}:{key}`` and is written with the 24-hour TTL the
  ``security.md`` idempotency rule mandates ("Persist keys for 24h").

The store is driven against an in-memory fake of the ``get`` / ``set``
(``NX`` / ``EX``) subset it touches — the same fake-redis approach the task-9.2
unit suite uses — so no real Redis is required and the property is a pure,
fast, in-memory check. ``IdempotencyStore`` serializes records to JSON
(``put``) and reconstructs them on the way out (``get``); the generators below
constrain bodies to JSON objects with string keys and finite JSON scalar /
container leaves so the round-trip is exact and record equality is a sound
oracle.

The async ``IdempotencyStore`` methods are driven per example via
:class:`asyncio.Runner` (the repository's established async-property-test
pattern, see ``tests/property/test_rate_limit_window.py``): ``Runner`` closes
its event loop deterministically on ``__exit__`` so the
``ResourceWarning("unclosed event loop")`` that bare ``asyncio.run`` can leak
in teardown — which this suite's ``filterwarnings = ["error"]`` would promote
to a failure — is impossible.

Generator note (key injectivity): ``route`` and ``key`` are drawn from a
colon-free alphabet. Production routes are fixed literals (``"resumes"`` /
``"matches"``) and client ``Idempotency-Key`` values are opaque tokens, and the
property under test is the store's idempotency *semantics* (round-trip,
first-writer-wins, namespacing, TTL) — not the textual injectivity of the
``idem:{user_id}:{route}:{key}`` separator scheme. Excluding the ``:`` separator
from the generated components keeps the key construction injective so the
namespacing assertions in (c) test the genuine guarantee rather than an
incidental ``"a:b" + ":" + "c"`` vs ``"a" + ":" + "b:c"`` collision.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from string import ascii_letters, digits
from typing import Any, Final
from uuid import UUID

from hypothesis import example, given, settings
from hypothesis import strategies as st

from matchlayer_api.core.dependencies import (
    IdempotencyRecord,
    IdempotencyStore,
    _build_idempotency_key,
)

# 24h, mirroring ``dependencies._IDEMPOTENCY_TTL_SECONDS`` (kept as a literal
# here so a regression that quietly changes the production TTL is caught — the
# same guard the task-9.2 unit suite applies).
_EXPECTED_IDEMPOTENCY_TTL_SECONDS: Final[int] = 24 * 60 * 60

# Colon-free alphabet for ``route`` / ``key`` so ``idem:{user_id}:{route}:{key}``
# stays injective (see module docstring "Generator note").
_TOKEN_ALPHABET: Final[str] = ascii_letters + digits + "-_."


# ---------------------------------------------------------------------------
# In-memory fake of the ``get`` / ``set`` (NX/EX) subset IdempotencyStore uses.
#
# Mirrors redis-py's contract closely enough for the store: ``set(..., nx=True)``
# is a no-op when the key already exists (returns ``None``), otherwise stores the
# value and returns ``True``. ``set_calls`` records the TTL and NX flag so the
# 24h-TTL / first-writer assertions can read them back. This is the same fake
# approach the task-9.2 unit suite (``test_user_rate_limit_and_idempotency.py``)
# uses — no real Redis needed.
# ---------------------------------------------------------------------------


class _FakeKVRedis:
    """In-memory fake of the ``get`` / ``set`` (NX/EX) subset the store uses."""

    def __init__(self) -> None:
        self.store: dict[str, Any] = {}
        self.set_calls: list[dict[str, Any]] = []

    async def get(self, name: str) -> Any:
        return self.store.get(name)

    async def set(
        self,
        name: str,
        value: Any,
        *,
        ex: int | None = None,
        px: int | None = None,
        nx: bool = False,
        **_kwargs: Any,
    ) -> bool | None:
        self.set_calls.append({"name": name, "ex": ex, "nx": nx})
        if nx and name in self.store:
            return None
        self.store[name] = value
        return True


def _run_sync(coro_factory: Callable[[], Awaitable[None]]) -> None:
    """Run an async test body via :class:`asyncio.Runner`.

    Mirrors ``tests/property/test_rate_limit_window.py``: ``Runner`` closes the
    event loop and its selectors deterministically on ``__exit__`` so the
    ``ResourceWarning("unclosed event loop")`` a bare ``asyncio.run`` can leak in
    teardown — which this suite's ``filterwarnings = ["error"]`` promotes to a
    failure — cannot occur. Hypothesis drives many examples per run, so the
    deterministic close is also cheaper than re-creating the loop scaffolding
    per example.
    """
    with asyncio.Runner() as runner:
        runner.run(coro_factory())


# ---------------------------------------------------------------------------
# Smart generators.
#
# ``IdempotencyStore.put`` JSON-serializes the record body and ``get`` rebuilds
# it; constraining bodies to JSON objects with string keys and finite JSON
# scalar / container leaves keeps the round-trip exact, so ``replayed == record``
# is a sound oracle (no float NaN/inf, no non-string keys that JSON would coerce,
# no tuples that JSON would turn into lists).
# ---------------------------------------------------------------------------

_uuid_str = st.uuids().map(str)

_token = st.text(alphabet=_TOKEN_ALPHABET, min_size=1, max_size=24)

# Finite JSON scalars only — each round-trips through ``json.dumps`` /
# ``json.loads`` to an equal Python value.
_json_scalars = (
    st.none()
    | st.booleans()
    | st.integers(min_value=-1_000_000, max_value=1_000_000)
    | st.floats(allow_nan=False, allow_infinity=False)
    | st.text(max_size=24)
)

# A JSON value: a scalar, or a (recursively) nested list / string-keyed object.
_json_values = st.recursive(
    _json_scalars,
    lambda children: (
        st.lists(children, max_size=4)
        | st.dictionaries(st.text(min_size=1, max_size=10), children, max_size=4)
    ),
    max_leaves=10,
)

# A response body is a JSON object (string keys) — the shape a Pydantic
# ``model_dump(mode="json")`` produces for the 201 response models.
_body = st.dictionaries(st.text(min_size=1, max_size=12), _json_values, max_size=6)

# A stored idempotent outcome: a created resource id (string), a 2xx/4xx/5xx
# status (always 201 in production, generated broadly here), and a JSON body.
_record = st.builds(
    IdempotencyRecord,
    resource_id=_uuid_str,
    status_code=st.integers(min_value=100, max_value=599),
    body=_body,
)

# A canonical example mirroring a real match-creation 201 outcome, so the
# representative edge is always exercised alongside the generated space.
_EXAMPLE_RECORD: Final[IdempotencyRecord] = IdempotencyRecord(
    resource_id="0190aaaa-0000-7000-8000-000000000001",
    status_code=201,
    body={"id": "0190aaaa-0000-7000-8000-000000000001", "score": 87, "scorer_version": "1+lex.1"},
)


# ---------------------------------------------------------------------------
# (a) Round-trip + (b) first-writer-wins + (d) key shape & 24h TTL.
# ---------------------------------------------------------------------------


@settings(max_examples=200, deadline=None)
@given(
    user_id=st.uuids(),
    route=_token,
    key=_token,
    first=_record,
    second_status=st.integers(min_value=100, max_value=599),
    second_body=_body,
    replays=st.integers(min_value=1, max_value=5),
)
@example(
    user_id=UUID("0190aaaa-0000-7000-8000-0000000000aa"),
    route="matches",
    key="idem-key-1",
    first=_EXAMPLE_RECORD,
    second_status=201,
    second_body={"id": "should-not-win"},
    replays=3,
)
def test_keyed_mutation_roundtrips_and_first_writer_wins(
    user_id: UUID,
    route: str,
    key: str,
    first: IdempotencyRecord,
    second_status: int,
    second_body: dict[str, Any],
    replays: int,
) -> None:
    """A keyed mutation stores exactly one outcome and replays it unchanged.

    Property 20 (Requirements 2.8, 8.9):

    * **(a) Round-trip** — ``get`` after the first ``put`` returns a record
      *equal* to the one stored (so a replay serves the original 201 body and
      status).
    * **(b) First-writer-wins** — replaying the mutation ``N`` times with a
      *different* record never overwrites the first; every ``get`` still returns
      the FIRST record, and the backing store holds exactly one value at the
      key. This is the ``SET ... NX`` guarantee that makes N replays create one
      resource, never N.
    * **(d) Key shape + TTL** — the value lands under exactly
      ``idem:{user_id}:{route}:{key}`` and every write uses ``NX`` with the 24h
      ``EX`` TTL.
    """
    # ``second`` is guaranteed distinct from ``first`` (its resource_id carries a
    # suffix ``first.resource_id`` cannot equal), so the NX guarantee is genuinely
    # exercised against a *different* candidate record, not an accidental dup.
    second = IdempotencyRecord(
        resource_id=first.resource_id + "::second-writer",
        status_code=second_status,
        body=second_body,
    )
    assert second != first

    expected_key = _build_idempotency_key(user_id=user_id, route=route, key=key)

    async def _run() -> None:
        fake = _FakeKVRedis()
        store = IdempotencyStore(fake)  # type: ignore[arg-type]

        # First writer stores its outcome.
        await store.put(user_id=user_id, route=route, key=key, record=first)

        # (a) Round-trip: the stored record comes back equal.
        replayed = await store.get(user_id=user_id, route=route, key=key)
        assert replayed == first

        # (b) Replaying with a different record never overwrites the first.
        for _ in range(replays):
            await store.put(user_id=user_id, route=route, key=key, record=second)
            again = await store.get(user_id=user_id, route=route, key=key)
            assert again == first
            assert again != second

        # Exactly one resource is memoized for this key (never two).
        assert list(fake.store.keys()) == [expected_key]

        # (d) Key shape + TTL: the slot is the namespaced key, and every write
        # used ``NX`` with the 24h ``EX`` TTL.
        assert expected_key in fake.store
        assert len(fake.set_calls) == 1 + replays
        for call in fake.set_calls:
            assert call["name"] == expected_key
            assert call["nx"] is True
            assert call["ex"] == _EXPECTED_IDEMPOTENCY_TTL_SECONDS

    _run_sync(_run)


# ---------------------------------------------------------------------------
# (c) Namespacing: a key is independent across user_id and route boundaries.
# ---------------------------------------------------------------------------


@settings(max_examples=200, deadline=None)
@given(
    user_a=st.uuids(),
    user_b=st.uuids(),
    route_x=_token,
    route_y=_token,
    key=_token,
    record=_record,
)
@example(
    user_a=UUID("0190aaaa-0000-7000-8000-0000000000a1"),
    user_b=UUID("0190aaaa-0000-7000-8000-0000000000b2"),
    route_x="resumes",
    route_y="matches",
    key="shared-key",
    record=_EXAMPLE_RECORD,
)
def test_keyed_mutations_are_namespaced_by_user_and_route(
    user_a: UUID,
    user_b: UUID,
    route_x: str,
    route_y: str,
    key: str,
    record: IdempotencyRecord,
) -> None:
    """The same key under a different user or route is an independent slot.

    Property 20 (Requirements 2.8, 8.9), namespacing facet: a record stored for
    ``(user_a, route_x, key)`` is invisible to the same key under a *different*
    user (``user_b``) and under a *different* route (``route_y``), while the
    originating slot still replays it. One account's (or route's) idempotency
    key can therefore never replay another's stored response.
    """
    # Focus on the genuinely-distinct-boundary case; the equal-boundary case is
    # the round-trip already covered above. UUID and token collisions here would
    # make "different slot" vacuous, so steer away from them.
    if user_a == user_b or route_x == route_y:
        return

    async def _run() -> None:
        fake = _FakeKVRedis()
        store = IdempotencyStore(fake)  # type: ignore[arg-type]

        await store.put(user_id=user_a, route=route_x, key=key, record=record)

        # Different user, same route + key → independent (miss).
        assert await store.get(user_id=user_b, route=route_x, key=key) is None
        # Same user, different route, same key → independent (miss).
        assert await store.get(user_id=user_a, route=route_y, key=key) is None
        # Different user AND different route → independent (miss).
        assert await store.get(user_id=user_b, route=route_y, key=key) is None

        # The originating slot still replays the original record.
        assert await store.get(user_id=user_a, route=route_x, key=key) == record

    _run_sync(_run)


# ---------------------------------------------------------------------------
# Round-trip soundness across the full generated body space.
# ---------------------------------------------------------------------------


@settings(max_examples=200, deadline=None)
@given(user_id=st.uuids(), route=_token, key=_token, record=_record)
def test_stored_record_roundtrips_through_json_exactly(
    user_id: UUID, route: str, key: str, record: IdempotencyRecord
) -> None:
    """The stored value is the record's JSON and ``get`` rebuilds it equal.

    Property 20 round-trip facet (Requirements 2.8, 8.9): for any record over the
    generated body space, the value written to the backing store is the JSON
    serialization of ``{resource_id, status_code, body}``, and :meth:`get`
    reconstructs a record *equal* to the one stored — the exactness a replay
    relies on to serve the byte-identical original response.
    """
    expected_key = _build_idempotency_key(user_id=user_id, route=route, key=key)

    async def _run() -> None:
        fake = _FakeKVRedis()
        store = IdempotencyStore(fake)  # type: ignore[arg-type]

        await store.put(user_id=user_id, route=route, key=key, record=record)

        # The stored bytes are exactly the record's JSON projection.
        stored_raw = fake.store[expected_key]
        assert json.loads(stored_raw) == {
            "resource_id": record.resource_id,
            "status_code": record.status_code,
            "body": record.body,
        }

        # And the public read path rebuilds an equal record.
        assert await store.get(user_id=user_id, route=route, key=key) == record

    _run_sync(_run)
