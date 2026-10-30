"""Which grimoire packages are pure, and what "pure" allows.

A pure package computes. It does not touch files, the network, processes,
threads, the environment, clocks or randomness, it does not load code
dynamically, and it depends on no provider, adapter, test harness or
third-party package. Pure standard-library computation (``re``,
``unicodedata``, ``hashlib`` ...) is fine.

The rule is an allowlist on purpose. Audit hooks cannot see ``stat``, clocks,
randomness or environment reads, so a module that is not listed here is not
trusted to be pure. Widening the list is a reviewed one-line change:
add a module only when its purpose is computation.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
GRIMOIRE = SRC / "grimoire"

#: Standard-library modules whose purpose is computation.
PURE_STDLIB = frozenset({
    "__future__", "abc", "bisect", "collections", "copy", "dataclasses", "decimal", "difflib",
    "enum", "fractions", "functools", "hashlib", "heapq", "itertools", "math", "numbers",
    "operator", "re", "statistics", "string", "textwrap", "types", "typing", "unicodedata",
})

#: Builtins that read input, write output, run code or reach interpreter state.
BANNED_NAMES = frozenset({
    "open", "exec", "eval", "compile", "__import__", "input", "print", "breakpoint",
    "globals", "locals", "vars", "__builtins__", "builtins", "__loader__", "__spec__",
})

#: Attributes that open files, import modules or escape to interpreter internals.
#: ``compile`` is deliberately absent: ``re.compile`` is pure.
BANNED_ATTRIBUTES = frozenset({
    "open", "import_module", "__globals__", "__builtins__", "__subclasses__", "__loader__",
})

#: Modules a pure import must never pull in, even transitively.
CAPABILITY_MODULES = (
    "socket", "ssl", "select", "selectors", "subprocess", "threading", "http.client",
    "urllib.request", "sqlite3", "ctypes", "multiprocessing", "asyncio", "tempfile", "shutil",
    "glob", "grimoire.library", "grimoire.testing",
)

#: grimoire packages that are adapters or test infrastructure, not pure.
ADAPTERS = frozenset({"grimoire.library", "grimoire.testing"})


@dataclass(frozen=True)
class PurePackage:
    name: str
    #: grimoire packages its modules may import. Widen deliberately.
    may_import: tuple[str, ...]
    #: grimoire packages it must not import even though they fall under `may_import`.
    never_import: tuple[str, ...] = ()
    #: modules (relative to the package) that are its file-reading edge.
    io_modules: frozenset[str] = frozenset()
    #: standard-library modules only those edge modules may add.
    io_stdlib: frozenset[str] = frozenset()
    #: builtins only those edge modules may use (a CLI prints; a loader opens).
    io_names: frozenset[str] = frozenset()

    @property
    def directory(self) -> Path:
        return SRC.joinpath(*self.name.split("."))


PURE_PACKAGES = (
    # The fiction domain. `core.inference` is a separate vocabulary that core does not import.
    PurePackage("grimoire.core", ("grimoire.core",), never_import=("grimoire.core.inference",)),
    PurePackage("grimoire.core.inference", ("grimoire.core.inference",)),
    PurePackage("grimoire.intent", ("grimoire.intent",)),
    PurePackage("grimoire.security", ("grimoire.security",)),
    # Offline evaluation: independent of the domain core; only its loader and CLI read files.
    PurePackage("grimoire.evals", ("grimoire.evals",),
                io_modules=frozenset({"loader.py", "story_quality.py"}),
                io_stdlib=frozenset({"json", "pathlib", "importlib", "sys", "argparse"}),
                io_names=frozenset({"print", "open"})),
)


def _under(name: str, package: str) -> bool:
    return name == package or name.startswith(package + ".")


def owning_package(path: Path) -> PurePackage | None:
    """The most specific pure package that `path` belongs to."""
    owners = [p for p in PURE_PACKAGES if path.resolve().is_relative_to(p.directory)]
    return max(owners, key=lambda p: len(p.directory.parts), default=None)


def pure_sources() -> list[tuple[Path, PurePackage]]:
    sources = {path for package in PURE_PACKAGES for path in package.directory.rglob("*.py")}
    return [(path, owning_package(path)) for path in sorted(sources)]


def module_package(path: Path) -> str:
    """The package a module's relative imports resolve against."""
    return ".".join(path.resolve().parent.relative_to(SRC).parts)


def _imported_names(node: ast.Import | ast.ImportFrom, package: str) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if node.level:
        parts = package.split(".")
        if node.level > len(parts):
            return ["<relative import beyond the top-level package>"]
        base = ".".join(parts[:len(parts) - node.level + 1])
        module = f"{base}.{node.module}" if node.module else base
    else:
        module = node.module or ""
    # `from x import y` may bind a submodule x.y, so both names must be allowed.
    return [module, *(f"{module}.{alias.name}" for alias in node.names if alias.name != "*")]


def boundary_problems(source: str, filename: str, package: PurePackage, within: str) -> list[str]:
    """Why `source`, a module of package `within` owned by `package`, is not pure."""
    io = Path(filename).name in package.io_modules
    allowed_stdlib = PURE_STDLIB | (package.io_stdlib if io else frozenset())
    banned_names = BANNED_NAMES - (package.io_names if io else frozenset())
    problems = []
    for node in ast.walk(ast.parse(source, filename)):
        where = f"{filename}:{getattr(node, 'lineno', '?')}"
        if isinstance(node, ast.Name) and node.id in banned_names:
            problems.append(f"{where} uses {node.id}")
        elif isinstance(node, ast.Attribute) and node.attr in BANNED_ATTRIBUTES:
            problems.append(f"{where} uses .{node.attr}")
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for name in _imported_names(node, within):
                top = name.split(".")[0]
                if top == "grimoire":
                    if name == "grimoire":
                        continue  # the namespace itself; its members are checked separately
                    if (not any(_under(name, p) for p in package.may_import)
                            or any(_under(name, p) for p in package.never_import)):
                        problems.append(f"{where} imports {name}")
                elif top not in allowed_stdlib:
                    problems.append(f"{where} imports {name}")
    return problems
