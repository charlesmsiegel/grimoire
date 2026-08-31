"""The absorb pass never edits or deletes a fact -- the user does.

`store/facts.py` argues the rule at length: a fact that stops being true is
retired and pointed at its replacement, because a ledger that rewrites its own
rows is `state.md` with extra keys and loses the history it exists to keep. That
holds for the *pass*. It never held for the person whose campaign it is, who has
always been entitled to fix a typo, and `facts.set_text` is theirs.

A rule about who may call a function is exactly the kind that decays: the
correction path and the extraction path both live in this codebase, both hold
the campaign lock, and nothing about `set_text`'s signature says which side of
the line it is on. So the line is a test. A materializer that reached for
`set_text` to "tidy" an extracted fact, or for `forget` to drop one it decided
was wrong, would be quietly converting the ledger into a snapshot -- with every
individual call looking reasonable.

Deliberately narrow: it names two functions and one directory. It is not a
theory of which writes are the model's to make, and adding a third fact mutator
means adding it here on purpose.
"""

from __future__ import annotations

import ast
import pathlib

import grimoire.store.absorb as absorb_pkg

ABSORB = pathlib.Path(absorb_pkg.__file__).parent

#: Fact mutators reserved to a hand edit. Retirement and supersession are
#: absent on purpose: those ARE the pass's vocabulary for a fact that stopped
#: being true, and `apply.py` is supposed to call them.
USER_ONLY = frozenset({"set_text", "forget"})


#: The module the rule is about, spelled absolutely. A relative import is
#: matched on its tail instead, because that is what the package uses.
STORE_FACTS = "grimoire.store.facts"


def _is_facts_module(node: ast.ImportFrom) -> bool:
    """`from <X> import <name>` where X IS store.facts."""
    mod = node.module or ""
    if node.level:                                      # relative: `..facts`
        return mod == "facts" or mod.endswith(".facts")
    return mod == STORE_FACTS


def _imports_facts_itself(node: ast.ImportFrom) -> bool:
    """`from <X> import facts` where X is the store package."""
    mod = node.module or ""
    return bool(node.level) or mod in ("grimoire.store", "store")


def _bindings(tree: ast.AST) -> tuple[dict[str, str], set[str]]:
    """What `store.facts` is called in this file: (function names, module names).

    Bindings are resolved rather than spellings compared, which the first
    version of this did and three rewrites walked straight past:

    - `from ..facts import set_text as revise` — the alias has to map back to
      `set_text`, or renaming the import defeats the guard.
    - `from .. import facts as ledger_facts` — the module alias has to be
      tracked, or renaming the import does.
    - `from other.facts import forget` — a module merely ENDING in "facts" is
      not this one, and flagging it is a false positive that would eventually
      be silenced with a marker and take the real check with it.
    """
    names: dict[str, str] = {}          # local name -> its name in store.facts
    modules: set[str] = set()           # local names bound to the module itself
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if _is_facts_module(node):
                for a in node.names:
                    names[a.asname or a.name] = a.name
            if _imports_facts_itself(node):
                modules |= {a.asname or "facts" for a in node.names if a.name == "facts"}
        elif isinstance(node, ast.Import):
            modules |= {a.asname or STORE_FACTS for a in node.names if a.name == STORE_FACTS}
    return names, modules


def _fact_calls(tree: ast.AST):
    """Calls that reach `store.facts`, as (line, ORIGINAL function name, source)."""
    names, modules = _bindings(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if isinstance(fn, ast.Attribute) and ast.unparse(fn.value) in modules:
            yield node.lineno, fn.attr, ast.unparse(fn)
        elif isinstance(fn, ast.Name) and fn.id in names:
            yield node.lineno, names[fn.id], fn.id


def test_the_absorb_pass_never_edits_or_forgets_a_fact():
    offenders = []
    for path in sorted(ABSORB.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for line, name, rendered in _fact_calls(tree):
            if name in USER_ONLY:
                offenders.append(f"{path.name}:{line} calls {rendered}")
    assert not offenders, (
        "the absorb pass may not edit or delete a fact -- retire it, or "
        "supersede it with `facts.record(..., supersedes=...)`:\n  "
        + "\n  ".join(offenders))


def test_the_guard_names_functions_that_exist():
    """A rule about `facts.tidy_up` guards nothing. Renaming a mutator has to
    fail here rather than silently retire the check."""
    from grimoire.store import facts
    missing = [n for n in USER_ONLY if not callable(getattr(facts, n, None))]
    assert not missing, f"USER_ONLY names functions store.facts no longer has: {missing}"


def test_the_pass_still_uses_the_vocabulary_it_is_supposed_to():
    """The other half, so the guard above cannot be satisfied by an absorb pass
    that stopped writing facts at all."""
    used = set()
    for path in sorted(ABSORB.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for _line, name, _rendered in _fact_calls(tree):
            if name in {"record", "retire"}:
                used.add(name)
    assert used == {"record", "retire"}, (
        f"the absorb pass should record and retire facts; it uses {sorted(used)}")


def _found(src: str) -> list[str]:
    return [n for _l, n, _r in _fact_calls(ast.parse(src))]


def test_the_detector_catches_every_spelling_of_the_call():
    """The guard above passes today, which on its own proves nothing. These are
    the ways somebody would actually make the call — including the two renames
    that walked past the first version of this detector."""
    assert _found("from . import facts\ndef go(c):\n    facts.set_text(c, 'f1', 'x')\n") \
        == ["set_text"]
    assert _found("from ..facts import forget\ndef go(c):\n    forget(c, 'f1')\n") \
        == ["forget"]
    # An aliased function: the local name is not the guarded one.
    assert _found("from ..facts import set_text as revise\ndef go(c):\n    revise(c, 'f', 'x')\n") \
        == ["set_text"]
    # An aliased MODULE: the attribute is guarded but the object is renamed.
    assert _found("from .. import facts as fx\ndef go(c):\n    fx.forget(c, 'f1')\n") \
        == ["forget"]
    # And absolutely, which the package does not use but a new file might.
    assert _found("import grimoire.store.facts\n"
                  "def go(c):\n    grimoire.store.facts.set_text(c, 'f', 'x')\n") == ["set_text"]


def test_the_detector_does_not_flag_a_module_that_merely_looks_like_this_one():
    """`forget` is an ordinary word: `chronicle.forget` is a call the pass is
    entitled to make, and a module whose name happens to end in "facts" is not
    this module. A guard that reads the spelling alone banned both."""
    assert _found("from . import chronicle\ndef go(c):\n    chronicle.forget(c, 's1')\n") == []
    assert _found("from other.facts import forget\ndef go(c):\n    forget(c, 'f1')\n") == []
    assert _found("def forget(c):\n    pass\ndef go(c):\n    forget(c)\n") == []
