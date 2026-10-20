"""Who may reach an overview projection (spec 04 section 3.8, 12.4).

Only the four overview read routes -- `GET /worlds`, `GET /campaigns`,
`GET /api/shell`, `GET /todo` -- the post-turn warm, and spec 05's sync
(`store.cache_sync`'s `overview` hook) read `store.overview`.
A decision site never does: `scenes.read.closed_by` is asked on every turn's
reservation and refuses a closed branch, and spec 03 section 5's residuals must
never decide that for the life of a persisted row; `list_campaigns` feeds a
write; the items expansion and the ignore route are on demand and live.

A syntax guard, in the style of `test_compiled_guard.py`: it resolves
imports, module-level helpers, facade re-exports and same-subsystem methods, and
follows them from each decision site. A dynamic call (`getattr`, a callable
handed in) is outside what it proves, and stated so.
"""

from __future__ import annotations

import ast
import functools
from pathlib import Path

import pytest

import grimoire

#: Modules allowed to import `store.overview` at all. `store.cache_sync` is
#: spec 05's: its `overview` hook calls `overview.warm.warm_paths` (05-S5),
#: which stores what a read would and decides nothing.
_IMPORTERS = {"routes.worlds", "routes.campaigns", "routes.shell", "routes.todo",
              "routes.streaming", "routes.streaming._followups", "store.cache_sync",
              "routes.campaigns._settings", "routes.worlds._record", "routes.todo._context",
              "store.cache_sync._other_hooks"}
# The campaign-list reader moved to _settings. Its old facade retains the
# imported name for compatibility; no other campaign leaf may use a projection.

#: Functions that must never reach an overview projection.
_DECISION_SITES = (
    "store.scenes.read.closed_by",
    "store.scenes.read.list_scenes",
    "store.scenes.read.read_scene",
    "store.campaigns.read.list_campaigns",
    "store.campaigns.read.read_campaign",
    "routes.todo.get_todo_items",
    "routes.todo.put_todo_ignored",
)
#: Whole modules that must never reach one (the stores the projections stand
#: beside, which decisions and writes read).
_DECISION_MODULES = ("store.continuity.effective", "store.continuity.pending")

# The old todo module's conservative method-name fallback must cover the same
# class bodies after their move. This is a call-graph boundary, not permission
# for these leaves to import overview: _IMPORTERS remains exact above.
_METHOD_SUBSYSTEMS = ("routes.todo",)


@functools.cache
def _real_sources() -> tuple[tuple[str, str], ...]:
    root = Path(grimoire.__file__).parent
    out = {}
    for p in root.rglob("*.py"):
        parts = p.relative_to(root).with_suffix("").parts
        if parts[-1] == "__init__":
            parts = parts[:-1]
        out[".".join(parts)] = p.read_text(encoding="utf-8")
    return tuple(sorted(out.items()))


def _sources() -> dict[str, str]:
    return dict(_real_sources())


def _aliases(tree: ast.Module, module: str, is_package: bool) -> dict[str, str]:
    out: dict[str, str] = {}
    package = module if is_package else module.rpartition(".")[0]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                name = a.name.removeprefix("grimoire.")
                out[a.asname or name.split(".")[0]] = name if a.asname else name.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".") if package else []
                base = base[:len(base) - (node.level - 1)] if node.level > 1 else base
                prefix = ".".join(base + ([node.module] if node.module else []))
            else:
                prefix = (node.module or "").removeprefix("grimoire")
                prefix = prefix.removeprefix(".")
            for a in node.names:
                out[a.asname or a.name] = f"{prefix}.{a.name}" if prefix else a.name
    return out


