"""Guard: every inference operation names a task it is registered for (spec 14.1).

An operation call is reached through what a module BINDS, never through what
it spells. `embed` is a common name -- the embeddings client's own method is
`.embed`, and so is the async form of the operation -- so a guard matching the
word would confuse the two, and one matching a fixed receiver would miss the
next alias. `bindings` resolves each module's imports (relative or absolute,
through `test_import_guard._target`, the half of `_resolve` that reads an
import's base) against a target module; each operation's half passes its own.

The embed half (slice D). An **operation call** is either
- a call whose callee is named `embed_sync` -- a `Name`, or an `Attribute` on
  any receiver -- or a name bound to `embed_sync`/`embed` by importing it from
  `store/inference/embed.py`; or
- `<expr>.embed(...)` where `<expr>` is a binding of that module (whatever its
  `as` name) or an attribute chain whose dotted tail is `inference.embed`
  (`store.inference.embed.embed(...)`).

Every other `X.embed(...)` is a **client call**: a request to the embeddings
endpoint (`embeddings.EmbeddingsClient.embed`). The rules:

- every operation call passes a first positional string literal in
  `routing.EMBED_TASKS` (`store/inference/embed.py` is exempt: its async
  `embed` forwards `task`), and every embed task is the literal of some call;
- the operation is never handed around as a value (an `embed_sync` passed to
  `asyncio.to_thread` would hide its literal);
- only the operation (`store/inference/embed.py`) and the model test's probe
  (`routes/config.py`, ruling 8) call the client, and nothing hands the
  client's `.embed` around as a value. `embeddings.py`, the client's own
  module, is outside both rules.

That the client calls it does allow are metered is `test_usage_guard.py`'s
`test_every_embeddings_request_is_metered`, which imports `client_calls` from
here, so there is one recogniser.

There is no marker family: an operation call that cannot name its task is the
bug this exists to catch.

The decide half (slice F). `decide` is a common method name too --
`Examination.decide` in continuity, `self.decide` in world-info activation --
so only a call through a binding of `grimoire.inference` is an operation call:

- every `decide` call passes a first positional string literal whose route
  (`routing.route`) has `operation == "decide"`, and passes `resolved=`;
- the operation is never handed around as a value (a `decide` passed to
  `run_in_threadpool` would hide its literal);
- the safety rule (spec 14), both ways: every task of a decide route is the
  literal of some call, and a route defaulting to the Decision role decides.

The generate half (slice I). `generate` is recognised the same way -- through
a binding of `grimoire.inference` only, so `decide`, H's `run_stages` and
every other object's `generate` are never swept in:

- every `generate` call passes `resolved=`, and a first positional task that
  is a string literal whose route generates (or a registered non-route task,
  `routing.NON_ROUTE_TASKS`) -- or a name that traces only to such literals.
  A turn's meter is opened where the turn is streamed (`_fence_stream`,
  `draft_completion`, a group round's `_stream_contribution`), so the task is
  a parameter there and the literal is at the caller; `TaskProvenance`
  follows it back exactly as `Provenance` follows a space (a parameter: what
  every package call site passes for it, or its default; a local: every value
  assigned; `x if c else y`: both), and accepts nothing else;
- the operation is never handed around as a value.

**Where the space comes from** (spec 7.3, rule 2). `embed_sync` never
re-resolves the space it is handed, so nothing in it stops a call site from
building one by hand -- a dict off a connection's `base_url` and `api_key`
embeds outside the Embedding role, past its known `no`, its confirmation and
its one space. `Provenance` holds that every operation call's `space=` comes
from the role's one reader: a call to `embed_space.endpoint` or
`embed_space.endpoint_of` (`resolve.embedding`'s dict). No call site resolves
it in the function that embeds -- each reads its cache first and hands the
space down -- so the check follows the value back through the package:

- a local name: every value assigned to it, in its function;
- a parameter: the argument every call site in the package passes for it
  (a `functools.partial` binding counts as one), or its default;
- a call to a package function: every value it returns, its parameters bound
  to that call's arguments (so `_soft(similarity.available, None)` is
  `similarity.available()` or None);
- a dict display: one whose `**` operands are all such spaces and whose own
  keys are none of an endpoint's;
- `a or b` and `x if c else y`: every branch; `None`: nothing handed.

Refused: any other expression (a subscript, an attribute, a call to
something outside the package), a dict display with no spread or with an
endpoint key of its own, a name rebound by unpacking, `+=`, a `for` or a
`with` target, a name stored into (`space["model"] = ...`), a parameter of a method or nested def (its callers are not
followed), and a function handed around as a value outside a partial or a
forwarding helper, whose call sites are out of sight.

**What it does not trace** -- it is a guard against a space built by hand at
an embed call site, not a proof. It reads a function's assignments
flow-insensitively and does not see: a comprehension, `except ... as` or
`match` binding that shadows the name; an in-place mutation through a method
(`space.update(...)`); or what is handed to `embed_space.endpoint_of` -- any argument
there is accepted, since that function's input is `resolve.embedding`'s
answer and the guard does not check it. None of these is a shape a real
call site has today.
"""

from __future__ import annotations

import ast
import functools
from collections.abc import Callable, Iterator

import pytest

from grimoire.store import routing

from .test_import_guard import MODULES, PACKAGES, SOURCES, _target


def _base(node: ast.ImportFrom, modname: str, is_pkg: bool) -> str:
    """The absolute grimoire module an `ImportFrom` imports FROM ("" outside
    grimoire), by `test_import_guard._target`."""
    target = _target(node, modname, is_pkg)
    return target if target.startswith("grimoire") else ""


def bindings(tree: ast.AST, modname: str, target: str, *,
             is_pkg: bool = False) -> tuple[set[str], dict[str, str]]:
    """`(module aliases, name aliases)` for `target` in one module.

    A module alias is a dotted local name the module is bound to:
    `from <parent> import <mod> [as x]` (relative or absolute), `import
    <target> as x`, or a bare `import <target>`, which binds the dotted chain
    itself. A name alias maps a local name to what it was imported as:
    `from <target> import <name> [as y]`."""
    modules: set[str] = set()
    names: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(a.asname or a.name for a in node.names if a.name == target)
        elif isinstance(node, ast.ImportFrom):
            base = _base(node, modname, is_pkg)
            for a in node.names:
                if base and f"{base}.{a.name}" == target:
                    modules.add(a.asname or a.name)
                elif base == target:
                    names[a.asname or a.name] = a.name
    return modules, names


