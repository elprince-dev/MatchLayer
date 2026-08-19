"""Worker processes (phase-4-agentic).

Long-running consumers that execute outside the HTTP request path. The
one Phase 4 worker is the Agent_Worker (:mod:`.agent_worker`) — the SQS
consumer that executes Agent_Jobs by running the compiled Agent_Graph
(Requirement 11.2). Workers reuse the ``matchlayer_api`` package (same
codebase as the API, design decision D9) but run as separate processes
with their own entrypoints, never importing FastAPI routing.
"""
