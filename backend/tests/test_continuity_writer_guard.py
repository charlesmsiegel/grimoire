"""Continuity discovery proposes; only a reviewed decision writes.

The reconciliation sweep scores pairs, embeds texts and asks a model whether a
thread looks finished. Every one of those is a guess, and the design's line is
that no score, threshold or nearest-neighbour rank may by itself merge two
records, close a thread, resolve a commitment or create a link. What the sweep
produces goes into `continuity_candidates.json` and nowhere else; the reader
reviews each finding, and `continuity.review` (or a route acting on the
reader's request) is what applies it.

That is a rule about who may call a function, which is the kind that decays:
the discovery modules and the apply path live side by side in
`store/continuity/`, both hold the campaign lock, and nothing about
`doc.put_alias`'s signature says which side of the line it is on. So the line
is a test, modelled on `test_absorb_writer_guard.py`, and it resolves import
bindings rather than comparing spellings for the same reason that one does.

Reach:

- It scans the discovery modules in `SCANNED`. Every one must exist, or the
  guard would quietly scan nothing.
- `pending` is scanned and is read-only by design. It is what Todo's count and
  both reconcile persists read a finding's current meaning through, so a write
  routed through it would let the sweep change reviewed state while looking
  like a read -- exactly the bypass the "changes nothing but the cache" rule
  is meant to close.
- It forbids the public mutators of every store the sweep reads but may not
  change: plot and commitment records, continuity.json's aliases, links and
  suppressions (and the scene repointing that rewrites a link's scene), the
  review mutators themselves, events and scene ideas.
- It sees every load of a guarded function, called or not: the scanned
  modules pass store functions to a fail-soft `_soft(fn, fallback, *args)`
  far more often than they call one directly. A module that reached a
  store's private `_write`, or wrote the file through `atomic` by hand, is
  `test_atomic_guard.py`'s and `test_lock_domain_guard.py`'s business, not
  this one's.
"""

from __future__ import annotations

import ast
import importlib
import pathlib
from collections.abc import Iterator

import grimoire.store.continuity as continuity_pkg

CONTINUITY = pathlib.Path(continuity_pkg.__file__).parent

#: The package every scanned module sits in, which relative imports resolve
#: against.
PACKAGE = "grimoire.store.continuity"

#: The discovery modules: they may read everything below and write none of it.
SCANNED = ("similarity", "identity", "reconcile", "pending", "pressure", "drivers", "graph")

#: Every scanned module: one missing means a rename the guard would otherwise
#: follow into scanning nothing.
REQUIRED_PRESENT = {"similarity", "identity", "reconcile", "pending", "pressure", "drivers",
                    "graph"}

#: Mutators the discovery modules may not reach, by absolute module. Matching
#: is exact on the module, never on its tail: `doc` is a common word.
FORBIDDEN: dict[str, frozenset[str]] = {
    "grimoire.store.plot": frozenset({
        "set_movement", "restore", "forget_scene", "repoint_scenes"}),
    "grimoire.store.commitments": frozenset({
        "set_movement", "restore", "forget_scene", "repoint_scenes"}),
    "grimoire.store.continuity.doc": frozenset({
        "put_alias", "drop_alias", "put_link", "drop_link",
        "restore_alias", "restore_link",
        "restore_alias_snapshot", "restore_link_snapshot",
        "repoint_scenes", "put_suppression", "drop_suppression"}),
    "grimoire.store.continuity.review": frozenset({
        "create_alias", "remove_alias", "create_link", "remove_link", "forget_ref",
        "settle", "dismiss", "restore_suppression"}),
    "grimoire.store.events": frozenset({"create", "update", "delete", "fire", "unfire"}),
    "grimoire.store.scene_ideas": frozenset({"add", "set_status", "mark_used", "repoint_scenes"}),
}


def _absolute(node: ast.ImportFrom, package: str) -> str:
    """The module an `ImportFrom` names, with `level` resolved against `package`."""
    if not node.level:
        return node.module or ""
    parts = package.split(".")
    base = parts[:len(parts) - (node.level - 1)]
    return ".".join(base + ([node.module] if node.module else []))


