---
inclusion: manual
---

# Phase 4 — Agentic AI System

**Status:** not started
**Depends on:** Phase 3 shipped.
**Goal:** restructure the AI layer into composable agents orchestrated with LangGraph. Move from one-shot LLM calls to multi-step reasoning with state.

## Why this phase exists

Modern AI systems aren't single LLM calls — they're graphs of specialized agents that pass state, retry, and converge on better outputs. This phase makes MatchLayer look like a 2025-era AI product, not a 2023-era one.

## In scope

- **Orchestration**
  - LangGraph for agent workflows. Each agent is a node; edges encode flow.
  - State schema as a typed Pydantic model passed between nodes.
  - Persistence via LangGraph's built-in checkpointer (Postgres-backed).
- **Agents**
  - **Resume Analysis Agent** — extracts structured candidate profile (sections, skills, experiences, gaps) from raw resume text.
  - **ATS Agent** — runs the Phase 2 composite score, tagged with confidence.
  - **Skill Gap Agent** — identifies missing/weak skills relative to the JD, prioritized by importance.
  - **Resume Improvement Agent** — generates rewrites and additions, calling Phase 3's coach prompt internally.
  - **Synthesizer** — non-LLM node that combines outputs into the final response.
- **Workflow**
  - User uploads resume + JD → orchestrator runs the graph → final response includes everything Phase 3 returned, plus structured agent traces.
  - Async execution: trigger via `POST /api/v1/matches/{id}/analyze`, return `202 Accepted` + a job id, poll status with `GET /api/v1/jobs/{id}`.
  - SQS introduced _here_, not in Phase 6, because async is now a real product requirement.
- **Observability**
  - OpenTelemetry tracing — every agent invocation is a span.
  - Each agent run persisted in `agent_runs` table for debugging and replay.
- **Frontend**
  - Polling-based progress UI: "Analyzing… ATS scoring… Generating improvements…"
  - Optional: stream agent step results as they complete (nice-to-have).
  - "Show reasoning" toggle revealing per-agent outputs.

## Explicitly out of scope

- Eval framework — that's Phase 5, even though it's tempting to build alongside.
- Multi-resume comparison / batch processing.
- Tool-use beyond what's needed (no web search, no code execution).
- Multi-turn conversational agents.

## Deliverables

1. LangGraph-orchestrated workflow replacing the linear Phase 3 calls.
2. SQS + worker process running agents asynchronously.
3. Trace UI on the frontend.
4. Architecture doc (`docs/adr/0004-agent-architecture.md`) describing the graph.

## Success criteria

- End-to-end agent run completes in < 30 seconds for typical inputs.
- Each agent has a single, clear responsibility (no agent does two unrelated things).
- A failing agent doesn't fail the whole graph — graceful degradation per node.
- Trace data is sufficient to reproduce any past run from inputs alone.

## Skills demonstrated

Agentic AI · LangGraph · stateful workflows · async orchestration · SQS · OpenTelemetry tracing · multi-agent design

## Risks & gotchas

- **Over-engineering.** Resist the urge to make every node an LLM agent. The Synthesizer and ATS agents should be deterministic. LLMs only where reasoning is genuinely needed.
- **Latency stacking.** 4 LLM calls × 5s each = 20s wall time. Use parallel branches in the graph where agents are independent (Skill Gap and Improvement can both depend on Analysis but run in parallel with each other).
- **Debugging is hard.** Without trace persistence, a failing graph is opaque. Build the trace table early.
- **Cost.** A graph with 4 LLM calls is 4x Phase 3 cost. Cache aggressively per (agent, input_hash). Reuse Phase 3's quota system.
- **State explosion.** Don't push the entire raw resume through every agent. Pass extracted/structured state, not raw text.

## Folder additions

```
apps/api/src/matchlayer_api/ml/
  agents/
    __init__.py
    state.py                        # shared Pydantic state schema
    resume_analysis_agent.py
    ats_agent.py
    skill_gap_agent.py
    improvement_agent.py
    synthesizer.py
    graph.py                        # LangGraph wiring
apps/api/src/matchlayer_api/workers/
  agent_worker.py                   # SQS consumer
infra/docker/worker.Dockerfile
docs/adr/0004-agent-architecture.md
```

DB additions:

- `agent_jobs` (id, user_id, match_id, status, created_at, started_at, completed_at, error_json)
- `agent_runs` (id, job_id, agent_name, input_state_json, output_state_json, latency_ms, status)

## Work breakdown

1. Add LangGraph + dependencies; design the graph on paper first.
2. Define the shared state Pydantic schema.
3. Implement each agent as a pure function over state — start with Resume Analysis (most foundational).
4. Wire the graph and run it synchronously end-to-end first to validate.
5. Add SQS to docker-compose (LocalStack) and create a worker process.
6. Move execution to async: enqueue on `POST /matches/{id}/analyze`, drain queue in worker.
7. Add `agent_jobs` and `agent_runs` migrations + persistence.
8. Add OpenTelemetry — instrument every agent.
9. Frontend: job-status polling UI and trace view.
10. Write the ADR.

## Definition of done

The user clicks "Analyze" and watches a multi-step agent workflow run, with per-agent visibility, traces persisted for every run, and the system handling agent failures without taking down the whole pipeline.
