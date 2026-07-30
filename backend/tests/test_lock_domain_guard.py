"""Guard: lock-domain membership is declared in code, not in a paragraph.

``store/locks.py`` opens by saying "**This list is the domain**". That was
literally true and is the bug: the domain was a prose list a human had to
remember to update, so a campaign-scoped mutator could be written, reviewed and
shipped entirely outside the exclusion without anything failing. It happened —
``rolls`` kept a private lock registry and was outside the shared domain until
#255, and the docstring itself admits four more (``campaigns.rename_campaign``
/ ``set_campaign_response`` / ``touch`` / ``delete_campaign``). Nothing detected
either omission, because nothing could: prose does not fail a test run.

This guard moves the domain into ``locks.DOMAIN_MODULES`` (its public mutators
all serialize), ``locks.OUTSIDE_DOMAIN`` (deliberately not, with the reason) and
``locks.UNREVIEWED`` (mutates campaign state, never assessed — a frozen
backlog), then holds those declarations and the code to each other:

- a module in ``DOMAIN_MODULES`` must have *every* public campaign-scoped
  mutator serialize, so a new ``scenes`` mutator that forgets ``@_serialized``
  fails — the transcript-loss case of #254;
- a module that mutates campaign state and appears in none of the three fails
  until somebody classifies it, and ``UNREVIEWED`` may not grow. This is the
  #255 shape, and the only check here that would have caught it;
- a module declared outside must still actually have an unserialized mutator, so
  the moment one is fixed the declaration has to move rather than quietly
  becoming a lie;
- a declared module that stops mutating campaign state has to be dropped, so the
  lists cannot accumulate phantoms.

What it deliberately does not do is *fix* anything. The four mutators
``locks.py`` used to name in prose are still unserialized; they are now recorded
in ``OUTSIDE_DOMAIN`` with the reason, where a test watches them, instead of in
a paragraph where nothing did.

Honest about its reach — the house standard set by ``test_atomic_guard.py``:

- **"Campaign-scoped" is approximated by a ``cid`` parameter.** That is the
  codebase's own convention, not a proof. A mutator that derives the campaign id
  some other way (from a path, a scene record, a request object) is not seen.
- **Serialization is recognized syntactically**, through module-local
  decorators, context managers and delegation. A lock acquired through a
  dynamic or cross-module indirection this walker cannot follow reads as
  *absent*, which fails loud rather than silent — the marker is the remedy.
- **It checks that a lock is taken, never that it is taken widely enough.** A
  read-modify-write whose read sits outside the ``with`` is exactly the bug
  ``scenes._serialized`` was written to fix ("The lock has to span the READ as
  well as the write"), and it is invisible here: the function locks, so it
  passes. Likewise two individually-locked calls made non-atomically.
- **It does not decide which list a module belongs in.** Classification is a
  human judgment about whether that state can lose an update; the guard only
  insists the judgment be written down, stay true, and be revisited when the
  code moves underneath it.
"""

from __future__ import annotations

import ast
import pathlib

import grimoire
from grimoire.store import locks

from . import guard_markers

PACKAGE = pathlib.Path(grimoire.__file__).parent

# The trailing colon is load-bearing -- see test_pydantic_guard.MARKER.
MARKER = "lock-domain-ok:"

# Publication primitives. Kept in step with `test_atomic_guard._PATH_WRITERS`:
# that guard proves every write goes through `store.atomic`, which is what lets
# this one watch a two-function helper surface instead of every `open()` mode.
_ATOMIC_WRITERS = ("write_text", "write_bytes")
# Removal and rename publish a change without writing bytes, so a guard that
# watched only `atomic.*` would miss `delete_campaign`'s `rmtree` entirely.
_DESTRUCTIVE = ("rmtree", "unlink", "rmdir", "remove", "rename", "replace")
# The two entry points into the domain. `hold_all` counts because the
# multi-campaign holders reach every campaign lock through it.
_LOCK_CALLS = ("campaign_lock", "hold_all")


def _called_name(func: ast.expr) -> str | None:
    """The trailing name of a call target: `f`, `m.f` and `pkg.m.f` all give `f`.

    Receiver-agnostic on purpose. `locks.campaign_lock`, `store.locks.
    campaign_lock` and a bare `campaign_lock` are the same acquisition, and
    resolving the receiver statically buys nothing here -- no other name in this
    package is `campaign_lock`.
    """
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _receiver_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Attribute):
        value = func.value
        if isinstance(value, ast.Name):
            return value.id
        if isinstance(value, ast.Attribute):
            return value.attr
    return None


