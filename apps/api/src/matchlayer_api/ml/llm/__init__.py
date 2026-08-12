"""Provider-neutral LLM client layer (Phase 3, phase-3-llm-layer).

This package is the single gateway between the API and any LLM provider.
``client.py`` defines the provider-neutral protocol and request/response
types — no provider-specific identifiers appear there (Requirement 1.1).
All provider-specific code is confined to a single adapter module in this
package, so a Phase 6 provider swap is a new adapter plus a configuration
change, never a rewrite of callers.

Dependency direction follows the existing ``ml/`` boundary rules: this layer
may read application settings and is imported by ``services/llm/`` — never
the reverse.
"""
