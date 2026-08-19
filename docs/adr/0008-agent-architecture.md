# 0008 — Agent architecture: LangGraph graph, class hierarchy, and degradation policy

**Status:** Accepted
**Date:** 2026-02-07
**Applies to:** Phase 4+ (agentic AI); realized by the `phase-4-agentic` spec

## Context

Phase 4 turns the single-shot Phase 3 LLM features into a multi-agent analysis workflow: one asynchronous Agent_Job produces a combined `AnalysisResult` (candidate profile, ATS score with confidence, prioritized skill gaps, improvement guidance, and per-agent trace summaries). The workflow runs in a queue-fed worker, not the API request path, and must satisfy several cross-cutting requirements at once:

- **Composability and inspectability** — the pipeline must be a declared graph over a typed state, not ad-hoc feature code (Requirement 1).
- **Cost and privacy discipline** — at most two LLM calls per run, every one flowing through the Phase 3 orchestrator's quota/redaction/caching/logging pipeline (Requirement 9); no raw resume text on any persistence or telemetry surface (Requirements 1.3, 2.3, 12.3, 13.3).
- **Resilience** — any single non-final agent failing must degrade that agent's output, never the job (Requirement 8).
- **Reproducibility** — every node invocation persisted as an immutable `agent_runs` row sufficient for Phase 5 replay (Requirement 12).
- **Latency** — a 30-second typical-path budget (Requirement 14), which demands real parallelism between independent agents.

The phase doc originally referenced "ADR 0004" for this decision, but ADR 0004 already records the pgvector decision and ADRs are numbered sequentially and immutable — hence this document is 0008 (Requirement 16.8).

## Decision

### Graph: five nodes, two levels of parallelism, one join point

The workflow is a LangGraph `StateGraph` over the typed `AgentState` (Pydantic), compiled in `apps/api/src/matchlayer_api/ml/agents/graph.py` with these nodes and edges:

```
START → resume_analysis          START → ats
resume_analysis → skill_gap      resume_analysis → improvement
[ats, skill_gap, improvement] → synthesizer → END
```

- **Resume_Analysis_Agent** (LLM) — structured `CandidateProfile` from the PII-redacted resume text.
- **ATS_Agent** (deterministic) — composite score + breakdown + Confidence_Level + Scorer_Version, reusing the persisted Match_Result score when the active Scorer_Version produced it, else rescoring via the Phase 2 `Semantic_Match_Scorer` (with its Degraded_Mode ladder) through an injected adapter.
- **Skill_Gap_Agent** (deterministic) — pure classification + prioritization rules (`gap_rules.py`, documented in `docs/agent-rules.md`) over JD skills vs. profile/matched skills.
- **Improvement_Agent** (LLM) — improvement actions + rewrite suggestions via the Phase 3 resume-coach prompt lineage resolved through the prompt registry.
- **Synthesizer** (deterministic) — assembles the four upstream outputs plus one `AgentTraceSummary` per agent into the `AnalysisResult`; the sole join point and terminal node.

Parallelism is structural: ATS runs alongside Resume Analysis (its inputs come entirely from the persisted Match_Result), and Skill Gap runs alongside Improvement (both consume the Candidate_Profile, neither consumes the other). The longest sequential path is Resume Analysis → branch → Synthesizer, so the two LLM nodes never serialize with each other's latency beyond that path — which is what makes the 30-second budget attainable.

### Typed state: `AgentState` carries identifiers plus derived content only

`AgentState` (`ml/agents/state.py`) holds the job/match/user identifiers, the **PII_Redactor-transformed** resume text, the Skill_Extractor-derived JD skill list, a `MatchSnapshot` projection of the persisted Match_Result, one output field per agent, and a per-agent status map. There is deliberately **no field for raw `extracted_text`**: every serialized state surface (checkpoints, `agent_runs` rows, spans) is PII-free by construction, not by filtering. Each output field is written by exactly one node, which is what makes LangGraph's parallel branch merge safe; the one field every node writes (`agent_status`) carries an explicit merge reducer.

### Class hierarchy: one lifecycle, structural LLM/no-LLM split

Agents are classes in a three-level hierarchy (`ml/agents/base.py`, `llm_agent.py`, `deterministic_agent.py`):

- **`BaseAgent[TOut]`** implements the entire cross-cutting lifecycle as a final template method (`__call__`, which is exactly the LangGraph node signature): span emission, `asyncio.wait_for` per-node timeout, exception classification onto a closed failure-trigger vocabulary, degraded-output construction (with a minimal schema-valid fallback if that itself fails), latency accounting, and Agent_Run persistence via an injected callback. Dependencies arrive through `AgentDeps` (timeout, persistence callback, tracer, clock) — no global state, no config reads.
- **`LLMAgent`** makes `run` final and delegates every provider interaction to the Phase 3 `LLMOrchestrator` (quota reserve, redaction, prompt registry, input hashing, caching, invocation logging, fallback envelopes). Concrete subclasses supply only the feature spec and the prompt-input builder. Span hooks attach prompt version, model id, and input hash **only when a provider call occurred**, mirroring the LLM_Invocation_Log.
- **`DeterministicAgent`** is a thin marker class whose module imports nothing from the LLM stack — Requirement 1.6 ("the deterministic agents make no LLM call") holds structurally, not behaviorally.

### Checkpointer: Postgres-backed, Alembic-provisioned, best-effort