def _functions(tree: ast.AST) -> dict[str, ast.AST]:
    """Every function in the module by name, nested ones included.

    A dict keyed by name cannot represent two functions sharing one name
    (a conditional redefinition, a method matching a module-level name). The
    collision is resolved toward the *first* definition rather than the last,
    because callers in this package reach module-level helpers, and letting a
    class method shadow `_write` would silently redirect the whole analysis.
    """
    out: dict[str, ast.AST] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.setdefault(node.name, node)
    return out


def _calls(fn: ast.AST):
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            yield node


def _takes_cid(fn: ast.AST) -> bool:
    """The codebase's convention for "this operates on one campaign"."""
    args = fn.args
    return any(a.arg == "cid"
               for a in [*args.posonlyargs, *args.args, *args.kwonlyargs])


def _writes_directly(fn: ast.AST) -> bool:
    for node in _calls(fn):
        name = _called_name(node.func)
        if name in _ATOMIC_WRITERS and _receiver_name(node.func) == "atomic":
            return True
        if name in _DESTRUCTIVE and isinstance(node.func, ast.Attribute):
            return True
    return False


def _locks_directly(fn: ast.AST) -> bool:
    return any(_called_name(c.func) in _LOCK_CALLS for c in _calls(fn))


def _with_context_names(fn: ast.AST) -> set[str]:
    """Names called as context managers anywhere in `fn`: the `f` of `with f(x)`.

    Entering a locking context manager is not the same as delegating to a locked
    function, and the difference decides whether the caller's OWN writes are
    covered. `with _lock(cid): atomic.write_text(...)` serializes that write;
    `_locked_helper(cid); atomic.write_text(...)` does not serialize anything.
    """
    out: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                expr = item.context_expr
                if isinstance(expr, ast.Call):
                    name = _called_name(expr.func)
                    if name is not None:
                        out.add(name)
    return out


def _serializing(funcs: dict[str, ast.AST]) -> set[str]:
    """Names whose bodies establish the campaign lock.

    Three forms, all present in the package today: a direct
    ``with locks.campaign_lock(cid)``; a module-local alias like
    ``audit._lock``; and a decorator that wraps the body in one, which is how
    every ``scenes`` mutator does it (``@_serialized``).

    Delegation counts too -- ``scenes.create_scene`` does its work in the
    ``@_serialized`` ``_create_scene`` -- but *only* for a function that does
    not also publish something itself. A function that calls a locked helper and
    writes on its own has an unserialized write, and treating that as covered is
    the exact hole this guard exists to close.
    """
    out = {name for name, fn in funcs.items() if _locks_directly(fn)}
    for _ in range(len(funcs) + 1):
        grown = set(out)
        for name, fn in funcs.items():
            if name in grown:
                continue
            if any(_called_name(d) in grown for d in fn.decorator_list):
                grown.add(name)                       # @_serialized and friends
            elif _with_context_names(fn) & grown:
                grown.add(name)                       # with _lock(cid): ...
            elif not _writes_directly(fn) and any(
                    _called_name(c.func) in grown for c in _calls(fn)):
                grown.add(name)                       # pure delegation
        if grown == out:
            break
        out = grown
    return out


def _mutators(funcs: dict[str, ast.AST], serializing: set[str]) -> set[str]:
    """Names that publish a change to campaign state.

    Propagation stops at a serializing callee: it is an atomic unit, so calling
    it does not make the caller a mutation site in its own right. Without that,
    every thin wrapper over a locked helper reads as an unlocked mutator and the
    guard drowns in false positives.
    """
    out = {name for name, fn in funcs.items() if _writes_directly(fn)}
    for _ in range(len(funcs) + 1):
        grown = set(out)
        for name, fn in funcs.items():
            if name in grown:
                continue
            for call in _calls(fn):
                callee = _called_name(call.func)
                if callee in grown and callee not in serializing:
                    grown.add(name)
                    break
        if grown == out:
            break
        out = grown
    return out


def _module_name(path: pathlib.Path) -> str:
    rel = path.relative_to(PACKAGE).with_suffix("")
    return ".".join(rel.parts)