def _dotted(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        head = _dotted(node.value)
        return f"{head}.{node.attr}" if head is not None else None
    return None


def _task(call: ast.Call) -> str | None:
    first = call.args[0] if call.args else None
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value
    return None


def _walk() -> Iterator[tuple[str, ast.Module, bool]]:
    for modname, (_path, _src, tree) in SOURCES.items():
        yield modname, tree, modname in PACKAGES


# ---- the embed half (slice D) ----

#: The module the embed operation lives in, and its two names there: the call
#: every store module makes, and the same call in a worker thread.
EMBED_MODULE = "grimoire.store.inference.embed"
EMBED_SYNC = "embed_sync"
EMBED = "embed"
#: The embeddings client's own module, outside the client rules.
CLIENT_MODULE = "grimoire.embeddings"
#: The only modules that call the client: the operation, and the model test's
#: probe, which stays on the provider under test (ruling 8).
CLIENT_DOORS = frozenset({EMBED_MODULE, "grimoire.routes.config"})

#: At least this many operation calls exist (vacuity insurance): lore recall
#: 2, library search 2, art 1, continuity 1. The async `embed` has no caller
#: yet (ruling 9).
MIN_EMBED_CALLS = 6


def _is_embed_module(node: ast.AST, modules: set[str]) -> bool:
    """Whether `node` names `store/inference/embed.py`: a binding of it, or a
    chain whose dotted tail is `inference.embed`."""
    dotted = _dotted(node)
    return dotted is not None and (dotted in modules or dotted == "inference.embed"
                                   or dotted.endswith(".inference.embed"))


def _names_operation(node: ast.AST, modules: set[str], names: dict[str, str]) -> bool:
    """Whether a loaded `Name` or `Attribute` names the embed operation."""
    if isinstance(node, ast.Name):
        return node.id == EMBED_SYNC or names.get(node.id) in (EMBED_SYNC, EMBED)
    if isinstance(node, ast.Attribute):
        return node.attr == EMBED_SYNC or (node.attr == EMBED
                                           and _is_embed_module(node.value, modules))
    return False


def _embed_refs(tree: ast.AST, modname: str, is_pkg: bool) -> Iterator[tuple[ast.AST, bool]]:
    """Every reference to the embed operation in one module, and whether it is
    a call's `func` (False: loaded as a value)."""
    modules, names = bindings(tree, modname, EMBED_MODULE, is_pkg=is_pkg)
    funcs = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    for node in ast.walk(tree):
        if (isinstance(node, (ast.Name, ast.Attribute)) and isinstance(node.ctx, ast.Load)
                and _names_operation(node, modules, names)):
            yield node, id(node) in funcs


def embed_calls(tree: ast.AST, modname: str, is_pkg: bool = False) -> list[ast.Call]:
    """Every operation call in one module (the module docstring's rule)."""
    called = {id(ref) for ref, is_call in _embed_refs(tree, modname, is_pkg) if is_call}
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call) and id(n.func) in called]


def client_calls(tree: ast.AST, modname: str, is_pkg: bool = False) -> list[ast.Call]:
    """Every `X.embed(...)` in one module that is not an operation call: a
    request to the embeddings client."""
    operations = {id(call) for call in embed_calls(tree, modname, is_pkg)}
    return [n for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == EMBED and id(n) not in operations]


