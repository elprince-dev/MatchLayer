"""Feature: phase-4-agentic — Property 12.

Property 12: Agent_Cache round-trip, degraded exclusion, and user isolation.

    *For any* user and input, a second identical lookup after a successful
    non-degraded agent output write returns output equal to the first;
    degraded executions write no cache entry (a subsequent lookup misses);
    and *for any* two distinct users with identical inputs, neither ever
    receives the other's cached output.

**Validates: Requirements 9.7**

The unit under test is ``services/agent_jobs/cache.AgentCache`` together
with ``compute_agent_input_hash``, driven directly around an injected
in-memory fake Redis (the ``tests/property/test_cache_isolation.py`` /
``tests/unit/test_agent_cache.py`` convention — no real Redis, no
FastAPI, no settings). Three properties:

* **Round-trip** — for any (user, agent name, prompt version, redacted
  input) coordinate and any non-degraded output, ``set`` followed by an
  identical ``get`` returns a model equal to the original, fully
  validated against the output schema.
* **Degraded exclusion** — for any output whose ``degraded`` marker is
  ``True``, ``set`` raises ``ValueError`` before any Redis I/O, nothing
  is stored, and the subsequent identical lookup is a miss.
* **User isolation** — for any set of distinct users issuing
  byte-identical lookups (same agent, prompt version, and input hash),
  an entry written for one user is never served to any other: every
  other user's lookup is a miss while the writer's own lookup hits.
  The user id is a structural component of every stored key, so the
  isolation is checked on the key material itself, not only on the
  lookup result.
"""

# Feature: phase-4-agentic, Property 12: Agent_Cache round-trip, degraded exclusion, and user isolation  # noqa: E501

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import cast

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from matchlayer_api.core.redis import Redis
from matchlayer_api.ml.agents.state import SkillGapEntry, SkillGapReport
from matchlayer_api.services.agent_jobs.cache import (
    AgentCache,
    compute_agent_input_hash,
)

_TTL_SECONDS = 86_400

# ---------------------------------------------------------------------------
# Fake Redis
# ---------------------------------------------------------------------------


class _FakeAsyncRedis:
    """Minimal in-memory stand-in exposing only ``get`` and ``set``.

    The two methods :class:`AgentCache` touches, mirroring the fake in
    ``tests/property/test_cache_isolation.py``. Expiry is irrelevant to
    these properties, so ``ex`` is recorded but not enforced.
    """

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.observed_expirations: list[int | None] = []

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.observed_expirations.append(ex)
        self.store[key] = value


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

# Agent names come from the fixed Phase 4 agent set; the deterministic
# agents are this cache's primary callers (LLM agents go through the
# Phase 3 orchestrator cache), but any agent name is a valid key part.
_AGENT_NAME = st.sampled_from(["ats", "skill_gap", "synthesizer"])

# Prompt/rule versions are short version strings (a rule change must
# never serve a stale entry — the version is a key component).
_PROMPT_VERSION = st.sampled_from(["gap_rules.v1", "gap_rules.v2", "ats.v1"])

# Redacted agent input: arbitrary text including placeholder-looking
# content; the hash is computed over exactly this string.
_REDACTED_INPUT = st.text(min_size=0, max_size=200)

# Canonical-looking skill names (same alphabet discipline as
# test_full_skill_coverage.py — lexicon names are compact ASCII).
_SKILL = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+#.- ",
    min_size=1,
    max_size=20,
)


@st.composite
def _reports(draw: st.DrawFn, *, degraded: bool) -> SkillGapReport:
    """A schema-valid Skill_Gap_Report — the shape a real caller caches.

    Gap entries carry sequential unique ranks 1..n, matching what
    ``gap_rules.prioritize_gaps`` produces, so the payload is exactly a
    real agent output.
    """
    skills = draw(st.lists(_SKILL, min_size=0, max_size=6, unique=True))
    gaps = [
        SkillGapEntry(
            skill=skill,
            classification=draw(st.sampled_from(("missing", "weak"))),
            rank=rank,
        )
        for rank, skill in enumerate(skills, start=1)
    ]
    return SkillGapReport(gaps=gaps, degraded=degraded)


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