def _bindings(tree: ast.AST, package: str) -> tuple[dict[str, tuple[str, str]], dict[str, str]]:
    """What each local name is bound to: (function names, module names).

    `from X import Y` cannot say from the syntax whether Y is a submodule or a
    function of X, so it is recorded both ways: as the function `(X, Y)` and
    as the module `X.Y`. Only one of them can ever match `FORBIDDEN`.
    """
    names: dict[str, tuple[str, str]] = {}
    modules: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            source = _absolute(node, package)
            for a in node.names:
                local = a.asname or a.name
                names[local] = (source, a.name)
                modules[local] = f"{source}.{a.name}" if source else a.name
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.asname:
                    modules[a.asname] = a.name
                else:
                    # `import a.b.c` binds `a`; `a.b.c.fn` resolves through it.
                    head = a.name.split(".", 1)[0]
                    modules[head] = head
    return names, modules


def _resolve(expr: str, modules: dict[str, str]) -> str | None:
    """The absolute module a dotted expression names, through its head binding."""
    head, _, rest = expr.partition(".")
    if head not in modules:
        return None
    return f"{modules[head]}.{rest}" if rest else modules[head]


def _references(tree: ast.AST, package: str) -> Iterator[tuple[int, str, str, str]]:
    """Every load that may name a module's function, as (line, module, function, source).

    A load, not only a call: reconcile, pressure and drivers reach nearly every
    store function through a fail-soft helper -- `_soft(events.list_events,
    None, cid, ...)` -- where the guarded function is an argument and the
    callee is the helper. Copying that line with `events.fire` in place of
    `events.list_events` is the most natural way to add a write, so the scan
    has to see a function wherever it is named, called or not.
    """
    names, modules = _bindings(tree, package)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
            module = _resolve(ast.unparse(node.value), modules)
            if module is not None:
                yield node.lineno, module, node.attr, ast.unparse(node)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in names:
            module, original = names[node.id]
            yield node.lineno, module, original, node.id


def _forbidden(tree: ast.AST, package: str = PACKAGE) -> list[tuple[int, str]]:
    return [(line, rendered) for line, module, name, rendered in _references(tree, package)
            if name in FORBIDDEN.get(module, ())]


def _present() -> dict[str, pathlib.Path]:
    return {m: CONTINUITY / f"{m}.py" for m in SCANNED if (CONTINUITY / f"{m}.py").is_file()}


def test_discovery_modules_never_write_reviewed_state():
    offenders = []
    for name, path in sorted(_present().items()):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        offenders += [f"{name}.py:{line} calls {rendered}"
                      for line, rendered in _forbidden(tree)]
    assert not offenders, (
        "a continuity discovery module may only propose: record the finding in "
        "the candidates cache and let `continuity.review` apply what the reader "
        "accepts:\n  " + "\n  ".join(offenders))


def test_the_scanned_modules_exist():
    """A rename would otherwise turn the scan above into a scan of nothing."""
    missing = REQUIRED_PRESENT - set(_present())
    assert not missing, f"SCANNED names discovery modules that no longer exist: {sorted(missing)}"


def test_the_guard_names_functions_that_exist():
    """A rule about `events.fire_all` guards nothing. Renaming a mutator has to
    fail here rather than silently retire the check."""
    missing = []
    for module, fns in sorted(FORBIDDEN.items()):
        mod = importlib.import_module(module)
        missing += [f"{module}.{fn}" for fn in sorted(fns) if not callable(getattr(mod, fn, None))]
    assert not missing, f"FORBIDDEN names functions that no longer exist: {missing}"


def _found(src: str) -> list[str]:
    return [rendered for _line, rendered in _forbidden(ast.parse(src))]


