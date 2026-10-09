"""Guard: the connection-dict lowering stays retired (inference slice I, Task 10).

Until slice I, every resolved attempt was LOWERED to a connection dict --
`{**connection, "model", "sampling", "model_params"}`, the model's facts laid
over the connection's legacy fields (`with_facts`), an account block under
`"_account"`, a structured flag under `"_structured"`, and the fallback the
facade sent riding on the primary's dict under `"_fallback"`. Slice I built a
typed `wire.Target` beside each dict (Task 7), moved the facade onto
`wire.Chain`s of them (Task 9), and Task 10 deleted the dict: the resolver
builds the target directly, a resolution's `chain` says whether its fallback
rides, and nothing reads a connection dict as an attempt any more.

A second spelling is how the two drift -- one copy of a fact on the dict,
another on the target, and a reader that picks the wrong one -- so this holds
the deleted names deleted. The claims, each held against the AST of
`backend/src/grimoire`, `evals/` and `backend/scripts/`:

- no name, attribute, definition or import spells a retired name
  (`RETIRED_NAMES`): the dict's keys (`FALLBACK_KEY`, `STRUCTURED_KEY`,
  `ACCOUNT_KEY`), the lowering itself (`_lowered`, `with_facts`,
  `own_sampling`), the shims that read a dict as a chain (`from_lowered`,
  `_as_chain`), the fallback strippers (`_without_fallback`, slice H's public
  `without_fallback`), and slice H's native table (`NATIVE_DECISION_KINDS`,
  `NativeAdapter`), which the adapter registry (`adapters.py`) absorbed;
- `lower` is not imported from `store.inference.resolve`, nor read off a name
  bound to that module (`inference.lower(...)`, `resolve.lower(...)`);
- `.conn` is not read off a resolution, an attempt, a decide stage or a call
  (`_conn_reads`): a name bound by `require_inference(...)`,
  `override_inference(...)[0]` or the FIRST name of a tuple unpack of
  `override_inference(...)` (`resolved, routed = ...` and `resolved, _ = ...`,
  the shape every production caller uses), `inference.resolve(...)` /
  `resolve.embedding(...)`, an `.attempts[i]` or a loop over `.attempts`; a
  `Stage(...)` or an item of `stages(...)`; a `_Call` -- the name `call`, or
  `replace(call, ...)`; and a parameter annotated as any of those types. The
  same expressions read directly (`require_inference(...).conn`) count too;
- no string constant spells a retired dict key (`RETIRED_KEYS`): `"_fallback"`,
  `"_structured"`, `"_account"` or `"_degrade"`.

`test_the_guard_flags_a_planted_lowering` plants one of each, the tuple-unpack
shape included, and asserts each is reported.

Honest about its reach, in the house style. It sees spellings and the
bindings above, not meaning: a dict assembled with keys built at runtime, a
resolution handed through a container and read back under another name, or a
`getattr(resolved, "conn")` passes. The deleted properties are the backstop
there -- `ResolvedInference` and `Attempt` no longer have a `conn` to read, so
such a read raises rather than reading a stale dict. As CLAUDE.md says of every
guard here: the guards do not prove absence.
"""

from __future__ import annotations

import ast
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[2]
SRC = REPO / "backend" / "src" / "grimoire"
#: Every tree that can make or read a call: the package, the scripts a skill
#: runs, and the eval harness.
ROOTS = (SRC, REPO / "backend" / "scripts", REPO / "evals")

#: Names the lowering and its shims went by.
RETIRED_NAMES = frozenset({
    "FALLBACK_KEY", "STRUCTURED_KEY", "ACCOUNT_KEY",
    "with_facts", "own_sampling", "_lowered",
    "_without_fallback", "without_fallback",
    "NATIVE_DECISION_KINDS", "NativeAdapter",
    "from_lowered", "_as_chain",
})

#: The lowered dict's private keys.
RETIRED_KEYS = frozenset({"_fallback", "_structured", "_account", "_degrade"})

#: The calls whose answer is a resolution (or, `override_inference`, a tuple
#: whose first item is one).
RESOLVING_CALLS = frozenset({"require_inference", "override_inference", "resolve",
                             "embedding", "_usable", "_narrowed"})

#: The types whose instances once carried, or could be mistaken for carrying,
#: a lowered dict as `.conn`.
CARRIER_TYPES = frozenset({"ResolvedInference", "UsableInference", "Attempt", "Stage",
                           "_Call"})

#: The name a `_Call` goes by (`inference._Call`, `replace(call, ...)`).
CALL_NAME = "call"


def _files() -> list[tuple[str, pathlib.Path]]:
    return [(path.relative_to(REPO).as_posix(), path)
            for root in ROOTS for path in sorted(root.rglob("*.py"))]


