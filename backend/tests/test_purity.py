"""Pure packages stay pure: one table (``purity.py``) for every package.

Purity means no side effects -- files, network, processes, threads, the
environment, clocks, randomness or dynamic code -- and no provider, adapter or
harness dependency. Pure standard-library computation is allowed.
"""

import os
import subprocess
import sys

import pytest

from purity import (
    ADAPTERS, CAPABILITY_MODULES, GRIMOIRE, PURE_PACKAGES, PurePackage, SRC, boundary_problems,
    module_package, pure_sources,
)

CORE = next(p for p in PURE_PACKAGES if p.name == "grimoire.core")
EVALS = next(p for p in PURE_PACKAGES if p.name == "grimoire.evals")


def test_pure_packages_respect_their_boundary():
    problems = []
    sources = pure_sources()
    assert len(sources) > 20
    for path, package in sources:
        problems += boundary_problems(path.read_text(encoding="utf-8"), str(path.relative_to(SRC)),
                                      package, module_package(path))
    assert problems == []


def test_every_grimoire_package_is_classified():
    # A new top-level package must be declared pure (and checked) or an adapter.
    found = {f"grimoire.{p.name}" for p in GRIMOIRE.iterdir() if (p / "__init__.py").is_file()}
    declared = {p.name for p in PURE_PACKAGES} | ADAPTERS
    assert found <= declared, f"classify these packages in purity.py: {sorted(found - declared)}"


def _check(source, package=CORE, filename="grimoire/core/sample.py", within="grimoire.core"):
    return boundary_problems(source, filename, package, within)


@pytest.mark.parametrize("source", [
    "import os",
    "import pathlib",
    "from os import path",
    "import time",
    "import random",
    "import uuid",
    "import sys",
    "import importlib",
    "import requests",
    "def f():\n    import socket",
    "from grimoire import testing",
    "from grimoire.library import resolve_library",
    "from .. import library",
    "from ..testing import faults",
    "from .inference import plan",
    "x = open('f')",
    "x = __import__('os')",
    "x = eval('1')",
    "print('hi')",
    "data = __loader__.get_data('/etc/hostname')",
    "def load(p):\n    return p.open().read()",
    "f = (lambda: 0).__globals__",
    "subclasses = object.__subclasses__()",
    "m = loader.import_module('os')",
])
def test_checker_flags_side_effect_spellings(source):
    assert _check(source), source


@pytest.mark.parametrize("source", [
    "import re\nPATTERN = re.compile('a+')",
    "import unicodedata\nunicodedata.normalize('NFC', 'x')",
    "from collections.abc import Mapping",
    "from .identity import SourceRef",
    "from grimoire.core.identity import SourceRef",
    "import hashlib\nhashlib.sha256(b'x')",
    "class Door:\n    def opened(self):\n        return self.state",
])
def test_checker_accepts_pure_computation(source):
    assert _check(source) == []


def test_relative_imports_resolve_against_the_modules_own_package():
    nested = "grimoire.core.entities"
    assert boundary_problems("from ..identity import SourceRef", "x.py", CORE, nested) == []
    assert boundary_problems("from ...library import paths", "x.py", CORE, nested)


def test_only_the_evals_edge_modules_read_files():
    assert boundary_problems("import json", "loader.py", EVALS, "grimoire.evals") == []
    assert boundary_problems("import json", "checks.py", EVALS, "grimoire.evals")
    assert boundary_problems("from grimoire.core import SourceRef", "loader.py", EVALS, "grimoire.evals")


def _modules_loaded_by(statement: str) -> set[str]:
    env = {**os.environ, "PYTHONPATH": str(SRC)}
    script = f"import sys\n{statement}\nprint('\\n'.join(sys.modules))\n"
    done = subprocess.run([sys.executable, "-c", script],
                          capture_output=True, text=True, env=env, check=True)
    return set(done.stdout.split())


def _pure_module_names(package: PurePackage) -> list[str]:
    names = []
    for path in sorted(package.directory.rglob("*.py")):
        if path.name in package.io_modules:
            continue
        parts = path.relative_to(SRC).with_suffix("").parts
        names.append(".".join(parts[:-1] if parts[-1] == "__init__" else parts))
    return names


@pytest.mark.parametrize("package", PURE_PACKAGES, ids=lambda p: p.name)
def test_importing_a_pure_package_loads_no_capability_module(package):
    baseline = _modules_loaded_by("pass")
    names = _pure_module_names(package)
    loaded = _modules_loaded_by("\n".join(f"import {name}" for name in names)) - baseline
    assert set(names) <= loaded | baseline
    reached = sorted(m for m in loaded if any(m == c or m.startswith(c + ".") for c in CAPABILITY_MODULES))
    assert reached == []
    if package is EVALS:
        assert not any(m == "grimoire.core" or m.startswith("grimoire.core.") for m in loaded)