class _Graph:
    def __init__(self, sources: dict[str, str]):
        self.funcs: dict[str, ast.AST] = {}
        self.module_of: dict[str, str] = {}
        self.aliases: dict[str, dict[str, str]] = {}
        self.methods: dict[str, dict[str, list[str]]] = {}
        self.defined: dict[str, set[str]] = {}
        self.classes: set[str] = set()
        packages = {m for m in sources if any(o.startswith(m + ".") for o in sources)}
        for module, src in sources.items():
            tree = ast.parse(src)
            self.aliases[module] = _aliases(tree, module, module in packages)
            self.defined[module] = set()
            self.methods[module] = {}
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    self._add(f"{module}.{node.name}", node, module)
                elif isinstance(node, ast.ClassDef):
                    self.classes.add(f"{module}.{node.name}")
                    self.defined[module].add(node.name)
                    for item in node.body:
                        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            name = f"{module}.{node.name}.{item.name}"
                            self._add(name, item, module)
                            self.methods[module].setdefault(item.name, []).append(name)
        self._share_subsystem_methods()

    def _share_subsystem_methods(self) -> None:
        """Keep the original conservative method boundary after extraction."""
        for subsystem in _METHOD_SUBSYSTEMS:
            members = [m for m in self.methods
                       if m == subsystem or m.startswith(subsystem + ".")]
            combined: dict[str, list[str]] = {}
            for member in members:
                for method, targets in self.methods[member].items():
                    combined.setdefault(method, []).extend(targets)
            for member in members:
                self.methods[member] = combined

    def _add(self, name: str, node: ast.AST, module: str) -> None:
        self.funcs[name] = node
        self.module_of[name] = module
        self.defined[module].add(name.split(".")[-1] if name.count(".") == module.count(".") + 1 else "")

    def _resolve(self, target: str, depth: int = 0) -> str:
        """Follow a facade re-export (`store.scenes.list_scenes` names
        `store.scenes.read.list_scenes`) to where the name is defined."""
        if depth > 8 or target in self.funcs or target in self.classes:
            return target
        package, _, name = target.rpartition(".")
        alias = self.aliases.get(package, {}).get(name)
        if alias and alias != target:
            return self._resolve(alias, depth + 1)
        return target

    def _chain(self, node: ast.expr, module: str) -> str | None:
        if isinstance(node, ast.Name):
            if f"{module}.{node.id}" in self.funcs or node.id in self.defined[module]:
                return f"{module}.{node.id}"
            alias = self.aliases[module].get(node.id)
            if alias is not None:
                return alias
            return None
        if isinstance(node, ast.Attribute):
            base = self._chain(node.value, module)
            return f"{base}.{node.attr}" if base else None
        if isinstance(node, ast.Call):
            target = self._chain(node.func, module)
            if target and self._resolve(target) in self.classes:
                return self._resolve(target)
        return None

    def references(self, name: str) -> set[str]:
        node, module = self.funcs[name], self.module_of[name]
        out: set[str] = set()
        for sub in ast.walk(node):
            if isinstance(sub, (ast.Name, ast.Attribute)):
                chain = self._chain(sub, module)
                if chain:
                    resolved = self._resolve(chain)
                    out.add(resolved)
                    out.add(self._resolve(resolved + ".__init__"))
                elif isinstance(sub, ast.Attribute):
                    # A method on a receiver this guard cannot type: every
                    # same-module method of that name, conservatively. The
                    # former todo module keeps that boundary across its leaves.
                    out.update(self.methods[module].get(sub.attr, ()))
        return out

    def reaches_overview(self, start: str) -> list[str]:
        seen, stack, path_to = {start}, [start], {start: [start]}
        while stack:
            name = stack.pop()
            for ref in self.references(name):
                if ref == "store.overview" or ref.startswith("store.overview."):
                    return [*path_to[name], ref]
                if ref in self.funcs and ref not in seen:
                    seen.add(ref)
                    path_to[ref] = [*path_to[name], ref]
                    stack.append(ref)
        return []


def _violations(sources: dict[str, str]) -> list[str]:
    graph = _Graph(sources)
    failures = []
    for module, src in sources.items():
        if module.startswith("store.overview"):
            continue
        failures.extend(f"{module} imports {ref}"
                        for ref in _aliases(ast.parse(src), module, False).values()
                        if (ref == "store.overview" or ref.startswith("store.overview."))
                        and module not in _IMPORTERS)
    sites = [graph._resolve(s) for s in _DECISION_SITES if graph._resolve(s) in graph.funcs]
    sites += [f for f in graph.funcs if graph.module_of[f] in _DECISION_MODULES]
    for site in sites:
        path = graph.reaches_overview(site)
        if path:
            failures.append(" -> ".join(path))
    return failures