def _client_values(tree: ast.AST, modname: str, is_pkg: bool) -> list[ast.Attribute]:
    """Every `.embed` loaded as a value -- neither a call's `func` nor the
    `.value` of a further attribute -- on a receiver that is not the embed
    module, and that is not the module itself (`store.inference.embed`)."""
    modules, _names = bindings(tree, modname, EMBED_MODULE, is_pkg=is_pkg)
    funcs = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    heads = {id(n.value) for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    return [n for n in ast.walk(tree)
            if isinstance(n, ast.Attribute) and n.attr == EMBED
            and isinstance(n.ctx, ast.Load) and id(n) not in funcs and id(n) not in heads
            and not _is_embed_module(n.value, modules) and not _is_embed_module(n, modules)]


def _task_problems(tree: ast.AST, modname: str, is_pkg: bool) -> list[str]:
    if modname == EMBED_MODULE:
        return []
    out: list[str] = []
    for call in embed_calls(tree, modname, is_pkg):
        task = _task(call)
        if task is None:
            out.append(f"{modname}:{call.lineno}: embed's task is not a string literal")
        elif task not in routing.EMBED_TASKS:
            out.append(f"{modname}:{call.lineno}: {task!r} is not an embed task")
    return out


def _value_problems(tree: ast.AST, modname: str, is_pkg: bool) -> list[str]:
    if modname == EMBED_MODULE:
        return []
    return [f"{modname}:{ref.lineno}: the embed operation handed around as a value"
            for ref, is_call in _embed_refs(tree, modname, is_pkg) if not is_call]


def _client_problems(tree: ast.AST, modname: str, is_pkg: bool) -> list[str]:
    if modname == CLIENT_MODULE or modname in CLIENT_DOORS:
        return []
    return [f"{modname}:{call.lineno}: calls the embeddings client directly"
            for call in client_calls(tree, modname, is_pkg)]


def _client_value_problems(tree: ast.AST, modname: str, is_pkg: bool) -> list[str]:
    if modname == CLIENT_MODULE:
        return []
    return [f"{modname}:{node.lineno}: the embeddings client handed around as a value"
            for node in _client_values(tree, modname, is_pkg)]


def embed_problems(tree: ast.AST, modname: str, is_pkg: bool = False) -> list[str]:
    """Everything wrong with one module's embedding, under every rule."""
    return [*_task_problems(tree, modname, is_pkg), *_value_problems(tree, modname, is_pkg),
            *_client_problems(tree, modname, is_pkg),
            *_client_value_problems(tree, modname, is_pkg)]


def _across(check) -> list[str]:
    return [p for modname, tree, is_pkg in _walk() for p in check(tree, modname, is_pkg)]


def _embedded_tasks() -> set[str]:
    return {task for modname, tree, is_pkg in _walk()
            for call in embed_calls(tree, modname, is_pkg)
            if (task := _task(call)) is not None}


def test_every_embed_names_a_registered_embed_task():
    found = _across(_task_problems)
    assert not found, ("the embed operation must name, as a literal, a task in "
                       "routing.EMBED_TASKS:\n  " + "\n  ".join(found))


def test_the_operation_is_never_handed_around_as_a_value():
    found = _across(_value_problems)
    assert not found, ("call the embed operation where its task is a literal, "
                       "rather than handing it on:\n  " + "\n  ".join(found))


@pytest.mark.parametrize("task", sorted(routing.EMBED_TASKS))
def test_every_embed_task_is_named_by_a_call_site(task):
    """The registry may not accumulate phantoms: an embed task nobody embeds
    under is a ledger column nothing fills."""
    assert task in _embedded_tasks(), f"{task!r} is in EMBED_TASKS but nothing embeds under it"


def test_only_the_operation_and_the_model_test_reach_the_embeddings_client():
    found = _across(_client_problems)
    assert not found, ("embed through `store.inference.embed.embed_sync(<task>, ...)`, "
                       "which meters the request and records its failure:\n  "
                       + "\n  ".join(found))


def test_the_client_is_never_handed_around_as_a_value():
    found = _across(_client_value_problems)
    assert not found, ("the embeddings client's `.embed` handed on as a value is "
                       "a request neither guard can see:\n  " + "\n  ".join(found))


def test_the_walk_finds_the_embed_call_sites():
    found = sum(len(embed_calls(tree, modname, is_pkg)) for modname, tree, is_pkg in _walk())
    assert found >= MIN_EMBED_CALLS, f"only {found} embed operation calls found; did they move?"
    doors = {modname for modname, tree, is_pkg in _walk()
             if modname != CLIENT_MODULE and client_calls(tree, modname, is_pkg)}
    assert doors == CLIENT_DOORS, f"the embeddings client is called from {sorted(doors)}"


# ---- where the space comes from ----

#: The role's one reader, as the dict `embed_sync` takes.
SPACE_SOURCES = frozenset({("grimoire.store.embed_space", "endpoint"),
                           ("grimoire.store.embed_space", "endpoint_of")})
#: An endpoint dict's own keys (`embed_space.endpoint`): a display that sets
#: one of them over a spread space is a space built by hand.
ENDPOINT_KEYS = frozenset({"model", "base_url", "key", "space", "provider",
                           "provider_name", "provider_kind", "conn"})

_Fn = ast.FunctionDef | ast.AsyncFunctionDef
#: `(module, enclosing function or None, parameter bindings or None)`. A
#: binding maps a parameter to the argument expression and the context it is
#: read in, for a call being followed into its callee.
_Ctx = tuple[str, "_Fn | None", "dict[str, tuple[ast.expr, _Ctx]] | None"]


class Provenance:
    """Whether a `space=` traces to `SPACE_SOURCES` (the module docstring's
    rule), over a set of parsed modules."""

    def __init__(self, trees: dict[str, tuple[ast.Module, bool]]):
        self.trees = trees
        self.known = MODULES | set(trees)
        self.defs: dict[tuple[str, str], _Fn] = {}
        #: Each node's innermost enclosing function.
        self.parent: dict[int, _Fn] = {}
        for mod, (tree, _is_pkg) in trees.items():
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    self.defs[(mod, node.name)] = node
            self._parents(tree, None)
        self.aliases = {mod: self._aliases(mod, tree, is_pkg)
                        for mod, (tree, is_pkg) in trees.items()}
        #: Every loaded reference to a package function, and every call by
        #: the id of its `func` / of its first argument.
        self.refs: dict[tuple[str, str], list[tuple[str, ast.AST]]] = {}
        self.calls: dict[int, ast.Call] = {}
        self.arg_of: dict[int, ast.Call] = {}
        for mod, (tree, _is_pkg) in trees.items():
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    self.calls[id(node.func)] = node
                    for arg in node.args:
                        self.arg_of[id(arg)] = node
                elif (isinstance(node, (ast.Name, ast.Attribute))
                      and isinstance(node.ctx, ast.Load)):
                    target = self.resolve(node, mod)
                    if target in self.defs:
                        self.refs.setdefault(target, []).append((mod, node))
        self._names: dict[tuple, bool] = {}
        self._params: dict[tuple, bool] = {}

    def _parents(self, node: ast.AST, fn: _Fn | None) -> None:
        for child in ast.iter_child_nodes(node):
            if fn is not None:
                self.parent[id(child)] = fn
            inner = child if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else fn
            self._parents(child, inner)

    # -- resolution --
    def _aliases(self, mod: str, tree: ast.Module,
                 is_pkg: bool) -> tuple[dict[str, str], dict[str, tuple[str, str]]]:
        modules: dict[str, str] = {}
        names: dict[str, tuple[str, str]] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.name in self.known:
                        modules[a.asname or a.name] = a.name
            elif isinstance(node, ast.ImportFrom):
                base = _base(node, mod, is_pkg)
                for a in node.names:
                    if not base:
                        continue
                    if f"{base}.{a.name}" in self.known:
                        modules[a.asname or a.name] = f"{base}.{a.name}"
                    else:
                        names[a.asname or a.name] = (base, a.name)
        return modules, names

    def resolve(self, node: ast.AST, mod: str) -> tuple[str, str] | None:
        """The `(module, name)` a callee or reference names, if a package one."""
        modules, names = self.aliases[mod]
        if isinstance(node, ast.Name):
            if (mod, node.id) in self.defs:
                return mod, node.id
            return names.get(node.id)
        dotted = _dotted(node)
        if dotted is None or "." not in dotted:
            return None
        parts = dotted.split(".")
        for cut in range(len(parts) - 1, 0, -1):
            head = ".".join(parts[:cut])
            if head in modules:
                target = modules[head]
                for part in parts[cut:-1]:
                    target = f"{target}.{part}"
                    if target not in self.known:
                        return None
                return target, parts[-1]
        return None

    # -- the rule --
    def ok(self, expr: ast.expr | None, ctx: _Ctx) -> bool:
        if expr is None or (isinstance(expr, ast.Constant) and expr.value is None):
            return True
        if isinstance(expr, ast.Call):
            return self._call(expr, ctx)
        if isinstance(expr, ast.Dict):
            spread = [v for k, v in zip(expr.keys, expr.values, strict=True) if k is None]
            own = [k for k in expr.keys if k is not None]
            return (bool(spread) and all(self.ok(v, ctx) for v in spread)
                    and all(isinstance(k, ast.Constant) and k.value not in ENDPOINT_KEYS
                            for k in own))
        if isinstance(expr, ast.BoolOp):
            return all(self.ok(v, ctx) for v in expr.values)
        if isinstance(expr, ast.IfExp):
            return self.ok(expr.body, ctx) and self.ok(expr.orelse, ctx)
        if isinstance(expr, ast.Name):
            return self._name(expr.id, ctx)
        return False

    def _call(self, call: ast.Call, ctx: _Ctx) -> bool:
        mod, _fn, bound = ctx
        if isinstance(call.func, ast.Name) and bound and call.func.id in bound:
            # A function handed in as an argument (`_soft(fn, ...)`): what it
            # returns, followed at the reference the caller passed.
            ref, ref_ctx = bound[call.func.id]
            target = self.resolve(ref, ref_ctx[0])
            return target is not None and self._returns(target, call=None, ctx=ref_ctx)
        target = self.resolve(call.func, mod)
        if target is None:
            return False
        return self._returns(target, call=call, ctx=ctx)

    def _returns(self, target: tuple[str, str], *, call: ast.Call | None, ctx: _Ctx) -> bool:
        if target in SPACE_SOURCES:
            return True
        fn = self.defs.get(target)
        if fn is None:
            return False
        bound = self._bind(fn, call, ctx) if call is not None else None
        if call is not None and bound is None:
            return False
        inner: _Ctx = (target[0], fn, bound)
        return all(self.ok(r.value, inner) for r in self._own(fn, ast.Return))

    def _bind(self, fn: _Fn, call: ast.Call,
              ctx: _Ctx) -> dict[str, tuple[ast.expr, _Ctx]] | None:
        """`fn`'s parameters bound to `call`'s arguments; None when the call
        cannot be read (a `*args` or `**kwargs` spread)."""
        if any(isinstance(a, ast.Starred) for a in call.args) or any(
                k.arg is None for k in call.keywords):
            return None
        positional = [*fn.args.posonlyargs, *fn.args.args]
        out: dict[str, tuple[ast.expr, _Ctx]] = {}
        for arg, value in zip(positional, call.args, strict=False):
            out[arg.arg] = (value, ctx)
        for kw in call.keywords:
            out[kw.arg] = (kw.value, ctx)  # type: ignore[index]
        return out

    @staticmethod
    def _own(fn: _Fn, kind: type) -> list:
        """`kind` nodes in `fn`'s own body, not in a nested def or lambda."""
        out = []
        stack = list(fn.body)
        while stack:
            node = stack.pop()
            if isinstance(node, kind):
                out.append(node)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda,
                                 ast.ClassDef)):
                continue
            stack.extend(ast.iter_child_nodes(node))
        return out

    def _name(self, name: str, ctx: _Ctx) -> bool:
        mod, fn, bound = ctx
        if fn is None:
            return False
        key = (mod, id(fn), name, id(bound))
        if key in self._names:
            return self._names[key]
        self._names[key] = True          # co-inductive: `x = x or source()`
        got = self._name_values(name, ctx)
        self._names[key] = got
        return got

    @staticmethod
    def _binds(target: ast.AST, name: str) -> bool:
        return any(isinstance(n, ast.Name) and n.id == name for n in ast.walk(target))

    @staticmethod
    def _targets(node: ast.AST) -> tuple[list[ast.AST], ast.expr | None]:
        """What a binding statement binds, and the value a plain name among
        them takes (None: a value that cannot be read -- `+=`, a loop or
        `with` target)."""
        if isinstance(node, ast.Assign):
            return list(node.targets), node.value
        if isinstance(node, (ast.AnnAssign, ast.NamedExpr)):
            return [node.target], node.value
        if isinstance(node, (ast.AugAssign, ast.For, ast.AsyncFor)):
            return [node.target], None
        if isinstance(node, (ast.With, ast.AsyncWith)):
            return [i.optional_vars for i in node.items if i.optional_vars is not None], None
        return [], None

    def _assigned(self, fn: _Fn, name: str) -> list[ast.expr | None] | None:
        """Every value `fn`'s own body assigns to `name`; None when one of
        them cannot be read (an unpacking, `+=`, a loop or `with` target)."""
        values: list[ast.expr | None] = []
        for node in self._own(fn, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.NamedExpr,
                                   ast.For, ast.AsyncFor, ast.With, ast.AsyncWith)):
            targets, value = self._targets(node)
            for t in targets:
                if isinstance(t, ast.Name) and t.id == name and value is not None:
                    values.append(value)
                elif self._binds(t, name):
                    return None
        return values

    def _param_ok(self, name: str, ctx: _Ctx) -> bool:
        """Whether parameter `name` of the context's function is handed a
        space: its binding at a call being followed, else its default there,
        else what every call site in the package passes."""
        mod, fn, bound = ctx
        assert fn is not None
        if bound is not None and name in bound:
            value, value_ctx = bound[name]
            return self.ok(value, value_ctx)
        if bound is not None:
            return self.ok(self._default(fn, name), (mod, None, None))
        return self._param(mod, fn, name)

    def _name_values(self, name: str, ctx: _Ctx) -> bool:
        mod, fn, _bound = ctx
        assert fn is not None
        values = self._assigned(fn, name)
        if values is None:
            return False
        params = {a.arg for a in (*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs)}
        if name in params:
            if not self._param_ok(name, ctx):
                return False
        elif not values:
            outer = self.parent.get(id(fn))
            return outer is not None and self._name(name, (mod, outer, None))
        return all(self.ok(v, ctx) for v in values)

    @staticmethod
    def _default(fn: _Fn, name: str) -> ast.expr | None:
        positional = [*fn.args.posonlyargs, *fn.args.args]
        defaults = dict(zip([a.arg for a in positional[len(positional)
                                                       - len(fn.args.defaults):]],
                            fn.args.defaults, strict=True))
        defaults.update((a.arg, d) for a, d in zip(fn.args.kwonlyargs, fn.args.kw_defaults,
                                                     strict=True) if d is not None)
        # No default: a required parameter every call site supplies.
        return defaults.get(name, ast.Name("<required>"))

    def _param(self, mod: str, fn: _Fn, name: str) -> bool:
        """Every call site in the package passes a space for `fn`'s `name`."""
        target = (mod, fn.name)
        if self.defs.get(target) is not fn:
            return False                 # a method or a nested def: unseen callers
        key = (target, name)
        if key in self._params:
            return self._params[key]
        self._params[key] = True
        got = self._sites_pass(target, fn, name)
        self._params[key] = got
        return got

    def _sites_pass(self, target: tuple[str, str], fn: _Fn, name: str) -> bool:
        positional = [a.arg for a in (*fn.args.posonlyargs, *fn.args.args)]
        refs = self.refs.get(target, [])
        for mod, node in refs:
            site = self._site(node, mod)
            if site is None:
                return False             # handed around: its call sites are unseen
            call, args, keywords, partial = site
            if any(isinstance(a, ast.Starred) for a in args) or any(
                    k.arg is None for k in keywords):
                return False
            given: ast.expr | None = None
            if name in positional and positional.index(name) < len(args):
                given = args[positional.index(name)]
            else:
                given = next((k.value for k in keywords if k.arg == name), None)
            if given is None:
                if partial:
                    return False         # left for the partial's caller: unseen
                given = self._default(fn, name)
            if not self.ok(given, (mod, self.parent.get(id(call)), None)):
                return False
        return bool(refs)

    def _site(self, node: ast.AST, mod: str) -> tuple[ast.Call, list[ast.expr],
                                                       list[ast.keyword], bool] | None:
        """`(call, positional args, keywords, partial)` for a reference to a
        function: called directly, bound by `functools.partial(<ref>, ...)`, or
        handed to a forwarding helper -- a package function `h(fn, ..., *args)`
        whose body calls `fn(*args)`, which calls it with what the call passes
        for `*args` (`_soft(_semantic, failed, cid, ...)`). None otherwise."""
        call = self.calls.get(id(node))
        if call is not None:
            return call, call.args, call.keywords, False
        outer = self.arg_of.get(id(node))
        if outer is None:
            return None
        if _dotted(outer.func) in ("functools.partial", "partial") and outer.args[0] is node:
            return outer, outer.args[1:], outer.keywords, True
        helper = self.resolve(outer.func, mod)
        h = self.defs.get(helper) if helper is not None else None
        if h is None or h.args.vararg is None or outer.keywords:
            return None
        params = [a.arg for a in (*h.args.posonlyargs, *h.args.args)]
        index = next(i for i, a in enumerate(outer.args) if a is node)
        if index >= len(params) or any(isinstance(a, ast.Starred) for a in outer.args):
            return None
        fn_param, rest = params[index], h.args.vararg.arg
        forwards = [c for c in self._own(h, ast.Call)
                    if isinstance(c.func, ast.Name) and c.func.id == fn_param]
        if not forwards or not all(
                not c.keywords and len(c.args) == 1 and isinstance(c.args[0], ast.Starred)
                and isinstance(c.args[0].value, ast.Name) and c.args[0].value.id == rest
                for c in forwards):
            return None
        return outer, outer.args[len(params):], [], False

    # -- the operation calls --
    def problems(self) -> list[str]:
        out: list[str] = []
        for mod, (tree, is_pkg) in self.trees.items():
            if mod == EMBED_MODULE:
                continue
            for call in embed_calls(tree, mod, is_pkg):
                space = next((k.value for k in call.keywords if k.arg == "space"), None)
                encl = self.parent.get(id(call))
                if space is None or not self.ok(space, (mod, encl, None)):
                    out.append(f"{mod}:{call.lineno}: embed's space= does not come from "
                               "embed_space.endpoint (the Embedding role's one reader)")
        return out


