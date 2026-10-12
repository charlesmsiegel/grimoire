"""No model writes `knowledge.json` -- only the user, and the delete hooks.

`store/knowledge.py` holds the user's word on who knows what (roadmap 11 §7),
and it beats every derived classification. That only stays true while nothing
derived can write it: an absorb pass "tidying" a statement, a continuity sweep
recording one, or an investigation tool filing what it concluded would each
turn a model's guess into an authoritative override with every individual
call looking reasonable. So the line is a test, modelled on
`test_absorb_writer_guard.py`, resolving import bindings rather than comparing
spellings.

The writers, each for a reason that is not a model's:

- `routes/ledger.py` -- the hand edits, journalled as ``manual``;
- `store/knowledge.py` itself;
- `store/overlay.py` -- `forget_audience`, beside an actor or group delete
  or a campaign's group reclassify;
- `store/reclassify.py` -- `forget_audience`, for a World group reclassify;
- `store/undo.py` -- `restore`, putting back a journalled hand edit;
- `store/branch.py` -- `copy_for_branch` and `drop_scene`, which carry the
  user's statements to a branch and drop a failed one's.

`store/context/`, `store/absorb/`, `store/continuity/` and 12's investigation
package are named in the spec as the places a call must never appear; the
allowlist is the stronger form of that, since it covers every other module too.
"""

from __future__ import annotations

import ast
import pathlib

import grimoire

PACKAGE = pathlib.Path(grimoire.__file__).parent

#: The mutators, all of them.
MUTATORS = frozenset({"add", "update", "remove", "restore", "forget_audience",
                      "copy_for_branch", "drop_scene"})

#: Which module may call which mutator, by path inside the package.
ALLOWED: dict[str, frozenset[str]] = {
    "routes/ledger.py": frozenset({"add", "update", "remove"}),
    "store/knowledge.py": MUTATORS,
    "store/overlay.py": frozenset({"forget_audience"}),
    "store/reclassify.py": frozenset({"forget_audience"}),
    "store/undo.py": frozenset({"restore"}),
    "store/branch.py": frozenset({"copy_for_branch", "drop_scene"}),
}

STORE_KNOWLEDGE = "grimoire.store.knowledge"


def _is_knowledge_module(node: ast.ImportFrom) -> bool:
    """`from <X> import <name>` where X IS store.knowledge."""
    mod = node.module or ""
    if node.level:
        return mod == "knowledge" or mod.endswith(".knowledge")
    return mod == STORE_KNOWLEDGE


def _imports_knowledge_itself(node: ast.ImportFrom) -> bool:
    """`from <X> import knowledge` where X is the store package."""
    mod = node.module or ""
    if node.level:
        return mod in ("", "store") or mod.endswith(".store")
    return mod in ("grimoire.store", "store")


def _bindings(tree: ast.AST) -> tuple[dict[str, str], set[str]]:
    """What `store.knowledge` is called in this file: (function names, module
    names). An aliased function or module maps back to the original."""
    names: dict[str, str] = {}
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if _is_knowledge_module(node):
                for a in node.names:
                    names[a.asname or a.name] = a.name
            elif _imports_knowledge_itself(node):
                modules |= {a.asname or "knowledge" for a in node.names if a.name == "knowledge"}
        elif isinstance(node, ast.Import):
            modules |= {a.asname or STORE_KNOWLEDGE for a in node.names
                        if a.name == STORE_KNOWLEDGE}
    return names, modules


def _calls(tree: ast.AST):
    """Calls that reach `store.knowledge`: (line, original name, source).

    `store.knowledge.add(...)` through the facade is caught by its tail, since
    `routes/` reaches the store as `store.<module>`."""
    names, modules = _bindings(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if isinstance(fn, ast.Attribute):
            owner = ast.unparse(fn.value)
            if owner in modules or owner.endswith("store.knowledge"):
                yield node.lineno, fn.attr, ast.unparse(fn)
        elif isinstance(fn, ast.Name) and fn.id in names:
            yield node.lineno, names[fn.id], fn.id


def _offenders(rel: str, tree: ast.AST) -> list[str]:
    allowed = ALLOWED.get(rel, frozenset())
    return [f"{rel}:{line} calls {rendered}" for line, name, rendered in _calls(tree)
            if name in MUTATORS and name not in allowed]


def test_only_the_listed_writers_call_a_knowledge_mutator():
    offenders = []
    for path in sorted(PACKAGE.rglob("*.py")):
        rel = path.relative_to(PACKAGE).as_posix()
        offenders += _offenders(rel, ast.parse(path.read_text(encoding="utf-8")))
    assert not offenders, (
        "knowledge.json is the user's word; only the ledger routes, the delete "
        "hooks, undo and the branch copy may write it:\n  " + "\n  ".join(offenders))


def test_the_guard_names_functions_that_exist():
    from grimoire.store import knowledge
    missing = [n for n in MUTATORS if not callable(getattr(knowledge, n, None))]
    assert not missing, f"MUTATORS names functions store.knowledge no longer has: {missing}"


def test_every_allowlisted_module_exists():
    missing = [rel for rel in ALLOWED if not (PACKAGE / rel).exists()]
    assert not missing, f"ALLOWED names modules that no longer exist: {missing}"


def test_the_delete_hooks_and_undo_really_call_what_they_are_allowed():
    """The allowlist is not a dead letter: each listed writer uses it."""
    used: dict[str, set[str]] = {}
    for rel in ("store/overlay.py", "store/undo.py", "store/branch.py"):
        tree = ast.parse((PACKAGE / rel).read_text(encoding="utf-8"))
        used[rel] = {name for _l, name, _r in _calls(tree)}
    assert "forget_audience" in used["store/overlay.py"]
    assert "restore" in used["store/undo.py"]
    assert {"copy_for_branch", "drop_scene"} <= used["store/branch.py"]


def _planted(rel: str, src: str) -> list[str]:
    return _offenders(rel, ast.parse(src))


def test_a_call_planted_in_absorb_is_caught_in_every_spelling():
    rel = "store/absorb/apply.py"
    assert _planted(rel, "from .. import knowledge\n"
                         "def go(c, e):\n    knowledge.add(c, e)\n")
    assert _planted(rel, "from ..knowledge import add as file_it\n"
                         "def go(c, e):\n    file_it(c, e)\n")
    assert _planted(rel, "from .. import knowledge as kn\n"
                         "def go(c, r):\n    kn.forget_audience(c, r)\n")
    assert _planted(rel, "import grimoire.store.knowledge\n"
                         "def go(c, k):\n    grimoire.store.knowledge.remove(c, k)\n")
    assert _planted("store/context/epistemic.py",
                    "from .. import knowledge\ndef go(c, k, v):\n    knowledge.restore(c, k, v)\n")
    assert _planted("routes/scenes.py",
                    "from .. import store\ndef go(c, e):\n    store.knowledge.add(c, e)\n")


def test_a_reader_is_not_an_offender():
    assert not _planted("store/context/epistemic.py",
                        "from .. import knowledge\n"
                        "def go(c):\n    return knowledge.read_checked(c)\n")
    # An allowlisted writer calling a mutator it was not given is still caught.
    assert _planted("store/undo.py",
                    "from . import knowledge\ndef go(c, e):\n    knowledge.add(c, e)\n")