def test_no_decision_site_reaches_an_overview_projection():
    assert _violations(_sources()) == []


@functools.cache
def _real_graph() -> _Graph:
    return _Graph(_sources())


def test_every_decision_site_named_here_still_exists():
    graph = _real_graph()
    assert [s for s in _DECISION_SITES if graph._resolve(s) not in graph.funcs] == []


_FIXTURE_BASE = {
    "store.overview": "from .read import scene_summary\n",
    "store.overview.read": "def scene_summary(cid): return {}\n",
}


def test_guard_fails_closed_by_calling_the_scene_summary():
    sources = {**_FIXTURE_BASE, "store.scenes.read": (
        "from .. import overview\n"
        "def closed_by(cid, sid):\n    return overview.scene_summary(cid)\n"
        "def list_scenes(cid): return []\ndef read_scene(cid, sid): return {}\n")}
    assert _violations(sources)


def test_guard_fails_the_items_route_reaching_a_projection_through_a_helper():
    sources = {**_FIXTURE_BASE, "routes.todo": (
        "from ..store import overview\n"
        "class _Ctx:\n"
        "    def summary(self):\n        return overview.scene_summary('x')\n"
        "def _items_open(cid):\n    return _Ctx().summary()\n"
        "def get_todo_items(chore_id, campaign=''):\n    return _items_open(campaign)\n"
        "def put_todo_ignored(chore_id, body): return {}\n")}
    assert any("get_todo_items" in v for v in _violations(sources))


def test_guard_fails_an_unlisted_importer():
    sources = {**_FIXTURE_BASE, "routes.scenes": "from ..store import overview\n"}
    assert _violations(sources) == ["routes.scenes imports store.overview"]


def test_campaign_package_does_not_grant_its_siblings_overview_access():
    sources = {**_FIXTURE_BASE, "routes.campaigns._actors":
               "from ...store import overview\n"}
    assert _violations(sources) == ["routes.campaigns._actors imports store.overview"]


@pytest.mark.parametrize("body", ["return _context.Ctx().summary()",
                                 "ctx = _context.Ctx()\n    return ctx.summary()",
                                 "ctx: object = _context.Ctx()\n    return ctx.summary()",
                                 "if (ctx := _context.Ctx()):\n        return ctx.summary()",
                                 "ctx = _context.make()\n    return ctx.summary()"])
def test_decision_route_is_followed_through_its_facade_and_context_owner(body):
    sources = {**_FIXTURE_BASE,
               "routes.todo": "from ._report import get_todo_items\n",
               "routes.todo._report": ("from . import _context\n"
                                       f"def get_todo_items():\n    {body}\n"),
               "routes.todo._context": ("from ...store import overview\n"
                                        "class Ctx:\n    def summary(self):\n"
                                        "        return overview.scene_summary('x')\n"
                                        "def make(): return Ctx()\n")}
    assert any("get_todo_items" in problem for problem in _violations(sources))


def test_todo_reader_permission_does_not_extend_to_other_package_leaves():
    sources = {**_FIXTURE_BASE, "routes.todo._unexpected": "from ...store import overview\n"}
    assert _violations(sources) == ["routes.todo._unexpected imports store.overview"]


def test_decision_context_class_shadowing_an_import_is_followed():
    sources = {**_FIXTURE_BASE,
               "routes.todo": ("from ._context import Ctx as Ctx\n"
                               "from ._report import get_todo_items as get_todo_items\n"
                               "from grimoire.store import overview\n"
                               "class Ctx:\n def summary(self):\n"
                               "  return overview.scene_summary('x')\n"),
               "routes.todo._context": "class Ctx:\n def summary(self):\n  return {}\n",
               "routes.todo._report": ("from . import Ctx\ndef get_todo_items():\n"
                                       " return Ctx().summary()\n")}
    assert any("get_todo_items" in problem for problem in _violations(sources))


@pytest.mark.parametrize("site", ["routes.todo.get_todo", "routes.shell.get_shell",
                                  "routes.campaigns.get_campaigns"])
def test_the_read_routes_do_reach_it(site):
    """The guard can see the real paths, so its silence on the decision sites
    is a finding rather than blindness."""
    graph = _real_graph()
    assert graph.reaches_overview(graph._resolve(site))
