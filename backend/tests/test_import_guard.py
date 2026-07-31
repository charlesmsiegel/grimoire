"""Every import sits at module scope, and the module graph is acyclic.

Three rules, one scan:

1. No import inside a function body. Deferred imports were how this package
   held a 15-module cycle together; each one moves an ImportError from load
   time to call time and hides the coupling from every static reader.
2. No cycle in the module-level import graph.
3. Inside ``store/``, a cross-package import binds a *module*, never a name.
   ``from ..campaigns import read`` is fine; ``from ..campaigns import
   world_refs`` reads a name that exists only once ``campaigns/__init__.py``
   has run, and ``from ..campaigns.read import world_refs`` binds the function
   by value so the caller caches it. The first fails at import time; the
   second silently defeats every test that patches it. Rule 2 catches
   neither -- the file graph stays perfectly acyclic either way.

This guard shipped with a ratchet baseline while the rest of the package was
brought into line; that baseline is gone now that the violation count is
zero, so the rule is absolute: any violation fails, with no grandfathered
exceptions. The only way around a hit is `# import-ok: <reason>` on the
import itself, and only when the reason is actually true.
"""

from __future__ import annotations

import ast
import pathlib

import grimoire

from . import guard_markers

PACKAGE = pathlib.Path(grimoire.__file__).parent

MARKER = "import-ok:"


def _modname(path: pathlib.Path) -> str:
    rel = path.relative_to(PACKAGE.parent).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _sources() -> dict[str, tuple[pathlib.Path, str, ast.Module]]:
    out = {}
    for p in sorted(PACKAGE.rglob("*.py")):
        src = p.read_text(encoding="utf-8")
        out[_modname(p)] = (p, src, ast.parse(src))
    return out


SOURCES = _sources()
MODULES = set(SOURCES)
PACKAGES = {m for m, (p, _, _) in SOURCES.items() if p.name == "__init__.py"}


def _kind_of(mod: str, name: str) -> str:
    """"function", "class" or "other" for a top-level name in `mod`."""
    entry = SOURCES.get(mod)
    if entry is None:
        return "other"
    for node in entry[2].body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return "function"
        if isinstance(node, ast.ClassDef) and node.name == name:
            return "class"
    return "other"


def _resolve(node, mod: str, is_pkg: bool) -> set[str]:
    """Absolute grimoire module names this import depends on."""
    pkg = mod if is_pkg else mod.rsplit(".", 1)[0]
    out: set[str] = set()
    if isinstance(node, ast.Import):
        for a in node.names:
            if a.name.startswith("grimoire"):
                out.add(a.name)
        return out
    if node.level:
        base = pkg.split(".")
        if node.level > 1:
            base = base[: len(base) - (node.level - 1)]
        target = ".".join(base) + ("." + node.module if node.module else "")
    else:
        target = node.module or ""
    if not target.startswith("grimoire"):
        return out
    for a in node.names:
        cand = f"{target}.{a.name}"
        out.add(cand if cand in MODULES else target)
    return out


class _Walker(ast.NodeVisitor):
    """Collects module-scope edges and in-function imports separately."""

    def __init__(self, mod: str, is_pkg: bool):
        self.mod, self.is_pkg = mod, is_pkg
        self.stack: list[str] = []
        self.edges: set[str] = set()
        self.deferred: list[tuple[str, str, ast.stmt]] = []
        self.form: list[tuple[str, ast.stmt]] = []

    def visit_FunctionDef(self, n):
        self.stack.append(n.name)
        self.generic_visit(n)
        self.stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, n):
        # A class body is not module scope either: `class C: import json` ran
        # with an empty stack and counted as a valid module-level edge.
        self.stack.append(n.name)
        self.generic_visit(n)
        self.stack.pop()

    def visit_Import(self, n):
        self._record(n, [a.name for a in n.names])

    def visit_ImportFrom(self, n):
        self._record(n, [n.module or ""])
        self._check_form(n)

    def _record(self, n, raw_names):
        targets = _resolve(n, self.mod, self.is_pkg)
        if self.stack:
            where = ".".join(self.stack)
            for name in sorted(targets) or sorted(raw_names):
                self.deferred.append((where, name, n))
            return
        self.edges |= targets

    def _check_form(self, n: ast.ImportFrom):
        """Rule 3. ``__init__.py`` files are exempt: re-exporting is their job,
        and they import their own submodules before anything reads through
        them."""
        if not self.mod.startswith("grimoire.store") or self.is_pkg:
            return
        pkg = self.mod.rsplit(".", 1)[0]
        if n.level:
            base = pkg.split(".")
            if n.level > 1:
                base = base[: len(base) - (n.level - 1)]
            target = ".".join(base) + ("." + n.module if n.module else "")
        else:
            # `from grimoire.store.campaigns import world_refs` does the same
            # package-attribute lookup as the relative form and fails the same
            # way; spelling it absolutely must not buy an exemption.
            target = n.module or ""
            if not target.startswith("grimoire.store"):
                return
        if target == pkg:
            return
        if target in PACKAGES:
            # `from ..campaigns import world_refs` -- a name off a package
            # whose __init__ may still be running.
            for a in n.names:
                if f"{target}.{a.name}" not in MODULES:
                    self.form.append((f"{target}.{a.name}", n))
            return
        # `from ..modules.binding import resolve` -- a name off a *leaf module*
        # in another split package. No init-order hazard, but it binds by
        # value, so the caller caches the function and a test patching
        # `modules.binding.resolve` stops intercepting. Only functions carry
        # that hazard: a class or exception bound by value is fine.
        parent = target.rsplit(".", 1)[0]
        if parent != pkg and parent.startswith("grimoire.store.") and parent in PACKAGES:
            for a in n.names:
                # `import *` binds every exported function by value, and
                # _kind_of("*") is "other" -- so it slipped straight through
                # the function test this rule turns on.
                if a.name == "*":
                    self.form.append((f"{target}.*", n))
                elif (f"{target}.{a.name}" not in MODULES
                        and _kind_of(target, a.name) == "function"):
                    self.form.append((f"{target}.{a.name}", n))