@functools.cache
def _package_problems() -> tuple[str, ...]:
    """The package's provenance problems, judged once for both tests."""
    prov = Provenance({mod: (tree, is_pkg) for mod, tree, is_pkg in _walk()})
    return tuple(prov.problems())


def test_every_embeds_space_comes_from_the_embedding_role():
    found = _package_problems()
    assert not found, ("hand embed_sync the space `embed_space.endpoint()` resolved, "
                       "read through to the call:\n  " + "\n  ".join(found))


def test_the_provenance_walk_follows_every_embed_call_site():
    """Vacuity insurance: every operation call is judged, and each one's space
    is followed past its own function (none resolves where it embeds)."""
    judged = 0
    for mod, tree, is_pkg in _walk():
        if mod == EMBED_MODULE:
            continue
        for call in embed_calls(tree, mod, is_pkg):
            space = next(k.value for k in call.keywords if k.arg == "space")
            assert isinstance(space, ast.Name), f"{mod}:{call.lineno}"
            judged += 1
    assert judged >= MIN_EMBED_CALLS


_SPACE_PLANTED_IN = "grimoire.store.context.semantic"


def _planted_space_problems(src: str) -> list[str]:
    return Provenance({_SPACE_PLANTED_IN: (ast.parse(src), False)}).problems()