# Feature: phase-4-agentic, Property 12: Agent_Cache round-trip, degraded exclusion, and user isolation  # noqa: E501
@settings(max_examples=100, deadline=None)
@given(
    user_id=_USER_ID,
    agent_name=_AGENT_NAME,
    prompt_version=_PROMPT_VERSION,
    redacted_input=_REDACTED_INPUT,
    report=_reports(degraded=False),
)
def test_set_then_get_round_trips_the_exact_output(
    user_id: str,
    agent_name: str,
    prompt_version: str,
    redacted_input: str,
    report: SkillGapReport,
) -> None:
    """Round-trip: an identical lookup after a non-degraded write returns
    output equal to the original (Requirement 9.7), fully validated
    against the output schema, and the write carries the configured TTL."""
    input_hash = compute_agent_input_hash(
        agent_name=agent_name,
        prompt_version=prompt_version,
        redacted_input=redacted_input,
    )

    async def _run() -> None:
        fake = _FakeAsyncRedis()
        cache = AgentCache(cast(Redis, fake), ttl_seconds=_TTL_SECONDS)

        await cache.set(
            user_id=user_id,
            agent_name=agent_name,
            prompt_version=prompt_version,
            input_hash=input_hash,
            output=report,
        )
        hit = await cache.get(
            user_id=user_id,
            agent_name=agent_name,
            prompt_version=prompt_version,
            input_hash=input_hash,
            output_type=SkillGapReport,
        )

        assert hit == report
        assert isinstance(hit, SkillGapReport)
        assert fake.observed_expirations == [_TTL_SECONDS]

    _run_sync(_run)


# Feature: phase-4-agentic, Property 12: Agent_Cache round-trip, degraded exclusion, and user isolation  # noqa: E501
@settings(max_examples=100, deadline=None)
@given(
    user_id=_USER_ID,
    agent_name=_AGENT_NAME,
    prompt_version=_PROMPT_VERSION,
    redacted_input=_REDACTED_INPUT,
    report=_reports(degraded=True),
)
def test_degraded_outputs_are_rejected_and_never_stored(
    user_id: str,
    agent_name: str,
    prompt_version: str,
    redacted_input: str,
    report: SkillGapReport,
) -> None:
    """Degraded exclusion: for any degraded output, ``set`` raises
    ``ValueError`` before any Redis I/O, nothing lands in the store, and
    the subsequent identical lookup is a miss (Requirement 9.7 —
    Degraded_Outputs are never cached)."""
    input_hash = compute_agent_input_hash(
        agent_name=agent_name,
        prompt_version=prompt_version,
        redacted_input=redacted_input,
    )

    async def _run() -> None:
        fake = _FakeAsyncRedis()
        cache = AgentCache(cast(Redis, fake), ttl_seconds=_TTL_SECONDS)

        with pytest.raises(ValueError, match="degraded"):
            await cache.set(
                user_id=user_id,
                agent_name=agent_name,
                prompt_version=prompt_version,
                input_hash=input_hash,
                output=report,
            )

        assert fake.store == {}  # nothing reached Redis
        miss = await cache.get(
            user_id=user_id,
            agent_name=agent_name,
            prompt_version=prompt_version,
            input_hash=input_hash,
            output_type=SkillGapReport,
        )
        assert miss is None

    _run_sync(_run)


# Feature: phase-4-agentic, Property 12: Agent_Cache round-trip, degraded exclusion, and user isolation  # noqa: E501
@settings(max_examples=100, deadline=None)
@given(
    users=st.lists(_USER_ID, min_size=2, max_size=4, unique=True),
    agent_name=_AGENT_NAME,
    prompt_version=_PROMPT_VERSION,
    redacted_input=_REDACTED_INPUT,
    report=_reports(degraded=False),
)
def test_cached_entry_never_served_to_another_user(
    users: list[str],
    agent_name: str,
    prompt_version: str,
    redacted_input: str,
    report: SkillGapReport,
) -> None:
    """User isolation: for any two distinct users with identical inputs,
    neither ever receives the other's cached output (Requirement 9.7,
    ``security.md``). Every non-writer's byte-identical lookup is a miss
    while the writer's own lookup hits; the writer's user id is a
    structural component of the single stored key."""
    writer, others = users[0], users[1:]
    input_hash = compute_agent_input_hash(
        agent_name=agent_name,
        prompt_version=prompt_version,
        redacted_input=redacted_input,
    )

    async def _run() -> None:
        fake = _FakeAsyncRedis()
        cache = AgentCache(cast(Redis, fake), ttl_seconds=_TTL_SECONDS)

        await cache.set(
            user_id=writer,
            agent_name=agent_name,
            prompt_version=prompt_version,
            input_hash=input_hash,
            output=report,
        )

        # Every other user's byte-identical lookup is a miss: the entry
        # created for the writer is never served to them.
        for other in others:
            hit = await cache.get(
                user_id=other,
                agent_name=agent_name,
                prompt_version=prompt_version,
                input_hash=input_hash,
                output_type=SkillGapReport,
            )
            assert hit is None

        # The writer's own identical lookup round-trips the output —
        # isolation comes from key scoping, not from hiding the entry.
        own = await cache.get(
            user_id=writer,
            agent_name=agent_name,
            prompt_version=prompt_version,
            input_hash=input_hash,
            output_type=SkillGapReport,
        )
        assert own == report

        # Structural check: exactly one entry exists and its key embeds
        # the writer's user id — the isolation mechanism itself.
        assert list(fake.store) == [
            f"agent-cache:{writer}:{agent_name}:{prompt_version}:{input_hash}"
        ]

    _run_sync(_run)
