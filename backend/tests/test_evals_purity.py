"""``grimoire.evals`` stays offline and independent of the domain core."""

import ast
import os
import pkgutil
import subprocess
import sys
from pathlib import Path

import grimoire.evals

SRC = Path(__file__).resolve().parents[1] / "src"
PACKAGE = SRC / "grimoire" / "evals"
BANNED_MODULES = ("grimoire.core", "socket", "ssl", "http.client", "urllib.request")
IO_MODULES = {"json", "pathlib", "importlib.resources", "io", "os"}
IO_ALLOWED = {"loader.py", "story_quality.py"}


def evals_modules():
    names = sorted(info.name for info in pkgutil.walk_packages(grimoire.evals.__path__,
                                                               prefix="grimoire.evals."))
    assert names, "no evals modules discovered"
    return names


def test_importing_every_evals_module_loads_no_network_or_core_module():
    script = (
        "import importlib, sys\n"
        f"for name in {evals_modules()!r}:\n"
        "    importlib.import_module(name)\n"
        f"print(sorted(m for m in {BANNED_MODULES!r} if m in sys.modules))\n"
    )
    env = {**os.environ, "PYTHONPATH": str(SRC)}
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                            env=env, check=True)
    assert result.stdout.strip() == "[]"


def _imports(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                yield node.module or ""
            else:  # resolve e.g. ``from ..core import x`` relative to grimoire.evals
                base = "grimoire.evals".split(".")[:len("grimoire.evals".split(".")) - node.level + 1]
                yield ".".join([*base, *([node.module] if node.module else [])])


def test_only_the_loader_and_cli_touch_files():
    for path in sorted(PACKAGE.glob("*.py")):
        tree = ast.parse(path.read_text("utf-8"))
        calls = {node.func.id if isinstance(node.func, ast.Name) else node.func.attr
                 for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and isinstance(node.func, (ast.Name, ast.Attribute))}
        assert not calls & {"__import__", "import_module"}, path.name
        imported = set(_imports(tree))
        assert not any(name == "grimoire.core" or name.startswith("grimoire.core.")
                       for name in imported), path.name
        if path.name not in IO_ALLOWED:
            assert not imported & IO_MODULES, path.name
            assert "open" not in calls, path.name