_PRELUDE = ("from .. import embed_space\n"
            "from ..inference import embed\n")


@pytest.mark.parametrize("src", [
    # Built by hand off a connection.
    ("def f(conn):\n"
     "    space = {'model': 'm', 'base_url': conn['base_url'], 'key': conn['api_key'],\n"
     "             'space': 's'}\n"
     "    embed.embed_sync('semantic-recall', t, space=space, client=c)\n"),
    # A parameter whose one caller builds it by hand.
    ("def f(space):\n"
     "    embed.embed_sync('semantic-recall', t, space=space, client=c)\n"
     "def g():\n"
     "    f({'model': 'm', 'space': 's'})\n"),
    # A parameter no call site in sight passes.
    ("def f(space):\n"
     "    embed.embed_sync('semantic-recall', t, space=space, client=c)\n"),
    # One of two callers is the role's, the other not.
    ("def f(space):\n"
     "    embed.embed_sync('semantic-recall', t, space=space, client=c)\n"
     "def g(other):\n"
     "    f(embed_space.endpoint())\n"
     "    f(other['space'])\n"),
    # The role's space with its model swapped.
    ("def f():\n"
     "    space = {**embed_space.endpoint(), 'model': 'other'}\n"
     "    embed.embed_sync('semantic-recall', t, space=space, client=c)\n"),
    # Reassigned to something else on another path.
    ("def f(conn):\n"
     "    space = embed_space.endpoint()\n"
     "    if conn:\n"
     "        space = conn\n"
     "    embed.embed_sync('semantic-recall', t, space=space, client=c)\n"),
    # A helper returning a hand-built space.
    ("def made():\n"
     "    return {'model': 'm', 'key': 'k', 'base_url': 'u', 'space': 's'}\n"
     "def f():\n"
     "    embed.embed_sync('semantic-recall', t, space=made(), client=c)\n"),
    # Handed around as a value, so its caller is out of sight.
    ("def f(space):\n"
     "    embed.embed_sync('semantic-recall', t, space=space, client=c)\n"
     "def g():\n"
     "    run(f)\n"
     "    f(embed_space.endpoint())\n"),
    # Forwarded by a helper, with a hand-built space.
    ("def soft(fn, fallback, *args):\n"
     "    return fn(*args)\n"
     "def missing(cid, space):\n"
     "    embed.embed_sync('continuity-similarity', t, space=space, client=c)\n"
     "def f(cid, conn):\n"
     "    soft(missing, None, cid, {'model': 'm', 'base_url': conn['base_url']})\n"),
    # No space at all.
    ("def f():\n"
     "    embed.embed_sync('semantic-recall', t, client=c)\n"),
    # An attribute, whatever holds it.
    ("def f(self):\n"
     "    embed.embed_sync('semantic-recall', t, space=self.space, client=c)\n"),
])
def test_the_provenance_guard_flags_planted_cases(src):
    assert _planted_space_problems(_PRELUDE + src), src


@pytest.mark.parametrize("src", [
    ("def f():\n"
     "    space = embed_space.endpoint()\n"
     "    embed.embed_sync('semantic-recall', t, space=space, client=c)\n"),
    # Through a parameter, a spread with keys of its own, a fallback, and a
    # helper that may answer None.
    ("def settings():\n"
     "    space = embed_space.endpoint(cfg)\n"
     "    if space is None:\n"
     "        return None\n"
     "    return {'depth': 2, **space}\n"
     "def f(cfg):\n"
     "    embed.embed_sync('semantic-recall', t, space=cfg, client=c)\n"
     "def g(space=None):\n"
     "    space = space or settings()\n"
     "    f({**space, 'threshold': 0.4})\n"
     "def h():\n"
     "    g()\n"
     "    g(space=embed_space.endpoint())\n"),
    # A function handed to a helper that calls it, and a partial.
    ("import functools\n"
     "def soft(fn, fallback):\n"
     "    try:\n"
     "        return fn()\n"
     "    except Exception:\n"
     "        return fallback\n"
     "def available():\n"
     "    return embed_space.endpoint()\n"
     "def missing(space, texts):\n"
     "    embed.embed_sync('continuity-similarity', texts, space=space, client=c)\n"
     "def f():\n"
     "    space = soft(available, None)\n"
     "    run = functools.partial(missing, space)\n"
     "    run(t)\n"),
    # A function forwarded its arguments by a helper (`reconcile._soft`).
    ("def soft(fn, fallback, *args):\n"
     "    try:\n"
     "        return fn(*args)\n"
     "    except Exception:\n"
     "        return fallback\n"
     "def missing(cid, space):\n"
     "    embed.embed_sync('continuity-similarity', t, space=space, client=c)\n"
     "def f(cid):\n"
     "    soft(missing, None, cid, embed_space.endpoint())\n"),
])
def test_the_provenance_guard_passes_planted_cases(src):
    assert _planted_space_problems(_PRELUDE + src) == [], src


# ---- planted cases ----
_EMBED_PLANTED_IN = "grimoire.store.context.semantic"


def _planted_embed_problems(src: str, modname: str = _EMBED_PLANTED_IN) -> list[str]:
    return embed_problems(ast.parse(src), modname)


