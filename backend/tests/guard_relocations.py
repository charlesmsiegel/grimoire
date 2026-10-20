"""Recover one legacy module's local-call graph after a package extraction.

This is an analysis adapter, never a runtime import shim. Exact registered
owners recover former local calls; per-file import names stay distinct, and
unresolved or shadowed internal module bindings fail closed. The lock guard
still owns all mutation, campaign-binding and serialization rules.

The frozen fixture describes old symbols and holes, not a permission for a
package prefix. A new sibling stays in the ordinary survey; importing it from
a registered leaf requires an explicit ownership-map review first.
"""

from __future__ import annotations

import ast
import symtable
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


class RelocationError(AssertionError):
    """An ownership proof is missing or ambiguous; do not discard the edge."""


@dataclass
class JoinedModule:
    tree: ast.Module
    origins: dict[int, tuple[str, Path]]
    paths: set[Path]
    decorators: frozenset[str]


def _target(node: ast.ImportFrom, package: str) -> str:
    if not node.level:
        return (node.module or "").removeprefix("grimoire.")
    parts = package.split(".")
    base = parts[:len(parts) - node.level + 1]
    return ".".join([*base, *([node.module] if node.module else [])])


def _definitions(tree: ast.Module) -> set[str]:
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names.update(n.id for target in targets for n in ast.walk(target)
                         if isinstance(n, ast.Name))
    return names


def _no_nested_internal_imports(tree, logical, package):
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)) and node not in tree.body:
            for alias in node.names:
                target = (f"{_target(node, package)}.{alias.name}"
                          if isinstance(node, ast.ImportFrom)
                          else alias.name.removeprefix("grimoire."))
                if target == logical or target.startswith(logical + "."):
                    raise RelocationError(f"{package}: nested internal import {target}")


