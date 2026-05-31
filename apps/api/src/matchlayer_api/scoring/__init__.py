"""Framework-free scoring core (phase-1-matching).

This package holds the deterministic, non-LLM ``Match_Scorer`` and its
collaborators (``Keyword_Analyzer``, ``Suggestion_Generator``, ``Skill_Lexicon``).
Per Requirement 10.1 and the design's import-boundary rule, modules in this
package import **only** scikit-learn and the Python standard library — never
FastAPI, SQLAlchemy, ``matchlayer_api.config``, or any storage/web module.

Task 3.1 establishes the committed ``Skill_Lexicon`` artifact under
``data/skill_lexicon.v1.json``; task 3.2 adds the loader (``lexicon.py``),
which exposes the canonical terms, alias normalization, per-term weight/
metadata lookup, ``lexicon_version`` and the composed ``Scorer_Version``. The
remaining scorer modules are added by later tasks.
"""
