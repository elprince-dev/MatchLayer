"""The no-LLM branch of the agent hierarchy (phase-4-agentic).

:class:`DeterministicAgent` is a thin abstract marker class — the
intermediate layer of the three-level hierarchy (design section
"2. Agent class hierarchy", decision D2) that fixes the LLM-vs-no-LLM
split **structurally** rather than by convention:

* **This module imports nothing from ``ml/llm/``** — directly or
  transitively at module level. Its only project import is
  :mod:`matchlayer_api.ml.agents.base`, which itself keeps the LLM
  orchestrator exceptions behind a lazy import inside
  :func:`~matchlayer_api.ml.agents.base.classify_failure` precisely so
  this branch's import graph stays clean.
* **The constructor accepts no orchestrator or client.** It is exactly
  :meth:`BaseAgent.__init__` — an :class:`~matchlayer_api.ml.agents.base.AgentDeps`
  carrying timeout, persistence callback, tracer, and clock. There is no
  field through which an LLM handle could arrive.

Together those two facts satisfy Requirement 1.6 ("the ATS_Agent,
Skill_Gap_Agent, and Synthesizer SHALL make no LLM_Provider call under
any input") by construction: a subclass of this class *cannot reach an
LLM* — there is nothing in its module namespace or instance state to
call. Property 3 and the hierarchy contract tests (task 5.6) verify the
guarantee dynamically; this module makes it hold statically.

Concrete deterministic agents (``ATSAgent``, ``SkillGapAgent``,
``SynthesizerAgent``) implement :meth:`BaseAgent.run` as a pure function
over :class:`~matchlayer_api.ml.agents.state.AgentState` (plus, for the
ATS agent, its injected Phase 2 scorer adapter). Everything cross-cutting
— timeout, degradation, Agent_Run persistence, spans, latency — is
inherited unchanged from :class:`BaseAgent`'s final template method.
"""

from __future__ import annotations

from abc import ABC

from pydantic import BaseModel

from matchlayer_api.ml.agents.base import BaseAgent

__all__ = ["DeterministicAgent"]


class DeterministicAgent[TOut: BaseModel](BaseAgent[TOut], ABC):
    """Abstract marker: an agent with no LLM dependency by construction.

    Adds no state and no behavior over :class:`BaseAgent` — its value is
    the *absence* of anything LLM-shaped (see the module docstring for
    why that absence is load-bearing for Requirement 1.6). Subclasses
    implement ``run`` / ``build_degraded`` / ``build_minimal`` as pure
    functions over state and injected non-LLM collaborators only.
    """