class _Bindings(ast.NodeTransformer):
    def __init__(self, tree, source, module, package, logical, prefix, registered, definitions,
                 shared_aliases):
        self.module = module
        self.package = package
        self.prefix = prefix
        self.registered = registered
        self.definitions = definitions
        self.shared_aliases = shared_aliases
        self.comprehension_locals = []
        self.table = symtable.symtable(source, module, "exec")
        self.children = Counter()
        self.modules = {}
        self.direct = {}
        self.external = {}
        _no_nested_internal_imports(tree, logical, package)
        imports = Counter()
        for node in tree.body:
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            for alias in node.names:
                bound = alias.asname or alias.name.split(".")[0]
                imports[bound] += 1
                if isinstance(node, ast.ImportFrom):
                    target = _target(node, package)
                    written = f"{target}.{alias.name}"
                    if target in registered and alias.name in definitions[target]:
                        self.direct[bound] = alias.name
                        continue
                else:
                    written = alias.name.removeprefix("grimoire.")
                if written in registered:
                    self.modules[bound] = written
                else:
                    if written.startswith(logical + "."):
                        raise RelocationError(f"{module}: unregistered sibling import {written}")
                    self.external[bound] = self._external_name(bound, written)
        for alias in self.modules.keys() | self.direct.keys():
            if imports[alias] != 1:
                raise RelocationError(f"{module}: ambiguous internal import {alias}")
            self._require_unshadowed(alias, self.table, module_scope=True)

    def _external_name(self, bound, target):
        if (bound, target) in {("locks", "store.locks"), ("store", "store")}:
            return bound
        if self.shared_aliases.get(bound) == target:
            return bound
        return f"{self.prefix}{bound}"

    def _require_unshadowed(self, name, table, *, module_scope=False):
        if name in table.get_identifiers():
            symbol = table.lookup(name)
            if symbol.is_assigned() or symbol.is_parameter() or (
                    not module_scope and symbol.is_imported()):
                raise RelocationError(f"{self.module}: rebound internal module alias {name}")
        for child in table.get_children():
            self._require_unshadowed(name, child)

    def _global(self, name):
        if any(name in names for names in self.comprehension_locals):
            return False
        try:
            return self.table.lookup(name).is_global()
        except KeyError:
            # Postponed annotations do not always have symbol-table entries.
            return True

    def visit_Name(self, node):
        if not self._global(node.id):
            return node
        if node.id in self.modules:
            raise RelocationError(f"{self.module}: internal module {node.id} escapes static resolution")
        node.id = self.direct.get(node.id, self.external.get(node.id, node.id))
        return node

    def visit_Attribute(self, node):
        if isinstance(node.value, ast.Name) and node.value.id in self.modules:
            owner = self.modules[node.value.id]
            if node.attr not in self.definitions[owner]:
                raise RelocationError(f"{self.module}: unknown internal symbol {owner}.{node.attr}")
            return ast.copy_location(ast.Name(id=node.attr, ctx=node.ctx), node)
        return self.generic_visit(node)

    def _scope(self, node, kind, body):
        matches = [t for t in self.table.get_children()
                   if t.get_name() == kind and t.get_lineno() == node.lineno]
        key = (id(self.table), kind, node.lineno)
        index = self.children[key]
        self.children[key] += 1
        if index >= len(matches):
            raise RelocationError(f"{self.module}:{node.lineno}: unresolved lexical scope {kind}")
        parent = self.table
        self.table = matches[index]
        try:
            return body()
        finally:
            self.table = parent

    def visit_FunctionDef(self, node):
        node.decorator_list = [self.visit(n) for n in node.decorator_list]
        node.args = self.visit(node.args)
        if node.returns is not None:
            node.returns = self.visit(node.returns)
        node.body = self._scope(node, node.name, lambda: [self.visit(n) for n in node.body])
        return node

    def visit_AsyncFunctionDef(self, node):
        return self.visit_FunctionDef(node)

    def visit_ClassDef(self, node):
        node.decorator_list = [self.visit(n) for n in node.decorator_list]
        node.bases = [self.visit(n) for n in node.bases]
        node.keywords = [self.visit(n) for n in node.keywords]
        node.body = self._scope(node, node.name, lambda: [self.visit(n) for n in node.body])
        return node

    def visit_Lambda(self, node):
        node.args = self.visit(node.args)
        node.body = self._scope(node, "lambda", lambda: self.visit(node.body))
        return node

    def _comprehension(self, node, name):
        # The outer iterable belongs to the containing scope; the rest belongs
        # to the implicit comprehension function on every supported Python.
        first = self.visit(node.generators[0].iter)
        def rest():
            for index, generator in enumerate(node.generators):
                generator.target = self.visit(generator.target)
                generator.iter = first if index == 0 else self.visit(generator.iter)
                generator.ifs = [self.visit(n) for n in generator.ifs]
            for field in ("elt", "key", "value"):
                if hasattr(node, field):
                    setattr(node, field, self.visit(getattr(node, field)))
            return node
        self.comprehension_locals.append({n.id for generator in node.generators
            for n in ast.walk(generator.target) if isinstance(n, ast.Name)})
        try:
            # PEP 709 inlines eager comprehensions in Python 3.12+; their
            # bindings still shadow globals even when symtable has no child.
            if any(t.get_name() == name and t.get_lineno() == node.lineno
                   for t in self.table.get_children()):
                return self._scope(node, name, rest)
            return rest()
        finally:
            self.comprehension_locals.pop()

    def visit_ListComp(self, node):
        return self._comprehension(node, "listcomp")

    def visit_SetComp(self, node):
        return self._comprehension(node, "setcomp")

    def visit_DictComp(self, node):
        return self._comprehension(node, "dictcomp")

    def visit_GeneratorExp(self, node):
        return self._comprehension(node, "genexpr")

    def visit_ImportFrom(self, node):
        target = _target(node, self.package)
        imports, aliases = [], []
        for alias in node.names:
            bound = alias.asname or alias.name
            if bound in self.direct:
                if bound != self.direct[bound]:
                    aliases.append(ast.copy_location(ast.Assign(
                        targets=[ast.Name(id=bound, ctx=ast.Store())],
                        value=ast.Name(id=self.direct[bound], ctx=ast.Load())), node))
            elif bound in self.modules:
                continue
            else:
                imports.append(ast.alias(name=alias.name, asname=self.external.get(bound, bound)))
        if imports:
            aliases.insert(0, ast.copy_location(ast.ImportFrom(
                module=target if target == "__future__" else f"grimoire.{target}"
                if target.startswith("store") else target,
                names=imports, level=0), node))
        return aliases

    def visit_Import(self, node):
        aliases = []
        for alias in node.names:
            bound = alias.asname or alias.name.split(".")[0]
            if bound in self.modules:
                continue
            renamed = self.external.get(bound, bound)
            aliases.append(ast.alias(name=alias.name,
                                     asname=alias.asname if renamed == bound else renamed))
        node.names = aliases
        return node if node.names else None