@pytest.mark.parametrize(("src", "modname"), [
    # Not an embed task.
    (("from ..inference import embed\n"
      "embed.embed_sync('chat', t, space=s, client=c)\n"), _EMBED_PLANTED_IN),
    # Not a literal.
    (("from ..inference import embed\n"
      "embed.embed_sync(task, t, space=s, client=c)\n"), _EMBED_PLANTED_IN),
    # An attribute chain, whatever binds its head.
    (("from .. import store\n"
      "store.inference.embed.embed_sync(task_var, t, space=s, client=c)\n"),
     "grimoire.routes.scenes"),
    # On an arbitrary receiver: the name is the operation, whatever holds it.
    ("self._ops.embed_sync(task_var, t, space=s, client=c)\n", _EMBED_PLANTED_IN),
    # A bare `Name` call, with no import to resolve.
    ("embed_sync(x, t, space=s, client=c)\n", _EMBED_PLANTED_IN),
    # A `Name` call.
    (("from ..store.inference.embed import embed_sync\n"
      "embed_sync(x, t, space=s, client=c)\n"), "grimoire.routes.scenes"),
    # A name bound to either form, aliased.
    (("from ..inference.embed import embed_sync as run\n"
      "run(task, t, space=s, client=c)\n"), _EMBED_PLANTED_IN),
    (("from ..inference.embed import embed as run\n"
      "run(task, t, space=s, client=c)\n"), _EMBED_PLANTED_IN),
    # The async form through a module alias, absolute or relative.
    (("import grimoire.store.inference.embed as ops\n"
      "ops.embed(task, t, space=s, client=c)\n"), _EMBED_PLANTED_IN),
    (("from .inference import embed as e\n"
      "e.embed('chat', t, space=s, client=c)\n"), "grimoire.store.semsearch"),
    # The operation handed around as a value.
    (("from ..inference import embed\n"
      "asyncio.to_thread(embed.embed_sync, task, t)\n"), _EMBED_PLANTED_IN),
    (("from ..inference.embed import embed_sync\n"
      "asyncio.to_thread(embed_sync, task, t)\n"), _EMBED_PLANTED_IN),
    # The client, called directly from a store module.
    (("_CLIENT = embeddings.EmbeddingsClient()\n"
      "_CLIENT.embed(t, m, k, u)\n"), "grimoire.store.semsearch"),
    # The client handed around as a value, even from a door.
    ("asyncio.to_thread(_CLIENT.embed, t, m, k, u)\n", "grimoire.store.semsearch"),
    ("asyncio.to_thread(_EMBEDDINGS.embed, t, m, k, u)\n", "grimoire.routes.config"),
])
def test_the_embed_guard_flags_planted_cases(src, modname):
    assert _planted_embed_problems(src, modname), src


@pytest.mark.parametrize(("src", "modname"), [
    # An embed task, through an aliased module binding.
    (("from ..inference import embed as e\n"
      "e.embed_sync('semantic-recall', t, space=s, client=c)\n"), _EMBED_PLANTED_IN),
    # The async form through an aliased module binding: not a client call.
    (("from ..inference import embed as e\n"
      "e.embed('semantic-recall', t, space=s, client=c)\n"), _EMBED_PLANTED_IN),
    # An operation call through an attribute chain: not a client call.
    (("from .. import store\n"
      "store.inference.embed.embed('semantic-search', t, space=s, client=c)\n"),
     "grimoire.routes.scenes"),
    # The module itself, reached through a chain, as the model test reaches
    # `record_failure`.
    (("from .. import store\n"
      "store.inference.embed.record_failure(m, exc)\n"), "grimoire.routes.config"),
    # The model test's probe, at its door.
    ("asyncio.to_thread(lambda: _EMBEDDINGS.embed([x], m, k, u, usage=m.usage))\n",
     "grimoire.routes.config"),
    # The operation's own module: the async form forwards `task`, and hands
    # `embed_sync` to a worker thread.
    (("async def embed(task, texts):\n"
      "    return await asyncio.to_thread(embed_sync, task, texts)\n"
      "def embed_sync(task, texts, client):\n"
      "    return client.embed(texts, m, k, u, usage=h)\n"), EMBED_MODULE),
    # The client's own module.
    ("def embed(self, texts):\n    return self.embed(texts[1:])\n", CLIENT_MODULE),
])
def test_the_embed_guard_passes_planted_cases(src, modname):
    assert _planted_embed_problems(src, modname) == [], src


def test_bindings_resolve_the_embed_module():
    tree = ast.parse("from ..inference import embed\n"
                     "from ..inference import embed as e\n"
                     "from ..inference.embed import embed_sync as run, record_failure\n"
                     "from ..inference import resolve\n"
                     "import grimoire.store.inference.embed\n")
    modules, names = bindings(tree, "grimoire.store.context.semantic", EMBED_MODULE)
    assert modules == {"embed", "e", "grimoire.store.inference.embed"}
    assert names == {"run": "embed_sync", "record_failure": "record_failure"}
    # One level up from a top-level store module is `grimoire.store`.
    one = ast.parse("from .inference import embed\n")
    assert bindings(one, "grimoire.store.semsearch", EMBED_MODULE)[0] == {"embed"}


# ---- the decide half (slice F) ----

#: The module `decide` lives in, and the operation's name there.
DECIDE_MODULE = "grimoire.inference"
DECIDE = "decide"

#: At least this many operation calls exist (vacuity insurance): scene-break,
#: voice drift and the speaker pick (slice F), and the duplicate check and the
#: reconciliation sweep (slice G).
MIN_DECIDE_CALLS = 5


def _decide_refs(tree: ast.AST, modname: str, is_pkg: bool) -> Iterator[tuple[ast.AST, bool]]:
    """Every reference to the `decide` operation in one module, and whether it
    is a call's `func` (False: loaded as a value)."""
    modules, names = bindings(tree, modname, DECIDE_MODULE, is_pkg=is_pkg)
    aliases = {local for local, name in names.items() if name == DECIDE}
    if not modules and not aliases:
        return
    funcs = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    for node in ast.walk(tree):
        through_module = (isinstance(node, ast.Attribute) and node.attr == DECIDE
                          and _dotted(node.value) in modules)
        by_name = (isinstance(node, ast.Name) and node.id in aliases
                   and isinstance(node.ctx, ast.Load))
        if through_module or by_name:
            yield node, id(node) in funcs


def decide_calls(tree: ast.AST, modname: str, is_pkg: bool = False) -> list[ast.Call]:
    """Every operation call: `x.decide(...)` for a module binding `x`, or
    `y(...)` for a name binding `y` of `decide`."""
    called = {id(ref) for ref, is_call in _decide_refs(tree, modname, is_pkg) if is_call}
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call) and id(n.func) in called]


def decide_problems(tree: ast.AST, modname: str, is_pkg: bool = False, *,
                    route_of: Callable[[str], routing.Route | None] = routing.route,
                    ) -> list[str]:
    """What is wrong with one module's use of the `decide` operation."""
    out: list[str] = []
    for ref, is_call in _decide_refs(tree, modname, is_pkg):
        if not is_call:
            out.append(f"{modname}:{ref.lineno}: decide handed around as a value")
    for call in decide_calls(tree, modname, is_pkg):
        task = _task(call)
        if task is None:
            out.append(f"{modname}:{call.lineno}: decide's task is not a string literal")
            continue
        route = route_of(task)
        if route is None or route.operation != "decide":
            out.append(f"{modname}:{call.lineno}: {task!r} is not a task of a decide route")
        if not any(k.arg == "resolved" for k in call.keywords):
            out.append(f"{modname}:{call.lineno}: decide({task!r}) passes no resolved=")
    return out