def _callee(node: ast.AST) -> str:
    """The bare name a call is made through: `f(...)` -> "f",
    `x.f(...)` -> "f"; "" for anything else."""
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Name):
            return func.id
        if isinstance(func, ast.Attribute):
            return func.attr
    return ""


def _is_attempts(node: ast.AST) -> bool:
    """`<x>.attempts`."""
    return isinstance(node, ast.Attribute) and node.attr == "attempts"


def _carries(node: ast.AST, bound: set[str]) -> bool:
    """Whether `node` is a resolution, attempt, stage or call -- by the
    expression that made it or by a name bound to one (`bound`)."""
    if isinstance(node, ast.Name):
        return node.id in bound or node.id == CALL_NAME
    if isinstance(node, ast.Call):
        name = _callee(node)
        if name in RESOLVING_CALLS - {"override_inference"} or name in {"Stage", "_Call"}:
            return True
        # `replace(call, ...)`: a `_Call` with fields replaced.
        return (name == "replace" and bool(node.args) and isinstance(node.args[0], ast.Name)
                and node.args[0].id == CALL_NAME)
    if isinstance(node, ast.Subscript):
        # `.attempts[i]`, and `override_inference(...)[0]`.
        if _is_attempts(node.value):
            return True
        return _callee(node.value) == "override_inference"
    return False


def _annotated_carrier(annotation: ast.AST | None) -> bool:
    """Whether a parameter's annotation names a carrier type (`X`, `m.X`,
    `X | None`, or the string `"X"`)."""
    if annotation is None:
        return False
    for node in ast.walk(annotation):
        if isinstance(node, ast.Name) and node.id in CARRIER_TYPES:
            return True
        if isinstance(node, ast.Attribute) and node.attr in CARRIER_TYPES:
            return True
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and any(t in node.value for t in CARRIER_TYPES):
            return True
    return False


def _parameters(scope: ast.AST) -> set[str]:
    """The parameters of function `scope` annotated with a carrier type."""
    if not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return set()
    args = scope.args
    return {arg.arg for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs)
            if _annotated_carrier(arg.annotation)}


def _bound_by(node: ast.AST, bound: set[str]) -> set[str]:
    """The names one statement binds to a carrier, given those already bound:
    an assignment from a carrying expression, the first name of a tuple
    unpack of `override_inference(...)`, and a loop target over `.attempts`
    or `stages(...)`."""
    if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        out = {t.id for t in targets if isinstance(t, ast.Name) and _carries(node.value, bound)}
        if _callee(node.value) == "override_inference":
            out |= {t.elts[0].id for t in targets
                    if isinstance(t, (ast.Tuple, ast.List)) and t.elts
                    and isinstance(t.elts[0], ast.Name)}
        return out
    if isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)) \
            and (_is_attempts(node.iter) or _callee(node.iter) == "stages") \
            and isinstance(node.target, ast.Name):
        return {node.target.id}
    return set()


def _bindings(scope: ast.AST) -> set[str]:
    """The names `scope` binds to a carrier (`_parameters`, `_bound_by`),
    iterated to a fixed point, so `a = resolved` after `resolved =
    require_inference(...)` binds `a` too."""
    bound = _parameters(scope)
    while True:
        grown = bound.union(*(_bound_by(node, bound) for node in ast.walk(scope)))
        if grown == bound:
            return bound
        bound = grown


def _conn_reads(tree: ast.Module) -> list[ast.Attribute]:
    """Every `.conn` read off a carrier, per function (module level counts as
    one scope)."""
    out: list[ast.Attribute] = []
    scopes: list[ast.AST] = [tree, *(n for n in ast.walk(tree)
                                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))]
    seen: set[int] = set()
    for scope in scopes:
        bound = _bindings(scope)
        for node in ast.walk(scope):
            if (isinstance(node, ast.Attribute) and node.attr == "conn"
                    and id(node) not in seen and _carries(node.value, bound)):
                seen.add(id(node))
                out.append(node)
    return out


#: The resolver's own module, where the lowering lived as a bare `lower`.
RESOLVER = "backend/src/grimoire/store/inference/resolve.py"


def _resolve_aliases(tree: ast.Module) -> set[str]:
    """The names a module binds `store.inference.resolve` to: `from
    ..store.inference import resolve [as X]`, `from .inference import
    resolve`, a relative `from . import resolve` / `from .. import resolve`
    (how `store/inference/`'s own modules reach it), and `import
    ...inference.resolve as X`. The package holds one module named
    `resolve`, so a relative import of that name is it."""
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                if alias.name == "resolve" and (module.split(".")[-1] == "inference"
                                                or (node.level > 0 and not module)):
                    out.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.endswith("inference.resolve") and alias.asname:
                    out.add(alias.asname)
    return out


