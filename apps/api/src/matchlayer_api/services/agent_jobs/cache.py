"""Agent_Cache — per-user Redis cache of non-degraded agent outputs.

Key: ``agent-cache:{user_id}:{agent_name}:{prompt_version}:{input_hash}``,
with the input hash computed over **redacted** input only (Requirement
9.7; the raw resume text never reaches the cache layer by construction —
Requirement 1.3). The owning user's id is always part of the key, so a
lookup can structurally never resolve to another user's entry, mirroring
the Phase 3 ``services/llm/cache.py`` pattern and ``security.md``'s
"never share cached LLM output across user boundaries" rule.

Scope note: the two LLM_Agents fulfil Requirement 9.7 through the Phase 3
``LLMOrchestrator`` cache configured with agent-specific namespaces, so
this module primarily serves Deterministic_Agent caching and the generic
Agent_Cache surface. ``prompt_version`` remains a key component for both
kinds of caller: LLM_Agents pass their registry-resolved Prompt_Template
version, deterministic agents pass the version of the rule/scorer that
produced the output, so a rule change can never serve a stale entry.

Value: the JSON-serialized, schema-validated agent output (a Pydantic
model from ``ml/agents/state.py``). Entries expire
``agent_cache_ttl_seconds`` after the write (default 86400); Redis's
``SET ... EX`` handles expiry, and an expired key reads back as ``None``
— a miss.

Failure posture (Requirement 9.10) — the cache is an optimization,
never a dependency:

- Read failure / Redis down / a corrupt entry → treated as a miss with
  one structured warning; the agent computes normally and the failure
  alone never fails a node or a job.
- Write failure → the computed output is still returned by the caller;
  one structured warning carrying no Restricted PII, never an error.

Misuse resistance (Requirement 9.7): :meth:`AgentCache.set` rejects any
output whose ``degraded`` marker is ``True`` with a :exc:`ValueError`
before touching Redis — Degraded_Outputs are never cached, so degraded
content can never mask recovered availability. The :exc:`ValueError`
marks a caller bug, not a runtime cache failure.

The ``redis`` import boundary lives in ``core/redis.py`` (the single
module allowed to import ``redis`` — Phase 3 design decision D6). This
module receives an *injected* client annotated via the ``core/redis.py``
re-exports, mirroring ``services/llm/cache.py``.

Design reference: phase-4-agentic design §8 "Persistence services" and
the "Agent_Cache keys (Redis)" data-model section.
"""

from __future__ import annotations

import hashlib
from typing import Annotated

import structlog
from fastapi import Depends
from pydantic import BaseModel

from matchlayer_api.config import get_settings
from matchlayer_api.core.redis import Redis, get_redis_client

_log = structlog.get_logger(__name__)

__all__ = ["AgentCache", "compute_agent_input_hash", "get_agent_cache"]


