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
    ADAPTERS, CAPABILITY_MODULES, GRIMOIRE, NETWORK_MODULES, PURE_PACKAGES, PurePackage, SRC,
    boundary_problems, module_package, pure_sources,
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
    # A new package or module must be declared pure (and so checked) or an adapter,
    # including a namespace package with no __init__.py.
    found = {".".join(("grimoire", *p.relative_to(GRIMOIRE).parts))
             for p in GRIMOIRE.iterdir() if p.is_dir() and p.name != "__pycache__" and any(p.rglob("*.py"))}
    found |= {f"grimoire.{p.stem}" for p in GRIMOIRE.glob("*.py") if p.name != "__init__.py"}
    declared = {p.name for p in PURE_PACKAGES} | ADAPTERS
    assert found <= declared, f"classify these in purity.py: {sorted(found - declared)}"


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
    "import dataclasses\ndataclasses.inspect.os.system('x')",
    "import typing\nm = typing.sys.modules",
    "from typing import sys",
    "from dataclasses import inspect as i",
    "from enum import sys as s",
    "import statistics",
    "import enum\nenum.bltns.exec('import os')",
    "import enum\nenum.bltns.__import__('os').system('x')",
    "import enum as e\nx = e.bltns",
    "from enum import bltns as b\nf = b.getattr(b, 'op' + 'en')",
    "import dataclasses\nhelper = dataclasses\ndef f():\n    return helper.inspect.os.getenv('HOME')",
    "import dataclasses\nx = getattr(dataclasses, 'inspect')",
    "import dataclasses\nx = dataclasses.__dict__['inspect']",
    "import re\nmodules = [re]",
    "def f(x):\n    return getattr(x, '__globals__')",
    "import re\nx = re.enum.bltns",
    "f = getattr_target.eval",
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
    "def modules_of(config):\n    return config.modules",
    "class Shell:\n    def kind(self):\n        return self.os",
    "from typing import Literal, NamedTuple",
    "import re\nPATTERN = re.compile('x')\nMATCH = re.match",
    "def f(self, label):\n    return getattr(self, label)",
])
def test_checker_accepts_pure_computation(source):
    assert _check(source) == []


def test_relative_imports_resolve_against_the_modules_own_package():
    nested = "grimoire.core.entities"
    assert boundary_problems("from ..identity import SourceRef", "x.py", CORE, nested) == []
    assert boundary_problems("from ...library import paths", "x.py", CORE, nested)


def test_only_the_evals_edge_modules_read_files():
    def check(source, module):
        return boundary_problems(source, module, EVALS, "grimoire.evals")
    loader, cli, checks = "grimoire/evals/loader.py", "grimoire/evals/story_quality.py", "grimoire/evals/checks.py"
    assert check("import json", loader) == []
    assert check("from importlib.resources import files", loader) == []
    assert check("import sys\nprint(sys.argv)", cli) == []
    assert check("import json", checks)
    assert check("print('x')", checks)
    assert check("import json", "grimoire/evals/sub/loader.py")  # only the named edge files
    assert check("from importlib import import_module", loader)
    assert check("import importlib.util", loader)
    assert check("x = open('f')", loader)
    assert check("from grimoire.core import SourceRef", loader)
    # A pure module cannot borrow the edge's I/O, even by a deferred import.
    assert check("def read_file(path):\n    from .loader import load_json\n    return load_json(path)", checks)
    assert check("from grimoire.evals.loader import load_json", checks)
    assert check("from . import loader", checks)
    assert check("from .loader import load_json", cli) == []


def _modules_loaded_by(statement: str) -> set[str]:
    # -S: no site hooks, so a start-up .pth file cannot preload (and hide) a module.
    env = {**os.environ, "PYTHONPATH": str(SRC)}
    script = f"import sys\n{statement}\nprint('\\n'.join(sys.modules))\n"
    done = subprocess.run([sys.executable, "-S", "-c", script], capture_output=True, text=True,
                          env=env, check=True)
    return set(done.stdout.split())


def _newly_loaded(statement: str) -> set[str]:
    return _modules_loaded_by(statement) - _modules_loaded_by("pass")


def _reached(loaded: set[str], forbidden) -> list[str]:
    return sorted(m for m in loaded if any(m == c or m.startswith(c + ".") for c in forbidden))


def _module_names(package: PurePackage, *, io: bool) -> list[str]:
    names = []
    for path in sorted(package.directory.rglob("*.py")):
        if (path.name in package.io_modules) != io:
            continue
        parts = path.relative_to(SRC).with_suffix("").parts
        names.append(".".join(parts[:-1] if parts[-1] == "__init__" else parts))
    return names


@pytest.mark.parametrize("package", PURE_PACKAGES, ids=lambda p: p.name)
def test_importing_a_pure_package_loads_no_capability_module(package):
    names = _module_names(package, io=False)
    loaded = _newly_loaded("\n".join(f"import {name}" for name in names))
    assert set(names) <= loaded
    assert _reached(loaded, CAPABILITY_MODULES) == []
    if package is EVALS:
        assert _reached(loaded, ["grimoire.core"]) == []


def test_the_evals_file_edge_loads_no_network_or_core_module():
    names = _module_names(EVALS, io=True)
    assert names
    loaded = _newly_loaded("\n".join(f"import {name}" for name in names))
    assert _reached(loaded, NETWORK_MODULES + ("grimoire.core",)) == []


def test_closure_check_sees_a_capability_module_loaded_indirectly():
    # Control: the same measurement catches a module pulled in through another import.
    loaded = _newly_loaded("import grimoire.core\nimport http.client")
    assert {"socket", "http.client"} <= set(_reached(loaded, CAPABILITY_MODULES))
