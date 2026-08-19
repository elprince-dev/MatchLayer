"""Agent_Job services — queue, lifecycle, run persistence, and cache.

Phase 4 (phase-4-agentic) package grouping the persistence and transport
services behind the async agent workflow:

* ``queue.py`` — the :class:`~matchlayer_api.services.agent_jobs.queue.JobQueue`
  async SQS client (the ONLY module in the API allowed to import
  ``aioboto3``).
* ``service.py`` — Agent_Job lifecycle (create-with-idempotency, guarded
  status transitions, owner-scoped reads). Added by task 8.1.
* ``runs.py`` — immutable Agent_Run row persistence. Added by task 8.2.
* ``cache.py`` — the Redis-backed Agent_Cache. Added by task 8.3.

Design reference: phase-4-agentic design §5 and §8.
"""
