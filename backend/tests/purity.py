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
    "operator", "re", "string", "textwrap", "types", "typing", "unicodedata",
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
    "exec", "eval", "__import__", "input", "breakpoint",
})
#: Modules a pure module happens to hold (``dataclasses.inspect.os``, ``typing.sys``).
#: Reaching them from an imported name is banned; ``self.os`` or ``config.modules`` is not.
MODULE_ROUTES = frozenset({"os", "sys", "inspect", "importlib", "builtins", "bltns", "modules",
                           "__dict__"})
#: Reflection that reaches an attribute by its name.
REFLECTION = frozenset({"getattr", "setattr", "delattr", "hasattr"})


def _impure_module_attribute(module_name: str, attr: str) -> bool:
    """Whether `module_name.attr` is a module outside the pure allowlist (``enum.bltns``)."""
    import importlib
    import types
    try:
        value = getattr(importlib.import_module(module_name), attr, None)
    except ImportError:
        return False
    return isinstance(value, types.ModuleType) and value.__name__.split(".")[0] not in PURE_STDLIB

#: Modules nothing in a pure package may pull in, even transitively -- its file
#: edge included (whose ``importlib.resources`` legitimately loads ``tempfile``).
NETWORK_MODULES = ("socket", "ssl", "select", "selectors", "http.client", "urllib.request",
                   "subprocess", "multiprocessing", "asyncio", "ctypes", "sqlite3",
                   "grimoire.capture", "grimoire.ledgers", "grimoire.library", "grimoire.providers",
                   "grimoire.store", "grimoire.testing")
#: ...and what its pure modules must not pull in either.
CAPABILITY_MODULES = NETWORK_MODULES + ("threading", "tempfile", "shutil", "glob", "random", "time",
                                         "datetime", "uuid", "secrets")

#: grimoire packages that are adapters or test infrastructure, not pure.
ADAPTERS = frozenset({"grimoire.capture", "grimoire.chatstore", "grimoire.ledgers", "grimoire.library",
                      "grimoire.migrate_v1", "grimoire.providers", "grimoire.store", "grimoire.testing"})


@dataclass(frozen=True)
class PurePackage:
    name: str
    #: grimoire packages its modules may import. Widen deliberately.
    may_import: tuple[str, ...]
    #: grimoire packages it must not import even though they fall under `may_import`.
    never_import: tuple[str, ...] = ()
    #: modules (paths relative to the package) that are its file-reading edge.
    io_modules: frozenset[str] = frozenset()
    #: standard-library modules (dotted prefixes) only those edge modules may add.
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
    # World-optional chats (107): values, codec and navigation; the store-backed service is an adapter.
    PurePackage("grimoire.chats", ("grimoire.chats", "grimoire.core"), never_import=("grimoire.core.inference",)),
    PurePackage("grimoire.security", ("grimoire.security",)),
    # Voice evidence: shares the gate's text matcher so screening and scoring agree.
    PurePackage("grimoire.voice", ("grimoire.voice", "grimoire.core", "grimoire.evals.text",
                                   "grimoire.evals.checks"),
                never_import=("grimoire.core.inference",)),
    # Offline evaluation: of the domain core it uses only the inference contracts (sprint 015);
    # only its loader and CLI read files.
    PurePackage("grimoire.evals", ("grimoire.evals", "grimoire.core.inference"),
                io_modules=frozenset({"loader.py", "story_quality.py"}),
                io_stdlib=frozenset({"json", "pathlib", "importlib.resources", "sys", "argparse"}),
                io_names=frozenset({"print"})),
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


def _chain(node: ast.Attribute) -> tuple[str | None, list[str]]:
    """``a.b.c`` as ("a", ["b", "c"]); the root is None unless it is a plain name."""
    attrs = []
    while isinstance(node, ast.Attribute):
        attrs.append(node.attr)
        node = node.value
    return (node.id if isinstance(node, ast.Name) else None), attrs[::-1]


def _reaches_impure_module(module_name: str, attrs: list[str]) -> bool:
    """Whether ``module_name.attrs...`` passes through a module outside the allowlist."""
    import importlib
    import types
    try:
        value = importlib.import_module(module_name)
    except ImportError:
        return False
    for attr in attrs:
        value = getattr(value, attr, None)
        if value is None:
            return False
        if isinstance(value, types.ModuleType) and value.__name__.split(".")[0] not in PURE_STDLIB:
            return True
    return False