def _analyze(path: pathlib.Path):
    """(unserialized mutator names, every campaign mutator name) for one file."""
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    funcs = _functions(tree)
    serializing = _serializing(funcs)
    mutators = _mutators(funcs, serializing)

    campaign_mutators, unserialized = set(), set()
    for name in mutators:
        fn = funcs[name]
        if not _takes_cid(fn):
            continue
        if name.startswith("_"):
            # A private helper is not its own domain member: it runs under
            # whatever its callers hold, which is how `rolls._write`,
            # `proposals._write` and `sheets._set_field_locked` are written.
            # This is not a hole, because propagation already charged the
            # helper's mutation to every unlocked *public* caller in the module
            # -- so forgetting the lock still fails, it just fails at the
            # caller. What it does miss is a private helper reached from
            # another module through an unlocked path.
            continue
        campaign_mutators.add(name)
        if name in serializing:
            continue
        if guard_markers.marker_reason(MARKER, src, fn) is not None:
            continue
        unserialized.add(name)
    return unserialized, campaign_mutators


def _survey() -> dict[str, tuple[set[str], set[str]]]:
    out = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        if path.name == "locks.py":
            continue                    # the lock itself is not a lock taker
        unserialized, mutators = _analyze(path)
        if mutators:
            out[_module_name(path)] = (unserialized, mutators)
    return out


def _declared() -> set[str]:
    return set(locks.DOMAIN_MODULES) | set(locks.OUTSIDE_DOMAIN) | set(locks.UNREVIEWED)


def test_every_campaign_mutating_module_is_classified():
    """The #255 check: a module can no longer mutate campaign state without
    somebody deciding, in code, whether it belongs in the exclusion.

    Before #255 ``rolls`` ran a private lock registry and was outside the shared
    domain. Nothing failed, because the domain was a sentence. This is the test
    that fails."""
    unclassified = sorted(set(_survey()) - _declared())
    assert not unclassified, (
        "module(s) mutating campaign state that the lock domain does not "
        "classify — add each to `locks.DOMAIN_MODULES` (its public mutators "
        "take `campaign_lock`) or to `locks.OUTSIDE_DOMAIN` with the reason "
        "they deliberately do not. `locks.UNREVIEWED` is a frozen backlog and "
        "is not open for new entries:\n  " + "\n  ".join(unclassified))


def test_the_unreviewed_backlog_only_shrinks():
    """A third bucket is a temptation: it takes an entry with no reason. It is
    allowed to exist only because it cannot grow — every module in it predates
    this guard, and a new mutator has to be classified for real."""
    assert len(locks.UNREVIEWED) <= 19, (
        f"`locks.UNREVIEWED` has grown to {len(locks.UNREVIEWED)}; it is a "
        "frozen backlog of modules that predate this guard. Classify the new "
        "module into DOMAIN_MODULES or OUTSIDE_DOMAIN instead.")


def test_no_module_is_declared_twice():
    counts = [len(locks.DOMAIN_MODULES), len(locks.OUTSIDE_DOMAIN), len(locks.UNREVIEWED)]
    assert sum(counts) == len(_declared()), (
        "a module appears in more than one of DOMAIN_MODULES / OUTSIDE_DOMAIN "
        "/ UNREVIEWED; the three are meant to partition the mutating modules")


def test_the_declaration_has_no_phantom_modules():
    """A declared module that no longer mutates campaign state (renamed,
    deleted, refactored) is stale documentation with a test guarding it, which
    is worse than none."""
    surveyed = set(_survey())
    phantom = sorted(_declared() - surveyed)
    assert not phantom, (
        "declared in the lock domain but no longer a campaign-mutating module "
        "— drop the entry:\n  " + "\n  ".join(phantom))


def test_every_mutator_in_a_domain_module_serializes():
    """The #254 check: a new ``scenes`` mutator that forgets ``@_serialized`` is
    a silent lost update on a transcript, the one artifact here that cannot be
    regenerated."""
    survey = _survey()
    offenders = []
    for module in sorted(locks.DOMAIN_MODULES):
        unserialized, _ = survey.get(module, (set(), set()))
        offenders += [f"{module}.{name}" for name in sorted(unserialized)]
    assert not offenders, (
        "campaign-scoped mutator(s) in a lock-domain module that do not take "
        "`locks.campaign_lock(cid)` — take it, or annotate the function with "
        f"`# {MARKER} <why this one is safe>`:\n  " + "\n  ".join(offenders))