The worker compiles the graph with LangGraph's `AsyncPostgresSaver` wrapped in a `BestEffortSaver`, keyed by `thread_id = job_id` so every checkpoint row is attributable to exactly one Agent_Job. The checkpointer schema is created by an Alembic migration invoking LangGraph's own setup routine — neither the API nor the worker performs runtime DDL. Checkpoint **writes are best-effort**: a write failure produces one structured warning (job id + exception class name, never payloads) and the run continues in memory; a checkpoint failure alone can never fail a job. Serialized snapshots inherit the PII-free property of `AgentState`.

### Degradation policy: per-node, schema-preserving, Synthesizer excepted

If any non-Synthesizer agent raises, exceeds the per-node timeout (`MATCHLAYER_AGENT_NODE_TIMEOUT_SECONDS`), or produces schema-invalid output, that agent contributes a **Degraded_Output conforming to its normal schema** (built from persisted Match_Result data, marked `degraded: true` with a structured failure reason), and the graph continues — including under multiple simultaneous degradations. Downstream agents consume degraded inputs without shape-branching, marking their outputs `derived_from_degraded_input`. The **Synthesizer re-raises instead of degrading**: with no synthesis there is no result, so its failure is the only single-node failure that fails the job — the worker persists the Synthesizer's `failed` Agent_Run row before recording the terminal `failed` Job_Status, and all upstream rows are retained.

## Rationale

- **A declared graph beats orchestration code.** Nodes, edges, and parallel branches are data (`build_graph`), so the topology is testable in isolation, inspectable at review time, and extensible without touching agent internals.
- **One tested lifecycle beats five hand-rolled copies.** Requirements 8, 12, and 13 impose identical timeout/degradation/persistence/tracing behavior on all five nodes; putting it in a final `BaseAgent.__call__` template method makes divergence impossible and keeps concrete `run` methods pure functions over state.
- **The structural LLM split makes cost properties provable.** `DeterministicAgent` subclasses cannot acquire an LLM dependency without changing their base class — the "at most two LLM calls per run" cost posture is enforced by the type hierarchy plus import-boundary tests, not by convention.
- **Reusing the Phase 3 orchestrator avoids drift.** Quota, redaction, caching, logging, and fallbacks already exist and are tested; duplicating them in agent code would guarantee divergence in exactly the places security and cost depend on.
- **PII-free by construction beats PII-free by filtering.** Removing the raw-text field from `AgentState` makes every downstream surface (checkpoints, runs, spans) safe without any scrubbing logic to forget.
- **Best-effort checkpointing matches the observability philosophy.** Job outcomes are determined solely by graph execution (the same stance as Phase 2's best-effort embedding persistence): plumbing never fails a run.

## Consequences

**Positive**

- Adding a sixth agent means one subclass + one node + edges; the lifecycle, persistence, tracing, and degradation come for free.
- Every run is replayable from persisted data: deterministic agents re-execute field-for-field identically, and LLM prompts are reconstructable from persisted input state + recorded prompt/redactor versions (Requirement 12.5's automated check).
- Degraded results remain schema-valid supersets of the Phase 3 feature outputs, so the frontend renders one shape with per-section degraded badges.
- The 30-second budget is defensible: two parallel branch levels bound the critical path to two LLM timeouts plus deterministic-node time.

**Negative**

- LangGraph becomes a load-bearing dependency (graph semantics, checkpointer schema, channel merge behavior); major-version upgrades need real regression coverage.
- The template-method lifecycle concentrates complexity in `BaseAgent.__call__` — subtle changes there affect all five nodes at once (mitigated by the hierarchy-contract unit tests and lifecycle property tests).
- Checkpoint rows and `agent_runs` rows grow without a Phase 4 retention path (deliberate: Phase 5 evaluation consumes them; retention is revisited then).
- A degraded-input chain (profile degrades → gap/improvement marked derived-from-degraded) can produce a completed job whose useful content is largely persisted Phase 1/2 data; the UI's degraded indicators are what keep that honest.

## Alternatives considered

- **Plain asyncio orchestration (no LangGraph):** rejected. Hand-rolled fan-out/fan-in, state merging, and checkpointing would re-implement exactly what LangGraph provides, without the inspectable topology; the checkpointer requirement (Requirement 2) would become bespoke persistence code.
- **Function-based nodes with decorators instead of a class hierarchy:** rejected. The cross-cutting lifecycle (timeout + degrade + persist + trace, in a fixed order) composes poorly as stacked decorators, and the LLM/no-LLM split would be a convention rather than a structural property.
- **Agents calling `LLMClient` directly with their own quota/redaction handling:** rejected. Duplicates the Phase 3 pipeline and guarantees drift in security-critical code paths (Requirement 9 explicitly demands orchestrator semantics).
- **Fail the whole job on any agent failure:** rejected. Requirement 8 mandates graceful degradation; the persisted Match_Result gives every non-Synthesizer agent a meaningful fallback, so a single flaky LLM call should not discard the four other agents' work.
- **Synchronous execution in the API request path:** rejected. A multi-LLM workflow cannot hold an HTTP request open within acceptable latency; the queue/worker split (Requirement 11) also delivers the `security.md` sandboxed-parsing direction — the API analyze path never reads `extracted_text`.
- **Numbering this ADR 0004 per the phase doc:** rejected. ADR 0004 (pgvector) exists and is immutable; ADRs number sequentially (Requirement 16.8).