def _collect():
    deferred, form, graph = [], [], {}
    for mod, (path, src, tree) in SOURCES.items():
        w = _Walker(mod, path.name == "__init__.py")
        w.visit(tree)
        graph[mod] = w.edges
        flagged = [n for _, _, n in w.deferred] + [n for _, n in w.form]

        def others(node):
            """Every *other* flagged node. The identity exclusion is load-bearing:
            passing the node itself makes `_shares_first_line(node, node)` true,
            so `_sole_owner` rejects the node's own inline marker and no
            `# import-ok:` exemption can ever be honoured."""
            return [o for o in flagged if o is not node]

        for where, name, node in w.deferred:
            if guard_markers.marker_reason(MARKER, src, node, others(node)):
                continue
            deferred.append(f"deferred {mod}::{where}::{name}")
        for target, node in w.form:
            if guard_markers.marker_reason(MARKER, src, node, others(node)):
                continue
            form.append(f"form {mod}::{target}")
    return deferred, form, graph


def _cycles(graph: dict[str, set[str]]) -> list[str]:
    """Tarjan. Reports every strongly connected component above size one."""
    index, low, onstk, stack, out = {}, {}, {}, [], []
    counter = [0]

    def strong(v):
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        onstk[v] = True
        for w in sorted(graph.get(v, ())):
            if w not in index:
                strong(w)
                low[v] = min(low[v], low[w])
            elif onstk.get(w):
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp = []
            while True:
                w = stack.pop()
                onstk[w] = False
                comp.append(w)
                if w == v:
                    break
            # A self-import (`read.py` doing `from . import read`) forms a
            # one-node component and exposes the same partially-initialized
            # module a larger cycle does, so size alone is not the test.
            if len(comp) > 1 or comp[0] in graph.get(comp[0], ()):
                out.append("cycle " + ",".join(sorted(comp)))

    for n in sorted(set(graph) | {t for v in graph.values() for t in v}):
        if n not in index:
            strong(n)
    return out


def _violations() -> list[str]:
    deferred, form, graph = _collect()
    return sorted(deferred + form + _cycles(graph))


def test_no_import_violations():
    """Every import is at module scope and the module graph is acyclic."""
    found = _violations()
    assert not found, (
        "import-graph violations:\n  " + "\n  ".join(found)
        + f"\n\nFix them, or -- only with a stated reason -- add a "
          f"`# {MARKER} <reason>` comment on the import.")


def test_no_submodule_is_shadowed_by_a_facade_export():
    """A package attribute that should be a module still is one.

    `sheets.read`, `module_edit.rename` and `absorb.materialize` are public
    *functions*, so a same-named submodule is overwritten the moment the
    package re-exports the function. A later `from ..sheets import read` then
    binds the function, and `read.list_refs(...)` raises AttributeError at
    call time -- having passed both import and the cycle check above. This
    catches the collision at its source instead.
    """
    import importlib
    import types

    shadowed = []
    for mod in sorted(PACKAGES):
        pkg = importlib.import_module(mod)
        for path in (pathlib.Path(pkg.__file__).parent).glob("*.py"):
            if path.name == "__init__.py":
                continue
            bound = getattr(pkg, path.stem, None)
            if bound is not None and not isinstance(bound, types.ModuleType):
                shadowed.append(f"{mod}.{path.stem} is {type(bound).__name__}, not a module")
    assert not shadowed, (
        "submodules shadowed by a facade export -- rename the file:\n  "
        + "\n  ".join(shadowed))