def test_modules_declared_outside_are_really_outside():
    """A declaration that has quietly become true again is worse than none: it
    reads as a known hole long after the hole was filled, and the next reader
    trusts it. If a module's mutators all serialize now, it belongs in
    ``DOMAIN_MODULES``."""
    survey = _survey()
    stale = []
    for module in sorted(set(locks.OUTSIDE_DOMAIN) | set(locks.UNREVIEWED)):
        unserialized, _ = survey.get(module, (set(), set()))
        if not unserialized:
            stale.append(f"{module}: every public mutator serializes now — promote it")
    assert not stale, (
        "lock-domain entries that no longer describe the code:\n  "
        + "\n  ".join(stale))


def test_every_outside_entry_states_a_reason():
    """The same rule the atomic guard applies to its marker: an exemption with
    no reason is a rubber stamp, and a growing pile of them means the domain is
    being routed around rather than applied."""
    thin = sorted(m for m, why in locks.OUTSIDE_DOMAIN.items() if len(why) < 30)
    assert not thin, f"`OUTSIDE_DOMAIN` entries with no real reason: {thin}"


def test_the_marker_is_not_a_rubber_stamp():
    marked = []
    for path in sorted(PACKAGE.rglob("*.py")):
        if path.name == "locks.py":
            continue
        src = path.read_text(encoding="utf-8")
        funcs = _functions(ast.parse(src))
        for name, fn in funcs.items():
            reason = guard_markers.marker_reason(MARKER, src, fn)
            if reason is not None:
                marked.append((f"{_module_name(path)}.{name}", reason))
    unexplained = [loc for loc, reason in marked if len(reason) < 15]
    assert not unexplained, f"`{MARKER}` with no real reason: {unexplained}"
    assert len(marked) <= 3, (
        f"{len(marked)} {MARKER} exemptions; each is a hole in the exclusion, "
        f"so they need review rather than a raised limit: {marked}")


# --- the guard can fail ------------------------------------------------------
# "A guard that cannot fail is worse than none -- it reads as coverage."
# (test_atomic_guard.py)

def _probe(src: str):
    tree = ast.parse(src)
    funcs = _functions(tree)
    serializing = _serializing(funcs)
    return funcs, serializing, _mutators(funcs, serializing)


def test_the_guard_detects_an_unlocked_mutator():
    src = ("def touch(cid):\n"
           "    meta = read(p)\n"
           "    atomic.write_text(p, meta)\n")
    funcs, serializing, mutators = _probe(src)
    assert mutators == {"touch"}
    assert "touch" not in serializing
    assert _takes_cid(funcs["touch"])


def test_the_guard_accepts_a_directly_locked_mutator():
    src = ("def append(cid):\n"
           "    with locks.campaign_lock(cid):\n"
           "        atomic.write_text(p, x)\n")
    _funcs, serializing, mutators = _probe(src)
    assert mutators == {"append"} and "append" in serializing


def test_a_decorator_that_locks_covers_what_it_wraps():
    """`scenes` serializes its whole mutator surface this way, so a guard that
    missed decorators would flag every one of them."""
    src = ("def _serialized(fn):\n"
           "    def locked(cid, *a):\n"
           "        with locks.campaign_lock(cid):\n"
           "            return fn(cid, *a)\n"
           "    return locked\n"
           "@_serialized\n"
           "def append_message(cid, text):\n"
           "    atomic.write_text(p, text)\n")
    _funcs, serializing, _mutators_ = _probe(src)
    assert "append_message" in serializing


def test_a_module_local_lock_alias_counts():
    """`audit` takes the campaign lock through its own `_lock` helper."""
    src = ("import contextlib\n"
           "@contextlib.contextmanager\n"
           "def _lock(cid):\n"
           "    with locks.campaign_lock(cid):\n"
           "        yield\n"
           "def clear_baselines(cid):\n"
           "    with _lock(cid):\n"
           "        atomic.write_text(p, '{}')\n")
    _funcs, serializing, _m = _probe(src)
    assert "clear_baselines" in serializing


