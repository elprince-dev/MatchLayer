"""LLMCache — per-user Redis cache for validated LLM result envelopes.

Key: ``llm:cache:{user_id}:{feature}:{input_hash}:v{template_version}:{model}``.
The requesting user's id is **always** part of the key and of every
lookup, so a lookup can structurally never resolve to another user's
entry (Requirements 15.1-15.3, ``security.md`` "no echoing other
users' data"). Any change to the redacted prompt input (the hash),
the Prompt_Template version, or the LLM_Model likewise produces a
different key, so a stale entry can never be served (Requirement 15.1).

Value: the serialized validated :class:`LLMResultEnvelope` as JSON.
Entries expire ``llm_cache_ttl_seconds`` after the write (default
86400); Redis's ``SET ... EX`` handles the expiry, and an expired key
simply reads back as ``None`` — a miss (Requirement 15.6).

Failure posture — the cache is an optimization, never a dependency:

- Lookup failure / Redis down / a corrupt entry → treated as a miss;
  the normal provider-call path proceeds and the failure alone never
  causes a 5xx (Requirement 15.7).
- Write failure → the validated result is still returned to the
  requester; the failure is logged as a structured event carrying no
  PII and no cached content, never a 5xx (Requirement 15.8).

Misuse resistance (Requirement 15.4): :meth:`LLMCache.set` accepts
only an :class:`LLMResultEnvelope` instance — i.e. a payload that has
already passed Pydantic schema validation, so unvalidated provider
output cannot reach the cache by construction — and it rejects any
envelope with ``is_fallback=True`` with a :exc:`ValueError` before
touching Redis. Fallbacks and invalid outputs are therefore never
written, so degraded output can never mask recovered LLM availability.

The ``redis`` import boundary lives in ``core/redis.py`` (the single
module allowed to import ``redis`` — design decision D6, enforced by
``tests/unit/test_import_boundaries.py``). This module receives an
*injected* client and annotates it via the ``core/redis.py``
re-exports, mirroring ``services/llm/quota.py``.

Design reference: phase-3-llm-layer §"LLMCache (services/llm/cache.py)".
"""

from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import Depends
from pydantic import BaseModel

from matchlayer_api.config import get_settings
from matchlayer_api.core.redis import Redis, get_redis_client
from matchlayer_api.services.llm.schemas import LLMResultEnvelope

_log = structlog.get_logger(__name__)


class LLMCache:
    """Per-user cache of validated LLM result envelopes on Redis.

    Constructed with an injected redis client (design decision D6) and
    the configured entry TTL (``llm_cache_ttl_seconds``, Requirement
    15.6). The public :func:`get_llm_cache` factory below wires the
    production instance through ``Depends``; tests construct instances
    directly around fake clients.
    """

    def __init__(self, redis_client: Redis, *, ttl_seconds: int) -> None:
        self._redis = redis_client
        self._ttl_seconds = ttl_seconds

    @staticmethod
    def _key(
        *,
        user_id: str,
        feature: str,
        input_hash: str,
        template_version: int,
        model: str,
    ) -> str:
        """Build the full cache key — user id always included (Req 15.1).

        Every component that could make a cached entry stale (redacted
        prompt-input hash, template version, model) or leak across users
        (user id) is part of the key, so lookups are scoped structurally
        rather than by a filter applied after the read.
        """
        return f"llm:cache:{user_id}:{feature}:{input_hash}:v{template_version}:{model}"

    async def get[T: BaseModel](
        self,
        *,
        user_id: str,
        feature: str,
        input_hash: str,
        template_version: int,
        model: str,
        envelope_type: type[LLMResultEnvelope[T]],
    ) -> LLMResultEnvelope[T] | None:
        """Look up a cached envelope; any failure is a miss (Req 15.7).

        ``envelope_type`` is the concretely parameterized envelope model
        (e.g. ``LLMResultEnvelope[CoachingReport]``) the raw JSON is
        validated against, so a hit hands the caller the same fully
        validated shape the original request produced (Requirement 15.2).

        Returns ``None`` — a miss — for an absent or expired key
        (Requirement 15.6), for any Redis error (Requirement 15.7),
        and for an entry that no longer validates against the current
        schema (defensive: a schema change mid-TTL must degrade to a
        fresh call, never to a 5xx or a malformed response).
        """
        key = self._key(
            user_id=user_id,
            feature=feature,
            input_hash=input_hash,
            template_version=template_version,
            model=model,
        )
        try:
            raw = await self._redis.get(key)
        except Exception:
            # Redis down or errored: miss; the normal call path proceeds
            # (Requirement 15.7). Structured event only — never the key
            # payload, never cached content.
            _log.warning("llm_cache_lookup_failed", feature=feature, user_id=user_id)
            return None

        if raw is None:
            return None

        try:
            return envelope_type.model_validate_json(raw)
        except Exception:
            # Corrupt or schema-incompatible entry: treat as a miss so
            # the request takes the normal provider-call path.
            _log.warning("llm_cache_entry_invalid", feature=feature, user_id=user_id)
            return None

    async def set[T: BaseModel](
        self,
        *,
        user_id: str,
        feature: str,
        input_hash: str,
        template_version: int,
        model: str,
        envelope: LLMResultEnvelope[T],
    ) -> None:
        """Best-effort write of a validated, non-fallback envelope.

        Only :class:`LLMResultEnvelope` instances are accepted, so the
        payload has passed schema validation by construction; a fallback
        envelope (``is_fallback=True``) is rejected with a
        :exc:`ValueError` before any Redis I/O — fallbacks are never
        cached (Requirement 15.4). The :exc:`ValueError` marks a caller
        bug (the orchestrator only reaches the cache-write step with
        validated LLM output), not a runtime cache failure.

        A Redis write failure is swallowed: the validated result is
        still returned to the requester by the caller, and the failure
        is recorded as a structured event with no PII and no cached
        content — never a 5xx (Requirement 15.8).
        """
        if envelope.is_fallback:
            raise ValueError(
                "LLMCache.set rejects fallback envelopes: fallbacks are never "
                "cached (Requirement 15.4)"
            )

        key = self._key(
            user_id=user_id,
            feature=feature,
            input_hash=input_hash,
            template_version=template_version,
            model=model,
        )
        try:
            await self._redis.set(key, envelope.model_dump_json(), ex=self._ttl_seconds)
        except Exception:
            # Best-effort: the result has already been produced and will
            # be returned regardless (Requirement 15.8).
            _log.warning("llm_cache_write_failed", feature=feature, user_id=user_id)


# ---------------------------------------------------------------------------
# Per-request cache factory.
#
# Mirrors ``services/llm/quota.py``'s ``get_daily_quota``: the client
# lifecycle (per-request construction, teardown, event-loop affinity)
# lives in ``core/redis.py``'s ``get_redis_client``. This factory only
# composes that dependency with the configured TTL.
# ---------------------------------------------------------------------------


async def get_llm_cache(
    client: Annotated[Redis, Depends(get_redis_client)],
) -> LLMCache:
    """Build a per-request :class:`LLMCache` around the injected client.

    The TTL comes from settings (``llm_cache_ttl_seconds``, Requirement
    15.6); the client is created and closed by
    :func:`matchlayer_api.core.redis.get_redis_client`. Tests that
    drive :class:`LLMCache` directly construct fake clients and skip
    this factory entirely.
    """
    settings = get_settings()
    return LLMCache(client, ttl_seconds=settings.llm_cache_ttl_seconds)