def compute_agent_input_hash(
    *,
    agent_name: str,
    prompt_version: str,
    redacted_input: str,
) -> str:
    """The canonical sha256 digest over an agent's redacted input.

    Mirrors the Phase 3 ``compute_input_hash`` discipline: the digest is
    computed **only** over redacted/derived content (Requirement 9.7 —
    "input hash computed over redacted input"), and every component that
    could make an entry stale (agent identity, prompt/rule version) is
    folded into the canonical byte string. The input's length is included
    as a prefix so no two distinct inputs can collide by delimiter
    ambiguity.
    """
    canonical = f"{agent_name}|{prompt_version}|{len(redacted_input)}|{redacted_input}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class AgentCache:
    """Per-user cache of non-degraded agent outputs on Redis.

    Constructed with an injected redis client (Phase 3 design decision
    D6) and the configured entry TTL (``agent_cache_ttl_seconds``,
    Requirement 9.7). The :func:`get_agent_cache` factory below wires
    the production instance through ``Depends``; the worker composes it
    directly around its own client, and tests construct instances
    around fakes.
    """

    def __init__(self, redis_client: Redis, *, ttl_seconds: int) -> None:
        self._redis = redis_client
        self._ttl_seconds = ttl_seconds

    @staticmethod
    def _key(
        *,
        user_id: str,
        agent_name: str,
        prompt_version: str,
        input_hash: str,
    ) -> str:
        """Build the full cache key — user id always included (Req 9.7).

        Every component that could make a cached entry stale (input
        hash over redacted input, prompt/rule version) or leak across
        users (user id) is part of the key, so lookups are scoped
        structurally rather than by a filter applied after the read.
        """
        return f"agent-cache:{user_id}:{agent_name}:{prompt_version}:{input_hash}"

    async def get[TOut: BaseModel](
        self,
        *,
        user_id: str,
        agent_name: str,
        prompt_version: str,
        input_hash: str,
        output_type: type[TOut],
    ) -> TOut | None:
        """Look up a cached output; any failure is a miss (Req 9.10).

        ``output_type`` is the concrete agent output model (for example
        ``SkillGapReport``) the raw JSON is validated against, so a hit
        hands the caller the same fully validated shape the original
        execution produced.

        Returns ``None`` — a miss — for an absent or expired key, for
        any Redis error (one structured warning, no PII), and for an
        entry that no longer validates against the current schema
        (defensive: a schema change mid-TTL must degrade to a fresh
        computation, never to an error or a malformed output).
        """
        key = self._key(
            user_id=user_id,
            agent_name=agent_name,
            prompt_version=prompt_version,
            input_hash=input_hash,
        )
        try:
            raw = await self._redis.get(key)
        except Exception:
            # Redis down or errored: miss; the agent computes normally
            # (Requirement 9.10). One structured warning — identifiers
            # only, never key payloads or cached content.
            _log.warning("agent_cache_read_failed", agent_name=agent_name, user_id=user_id)
            return None

        if raw is None:
            return None

        try:
            return output_type.model_validate_json(raw)
        except Exception:
            # Corrupt or schema-incompatible entry: treat as a miss so
            # the agent takes the normal computation path.
            _log.warning("agent_cache_entry_invalid", agent_name=agent_name, user_id=user_id)
            return None

    async def set(
        self,
        *,
        user_id: str,
        agent_name: str,
        prompt_version: str,
        input_hash: str,
        output: BaseModel,
    ) -> None:
        """Best-effort write of a validated, non-degraded agent output.

        Only Pydantic model instances are accepted, so the payload has
        passed schema validation by construction. An output whose
        ``degraded`` marker is ``True`` is rejected with a
        :exc:`ValueError` before any Redis I/O — Degraded_Outputs are
        never cached (Requirement 9.7). The :exc:`ValueError` marks a
        caller bug (agents only reach the cache-write step with a
        non-degraded output), not a runtime cache failure.

        A Redis write failure is swallowed: the computed output is
        still returned to the caller regardless, and the failure is
        recorded as one structured warning with no Restricted PII and
        no cached content (Requirement 9.10).
        """
        # Every agent output model in ml/agents/state.py carries a
        # ``degraded: bool = False`` marker; ``getattr`` keeps this
        # module decoupled from the concrete output types. The ``Any``
        # from getattr is immediately narrowed to bool.
        degraded = bool(getattr(output, "degraded", False))
        if degraded:
            raise ValueError(
                "AgentCache.set rejects degraded outputs: Degraded_Outputs are "
                "never cached (Requirement 9.7)"
            )

        key = self._key(
            user_id=user_id,
            agent_name=agent_name,
            prompt_version=prompt_version,
            input_hash=input_hash,
        )
        try:
            await self._redis.set(key, output.model_dump_json(), ex=self._ttl_seconds)
        except Exception:
            # Best-effort: the output has already been produced and will
            # be returned regardless (Requirement 9.10).
            _log.warning("agent_cache_write_failed", agent_name=agent_name, user_id=user_id)


# ---------------------------------------------------------------------------
# Per-request cache factory.
#
# Mirrors ``services/llm/cache.py``'s ``get_llm_cache``: the client
# lifecycle (per-request construction, teardown, event-loop affinity)
# lives in ``core/redis.py``'s ``get_redis_client``. This factory only
# composes that dependency with the configured TTL.
# ---------------------------------------------------------------------------


async def get_agent_cache(
    client: Annotated[Redis, Depends(get_redis_client)],
) -> AgentCache:
    """Build a per-request :class:`AgentCache` around the injected client.

    The TTL comes from settings (``agent_cache_ttl_seconds``, Requirement
    9.7); the client is created and closed by
    :func:`matchlayer_api.core.redis.get_redis_client`. The Agent_Worker
    and tests construct :class:`AgentCache` directly around their own
    clients and skip this factory entirely.
    """
    settings = get_settings()
    return AgentCache(client, ttl_seconds=settings.agent_cache_ttl_seconds)