def _from_resolve(node: ast.ImportFrom) -> bool:
    """Whether `node` imports from `store.inference.resolve` (absolute, or
    relatively as `.resolve` inside the package)."""
    module = node.module or ""
    return module.endswith("inference.resolve") or (node.level > 0 and module == "resolve")


def _lowers(node: ast.AST, aliases: set[str], rel: str) -> str | None:
    """What `node` says, if it reaches the resolver's `lower`: `X.lower` on a
    name bound to the module (`_resolve_aliases`), `<...>.resolve.lower` on a
    dotted path to it, and -- inside the resolver itself -- a `def lower` or
    a bare `lower(...)` call."""
    if isinstance(node, ast.Attribute) and node.attr == "lower":
        if isinstance(node.value, ast.Name) and node.value.id in aliases:
            return f"reads {node.value.id}.lower (the resolver's lowering)"
        if isinstance(node.value, ast.Attribute) and node.value.attr == "resolve":
            return "reads resolve.lower (the resolver's lowering)"
    if rel != RESOLVER:
        return None
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "lower":
        return "defines lower in the resolver"
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "lower"):
        return "calls lower in the resolver"
    return None


def _named(node: ast.AST) -> str | None:
    """What `node` says, if it spells a retired name: a name, an attribute,
    a definition, or an import."""
    if isinstance(node, ast.Name) and node.id in RETIRED_NAMES:
        return f"names {node.id}"
    if isinstance(node, ast.Attribute) and node.attr in RETIRED_NAMES:
        return f"reads .{node.attr}"
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) \
            and node.name in RETIRED_NAMES:
        return f"defines {node.name}"
    return None


def _imported(node: ast.AST) -> list[str]:
    """What an import of `node` brings in that is retired: a retired name,
    and `lower` from `store.inference.resolve`."""
    if not isinstance(node, (ast.Import, ast.ImportFrom)):
        return []
    out = [f"imports {alias.name}" for alias in node.names
           if alias.name.split(".")[-1] in RETIRED_NAMES or (alias.asname or "") in RETIRED_NAMES]
    if isinstance(node, ast.ImportFrom) and _from_resolve(node) \
            and any(alias.name == "lower" for alias in node.names):
        out.append("imports lower from store.inference.resolve")
    return out


def scan(source: str, rel: str) -> list[str]:
    """Every retired spelling in `source` (module `rel`), as `rel:line: what`."""
    tree = ast.parse(source)
    aliases = _resolve_aliases(tree)
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        line = getattr(node, "lineno", 0)
        named = _named(node)
        if named is not None:
            found.append((line, named))
        found.extend((line, what) for what in _imported(node))
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and node.value in RETIRED_KEYS:
            found.append((line, f"spells the dict key {node.value!r}"))
        lowers = _lowers(node, aliases, rel)
        if lowers is not None:
            found.append((line, lowers))
    found.extend((node.lineno, "reads .conn off a resolution, attempt, stage or call")
                 for node in _conn_reads(tree))
    return [f"{rel}:{line}: {what}" for line, what in sorted(set(found))]


def test_the_lowering_stays_retired():
    found = [hit for rel, path in _files()
             for hit in scan(path.read_text(encoding="utf-8"), rel)]
    assert not found, ("the connection-dict lowering was retired in inference slice I "
                       "(Task 10); build or read a wire.Target / wire.Chain instead:\n"
                       + "\n".join(found))


def test_the_guard_reaches_every_tree():
    """The scan covers the package, the scripts and the evals -- each has
    files, so a moved root fails here rather than scanning nothing."""
    for root in ROOTS:
        assert any(root.rglob("*.py")), root
    rels = {rel for rel, _ in _files()}
    assert "backend/src/grimoire/llm.py" in rels
    assert any(r.startswith("evals/") for r in rels)
    assert any(r.startswith("backend/scripts/") for r in rels)


