"""Feature: phase-3-llm-layer — Property 13.

Property 13: Cache isolation across users.

    *For any* two distinct User_Accounts issuing byte-identical feature
    inputs under the same template version and model, a cached entry
    created for one is never served to the other — the second user's
    request is a cache miss that takes the normal call path.

**Validates: Requirements 15.3**

The property drives ``services/llm/cache.LLMCache`` directly around an
injected in-memory fake Redis (the ``tests/unit/test_rate_limit.py`` /
``tests/property/test_quota_call_accounting.py`` convention sanctioned
by the design's Testing Strategy). The fake exposes only ``get`` and
``set`` — the two methods :class:`LLMCache` touches.

Scenario: a *writer* user caches a validated ``LLMResultEnvelope`` for
some (feature, input_hash, template_version, model) coordinate. Then a
generated set of *other*, distinct users issues byte-identical lookups
— the exact same coordinate, differing only in the requesting user id.
Every one of those lookups must be a miss (``None`` — the normal call
path proceeds), while the writer's own identical lookup is a hit that
round-trips the envelope. A miss for every non-writer user is the
observable form of Requirement 15.3's "never serve an entry to a
User_Account other than the one it was created for": the user id is
part of the key and of every lookup, so isolation holds structurally,
not via a post-read filter.
"""

# Feature: phase-3-llm-layer, Property 13: Cache isolation across users

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import cast

from hypothesis import given, settings
from hypothesis import strategies as st

from matchlayer_api.core.redis import Redis
from matchlayer_api.services.llm.cache import LLMCache
from matchlayer_api.services.llm.schemas import (
    CoachingReport,
    ImprovementAction,
    LLMResultEnvelope,
)

# ---------------------------------------------------------------------------
# Fake Redis
# ---------------------------------------------------------------------------


class _FakeAsyncRedis:
    """Minimal stand-in for ``redis.asyncio.Redis``.

    Exposes only ``get`` and ``set`` — the two methods
    :class:`LLMCache` touches. Values are stored and returned as bytes,
    mirroring the real client's wire behavior.
    """

    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}

    async def get(self, key: str) -> bytes | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value.encode()


def _run_sync(coro_factory: Callable[[], Awaitable[None]]) -> None:
    """Drive an async body from a sync Hypothesis example via ``Runner``."""
    with asyncio.Runner() as runner:
        runner.run(coro_factory())


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# User ids are UUID strings in production (UUIDv7 exposed as strings per
# conventions.md); uuid4 strings share the exact shape and are always
# collision-free across draws.
_USER_ID = st.uuids(version=4).map(str)

# The three Phase 3 LLM_Features (design §"LLMCache": the feature is a
# fixed key segment per feature service).
_FEATURE = st.sampled_from(["coaching-reports", "bullet-rewrites", "interview-question-sets"])

# The deterministic hash of the redacted prompt input is a hex digest
# (Requirement 15.1); byte-identical inputs share it exactly.
_INPUT_HASH = st.text(alphabet="0123456789abcdef", min_size=64, max_size=64)

_TEMPLATE_VERSION = st.integers(min_value=1, max_value=9)

_MODEL = st.sampled_from(["gpt-4o-mini", "gpt-4o"])

_SUMMARY = st.text(
    alphabet=st.characters(categories=("L", "N"), include_characters=" "),
    min_size=1,
    max_size=40,
).filter(lambda s: s.strip() != "")


@st.composite
def _envelopes(draw: st.DrawFn) -> LLMResultEnvelope[CoachingReport]:
    """A validated, non-fallback envelope — the only payload LLMCache accepts."""
    report = CoachingReport(
        summary=draw(_SUMMARY),
        strengths=[],
        gaps=[],
        improvements=[
            ImprovementAction(priority=rank, action=f"action {rank}") for rank in (1, 2, 3)
        ],
    )
    return LLMResultEnvelope[CoachingReport](
        id=draw(_USER_ID),  # any UUID string works as the result id
        is_fallback=False,
        prompt_template_version=draw(_TEMPLATE_VERSION),
        result=report,
    )


@st.composite
def _scenarios(
    draw: st.DrawFn,
) -> tuple[str, list[str], str, str, int, str, LLMResultEnvelope[CoachingReport]]:
    """(writer, other distinct users, feature, hash, version, model, envelope)."""
    users = draw(st.lists(_USER_ID, min_size=2, max_size=4, unique=True))
    writer, others = users[0], users[1:]
    feature = draw(_FEATURE)
    input_hash = draw(_INPUT_HASH)
    template_version = draw(_TEMPLATE_VERSION)
    model = draw(_MODEL)
    envelope = draw(_envelopes())
    return writer, others, feature, input_hash, template_version, model, envelope


# ---------------------------------------------------------------------------
# Property
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=None)
@given(scenario=_scenarios())
def test_cached_entry_never_served_to_another_user(
    scenario: tuple[str, list[str], str, str, int, str, LLMResultEnvelope[CoachingReport]],
) -> None:
    """A cache entry is served only to the user it was created for.

    After the writer caches an envelope, every other user's
    byte-identical lookup (same feature, input hash, template version,
    and model — differing only in the requesting user id) is a miss
    (``None``), so their request takes the normal call path; the
    writer's own identical lookup round-trips the envelope
    (Requirement 15.3).
    """
    writer, others, feature, input_hash, template_version, model, envelope = scenario

    async def _run() -> None:
        fake = _FakeAsyncRedis()
        cache = LLMCache(cast(Redis, fake), ttl_seconds=86400)

        await cache.set(
            user_id=writer,
            feature=feature,
            input_hash=input_hash,
            template_version=template_version,
            model=model,
            envelope=envelope,
        )

        # Every other user's byte-identical lookup is a miss: the entry
        # created for the writer is never served to them.
        for other in others:
            hit = await cache.get(
                user_id=other,
                feature=feature,
                input_hash=input_hash,
                template_version=template_version,
                model=model,
                envelope_type=LLMResultEnvelope[CoachingReport],
            )
            assert hit is None

        # The writer's own identical lookup round-trips the envelope —
        # isolation comes from key scoping, not from hiding the entry.
        own = await cache.get(
            user_id=writer,
            feature=feature,
            input_hash=input_hash,
            template_version=template_version,
            model=model,
            envelope_type=LLMResultEnvelope[CoachingReport],
        )
        assert own == envelope

        # Structural check: exactly one entry exists and its key embeds
        # the writer's user id — the isolation mechanism itself.
        assert list(fake.store) == [
            f"llm:cache:{writer}:{feature}:{input_hash}:v{template_version}:{model}"
        ]

    _run_sync(_run)