def _sources(root, logical, owners):
    """Exact owners, including the facade, without swallowing new siblings."""
    folder = root.joinpath(*logical.split("."))
    registered = {logical, *(f"{logical}.{owner}" for owner in owners.values())}
    sources = {}
    for module in sorted(registered):
        path = folder / "__init__.py" if module == logical else root.joinpath(
            *module.split(".")).with_suffix(".py")
        if not path.is_file():
            raise RelocationError(f"{module}: missing registered relocation source")
        source = path.read_text(encoding="utf-8")
        sources[module] = (path, source, ast.parse(source))
    definitions = {module: _definitions(tree) for module, (_, _, tree) in sources.items()}
    definitions[logical] = set(owners)
    for name, owner in owners.items():
        if name not in definitions[f"{logical}.{owner}"]:
            raise RelocationError(f"{logical}.{owner}: missing original symbol {name}")
    actual = {}
    for module, names in definitions.items():
        if module == logical:
            continue
        for name in names:
            if name in actual and actual[name] != module:
                raise RelocationError(f"{logical}: duplicate symbol {name}")
            actual[name] = module
    return sources, definitions, registered, actual


def _shared_aliases(sources, logical, actual):
    """Retain original protocol spellings only for the same imported object.

    For example, the existing analyzer recognizes contextmanager and locks.
    Prefixing every import would erase that proof; unioning conflicting
    bindings would grant a foreign locks object the real module's authority.
    """
    targets = {}
    for module, (path, _, tree) in sources.items():
        package = logical if path.name == "__init__.py" else module.rpartition(".")[0]
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    bound = alias.asname or alias.name.split(".")[0]
                    target = (f"{_target(node, package)}.{alias.name}" if isinstance(node, ast.ImportFrom)
                              else alias.name.removeprefix("grimoire."))
                    targets.setdefault(bound, set()).add(target)
    return {name: next(iter(values)) for name, values in targets.items()
            if len(values) == 1 and name not in actual}


def join(root: Path, logical: str, owners: dict[str, str],
         sibling_decorators: Callable) -> JoinedModule:
    """Join exact registered leaves, preserving source nodes and import scope."""
    sources, definitions, registered, actual = _sources(root, logical, owners)
    shared_aliases = _shared_aliases(sources, logical, actual)
    body, origins, decorators = [], {}, set()
    for index, (module, (path, source, tree)) in enumerate(sources.items()):
        package = logical if path.name == "__init__.py" else module.rpartition(".")[0]
        # Read sibling decorators before import normalization changes the paths.
        external_decorators = sibling_decorators(tree, path, package)
        transformer = _Bindings(tree, source, module, package, logical,
                                f"__relocated_{index}_", registered, definitions, shared_aliases)
        for target in transformer.external:
            written = transformer.external[target]
            decorators.update(f"{written}.{name.split('.', 1)[1]}"
                              for name in external_decorators if name.startswith(target + "."))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                origins[id(node)] = (source, path)
        transformed = transformer.visit(tree)
        body.extend(transformed.body)
    return JoinedModule(ast.Module(body=body, type_ignores=[]), origins,
                        {path for path, _, _ in sources.values()}, frozenset(decorators))
