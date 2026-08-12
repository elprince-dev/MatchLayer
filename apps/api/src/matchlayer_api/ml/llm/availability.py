"""LLM availability state — the key-present half of LLM_Unavailable.

Phase 3 mirror of the Phase 2 module-level availability probe
(:mod:`matchlayer_api.ml.semantic_adapter`): the FastAPI lifespan calls
:func:`initialize_llm_availability` exactly once at startup, and the rest of
the app reads the recorded state through :func:`llm_key_present`.

Behavior (design "Startup wiring"; Requirements 1.7, 1.8, 1.10, 10.2):

* **Key absent or empty** → the app starts normally with LLM_Features in the
  LLM_Unavailable state: ``llm_key_present()`` returns ``False``, features
  serve their Fallback_Response, and ``/healthz`` reports
  ``llm: unavailable`` (Requirements 1.8, 10.2). One structured
  ``llm_unavailable_key_absent`` event is emitted so operators can tell a
  deliberate keyless deployment from a broken one.
* **Key present** → the provider adapter's ``validate_credentials()`` runs
  during lifespan startup, bounded by ``MATCHLAYER_LLM_TIMEOUT_SECONDS``
  inside the adapter. Failure raises :class:`LLMStartupValidationError`
  whose message names the cause category (``invalid_key`` / ``unreachable``
  / ``timeout``) and **never** the key value (Requirement 1.10) — the
  lifespan lets it propagate so uvicorn exits non-zero before binding a
  port, the established fail-fast startup pattern.
* There is **no endpoint and no configuration path** by which a user account
  can supply its own provider API key (Requirement 1.7): the only key this
  module ever consults is the app-owned ``Settings.llm_api_key``.

The recorded state covers only the *key-present* cause of LLM_Unavailable.
The other cause — the Spend_Circuit_Breaker being open — lives with the
breaker itself (``services/llm/spend.py``); the ``/healthz`` ``llm`` field
composes both (design "healthz" section; Requirement 10.2).

Like Degraded_Mode in Phase 2, the only way a key-absent instance becomes
available is a process restart with the key configured (Requirement 10.4's
"valid API key is present at the next startup").
"""

from __future__ import annotations

import structlog

from matchlayer_api.config import Settings, get_settings
from matchlayer_api.ml.llm.client import LLMClient, LLMError
from matchlayer_api.ml.llm.openrouter import OpenRouterClient

__all__ = [
    "LLMStartupValidationError",
    "build_llm_client",
    "initialize_llm_availability",
    "llm_key_present",
]

_log = structlog.get_logger(__name__)

# The process-wide key-present state. ``False`` means LLM_Unavailable via
# the absent-key cause: either ``initialize_llm_availability`` has not run
# yet or it found no usable key. Set exactly once by the lifespan's call;
# the only way out is a process restart with the key configured.
_key_present: bool = False


class LLMStartupValidationError(RuntimeError):
    """Startup credential validation against the LLM provider failed.

    Raised by :func:`initialize_llm_availability` when a configured key
    fails ``validate_credentials()``. The message carries the failure
    *category* only (``invalid_key`` / ``unreachable`` / ``timeout``) —
    never the key value, which stays inside its ``SecretStr`` (Requirements
    1.9, 1.10). The lifespan deliberately does not catch this: startup must
    abort, because running with a key the provider rejects would silently
    turn every LLM_Feature into its fallback while ``/healthz`` claims
    ``llm: available``.
    """


async def initialize_llm_availability(
    settings: Settings | None = None,
    client: LLMClient | None = None,
) -> None:
    """Record LLM availability and validate credentials at startup.

    Called exactly once from the FastAPI lifespan (design "Startup
    wiring"). Key absent/empty → records ``key_present=False`` and returns
    normally so the app starts with LLM_Features in LLM_Unavailable
    (Requirement 1.8). Key present → runs the adapter's
    ``validate_credentials()``; success records ``key_present=True``,
    failure raises :class:`LLMStartupValidationError` naming the cause
    category (Requirement 1.10).

    Args:
        settings: Optional :class:`Settings` override; the cached
            process-wide instance is used when omitted (the production
            path).
        client: Optional :class:`LLMClient` override for tests. When
            omitted and a key is configured, the OpenRouter adapter is
            constructed — the one place startup touches provider-specific
            code, and only via the provider-neutral protocol surface.
    """
    # The single sanctioned module-state write: the lifespan calls this once.
    global _key_present
    _key_present = False

    cfg = settings if settings is not None else get_settings()
    api_key = cfg.llm_api_key
    if api_key is None or not api_key.get_secret_value():
        # Deliberate keyless deployment — not an error. The app serves
        # every LLM_Feature request via its Fallback_Response and reports
        # ``llm: unavailable`` on /healthz (Requirements 1.8, 10.2). The
        # event carries no key material — there is none to carry.
        _log.info("llm_unavailable_key_absent")
        return

    resolved_client = client if client is not None else OpenRouterClient(cfg)
    try:
        await resolved_client.validate_credentials()
    except LLMError as exc:
        # The category is one of the adapter's three startup-check values
        # (invalid_key / unreachable / timeout). Chaining ``from exc`` is
        # safe: LLMError never carries response bodies or the key
        # (Requirement 1.9), so the traceback stays operator-safe.
        raise LLMStartupValidationError(
            f"LLM provider credential validation failed at startup: {exc.category}"
        ) from exc

    _key_present = True
    _log.info("llm_credentials_validated")


def build_llm_client(settings: Settings | None = None) -> LLMClient:
    """Construct the configured provider adapter behind the neutral protocol.

    The single provider-neutral seam through which the rest of the app
    (the LLM routers composing the orchestrator's ``client_factory``)
    obtains an :class:`~matchlayer_api.ml.llm.client.LLMClient` without
    referencing provider-specific code (Requirement 1.1): this module is
    the sanctioned composition root that may name the adapter, so swapping
    providers in Phase 6 changes this one function plus configuration.

    Constructed lazily, once per provider call — the orchestrator invokes
    the factory only after the key-present check and every other pre-call
    gate passed, so a keyless deployment never reaches this constructor.

    Args:
        settings: Optional :class:`Settings` override; the cached
            process-wide instance is used when omitted (the production
            path — the zero-argument shape the orchestrator's
            ``client_factory`` calls).

    Returns:
        A fresh adapter instance satisfying the provider-neutral
        :class:`~matchlayer_api.ml.llm.client.LLMClient` protocol.
    """
    cfg = settings if settings is not None else get_settings()
    return OpenRouterClient(cfg)


def llm_key_present() -> bool:
    """Whether a provider-validated API key was present at startup.

    ``False`` means the absent-key cause of LLM_Unavailable holds: features
    must serve their Fallback_Response without attempting a provider call
    (Requirement 10.2/10.3), and the ``/healthz`` ``llm`` field reports
    ``unavailable``. Read-only accessor for the state
    :func:`initialize_llm_availability` recorded.
    """
    return _key_present