def test_a_context_manager_that_locks_covers_the_bodys_own_writes():
    """The distinction `_with_context_names` exists for. `with _lock(cid):` puts
    the caller's own write under the lock; merely *calling* a locked helper
    beside an unlocked write does not (the test below)."""
    src = ("def _lock(cid):\n"
           "    return locks.campaign_lock(cid)\n"
           "def clear(cid):\n"
           "    with _lock(cid):\n"
           "        atomic.write_text(p, x)\n")
    _funcs, serializing, _m = _probe(src)
    assert "clear" in serializing


def test_pure_delegation_to_a_locked_helper_is_covered():
    """`scenes.create_scene` resolves the calendar, then hands off to the
    `@_serialized` `_create_scene`. Flagging it would be a false positive."""
    src = ("def _serialized(fn):\n"
           "    def locked(cid, *a):\n"
           "        with locks.campaign_lock(cid):\n"
           "            return fn(cid, *a)\n"
           "    return locked\n"
           "@_serialized\n"
           "def _create_scene(cid, title):\n"
           "    atomic.write_text(p, title)\n"
           "def create_scene(cid, title):\n"
           "    return _create_scene(cid, hint(title))\n")
    _funcs, serializing, _m = _probe(src)
    assert "create_scene" in serializing


def test_delegation_does_not_cover_a_write_of_the_callers_own():
    """The hole that makes "delegates to a locked helper" dangerous: the wrapper
    also publishes something itself, and that write is serialized by nothing."""
    src = ("def _serialized(fn):\n"
           "    def locked(cid, *a):\n"
           "        with locks.campaign_lock(cid):\n"
           "            return fn(cid, *a)\n"
           "    return locked\n"
           "@_serialized\n"
           "def _inner(cid):\n"
           "    atomic.write_text(p, x)\n"
           "def outer(cid):\n"
           "    _inner(cid)\n"
           "    atomic.write_text(other, y)\n")
    _funcs, serializing, mutators = _probe(src)
    assert "outer" in mutators, "the caller's own write was not seen"
    assert "outer" not in serializing, "an unserialized write read as covered"


def test_a_read_modify_write_split_across_helpers_is_caught():
    """`appearances`-shaped: neither helper locks, so the caller inherits both
    halves and is the mutation site."""
    src = ("def record(cid):\n"
           "    return json.loads(p.read_text())\n"
           "def _write(cid, data):\n"
           "    atomic.write_text(p, data)\n"
           "def pick_version(cid, v):\n"
           "    data = record(cid)\n"
           "    data['v'] = v\n"
           "    _write(cid, data)\n")
    _funcs, serializing, mutators = _probe(src)
    assert "pick_version" in mutators and "pick_version" not in serializing


def test_removal_counts_as_a_mutation():
    """`delete_campaign` publishes nothing through `atomic`; it calls `rmtree`.
    A guard watching only writers would not see it."""
    for src in ("def delete_campaign(cid):\n    shutil.rmtree(root)\n",
                "def drop(cid):\n    p.unlink()\n",
                "def rehome(cid):\n    p.rename(q)\n"):
        _funcs, _s, mutators = _probe(src)
        assert mutators, f"missed a removal/rename mutation: {src!r}"


def test_a_function_without_a_cid_is_not_campaign_scoped():
    """World- and module-scoped writers share these primitives and are not in
    this domain; without the `cid` test the guard would flag the whole store."""
    src = "def write_world(wid):\n    atomic.write_text(p, x)\n"
    funcs, _s, mutators = _probe(src)
    assert mutators == {"write_world"}
    assert not _takes_cid(funcs["write_world"])


def test_cyclic_helpers_terminate():
    """`scenes` and `overlay` both contain call cycles; a recursive walker hangs
    on either."""
    src = ("def a(cid):\n    b(cid)\n"
           "def b(cid):\n    a(cid)\n    atomic.write_text(p, x)\n")
    _funcs, _s, mutators = _probe(src)
    assert mutators == {"a", "b"}


def test_a_marker_exempts_only_its_own_function():
    src = (f"# {MARKER} runs before the campaign is reachable by anyone else\n"
           "def seed(cid):\n"
           "    atomic.write_text(p, x)\n"
           "def other(cid):\n"
           "    atomic.write_text(q, y)\n")
    funcs = _functions(ast.parse(src))
    assert guard_markers.marker_reason(MARKER, src, funcs["seed"]) is not None
    assert guard_markers.marker_reason(MARKER, src, funcs["other"]) is None