def _decided_tasks() -> set[str]:
    return {task for modname, tree, is_pkg in _walk()
            for call in decide_calls(tree, modname, is_pkg)
            if (task := _task(call)) is not None}


def test_every_decide_names_a_task_on_a_decide_route():
    found = [p for modname, tree, is_pkg in _walk() for p in decide_problems(tree, modname, is_pkg)]
    assert not found, ("inference.decide must name, as a literal, a task whose route "
                       "decides, and pass resolved=:\n  " + "\n  ".join(found))


@pytest.mark.parametrize("task", sorted(
    task for r in routing.ROUTES if r.operation == "decide" for task in r.tasks))
def test_every_decide_task_is_decided_by_a_call_site(task):
    """The safety rule (spec 14): a route flips to `decide` only in the task
    whose call site calls `decide()` for it."""
    assert task in _decided_tasks(), f"{task!r} is on a decide route that nothing decides"


def test_a_route_defaulting_to_the_decision_role_decides():
    wrong = [r.key for r in routing.ROUTES
             if r.default_role == "decision" and r.operation != "decide"]
    assert not wrong, f"routes on the Decision role that do not decide: {wrong}"


def test_the_walk_finds_the_decide_call_sites():
    found = sum(len(decide_calls(tree, modname, is_pkg)) for modname, tree, is_pkg in _walk())
    assert found >= MIN_DECIDE_CALLS, f"only {found} decide calls found; did they move?"


# ---- planted cases (decide) ----
_DECIDE_PLANTED_IN = "grimoire.routes.scenes"


def _planted_decide_problems(src: str, modname: str = _DECIDE_PLANTED_IN) -> list[str]:
    """`decide_problems` over planted source, against the real routes: `scene-break`
    is a decide route and `chat` is not."""
    return decide_problems(ast.parse(src), modname)


@pytest.mark.parametrize("src", [
    # Not a decide route.
    ("from .. import inference as operations\n"
     "operations.decide('chat', items, client=c, resolved=r)\n"),
    # Not a literal.
    ("from .. import inference as operations\n"
     "operations.decide(task, items, client=c, resolved=r)\n"),
    # A decide route, but no resolution.
    ("from .. import inference as operations\n"
     "operations.decide('scene-break', items, client=c)\n"),
    # A name binding, aliased.
    ("from ..inference import decide as pick\n"
     "pick('chat', items, client=c, resolved=r)\n"),
    # The operation handed around as a value.
    ("from .. import inference as operations\n"
     "run_in_threadpool(operations.decide, 'scene-break', items, resolved=r)\n"),
    ("from ..inference import decide\n"
     "run_in_threadpool(decide, 'scene-break', items, resolved=r)\n"),
    # Absolute spellings bind the same module.
    "import grimoire.inference as ops\nops.decide('chat', items, client=c, resolved=r)\n",
    ("import grimoire.inference\n"
     "grimoire.inference.decide('chat', items, client=c, resolved=r)\n"),
    "from grimoire import inference\ninference.decide(t, items, client=c, resolved=r)\n",
])
def test_the_decide_guard_flags_planted_cases(src):
    assert _planted_decide_problems(src), src


@pytest.mark.parametrize(("src", "modname"), [
    # A route that decides, as scene-break does.
    (("from .. import inference as operations\n"
      "operations.decide('scene-break', items, client=c, resolved=r)\n"), _DECIDE_PLANTED_IN),
    (("from ..inference import decide as pick\n"
      "pick('scene-break', items, client=c, resolved=r)\n"), _DECIDE_PLANTED_IN),
    # Other objects' `decide`, in a module that binds the operation too.
    ("from .. import inference as operations\nexam.decide(rows)\n", _DECIDE_PLANTED_IN),
    ("from .. import inference as operations\nself.decide(p, level, pullers)\n",
     _DECIDE_PLANTED_IN),
    # `inference` bound to the STORE's resolver, as routes/common.py binds it.
    ("from ..store.inference import resolve as inference\ninference.decide('chat')\n",
     _DECIDE_PLANTED_IN),
    # A local `decide` in a module that never imports grimoire.inference.
    ("def decide(x):\n    return x\ndecide('chat')\nrun(decide)\n",
     "grimoire.store.context.activation"),
])
def test_the_decide_guard_passes_planted_cases(src, modname):
    assert _planted_decide_problems(src, modname) == [], src


def test_bindings_resolve_the_decide_module():
    tree = ast.parse("from .. import inference as a\n"
                     "from ..inference import decide as b, structured_messages\n"
                     "from grimoire import inference\n"
                     "import grimoire.inference as c\n"
                     "from ..store import inference as not_it\n")
    modules, names = bindings(tree, "grimoire.routes.scenes", DECIDE_MODULE)
    assert modules == {"a", "inference", "c"}
    assert names == {"b": "decide", "structured_messages": "structured_messages"}
    # From a package's own `__init__`, one level up is the package itself.
    pkg = ast.parse("from .. import inference as a\n")
    assert bindings(pkg, "grimoire.routes", DECIDE_MODULE, is_pkg=True)[0] == {"a"}


# ---- the generate half (slice I) ----

#: The operation's name in `grimoire.inference` (`DECIDE_MODULE`).
GENERATE = "generate"

#: At least this many operation calls exist (vacuity insurance): the turn
#: stream, a group round's contribution, the opener, the drafts' shared
#: completion, the tracker, the tagline batch, absorb's extraction, audit and
#: dossiers, the rolling summary and the scene-break title.
MIN_GENERATE_CALLS = 11


def _inference_refs(tree: ast.AST, modname: str, is_pkg: bool,
                    op: str) -> Iterator[tuple[ast.AST, bool]]:
    """`_decide_refs` for any operation of `grimoire.inference`: every
    reference to `op` through a binding of that module, and whether it is a
    call's `func`."""
    modules, names = bindings(tree, modname, DECIDE_MODULE, is_pkg=is_pkg)
    aliases = {local for local, name in names.items() if name == op}
    if not modules and not aliases:
        return
    funcs = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    for node in ast.walk(tree):
        through_module = (isinstance(node, ast.Attribute) and node.attr == op
                          and _dotted(node.value) in modules)
        by_name = (isinstance(node, ast.Name) and node.id in aliases
                   and isinstance(node.ctx, ast.Load))
        if through_module or by_name:
            yield node, id(node) in funcs


def generate_calls(tree: ast.AST, modname: str, is_pkg: bool = False) -> list[ast.Call]:
    """Every `generate` operation call in one module."""
    called = {id(ref) for ref, is_call in _inference_refs(tree, modname, is_pkg, GENERATE)
              if is_call}
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call) and id(n.func) in called]


def _a_generate_task(task: str, route_of: Callable[[str], routing.Route | None]) -> bool:
    route = route_of(task)
    return (route is not None and route.operation == "generate") or (
        task in routing.NON_ROUTE_TASKS)