def _root_name(node: ast.expr) -> str | None:
    while isinstance(node, (ast.Attribute, ast.Call, ast.Subscript)):
        node = node.func if isinstance(node, ast.Call) else node.value
    return node.id if isinstance(node, ast.Name) else None


def _imported_aliases(tree: ast.Module) -> dict[str, str | None]:
    """Each name an import binds, with the stdlib module it is (None if not a plain module)."""
    names: dict[str, str | None] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                bound = alias.asname or alias.name.split(".")[0]
                names[bound] = alias.name if alias.asname else alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            names |= {alias.asname or alias.name: None for alias in node.names}
    return {name: (module if module and module.split(".")[0] in sys.stdlib_module_names else None)
            for name, module in names.items()}


def boundary_problems(source: str, filename: str, package: PurePackage, within: str) -> list[str]:
    """Why `source`, a module of package `within` owned by `package`, is not pure.

    `filename` is the module's path under ``src`` (``grimoire/evals/loader.py``).
    """
    edge = package.name.replace(".", "/") + "/"
    io = any(filename.replace("\\", "/") == edge + module for module in package.io_modules)
    io_stdlib = package.io_stdlib if io else frozenset()
    banned_names = BANNED_NAMES - (package.io_names if io else frozenset())
    banned_attributes = BANNED_ATTRIBUTES
    problems = []
    tree = ast.parse(source, filename)
    imported = _imported_aliases(tree)
    modules = {name for name, module in imported.items() if module}
    # A module object may only be used to reach its own attributes: assigned or passed
    # around (`helper = dataclasses`, `getattr(dataclasses, ...)`) it hides what is reached.
    attribute_roots = {id(node.value) for node in ast.walk(tree)
                       if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)}
    io_edge = [f"{package.name}.{Path(m).with_suffix('').as_posix().replace('/', '.')}"
               for m in package.io_modules] if not io else []
    for node in ast.walk(tree):
        where = f"{filename}:{getattr(node, 'lineno', '?')}"
        if isinstance(node, ast.Name) and node.id in banned_names:
            problems.append(f"{where} uses {node.id}")
        elif (isinstance(node, ast.Name) and node.id in modules and isinstance(node.ctx, ast.Load)
              and id(node) not in attribute_roots):
            problems.append(f"{where} uses module {node.id} as a value")
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in REFLECTION
              and len(node.args) > 1 and isinstance(node.args[1], ast.Constant)
              and node.args[1].value in MODULE_ROUTES | BANNED_ATTRIBUTES):
            problems.append(f"{where} reaches .{node.args[1].value} by name")
        elif isinstance(node, ast.Attribute) and (
                node.attr in banned_attributes
                or (node.attr in MODULE_ROUTES and _root_name(node.value) in imported)
                or ((root := _chain(node))[0] in modules
                    and _reaches_impure_module(imported[root[0]], root[1]))):
            problems.append(f"{where} uses .{node.attr}")
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.ImportFrom) and not node.level and (node.module or "").split(".")[0] != "grimoire":
                # `from typing import sys`: a module held by an allowed one is still that module.
                # So is one held under another name (`from enum import bltns`).
                problems += [f"{where} imports {alias.name} through {node.module}" for alias in node.names
                             if (alias.name in sys.stdlib_module_names and alias.name not in PURE_STDLIB
                                 and not any(_under(alias.name, m) for m in io_stdlib))
                             or _impure_module_attribute(node.module, alias.name)]
            for name in _imported_names(node, within):
                if any(_under(name, edge) for edge in io_edge):
                    problems.append(f"{where} imports the file edge {name}")
                    continue
                top = name.split(".")[0]
                if top == "grimoire":
                    if name == "grimoire":
                        continue  # the namespace itself; its members are checked separately
                    if (not any(_under(name, p) for p in package.may_import)
                            or any(_under(name, p) for p in package.never_import)):
                        problems.append(f"{where} imports {name}")
                elif top not in PURE_STDLIB and not any(_under(name, m) for m in io_stdlib):
                    problems.append(f"{where} imports {name}")
    return problems
