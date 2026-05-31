"""Committed package-data for the scoring core (phase-1-matching).

This subpackage ships the ``Skill_Lexicon`` runtime artifact
(``skill_lexicon.v1.json``) that the API loads via ``importlib.resources``
(loader added in task 3.2). The artifact is a **committed copy** of the
canonical source under ``ml/lexicon/skill_lexicon.v1.json``; the two are kept
byte-identical by ``ml/pipelines/build_skill_lexicon.py`` and enforced by the
CI drift check ``tools/check_lexicon_drift.py`` (Requirement 10.3).

Being a real package (rather than a bare directory) keeps the JSON shipped in
the built wheel and makes ``importlib.resources.files(...)`` resolution
straightforward.
"""