#: One plant per rule, each with the text its report must carry.
PLANTS: tuple[tuple[str, str], ...] = (
    ("from grimoire import llm\nllm.FALLBACK_KEY\n",
     "reads .FALLBACK_KEY"),
    ("STRUCTURED_KEY = 1\n",
     "names STRUCTURED_KEY"),
    ("from grimoire.llm_usage import ACCOUNT_KEY\n",
     "imports ACCOUNT_KEY"),
    ("def with_facts(conn, facts):\n    return conn\n",
     "defines with_facts"),
    ("x = resolve.own_sampling(conn)\n",
     "reads .own_sampling"),
    ("class C:\n    async def _lowered(self):\n        pass\n",
     "defines _lowered"),
    ("client._without_fallback(conn)\n",
     "reads ._without_fallback"),
    ("inference.without_fallback(conn)\n",
     "reads .without_fallback"),
    ("kinds = NATIVE_DECISION_KINDS\n",
     "names NATIVE_DECISION_KINDS"),
    ("class NativeAdapter:\n    pass\n",
     "defines NativeAdapter"),
    ("wire.from_lowered(conn).primary\n",
     "reads .from_lowered"),
    ("def _as_chain(x):\n    return x\n",
     "defines _as_chain"),
    ("from grimoire.store.inference.resolve import lower\n",
     "imports lower from store.inference.resolve"),
    ("from .resolve import lower\n",
     "imports lower from store.inference.resolve"),
    (("from ..store.inference import resolve as inference\n"
     "inference.lower(raw, sampling, model)\n"),
     "reads inference.lower"),
    ("def f():\n    resolved = require_inference('chat', cid)\n    return resolved.conn\n",
     "reads .conn"),
    (("def f(body):\n    resolved, routed = override_inference(body, 'chat', cid)\n"
     "    return resolved.conn\n"),
     "reads .conn"),
    (("def f(body):\n    resolved, _ = override_inference(body, 'chat', cid)\n"
     "    return resolved.conn\n"),
     "reads .conn"),
    (("def f(body):\n    served = override_inference(body, 'chat', cid)[0]\n"
     "    return served.conn\n"),
     "reads .conn"),
    ("def f():\n    got = inference.resolve('chat')\n    return got.conn\n",
     "reads .conn"),
    ("def f(resolved):\n    return resolved.attempts[1].conn\n",
     "reads .conn"),
    ("def f(resolved):\n    for a in resolved.attempts:\n        a.conn\n",
     "reads .conn"),
    ("def f(resolved: ResolvedInference):\n    return resolved.conn\n",
     "reads .conn"),
    ("def f(x: 'UsableInference'):\n    return x.conn\n",
     "reads .conn"),
    ("def f(a: resolved.Attempt):\n    return a.conn\n",
     "reads .conn"),
    (("def f(resolved):\n    for stage in stages(resolved, 'decide'):\n"
     "        stage.conn\n"),
     "reads .conn"),
    ("Stage('native', chain, None).conn\n",
     "reads .conn"),
    ("def f(call):\n    return call.conn\n",
     "reads .conn"),
    ("def f(call):\n    return replace(call, items=[]).conn\n",
     "reads .conn"),
    ("require_inference('chat').conn\n",
     "reads .conn"),
    ("conn['_fallback']\n",
     "spells the dict key '_fallback'"),
    ("conn.get('_structured')\n",
     "spells the dict key '_structured'"),
    ("block = conn['_account']\n",
     "spells the dict key '_account'"),
    ("{'_degrade': True}\n",
     "spells the dict key '_degrade'"),
)


#: Plants that only count in one module: `(module, source, expected)`. The
#: relative import is how `store/inference/controls.py` reached the lowering
#: before slice I deleted it; the bare `lower` is how the resolver itself did.
PLACED_PLANTS: tuple[tuple[str, str, str], ...] = (
    ("backend/src/grimoire/store/inference/controls.py",
     "from . import capabilities, resolve\nresolve.lower(conn, {}, model)\n",
     "reads resolve.lower"),
    ("backend/src/grimoire/store/inference/settings.py",
     "from . import resolve as inf\ninf.lower(conn, {}, model)\n",
     "reads inf.lower"),
    ("backend/src/grimoire/routes/config.py",
     "x = store.inference.resolve.lower(raw, sampling)\n",
     "reads resolve.lower"),
    (RESOLVER, "def lower(raw, sampling, model=None):\n    return raw\n",
     "defines lower in the resolver"),
    (RESOLVER, "def own(conn):\n    return lower(conn, {})\n",
     "calls lower in the resolver"),
)


def test_the_guard_flags_a_planted_lowering():
    for source, expected in PLANTS:
        hits = scan(source, "planted.py")
        assert any(expected in hit for hit in hits), (source, hits)
    for rel, source, expected in PLACED_PLANTS:
        hits = scan(source, rel)
        assert any(expected in hit for hit in hits), (rel, source, hits)


def test_a_lower_outside_the_resolver_is_not_the_lowering():
    """A `def lower` or a bare `lower(...)` elsewhere is some other `lower`;
    only the resolver's own module once held this one."""
    source = "def lower(x):\n    return x\nlower(1)\n"
    assert scan(source, "backend/src/grimoire/store/inference/controls.py") == []


def test_the_guard_passes_what_is_not_the_lowering():
    """A `.conn` off anything that is not a carrier, a `str.lower()`, and a
    key that merely starts the same way are not the lowering."""
    clean = ("def f(holder, text):\n"
             "    holder.conn\n"
             "    text.lower()\n"
             "    x = {'_estimate': 1, '_fallbacks': 2}\n"
             "    chain.fallback\n")
    assert scan(clean, "clean.py") == []
