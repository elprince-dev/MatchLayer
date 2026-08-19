"""Structural no-LLM guarantee of ``DeterministicAgent`` (phase-4-agentic, task 4.3).

Verifies that the ``ml/agents/deterministic_agent.py`` module keeps
Requirement 1.6 structurally true:

* importing the module in a fresh interpreter pulls **no**
  ``matchlayer_api.ml.llm`` / ``matchlayer_api.services.llm`` module into
  ``sys.modules`` (the transitive module-level import graph is clean);
* the module source contains no import statement reaching those packages
  (the direct check, immune to environment caching);
* the constructor is exactly ``BaseAgent.__init__`` — one ``AgentDeps``
  parameter, no slot through which an orchestrator or client could arrive.

The full hierarchy contract tests (every concrete agent subclasses exactly
one intermediate class, no ``__call__`` overrides, Synthesizer re-raises)
are task 5.6 and are deliberately NOT here.

Design reference: design.md "DeterministicAgent" (thin abstract marker).
Validates: Requirement 1.6.
"""

from __future__ import annotations

import ast
import inspect
import json
import subprocess
import sys
from pathlib import Path

from matchlayer_api.ml.agents.base import BaseAgent
from matchlayer_api.ml.agents.deterministic_agent import DeterministicAgent

_MODULE = "matchlayer_api.ml.agents.deterministic_agent"
_FORBIDDEN_PREFIXES = ("matchlayer_api.ml.llm", "matchlayer_api.services.llm")

_MODULE_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "src"
    / "matchlayer_api"
    / "ml"
    / "agents"
    / "deterministic_agent.py"
)


def test_fresh_import_pulls_no_llm_modules() -> None:
    """A fresh interpreter importing the module loads no LLM machinery.

    Runs ``python -c`` in a subprocess so the check sees the true
    module-level import graph, unpolluted by whatever this test process
    has already imported. Any ``matchlayer_api.ml.llm*`` or
    ``matchlayer_api.services.llm*`` entry in ``sys.modules`` afterwards
    means the deterministic branch acquired an LLM dependency at import
    time — a structural Requirement 1.6 regression.

    Validates: Requirement 1.6.
    """
    probe = (
        "import importlib, json, sys\n"
        f"importlib.import_module({_MODULE!r})\n"
        f"prefixes = {_FORBIDDEN_PREFIXES!r}\n"
        "loaded = sorted(\n"
        "    name for name in sys.modules\n"
        "    if any(name == p or name.startswith(p + '.') for p in prefixes)\n"
        ")\n"
        "print(json.dumps(loaded))\n"
    )
    # Fixed argv built from our own interpreter — nothing user-supplied.
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, (
        f"importing {_MODULE} in a fresh interpreter failed:\n{result.stderr}"
    )
    loaded: list[str] = json.loads(result.stdout)
    assert loaded == [], (
        f"importing {_MODULE} must not load any LLM module "
        f"(Requirement 1.6); found in sys.modules: {loaded}"
    )


def test_module_source_has_no_llm_imports() -> None:
    """No import statement in the module source reaches an LLM package.

    AST walk (not text grep) so docstring prose that *names* the banned
    packages — as this module's docstring does — cannot false-positive.

    Validates: Requirement 1.6.
    """
    tree = ast.parse(_MODULE_PATH.read_text(encoding="utf-8"), filename=str(_MODULE_PATH))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            offenders.extend(
                alias.name
                for alias in node.names
                if any(
                    alias.name == p or alias.name.startswith(f"{p}.") for p in _FORBIDDEN_PREFIXES
                )
            )
        elif (
            isinstance(node, ast.ImportFrom)
            and node.level == 0
            and node.module is not None
            and any(
                node.module == p or node.module.startswith(f"{p}.") for p in _FORBIDDEN_PREFIXES
            )
        ):
            offenders.append(node.module)
    assert not offenders, (
        f"{_MODULE_PATH.name} must import nothing from ml/llm/ or services/llm/ "
        f"(Requirement 1.6); found: {offenders}"
    )


def test_constructor_accepts_no_orchestrator_or_client() -> None:
    """The constructor is exactly ``BaseAgent.__init__``: ``(self, deps)``.

    ``DeterministicAgent`` declares no ``__init__`` of its own, so there is
    no parameter through which an orchestrator or LLM client could be
    injected — the structural half of Requirement 1.6.

    Validates: Requirement 1.6.
    """
    assert DeterministicAgent.__init__ is BaseAgent.__init__, (
        "DeterministicAgent must not define its own __init__ — the marker "
        "class adds no constructor surface (Requirement 1.6)."
    )
    params = list(inspect.signature(DeterministicAgent.__init__).parameters)
    assert params == ["self", "deps"], (
        f"expected the inherited BaseAgent constructor (self, deps); got {params}"
    )
    assert inspect.isabstract(DeterministicAgent), (
        "DeterministicAgent must remain abstract (run/build_degraded/build_minimal unimplemented)."
    )
