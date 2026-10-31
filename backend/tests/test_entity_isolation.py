"""Facet code stays out of authority, intent and storage-shaped boundaries (009).

Control must never depend on capabilities. ``grimoire.core/__init__`` re-exports
the entity modules, so importing ``grimoire.core.authority`` normally executes
it; these checks stub the package to load only what a module itself imports.
"""

import ast
import os
import subprocess
import sys

import pytest
from purity import GRIMOIRE, PURE_STDLIB, SRC

ENTITY_MODULES = ("json_value", "facets", "entity", "roster", "entity_codec")
CORE = GRIMOIRE / "core"
_STUB = (
    "import sys, types\n"
    "core = types.ModuleType('grimoire.core')\n"
    "core.__path__ = [{path!r}]\n"
    "sys.modules['grimoire.core'] = core\n"
)


def _loaded_by(statement: str, stub_core: bool = True) -> set[str]:
    env = {**os.environ, "PYTHONPATH": str(SRC)}
    prelude = _STUB.format(path=str(CORE)) if stub_core else "import sys\n"
    script = f"{prelude}{statement}\nprint('\\n'.join(sys.modules))\n"
    done = subprocess.run([sys.executable, "-S", "-c", script], capture_output=True, text=True,
                          env=env, check=True)
    return set(done.stdout.split())


def _entity_modules(loaded: set[str]) -> list[str]:
    return sorted(m for m in loaded if m in {f"grimoire.core.{name}" for name in ENTITY_MODULES})


@pytest.mark.parametrize("module", ["grimoire.core.authority", "grimoire.core.model",
                                    "grimoire.core.identity", "grimoire.core.index"])
def test_authority_and_identity_load_no_entity_module(module):
    assert _entity_modules(_loaded_by(f"import {module}")) == []


def test_the_isolation_harness_sees_entity_imports():
    # Positive control: the same harness does notice a module that needs facets.
    assert "grimoire.core.facets" in _loaded_by("import grimoire.core.roster")


def test_intent_routing_loads_no_entity_module():
    assert _entity_modules(_loaded_by("import grimoire.intent", stub_core=False)) == []


ALLOWED = {
    "json_value": set(),
    "facets": {"identity", "json_value"},
    "entity": {"facets", "identity", "index", "json_value", "model"},
    "roster": {"authority", "entity", "identity", "index", "model"},
    "entity_codec": {"entity", "facets", "identity", "json_value"},
}


@pytest.mark.parametrize("name", ENTITY_MODULES)
def test_entity_modules_import_only_their_allowlist(name):
    tree = ast.parse((CORE / f"{name}.py").read_text(encoding="utf-8"))
    siblings, stdlib = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 1:
            siblings.add(node.module)
        elif isinstance(node, ast.ImportFrom):
            stdlib.add(node.module.split(".")[0])
        elif isinstance(node, ast.Import):
            stdlib |= {alias.name.split(".")[0] for alias in node.names}
        assert not (isinstance(node, ast.ImportFrom) and node.level > 1), "no parent-package imports"
    assert siblings <= ALLOWED[name]
    assert stdlib <= PURE_STDLIB
