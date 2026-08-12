"""LLM feature services for the Phase 3 LLM Layer.

This package holds the shared LLM request pipeline and its supporting
components (design §"Backend layout"): result schemas, PII redaction,
prompt assembly, the three feature specs, quota accounting, the spend
circuit breaker, caching, invocation logging, and result persistence.
Everything here sits above the provider-neutral ``LLMClient`` abstraction
in ``matchlayer_api.ml.llm`` — no module in this package constructs an
HTTP request to the LLM provider directly (Requirement 1.2).
"""