class TaskProvenance(Provenance):
    """Whether a `generate` call's task traces to string literals of generate
    tasks, followed through the package as `Provenance` follows a space."""

    def __init__(self, trees: dict[str, tuple[ast.Module, bool]], *,
                 route_of: Callable[[str], routing.Route | None] = routing.route):
        super().__init__(trees)
        self.route_of = route_of

    def ok(self, expr: ast.expr | None, ctx: _Ctx) -> bool:
        if isinstance(expr, ast.Constant):
            return isinstance(expr.value, str) and _a_generate_task(expr.value, self.route_of)
        if isinstance(expr, ast.IfExp):
            return self.ok(expr.body, ctx) and self.ok(expr.orelse, ctx)
        if isinstance(expr, ast.Name):
            return self._name(expr.id, ctx)
        return False

    def problems(self) -> list[str]:
        out: list[str] = []
        for mod, (tree, is_pkg) in self.trees.items():
            for ref, is_call in _inference_refs(tree, mod, is_pkg, GENERATE):
                if not is_call:
                    out.append(f"{mod}:{ref.lineno}: generate handed around as a value")
            for call in generate_calls(tree, mod, is_pkg):
                task = call.args[0] if call.args else None
                if not self.ok(task, (mod, self.parent.get(id(call)), None)):
                    out.append(f"{mod}:{call.lineno}: generate's task is not a literal of a "
                               "generate task, nor a name that traces only to one")
                if not any(k.arg == "resolved" for k in call.keywords):
                    out.append(f"{mod}:{call.lineno}: generate passes no resolved=")
        return out


@functools.cache
def _generate_problems() -> tuple[str, ...]:
    prov = TaskProvenance({mod: (tree, is_pkg) for mod, tree, is_pkg in _walk()})
    return tuple(prov.problems())


def test_every_generate_names_a_generate_task_and_passes_resolved():
    found = _generate_problems()
    assert not found, ("inference.generate must name a generate task -- a literal, or a "
                       "name every caller hands one -- and pass resolved=:\n  "
                       + "\n  ".join(found))


def test_generate_is_never_handed_around_as_a_value():
    handed = [p for p in _generate_problems() if "handed around" in p]
    assert not handed, handed


def test_the_walk_finds_the_generate_call_sites():
    found = sum(len(generate_calls(tree, modname, is_pkg)) for modname, tree, is_pkg in _walk())
    assert found >= MIN_GENERATE_CALLS, f"only {found} generate calls found; did they move?"


def test_the_generate_walk_never_sweeps_in_decide():
    """`decide` (and H's `run_stages`) is not a generation: neither half
    counts the other's calls."""
    for modname, tree, is_pkg in _walk():
        generating = {id(c) for c in generate_calls(tree, modname, is_pkg)}
        assert not generating & {id(c) for c in decide_calls(tree, modname, is_pkg)}, modname
        for call in generate_calls(tree, modname, is_pkg):
            assert isinstance(call.func, (ast.Attribute, ast.Name))
            name = call.func.attr if isinstance(call.func, ast.Attribute) else call.func.id
            assert name == GENERATE, (modname, call.lineno)


# ---- planted cases (generate) ----
_GENERATE_PLANTED_IN = "grimoire.routes.scenes"
_OPS = "from .. import inference as operations\n"


def _planted_generate_problems(src: str, modname: str = _GENERATE_PLANTED_IN) -> list[str]:
    """`TaskProvenance.problems` over planted source, against the real routes:
    `chat` generates and `scene-break` decides."""
    return TaskProvenance({modname: (ast.parse(src), False)}).problems()


@pytest.mark.parametrize("src", [
    # A decide task.
    _OPS + "operations.generate('scene-break', m, client=c, resolved=r)\n",
    # No such task.
    _OPS + "operations.generate('no-such-task', m, client=c, resolved=r)\n",
    # A name bound at module scope: nothing traces it.
    _OPS + "TASK = 'chat'\noperations.generate(TASK, m, client=c, resolved=r)\n",
    # A literal task, but no resolution.
    _OPS + "operations.generate('chat', m, client=c)\n",
    # An attribute, not a name: `resolved.task` is whatever was resolved.
    _OPS + ("def f(resolved):\n"
            "    return operations.generate(resolved.task, m, client=c, resolved=resolved)\n"
            "f(r)\n"),
    # A forwarder one of whose callers passes a decide task.
    _OPS + ("def f(task):\n"
            "    return operations.generate(task, m, client=c, resolved=r)\n"
            "f('chat')\nf('scene-break')\n"),
    # A forwarder nobody calls: its task is unseen.
    _OPS + ("def f(task):\n"
            "    return operations.generate(task, m, client=c, resolved=r)\n"),
    # A forwarder handed around as a value: its callers are unseen.
    _OPS + ("def f(task):\n"
            "    return operations.generate(task, m, client=c, resolved=r)\n"
            "run(f, 'chat')\n"),
    # A forwarder whose default is a decide task.
    _OPS + ("def f(task='scene-break'):\n"
            "    return operations.generate(task, m, client=c, resolved=r)\n"
            "f()\n"),
    # One branch of a conditional is not a generate task.
    _OPS + "operations.generate('chat' if x else 'scene-break', m, client=c, resolved=r)\n",
    # A name binding, aliased.
    "from ..inference import generate as gen\ngen('scene-break', m, client=c, resolved=r)\n",
    # The operation handed around as a value.
    _OPS + "run_in_threadpool(operations.generate, 'chat', m, resolved=r)\n",
    # Absolute spellings bind the same module.
    "import grimoire.inference as ops\nops.generate(t, m, client=c, resolved=r)\n",
])
def test_the_generate_guard_flags_planted_cases(src):
    assert _planted_generate_problems(src), src


@pytest.mark.parametrize("src", [
    _OPS + "operations.generate('chat', m, client=c, resolved=r, usage=x.usage)\n",
    _OPS + "operations.generate('tagline', m, client=c, resolved=r, stream=False)\n",
    _OPS + "operations.generate('chat' if x else 'continuation', m, client=c, resolved=r)\n",
    # A forwarder: the literal is at every caller, or the default.
    _OPS + ("def f(cid, task='chat'):\n"
            "    return operations.generate(task, m, client=c, resolved=r)\n"
            "f(1)\nf(1, 'retry')\nf(1, task='director')\n"),
    # Two forwarders, and a closure inside the inner one.
    _OPS + ("def inner(task):\n"
            "    async def frames():\n"
            "        async for d in operations.generate(task, m, client=c, resolved=r):\n"
            "            yield d\n"
            "    return frames\n"
            "def outer(task='chat'):\n"
            "    return inner(task=task)\n"
            "outer()\nouter(task='replay')\n"),
    # A local chosen between two literals.
    _OPS + ("def f(x):\n"
            "    task = 'continuation' if x else 'chat'\n"
            "    return operations.generate(task, m, client=c, resolved=r)\n"),
    # Other objects' `generate`, and `decide`, in a module that binds the operation.
    _OPS + "image.generate(prompt)\nself.generate()\n",
    _OPS + "operations.decide('scene-break', items, client=c, resolved=r)\n",
    # `inference` bound to the STORE's resolver, as routes/common.py binds it.
    "from ..store.inference import resolve as inference\ninference.generate('scene-break')\n",
])
def test_the_generate_guard_passes_planted_cases(src):
    assert _planted_generate_problems(src) == [], src