def test_the_detector_catches_every_spelling_of_the_call():
    """The scan passes today, which on its own proves nothing. These are the
    ways somebody would actually make the call from inside the package."""
    assert _found("from .. import plot\ndef go(c):\n    plot.set_movement(c, 'p', 't', 's', 'b', 'x')\n") \
        == ["plot.set_movement"]
    # An aliased module, the spelling the package already uses for commitments.
    assert _found("from .. import commitments as commitments_store\n"
                  "def go(c):\n    commitments_store.restore(c, 'm', None)\n") \
        == ["commitments_store.restore"]
    assert _found("from . import doc\ndef go(c):\n    doc.put_alias(c, 'thread:a', {})\n") \
        == ["doc.put_alias"]
    # An aliased function: the local name is not the guarded one.
    assert _found("from .doc import put_alias as pa\ndef go(c):\n    pa(c, 'thread:a', {})\n") \
        == ["pa"]
    # Reaching the sibling through its package.
    assert _found("from ..continuity import doc\ndef go(c):\n    doc.drop_link(c, 'l1')\n") \
        == ["doc.drop_link"]
    assert _found("from .. import continuity\ndef go(c):\n    continuity.doc.put_suppression(c, 'f', {})\n") \
        == ["continuity.doc.put_suppression"]
    # Absolutely, which the package does not use but a new file might.
    assert _found("import grimoire.store.plot\n"
                  "def go(c):\n    grimoire.store.plot.restore(c, 'p', None)\n") \
        == ["grimoire.store.plot.restore"]
    assert _found("import grimoire.store.events as ev\ndef go(c):\n    ev.create(c, 'n', 'd')\n") \
        == ["ev.create"]
    # The two stores §11.5 names beyond the minimum list.
    assert _found("from .. import events\ndef go(c):\n    events.fire(c, ['e1'], 'now')\n") \
        == ["events.fire"]
    assert _found("from ..scene_ideas import add\ndef go(c):\n    add(c, 't', 'p')\n") == ["add"]
    # The review mutators, reached from a discovery module.
    assert _found("from . import review\ndef go(c):\n    review.create_link(c, 'a', 'b', 'related')\n") \
        == ["review.create_link"]
    # Passed by reference to a fail-soft helper, the idiom reconcile, pressure
    # and drivers use for nearly every store call: the guarded function is an
    # argument, never the callee.
    assert _found("from .. import events\ndef go(c):\n    _soft(events.fire, None, c, ['e1'], 'now')\n") \
        == ["events.fire"]
    assert _found("from . import doc\ndef go(c):\n    _soft(doc.put_alias, None, c, 'thread:a', {})\n") \
        == ["doc.put_alias"]
    assert _found("from .doc import put_alias\ndef go(c):\n    _soft(put_alias, None, c, 'thread:a', {})\n") \
        == ["put_alias"]
    # Bound to a local first and called later under another name.
    assert _found("from .. import plot\ndef go(c):\n    fn = plot.restore\n    fn(c, 'p', None)\n") \
        == ["plot.restore"]


def test_the_detector_does_not_flag_a_module_that_merely_looks_like_this_one():
    """`doc` and `add` are ordinary words, and a module whose name merely ends
    in one of the guarded names is not that module."""
    # A local dict called `doc`, never imported.
    assert _found("def go(c):\n    doc = {'put_alias': print}\n"
                  "    doc['put_alias'](c)\n    doc.get('put_alias')\n") == []
    # The cache is exactly what discovery is supposed to write.
    assert _found("from . import candidates\ndef go(c):\n    candidates.write(c, {})\n") == []
    # Reading the guarded stores is the point of discovery.
    assert _found("from . import doc\ndef go(c):\n    doc.read(c)\n") == []
    assert _found("from .. import plot\ndef go(c):\n    plot.read(c)\n") == []
    # A different module whose tail is `doc`.
    assert _found("from other import doc\ndef go(c):\n    doc.put_alias(c, 'x', {})\n") == []
    assert _found("from other.doc import put_alias\ndef go(c):\n    put_alias(c, 'x', {})\n") == []
    # A local function that happens to share a guarded name.
    assert _found("def add(c):\n    pass\ndef go(c):\n    add(c)\n") == []
    # A reader passed by reference is what the fail-soft helpers are for.
    assert _found("from .. import events\ndef go(c):\n    _soft(events.list_events, None, c)\n") == []


def test_review_still_uses_the_vocabulary_it_is_supposed_to():
    """The other half, so the scan cannot be satisfied by a detector that has
    stopped seeing calls: `review` (not scanned) is where reviewed aliases and
    links are written, and the detector has to see it doing so."""
    tree = ast.parse((CONTINUITY / "review.py").read_text(encoding="utf-8"))
    used = {(module, fn) for _line, module, fn, _r in _references(tree, PACKAGE)
            if fn in FORBIDDEN.get(module, ())}
    expected = {("grimoire.store.continuity.doc", "put_alias"),
                ("grimoire.store.continuity.doc", "put_link")}
    assert expected <= used, f"review should write aliases and links through doc; it uses {sorted(used)}"
