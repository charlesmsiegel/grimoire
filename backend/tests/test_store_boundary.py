"""The generic store is facet-agnostic: it depends on identity, paths and authority only (F8)."""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
STORE = SRC / "grimoire" / "store"

#: What the store may import from grimoire. A 009 facet module is outside it by default.
ALLOWED = ("grimoire.core.identity", "grimoire.core.index", "grimoire.library", "grimoire.security",
           "grimoire.store")
#: Exactly what ``import grimoire.store`` loads today. ``grimoire.core``'s package
#: ``__init__`` re-exports model and authority, so they load; anything new fails here.
EXPECTED_RUNTIME = {
    "grimoire", "grimoire.core", "grimoire.core.authority", "grimoire.core.identity", "grimoire.core.index",
    "grimoire.core.model", "grimoire.core.resolve", "grimoire.library", "grimoire.library.paths",
    "grimoire.security", "grimoire.security.authorizer", "grimoire.security.policy",
    "grimoire.security.resource",
}


def _grimoire_imports(path: Path) -> list[str]:
    names = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                names.append("grimoire.store" + (f".{node.module}" if node.module else ""))
            elif node.module:
                names.append(node.module)
        elif isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
    return [name for name in names if name.split(".")[0] == "grimoire"]


def test_store_imports_only_identity_paths_and_authority():
    problems = [
        f"{path.name} imports {name}"
        for path in sorted(STORE.glob("*.py"))
        for name in _grimoire_imports(path)
        if not any(name == allowed or name.startswith(allowed + ".") for allowed in ALLOWED)
    ]
    assert problems == []


def test_importing_the_store_loads_no_unexpected_grimoire_module():
    code = ("import json, sys; import grimoire.store; "
            "print(json.dumps(sorted(m for m in sys.modules if m.split('.')[0] == 'grimoire')))")
    output = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True,
                            env={**os.environ, "PYTHONPATH": str(SRC)}).stdout
    loaded = set(json.loads(output))
    store_modules = {name for name in loaded if name.startswith("grimoire.store")}
    assert loaded - store_modules == EXPECTED_RUNTIME
