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
- **Analysis is per-module.** Mutation propagates through a module's own
  helpers, never across an import, so a function whose only mutation happens
  inside a *different* module's unserialized mutator is not itself flagged. The
  callee is flagged in its own file instead, which is where the fix goes; what
  this misses is the caller that spans two such calls non-atomically.
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
import functools
import pathlib

import grimoire
from grimoire.store import locks

from . import guard_markers

PACKAGE = pathlib.Path(grimoire.__file__).parent
# Compared by path, not by `name == "locks.py"`: a second module with that name
# anywhere under the package would otherwise be skipped along with it.
LOCKS_PY = pathlib.Path(locks.__file__)

# The trailing colon is load-bearing -- see test_pydantic_guard.MARKER.
MARKER = "lock-domain-ok:"

# Publication primitives. Kept in step with `test_atomic_guard._PATH_WRITERS`:
# that guard proves every write goes through `store.atomic`, which is what lets
# this one watch a two-function helper surface instead of every `open()` mode.
_ATOMIC_WRITERS = ("write_text", "write_bytes")
# Removal and rename publish a change without writing bytes, so a guard that
# watched only `atomic.*` would miss `delete_campaign`'s `rmtree` entirely.
#
# Split by whether the NAME alone is evidence, because two of these are also
# ordinary methods on builtins and being receiver-blind about them was wrong:
# `doc.replace(...)` in `store/export.py` is `str.replace`, and
# `rec["scenes"].remove(sid)` in `store/appearances.py` is `list.remove`.
# Neither touches a filesystem, and counting them classified `store.export` as a
# campaign mutator on the strength of a string operation.
_FS_ONLY = ("rmtree", "unlink", "rmdir", "rename")   # no builtin type has these
_FS_AMBIGUOUS = ("remove", "replace")                # also str/list/set methods
_FS_MODULES = ("os", "shutil")                       # ...so require the receiver
# `touch` is ambiguous the other way round: `Path.touch` has no fixed receiver
# to match, so it is treated as a file creation UNLESS the receiver names a
# module in this package that defines its own `touch`. Only one does.
_TOUCH_FUNCTIONS = ("campaigns",)
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


def _functions(tree: ast.AST) -> dict[str, list[ast.AST]]:
    """Every function in the module, grouped by name, nested ones included.

    EVERY definition, not the first one. Keeping only the first discarded the
    implementation that actually runs: an `@overload` stub ahead of a writing
    body left the guard analyzing the stub, so the module read as non-mutating;
    a conditional definition whose first branch locks and whose second does not
    read as serialized. Both are silent false negatives.

    The predicates below resolve a group conservatively in the direction that
    fails loud -- a name MUTATES if any definition does, and SERIALIZES only if
    every definition does -- so an ambiguous name can never be quieter than its
    worst branch.
    """
    out: dict[str, list[ast.AST]] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.setdefault(node.name, []).append(node)
    return out


def _any(defs, predicate) -> bool:
    return any(predicate(d) for d in defs)


def _all(defs, predicate) -> bool:
    return bool(defs) and all(predicate(d) for d in defs)


def _calls(fn: ast.AST):
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            yield node


def _takes_cid(fn: ast.AST) -> bool:
    """The codebase's convention for "this operates on one campaign"."""
    args = fn.args
    return any(a.arg == "cid"
               for a in [*args.posonlyargs, *args.args, *args.kwonlyargs])


def _imported_writers(tree: ast.AST) -> set[str]:
    """Local names bound by `from <atomic|os|shutil> import <primitive>`.

    Without this, `from .atomic import write_text` followed by a bare
    `write_text(p, x)` is invisible: the call's target is an `ast.Name`, not an
    attribute, so there is no receiver to key on. The comment here used to claim
    `test_atomic_guard` caught that form as a fallback. It does not — it matches
    `write_text`/`write_bytes` only as attributes — so nothing did.

    Resolved from the import rather than matching the bare names everywhere,
    which would flag any local helper that happened to be called `remove`.
    """
    out: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.module is None:
            continue
        source = node.module.rsplit(".", 1)[-1]
        if source == "atomic":
            wanted = _ATOMIC_WRITERS
        elif source in _FS_MODULES:
            wanted = _FS_ONLY + _FS_AMBIGUOUS
        else:
            continue
        out |= {(a.asname or a.name) for a in node.names if a.name in wanted}
    return out


def _writes_directly(fn: ast.AST, imported: frozenset[str] = frozenset()) -> bool:
    """Whether `fn` publishes a change to disk itself.

    `Path.replace` — a real atomic rename — is deliberately NOT matched: it is
    indistinguishable from `str.replace` without type inference. That is a known
    blind spot, accepted so that every string substitution is not read as a
    campaign mutation; `os.replace`, which is how `store.atomic` actually
    publishes, is matched.
    """
    for node in _calls(fn):
        name = _called_name(node.func)
        if isinstance(node.func, ast.Name):
            # `from .atomic import write_text` — no receiver to key on.
            if name in imported:
                return True
            continue
        if not isinstance(node.func, ast.Attribute):
            continue
        receiver = _receiver_name(node.func)
        if name in _ATOMIC_WRITERS and receiver == "atomic":
            return True
        if name in _FS_ONLY:
            return True
        if name in _FS_AMBIGUOUS and receiver in _FS_MODULES:
            return True
        if name == "touch" and receiver not in _TOUCH_FUNCTIONS:
            # `Path.touch()` publishes a pathname others can observe (a marker
            # or completion file) while writing no bytes, so neither this
            # guard's writers nor `test_atomic_guard` would see it.
            #
            # Keyed on the receiver, NOT on arity. Arity looked like a clean
            # discriminator against this package's own `campaigns.touch(cid)`
            # and was simply wrong: `Path.touch(self, mode=0o666,
            # exist_ok=True)` takes both positionally, so `marker.touch(0o600)`
            # is a real file creation that a zero-positional-args test rejects.
            return True
    return False


def _own_body(fn: ast.AST):
    """Nodes belonging to `fn` itself, NOT to functions nested inside it.

    `ast.walk` descends into nested `def`s, and taking that at face value was a
    hole: a lock inside a *callback* made the enclosing function read as
    serialized while its own writes ran under nothing —

        def outer(cid):
            def _cb():
                with locks.campaign_lock(cid): ...
            atomic.write_text(p, x)     # serialized by nothing
            register(_cb)

    which is precisely the shape this package uses (`proposals` persists a reply
    through a callback). The decorator case is the one place a nested lock really
    does cover the outer name, and it is handled separately in `_serializing`.
    """
    stack = list(ast.iter_child_nodes(fn))
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue                     # its body is its own, not `fn`'s
        stack.extend(ast.iter_child_nodes(node))


def _guards_the_campaign(call: ast.Call) -> bool:
    """The lock being entered is keyed on `cid` — this codebase's name for the
    campaign id, and the same convention `_takes_cid` uses to decide a function
    is in scope at all.

    Checking only the callee name let a mutator enter the WRONG lock and pass:
    `with locks.campaign_lock(sid): atomic.write_text(...)` serializes two
    concurrent calls for one campaign under two different locks, which loses
    updates exactly as if there were no lock.

    Matched on the name `cid` rather than on membership in this function's
    parameters, because a closure that captures `cid` from an enclosing scope
    locks the right campaign while having no parameter of its own.
    """
    return bool(call.args) and isinstance(call.args[0], ast.Name) \
        and call.args[0].id == "cid"


def _produces_lock(fn: ast.AST) -> bool:
    """`fn` hands back a campaign lock, keyed on `cid`, for someone else to enter.

    `audit`-shaped aliasing: a one-line helper that *returns* `campaign_lock(cid)`
    never enters it, so it is not itself serialized — but `with _alias(cid):` in
    a caller is a real acquisition. Kept separate from `_locks_in_own_body` so
    that distinction survives.

    It has to be a `return`/`yield` of the lock, not a mention of the name.
    Accepting any helper that names `campaign_lock` would let
    `def _helper(cid): log(campaign_lock); return nullcontext()` stand in for an
    acquisition, and would accept a helper that locks some *other* id — the same
    two holes the direct path already closes.

    And the acquisition has to still be HELD when the caller's body runs.
    Deferring to `_locks_in_own_body` accepted a helper that takes the lock,
    releases it, and hands back something else —

        def helper(cid):
            with locks.campaign_lock(cid):
                pass
            return nullcontext()        # caller's write runs unlocked

    so the `@contextmanager` case is recognized by its `yield` sitting INSIDE
    the `with`, which is what keeps the lock open across the caller's block.
    """
    if any(_is_yield(n) for n in _own_body(fn)):
        return _yields_inside_lock(fn)   # a @contextmanager, judged by its yields

    # Otherwise it must HAND BACK the lock -- and on every path, through
    # `_is_lock_context` so `hold_all([sid])` is validated the same way it is
    # everywhere else. A bare `campaign_lock(cid)` statement is not a return; it
    # constructs a lock and discards it, which locks nothing.
    returns = [n.value for n in _own_body(fn)
               if isinstance(n, ast.Return) and n.value is not None]
    return bool(returns) and all(_is_lock_context(v, fn) for v in returns)


def _locks_in_own_body(fn: ast.AST) -> bool:
    """`fn`'s own body ENTERS the campaign lock for its own campaign.

    Being entered is the point, not merely called. `_ProcessScopedLock` acquires
    in `__enter__`, so a bare `locks.campaign_lock(cid)` statement constructs a
    lock and locks nothing -- and reading any mention of the name as an
    acquisition would have let `campaign_lock(cid); atomic.write_text(...)` pass
    completely unlocked. Every acquisition in this package is a `with`; the one
    place calling `.acquire()` directly is `hold_all`, inside `locks.py`, which
    this guard does not scan.

    `hold_all` qualifies on name alone: it takes an iterable of ids rather than
    one `cid`, so there is no single argument to tie to this function.
    """
    return _enters_lock(fn, ())


def _holds_this_campaign(call: ast.Call, fn: ast.AST) -> bool:
    """`hold_all(...)` demonstrably covers this function's campaign.

    Unconditional acceptance was wrong for a `cid`-scoped writer:
    `with locks.hold_all([sid])` holds *a* set of campaign locks, not
    necessarily this one, so concurrent writes to `cid` stayed unprotected while
    the guard passed. A function that takes no `cid` is a multi-campaign holder
    (`module_edit._campaign_locks`, the world-module rebind route) and is not
    campaign-scoped, so there is nothing to tie; one that does take `cid` has to
    mention it in the argument. An expression this cannot read fails loud.
    """
    if not _takes_cid(fn):
        return True
    return any(isinstance(n, ast.Name) and n.id == "cid"
               for a in call.args for n in ast.walk(a))


def _is_lock_context(expr: ast.expr, fn: ast.AST) -> bool:
    """`expr`, used as a `with` context, holds THIS function's campaign lock.

    The single place that answers the question. It was previously answered in
    two — once on the direct path and once in the decorator check — and the copy
    drifted twice: the decorator path shipped without the `cid` argument check
    the direct path had just gained, and then without the `hold_all` coverage
    check it gained after that. Both were the same bug arriving twice because
    the rule lived in two places.
    """
    if not isinstance(expr, ast.Call):
        return False
    name = _called_name(expr.func)
    if name == "hold_all":
        return _holds_this_campaign(expr, fn)
    return name == "campaign_lock" and _guards_the_campaign(expr)


def _enters_lock(fn: ast.AST, aliases) -> bool:
    """`fn` enters `campaign_lock`/`hold_all` directly, or a local alias of one,
    keyed on this campaign.

    `aliases` is deliberately NOT "every name that serializes". A function can
    hold the lock internally and hand back something else —

        def helper(cid):
            with locks.campaign_lock(cid):
                pass
            return nullcontext()

    which serializes `helper`'s own body and nothing of its caller's. Only a
    name that keeps the acquisition live across the caller's block qualifies,
    which is what `_produces_lock` establishes.
    """
    for call in _with_context_calls(fn):
        if _is_lock_context(call, fn):
            return True
        if _called_name(call.func) in aliases and _guards_the_campaign(call):
            return True
    return False


def _yields_inside_lock(fn: ast.AST) -> bool:
    """A `@contextmanager` whose EVERY `yield` sits inside a campaign lock, so
    the lock is held across the caller's block on every path it can take.

    One yield under the lock is not enough: a helper that yields unlocked on one
    branch hands its caller an unprotected block, and the caller cannot tell.
    """
    return _every_one_locked(fn, fn, _is_yield)


def _under_lock(node: ast.AST, fn: ast.AST, locked: bool, want):
    """Yield, for every node matching `want` that this node executes DIRECTLY,
    whether it runs while a campaign lock is held.

    The one traversal every "is this protected?" question goes through, so the
    two rules that kept being got wrong live in exactly one place.

    It does not descend into nested `def`s or lambdas: code there runs when
    someone calls it, not here, so `later = lambda: fn(cid)` written inside a
    locked block is not a locked invocation. And it reports EVERY match rather
    than stopping at the first, because `locked` differs per branch -- an early
    `return fn(cid)` under a feature flag has to be visible beside the locked
    fallback rather than hidden by it.
    """
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue                       # deferred, not executed here
        if isinstance(child, (ast.With, ast.AsyncWith)):
            inner = locked or any(_is_lock_context(i.context_expr, fn)
                                  for i in child.items)
            for item in child.items:       # the context expressions themselves
                yield from _under_lock(item, fn, locked, want)
            for stmt in child.body:
                yield from _under_lock(stmt, fn, inner, want)
            continue
        if want(child):
            yield locked
        yield from _under_lock(child, fn, locked, want)


def _every_one_locked(node: ast.AST, fn: ast.AST, want) -> bool:
    """`node` does the thing at least once, and EVERY time it does, under a lock.

    "At least one occurrence is locked" was the wrong polarity in four separate
    predicates -- a `@contextmanager` yielding unlocked on one branch, a
    decorator returning either a locked or an unlocked wrapper, a wrapper
    invoking its target on a flagged fast path, an alias returning a lock on one
    branch and something else on another. All four are this function now.
    """
    seen = list(_under_lock(node, fn, False, want))
    return bool(seen) and all(seen)


def _is_call_to(target: str):
    return lambda n: isinstance(n, ast.Call) and _called_name(n.func) == target


def _is_yield(node: ast.AST) -> bool:
    return isinstance(node, (ast.Yield, ast.YieldFrom))


def _wraps_target_under_lock(fn: ast.AST) -> bool:
    """`fn` is a decorator whose returned wrapper calls the decorated function,
    and does so under this campaign's lock on EVERY path it executes.

    Descending into nested defs is unavoidable here -- a decorator's acquisition
    lives in the closure it returns -- which is exactly why this needs to be
    precise about three separate things, each of which was a hole in turn:

    - it must be the wrapper the decorator RETURNS, not any closure it happens
      to define (a `safe` closure that locks, never used, beside an `unsafe` one
      that is);
    - the invocation must be *executed* inside the `with`, not merely written
      there (a lambda defined in the block and called after it);
    - and EVERY executed invocation must be covered, not one of them, so a
      wrapper that returns `fn(cid)` early under a flag and locks only on the
      fallback does not read as protective.

    The decorated function is identified by the decorator's own first parameter,
    which is what `@_serialized`'s `fn` is.
    """
    params = [a.arg for a in [*fn.args.posonlyargs, *fn.args.args]]
    if not params:
        return False
    target = params[0]

    # EVERY name the decorator can return, and every definition of each. A
    # decorator that returns a locked wrapper on one branch and an unlocked one
    # on another was accepted as soon as the locked one was inspected; if the
    # flag selects the other, the decorated writer runs unlocked.
    returns = [node.value for node in _own_body(fn)
               if isinstance(node, ast.Return) and node.value is not None]
    if not returns or not all(isinstance(v, ast.Name) for v in returns):
        return False                     # `return fn` unchanged, or a call/expr
    returned = {v.id for v in returns}

    wrappers: dict[str, list[ast.AST]] = {}
    for nested in ast.walk(fn):
        if isinstance(nested, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and nested is not fn and nested.name in returned:
            wrappers.setdefault(nested.name, []).append(nested)

    if set(wrappers) != returned:
        return False                     # a returned name this cannot resolve
    return all(_every_one_locked(w, w, _is_call_to(target))
               for defs in wrappers.values() for w in defs)


def _locks_anywhere(fn: ast.AST) -> bool:
    """Whether applying `fn` as a decorator serializes what it decorates.

    Only the returned wrapper counts. A lock in the decorator's OWN body is held
    while the decoration runs -- once, at import -- and is long released by the
    time the decorated function is ever called, so `_locks_in_own_body(fn)` was
    never evidence of anything here.
    """
    return _wraps_target_under_lock(fn)


def _with_context_names(fn: ast.AST) -> set[str]:
    """Names called as context managers anywhere in `fn`: the `f` of `with f(x)`.

    Entering a locking context manager is not the same as delegating to a locked
    function, and the difference decides whether the caller's OWN writes are
    covered. `with _lock(cid): atomic.write_text(...)` serializes that write;
    `_locked_helper(cid); atomic.write_text(...)` does not serialize anything.
    """
    return {name for name in map(lambda c: _called_name(c.func),
                                 _with_context_calls(fn)) if name is not None}


def _with_context_calls(fn: ast.AST):
    """The `f(...)` of every `with f(...)` in `fn`'s own body.

    Own body only: a `with` inside a nested def covers that def, not this one.
    """
    for node in _own_body(fn):
        if isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if isinstance(item.context_expr, ast.Call):
                    yield item.context_expr


def _own_calls(fn: ast.AST):
    """Calls `fn` makes itself. Delegation is only delegation when the caller
    actually makes the call; one buried in a nested def is that def's."""
    for node in _own_body(fn):
        if isinstance(node, ast.Call):
            yield node


def _reaches_a_write(funcs: dict[str, ast.AST], imported=frozenset()) -> set[str]:
    """Names that reach a write through module-local calls, ignoring locks.

    Deliberately lock-blind, unlike `_mutators`: it answers "is there a write
    down this path at all", which is what the delegation rule needs before
    `_serializing` has decided anything. Computing it the other way round would
    be circular.
    """
    out = {n for n, ds in funcs.items() if _any(ds, lambda d: _writes_directly(d, imported))}
    for _ in range(len(funcs) + 1):
        grown = set(out)
        for name, defs in funcs.items():
            if name not in grown and _any(defs, lambda d: any(
                    _called_name(c.func) in grown for c in _own_calls(d))):
                grown.add(name)
        if grown == out:
            break
        out = grown
    return out


def _serializing(funcs: dict[str, ast.AST], imported=frozenset()) -> set[str]:
    """Names whose bodies establish the campaign lock.

    Three forms, all present in the package today: a direct
    ``with locks.campaign_lock(cid)``; a module-local alias like
    ``audit._lock``; and a decorator that wraps the body in one, which is how
    every ``scenes`` mutator does it (``@_serialized``).

    Delegation counts too -- ``scenes.create_scene`` does its work in the
    ``@_serialized`` ``_create_scene`` -- but only when EVERY write the wrapper
    reaches is covered. Two ways that fails: the wrapper publishes something
    itself, or it calls a second, unlocked helper alongside the locked one.
    Accepting "some callee is locked" let the latter through, and because
    `_analyze` skips private helpers, the unlocked write then had nowhere left
    to be reported -- a domain module could hold an unserialized write and pass.
    """
    # A decorator's acquisition is nested inside the wrapper it returns, so it is
    # the one case that must look through nested defs -- see `_locks_anywhere`.
    decorating = {n for n, ds in funcs.items() if _all(ds, _locks_anywhere)}
    # Local helpers that hand back a campaign lock. Entering one of these IS an
    # acquisition, even though the helper itself never enters anything.
    aliases = {n for n, ds in funcs.items() if _all(ds, _produces_lock)}
    writing = _reaches_a_write(funcs, imported)
    out = {n for n, ds in funcs.items() if _all(ds, _locks_in_own_body)}
    for _ in range(len(funcs) + 1):
        grown = set(out)
        for name, defs in funcs.items():
            if name in grown:
                continue

            def covered(fn, grown=grown, imported=imported):
                if any(_called_name(d) in decorating for d in fn.decorator_list):
                    return True                       # @_serialized and friends
                if _enters_lock(fn, aliases):
                    return True                       # with _lock(cid): ...
                if _writes_directly(fn, imported):
                    return False
                # Pure delegation: every local callee that reaches a write must
                # itself be covered, and at least one must exist.
                delegated = {_called_name(c.func) for c in _own_calls(fn)} & writing
                return bool(delegated) and delegated <= grown

            if _all(defs, covered):
                grown.add(name)
        if grown == out:
            break
        out = grown
    return out


def _mutators(funcs: dict[str, ast.AST], serializing: set[str],
              imported=frozenset()) -> set[str]:
    """Names that publish a change to campaign state.

    Propagation stops at a serializing callee: it is an atomic unit, so calling
    it does not make the caller a mutation site in its own right. Without that,
    every thin wrapper over a locked helper reads as an unlocked mutator and the
    guard drowns in false positives.
    """
    out = {n for n, ds in funcs.items() if _any(ds, lambda d: _writes_directly(d, imported))}
    for _ in range(len(funcs) + 1):
        grown = set(out)
        for name, defs in funcs.items():
            if name in grown:
                continue
            if _any(defs, lambda d: any(
                    _called_name(c.func) in grown and _called_name(c.func) not in serializing
                    for c in _calls(d))):
                grown.add(name)
        if grown == out:
            break
        out = grown
    return out


def _module_name(path: pathlib.Path) -> str:
    rel = path.relative_to(PACKAGE).with_suffix("")
    return ".".join(rel.parts)


def _exemption(src: str, fn: ast.AST, others=()) -> str | None:
    """The `# lock-domain-ok: <reason>` attached to THIS function, if any.

    Two things `guard_markers.marker_reason(MARKER, src, fn)` alone gets wrong
    for a *function* node, as opposed to the call nodes the other guards pass it:

    - ``ast.FunctionDef.lineno`` is the ``def`` line, not the first decorator's,
      so the comment block above a decorated function does not attach and the
      marker is silently ignored. Every ``scenes`` mutator is decorated, which is
      the one domain module where that matters most. Retry from the decorator.
    - a function's span covers its whole body, so a marker written inside a
      NESTED function exempts the enclosing one too. Passing the module's other
      functions as `others` hands the marker to the innermost that contains it,
      which is what that parameter is for.
    """
    reason = guard_markers.marker_reason(MARKER, src, fn, others)
    if reason is not None:
        return reason
    for decorator in getattr(fn, "decorator_list", []):
        reason = guard_markers.marker_reason(MARKER, src, decorator, others)
        if reason is not None:
            return reason
    return None


def _analyze(path: pathlib.Path):
    """(unserialized mutator names, every campaign mutator name) for one file."""
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    funcs = _functions(tree)
    imported = frozenset(_imported_writers(tree))
    serializing = _serializing(funcs, imported)
    mutators = _mutators(funcs, serializing, imported)

    every = [d for ds in funcs.values() for d in ds]
    campaign_mutators, unserialized = set(), set()
    for name in mutators:
        defs = funcs[name]
        if not _any(defs, _takes_cid):
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
        # EVERY definition must carry its own exemption; one marker cannot
        # clear a name whose other branch is the unlocked one.
        if _all(defs, lambda d: _exemption(src, d, [o for o in every if o is not d])
                is not None):
            continue
        unserialized.add(name)
    return unserialized, campaign_mutators


@functools.cache
def _survey() -> dict[str, tuple[set[str], set[str]]]:
    """Every campaign-mutating module -> (unserialized public mutators, all of
    them). Cached: six tests below ask for it, the package is ~150 files, and
    the per-module fixed points are quadratic in the module's function count.
    Nothing rewrites the tree mid-run."""
    out = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        if path == LOCKS_PY:
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


# The backlog exactly as it stood when this guard landed. Frozen HERE, not
# derived from `locks.UNREVIEWED`, because that is the whole point: the
# declaration is the thing under test and cannot also be the baseline.
_UNREVIEWED_AT_LANDING = frozenset({
    "store.appearances", "store.assets", "store.campaign_climate",
    "store.changes", "store.characters", "store.chronicle", "store.commits",
    "store.dossiers", "store.modules", "store.overlay", "store.playing",
    "store.playstate", "store.plot", "store.relationships", "store.sync",
    "store.taglines", "store.weather.overrides",
})


def test_the_unreviewed_backlog_only_shrinks():
    """A third bucket is a temptation: it takes an entry with no reason. It is
    allowed to exist only because it cannot grow — every module in it predates
    this guard, and a new mutator has to be classified for real.

    Membership, not cardinality. A count-only bound let one legacy entry be
    swapped for a brand-new module in the same change: the length stays put, the
    classification and phantom checks are satisfied, and an unreviewed mutator
    joins the backlog without anyone reviewing it. Names are checked because
    names are what the guarantee is about."""
    added = sorted(set(locks.UNREVIEWED) - _UNREVIEWED_AT_LANDING)
    assert not added, (
        "`locks.UNREVIEWED` is a frozen backlog of modules that predate this "
        "guard; it may only shrink. Classify these into DOMAIN_MODULES or "
        "OUTSIDE_DOMAIN instead:\n  " + "\n  ".join(added))


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
        if path == LOCKS_PY:            # same exact-path skip as `_survey`; by
                                        # filename, a second `locks.py` anywhere
                                        # in the package would be surveyed but
                                        # have its markers left unaudited
            continue
        src = path.read_text(encoding="utf-8")
        funcs = _functions(ast.parse(src))
        every = [d for ds in funcs.values() for d in ds]
        for name, defs in funcs.items():
            for fn in defs:
                reason = _exemption(src, fn, [o for o in every if o is not fn])
                if reason is not None:
                    marked.append((f"{_module_name(path)}.{name}", reason))
    unexplained = [loc for loc, reason in marked if len(reason) < 15]
    assert not unexplained, f"`{MARKER}` with no real reason: {unexplained}"
    assert len(marked) <= 2, (
        f"{len(marked)} {MARKER} exemptions; each is a hole in the exclusion, "
        f"so they need review rather than a raised limit: {marked}")


# --- the guard can fail ------------------------------------------------------
# "A guard that cannot fail is worse than none -- it reads as coverage."
# (test_atomic_guard.py)

def _probe(src: str):
    tree = ast.parse(src)
    funcs = _functions(tree)
    imported = frozenset(_imported_writers(tree))
    serializing = _serializing(funcs, imported)
    return funcs, serializing, _mutators(funcs, serializing, imported)


def test_the_guard_detects_an_unlocked_mutator():
    src = ("def touch(cid):\n"
           "    meta = read(p)\n"
           "    atomic.write_text(p, meta)\n")
    funcs, serializing, mutators = _probe(src)
    assert mutators == {"touch"}
    assert "touch" not in serializing
    assert _takes_cid(funcs["touch"][0])


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


def test_a_lock_that_is_never_entered_does_not_count():
    """`_ProcessScopedLock` acquires in `__enter__`. A bare call constructs the
    lock object and locks nothing, so reading any mention of the name as an
    acquisition let a completely unlocked mutator pass."""
    src = ("def append(cid):\n"
           "    locks.campaign_lock(cid)\n"
           "    atomic.write_text(p, x)\n")
    _funcs, serializing, mutators = _probe(src)
    assert mutators == {"append"}
    assert "append" not in serializing, \
        "a lock that was constructed but never entered read as serialization"


def test_entering_the_lock_for_a_different_id_does_not_count():
    """Two concurrent calls for one `cid` serialized under two different locks
    lose an update exactly as if there were no lock at all."""
    wrong = ("def set_datetime(cid, sid):\n"
             "    with locks.campaign_lock(sid):\n"
             "        atomic.write_text(p, x)\n")
    _f, serializing, _m = _probe(wrong)
    assert "set_datetime" not in serializing, "the wrong campaign's lock passed"

    right = ("def set_datetime(cid, sid):\n"
             "    with locks.campaign_lock(cid):\n"
             "        atomic.write_text(p, x)\n")
    _f, serializing, _m = _probe(right)
    assert "set_datetime" in serializing


def test_hold_all_qualifies_without_a_single_cid():
    """The multi-campaign form takes an iterable, so there is no one argument to
    tie to the caller — it must not be caught by the cid check."""
    src = ("def rebind(all_cids):\n"
           "    with locks.hold_all(all_cids):\n"
           "        atomic.write_text(p, x)\n")
    _f, serializing, _m = _probe(src)
    assert "rebind" in serializing


def test_creating_an_empty_file_counts_as_a_mutation():
    """`Path.touch()` publishes a pathname others can observe while writing no
    bytes, so neither this guard's writers nor the atomic guard would see it."""
    _f, _s, mutators = _probe("def mark(cid):\n    (root / 'done').touch()\n")
    assert mutators, "Path.touch() was not seen as publishing state"


def test_this_repos_own_touch_function_is_not_a_filesystem_call():
    """`campaigns.touch(cid)` is a module function, not `Path.touch`. Matching
    the bare name would have made every caller of it a direct writer."""
    _f, _s, mutators = _probe("def bump(cid):\n    campaigns.touch(cid)\n")
    assert not mutators, "campaigns.touch(cid) was read as a filesystem call"


def test_a_contextmanager_must_yield_under_the_lock_on_every_branch():
    """One yield inside the lock is not enough: the branch that yields unlocked
    hands its caller an unprotected block, and the caller cannot tell."""
    mixed = ("@contextlib.contextmanager\n"
             "def helper(cid):\n"
             "    if FAST:\n"
             "        yield\n"
             "        return\n"
             "    with locks.campaign_lock(cid):\n"
             "        yield\n"
             "def put(cid):\n"
             "    with helper(cid):\n"
             "        atomic.write_text(p, x)\n")
    _f, serializing, _m = _probe(mixed)
    assert "put" not in serializing, "an unlocked yield branch was hidden by the locked one"


def test_a_decorator_that_locks_only_while_decorating_does_not_count():
    """A lock in the decorator's own body is held once, at import, and released
    long before the decorated function is ever called."""
    src = ("def audited(fn):\n"
           "    with locks.campaign_lock(cid):\n"
           "        register(fn)\n"
           "    return fn\n"
           "@audited\n"
           "def append_message(cid, text):\n"
           "    atomic.write_text(p, text)\n")
    _f, serializing, _m = _probe(src)
    assert "append_message" not in serializing, \
        "a decoration-time lock vouched for every runtime call"


def test_an_alias_returning_hold_all_for_another_campaign_does_not_count():
    """`hold_all` inside an alias went unvalidated, so the caller's own
    `_guards_the_campaign` check passed on its argument while the helper held a
    different campaign's locks entirely."""
    src = ("def helper(cid, sid):\n"
           "    return locks.hold_all([sid])\n"
           "def put(cid, sid):\n"
           "    with helper(cid, sid):\n"
           "        atomic.write_text(p, x)\n")
    _f, serializing, _m = _probe(src)
    assert "put" not in serializing, "an alias holding another campaign passed"


def test_every_wrapper_a_decorator_can_return_must_lock():
    """`returned` can hold more than one name. Accepting the decorator as soon
    as the locked wrapper was inspected let the flag select the other one."""
    src = ("def audited(fn):\n"
           "    def safe(cid, *a):\n"
           "        with locks.campaign_lock(cid):\n"
           "            return fn(cid, *a)\n"
           "    def fast(cid, *a):\n"
           "        return fn(cid, *a)\n"
           "    if FAST:\n"
           "        return fast\n"
           "    return safe\n"
           "@audited\n"
           "def append_message(cid, text):\n"
           "    atomic.write_text(p, text)\n")
    _f, serializing, _m = _probe(src)
    assert "append_message" not in serializing, \
        "one locked wrapper vouched for the unlocked one beside it"


def test_the_real_serialized_decorator_still_qualifies():
    """The counterweight to the four tests above: none of that strictness may
    stop recognizing the shape `scenes` actually uses."""
    src = ("def _serialized(fn):\n"
           "    @functools.wraps(fn)\n"
           "    def locked(cid, *args, **kwargs):\n"
           "        with locks.campaign_lock(cid):\n"
           "            return fn(cid, *args, **kwargs)\n"
           "    return locked\n"
           "@_serialized\n"
           "def append_message(cid, text):\n"
           "    atomic.write_text(p, text)\n")
    _f, serializing, _m = _probe(src)
    assert "append_message" in serializing, "the real @_serialized shape regressed"


def test_path_touch_with_positional_arguments_is_a_mutation():
    """`Path.touch(self, mode=0o666, exist_ok=True)` takes both positionally, so
    the arity discriminator this used to rely on was simply wrong — a real file
    creation written `marker.touch(0o600)` was rejected."""
    for src in ("def mark(cid):\n    (root / 'done').touch()\n",
                "def mark(cid):\n    (root / 'done').touch(0o600)\n",
                "def mark(cid):\n    (root / 'done').touch(0o600, True)\n",
                "def mark(cid):\n    (root / 'done').touch(exist_ok=False)\n"):
        _f, _s, mutators = _probe(src)
        assert mutators, f"missed a Path.touch publication: {src!r}"


def test_a_decorator_holding_another_campaigns_hold_all_does_not_count():
    """The `hold_all` coverage check lived only on the direct path; the
    decorator path accepted it by callee name. Same rule, two places, one of
    them stale — which is why both now go through `_is_lock_context`."""
    src = ("def audited(fn):\n"
           "    def locked(cid, sid):\n"
           "        with locks.hold_all([sid]):\n"
           "            return fn(cid, sid)\n"
           "    return locked\n"
           "@audited\n"
           "def put(cid, sid):\n"
           "    atomic.write_text(p, x)\n")
    _f, serializing, _m = _probe(src)
    assert "put" not in serializing, \
        "a decorator holding another campaign's locks passed"


def test_a_decorator_with_one_unlocked_path_does_not_count():
    """Accepting the first protected invocation hid the branch beside it: an
    early `return fn(cid)` under a flag runs the decorated writer unlocked."""
    src = ("def audited(fn):\n"
           "    def locked(cid, *a):\n"
           "        if FAST:\n"
           "            return fn(cid, *a)\n"
           "        with locks.campaign_lock(cid):\n"
           "            return fn(cid, *a)\n"
           "    return locked\n"
           "@audited\n"
           "def append_message(cid, text):\n"
           "    atomic.write_text(p, text)\n")
    _f, serializing, _m = _probe(src)
    assert "append_message" not in serializing, \
        "an unlocked branch was hidden by the locked one beside it"


def test_an_alias_that_releases_before_returning_does_not_count():
    """The acquisition has to still be held while the caller's block runs. A
    helper that takes the lock, releases it, and hands back something else left
    the caller's write unprotected."""
    released = ("def helper(cid):\n"
                "    with locks.campaign_lock(cid):\n"
                "        pass\n"
                "    return nullcontext()\n"
                "def put(cid):\n"
                "    with helper(cid):\n"
                "        atomic.write_text(p, x)\n")
    _f, serializing, _m = _probe(released)
    assert "put" not in serializing, \
        "a helper that released the lock before returning passed as an alias"

    held = ("@contextlib.contextmanager\n"
            "def helper(cid):\n"
            "    with locks.campaign_lock(cid):\n"
            "        yield\n"
            "def put(cid):\n"
            "    with helper(cid):\n"
            "        atomic.write_text(p, x)\n")
    _f, serializing, _m = _probe(held)
    assert "put" in serializing, "the real @contextmanager alias regressed"


def test_a_call_deferred_out_of_the_locked_block_does_not_count():
    """The returned-wrapper check still walked into nested defs and lambdas, so
    a target invocation written inside the `with` but executed after it read as
    protected."""
    src = ("def audited(fn):\n"
           "    def locked(cid, *a):\n"
           "        with locks.campaign_lock(cid):\n"
           "            later = lambda: fn(cid, *a)\n"
           "        return later()\n"
           "    return locked\n"
           "@audited\n"
           "def append_message(cid, text):\n"
           "    atomic.write_text(p, text)\n")
    _f, serializing, _m = _probe(src)
    assert "append_message" not in serializing, \
        "a call deferred out of the locked block vouched for the decorator"


def test_hold_all_must_cover_this_campaign():
    """`hold_all` holds *a* set of campaign locks. For a cid-scoped writer it
    has to be shown to include this one."""
    wrong = ("def put(cid, sid):\n"
             "    with locks.hold_all([sid]):\n"
             "        atomic.write_text(p, x)\n")
    _f, serializing, _m = _probe(wrong)
    assert "put" not in serializing, "hold_all over another campaign passed"

    right = ("def put(cid, sid):\n"
             "    with locks.hold_all([cid, sid]):\n"
             "        atomic.write_text(p, x)\n")
    _f, serializing, _m = _probe(right)
    assert "put" in serializing

    multi = ("def rebind(all_cids):\n"
             "    with locks.hold_all(all_cids):\n"
             "        atomic.write_text(p, x)\n")
    _f, serializing, _m = _probe(multi)
    assert "rebind" in serializing, "the multi-campaign holder shape regressed"


def test_every_definition_of_a_name_is_analyzed():
    """Keeping only the first definition discarded the one that runs: an
    `@overload` stub ahead of a writing body, or a conditional definition whose
    first branch locks and whose second does not."""
    overloaded = ("@overload\n"
                  "def write_note(cid: str) -> None: ...\n"
                  "def write_note(cid):\n"
                  "    atomic.write_text(p, x)\n")
    _f, _s, mutators = _probe(overloaded)
    assert "write_note" in mutators, "the stub hid the implementation"

    conditional = ("if FAST:\n"
                   "    def put(cid):\n"
                   "        with locks.campaign_lock(cid):\n"
                   "            atomic.write_text(p, x)\n"
                   "else:\n"
                   "    def put(cid):\n"
                   "        atomic.write_text(p, x)\n")
    _f, serializing, mutators = _probe(conditional)
    assert "put" in mutators
    assert "put" not in serializing, \
        "a locked first branch vouched for an unlocked second one"


def test_a_directly_imported_writer_is_seen():
    """`from .atomic import write_text` leaves the call an `ast.Name`, so there
    is no receiver to key on and the attribute path skips it. `test_atomic_guard`
    matches those names only as attributes, so it is not a fallback either —
    nothing saw this form."""
    src = ("from .atomic import write_text\n"
           "def write_note(cid, t):\n"
           "    write_text(p, t)\n")
    _f, _s, mutators = _probe(src)
    assert "write_note" in mutators, "a directly-imported writer was invisible"

    aliased = ("from .atomic import write_text as _wt\n"
               "def write_note(cid, t):\n"
               "    _wt(p, t)\n")
    _f, _s, mutators = _probe(aliased)
    assert "write_note" in mutators, "the import alias was not followed"

    unrelated = ("def write_text(a, b):\n    return a\n"
                 "def helper(cid):\n    write_text(1, 2)\n")
    _f, _s, mutators = _probe(unrelated)
    assert not mutators, "a local function sharing the name was read as a writer"


def test_the_returned_wrapper_is_the_one_that_must_lock():
    """One level down from the decorator fix: define a `safe` closure that calls
    the target under the lock, never use it, and return an `unsafe` one that
    calls the target bare."""
    src = ("def audited(fn):\n"
           "    def safe(cid, *a):\n"
           "        with locks.campaign_lock(cid):\n"
           "            return fn(cid, *a)\n"
           "    def unsafe(cid, *a):\n"
           "        return fn(cid, *a)\n"
           "    return unsafe\n"
           "@audited\n"
           "def append_message(cid, text):\n"
           "    atomic.write_text(p, text)\n")
    _f, serializing, _m = _probe(src)
    assert "append_message" not in serializing, \
        "an unused locking closure vouched for the wrapper actually returned"


def test_a_decorator_locking_the_wrong_id_does_not_count():
    """The direct path validates the lock's argument; the decorator path was
    bypassing that check entirely."""
    src = ("def audited(fn):\n"
           "    def locked(cid, sid):\n"
           "        with locks.campaign_lock(sid):\n"
           "            return fn(cid, sid)\n"
           "    return locked\n"
           "@audited\n"
           "def set_datetime(cid, sid):\n"
           "    atomic.write_text(p, x)\n")
    _f, serializing, _m = _probe(src)
    assert "set_datetime" not in serializing, \
        "a decorator holding another campaign's lock passed"


def test_an_alias_must_return_the_lock_not_merely_call_it():
    """A bare `campaign_lock(cid)` statement constructs a lock and drops it, so
    a helper that does that and returns a `nullcontext` locks nothing."""
    src = ("def _helper(cid):\n"
           "    locks.campaign_lock(cid)\n"
           "    return nullcontext()\n"
           "def put(cid):\n"
           "    with _helper(cid):\n"
           "        atomic.write_text(p, x)\n")
    _f, serializing, _m = _probe(src)
    assert "put" not in serializing, \
        "a helper that constructed and discarded the lock passed as an alias"


def test_a_decorator_must_actually_wrap_its_target_under_the_lock():
    """Decorator recognition has to descend into nested defs, which is a second
    door into the callback hole: a decorator carrying an unrelated locking
    callback, that hands its target back unchanged, would launder every writer
    it decorates."""
    sham = ("def audited(fn):\n"
            "    def _report(cid):\n"
            "        with locks.campaign_lock(cid):\n"
            "            log(cid)\n"
            "    schedule(_report)\n"
            "    return fn\n"           # target returned UNWRAPPED
            "@audited\n"
            "def append_message(cid, text):\n"
            "    atomic.write_text(p, text)\n")
    _f, serializing, _m = _probe(sham)
    assert "append_message" not in serializing, \
        "a decorator that never wraps its target under the lock laundered a write"

    real = ("def _serialized(fn):\n"
            "    def locked(cid, *a):\n"
            "        with locks.campaign_lock(cid):\n"
            "            return fn(cid, *a)\n"
            "    return locked\n"
            "@_serialized\n"
            "def append_message(cid, text):\n"
            "    atomic.write_text(p, text)\n")
    _f, serializing, _m = _probe(real)
    assert "append_message" in serializing, "the real _serialized shape regressed"


def test_delegation_requires_every_mutating_path_to_be_covered():
    """A wrapper that calls a locked helper AND an unlocked private one was
    marked serialized because *some* callee was. `_analyze` skips private
    helpers, so the unlocked write then had nowhere left to be reported."""
    src = ("def _serialized(fn):\n"
           "    def locked(cid, *a):\n"
           "        with locks.campaign_lock(cid):\n"
           "            return fn(cid, *a)\n"
           "    return locked\n"
           "@_serialized\n"
           "def _safe(cid):\n"
           "    atomic.write_text(p, x)\n"
           "def _unsafe(cid):\n"
           "    atomic.write_text(q, y)\n"
           "def publish(cid):\n"
           "    _safe(cid)\n"
           "    _unsafe(cid)\n")
    _f, serializing, mutators = _probe(src)
    assert "publish" in mutators
    assert "publish" not in serializing, \
        "an unlocked helper alongside a locked one passed as delegation"


def test_a_helper_that_only_mentions_the_lock_is_not_an_alias():
    """`_produces_lock` keyed on the callee name alone would accept a helper
    that returns a `nullcontext`, or one that locks a different id."""
    sham = ("def _helper(cid):\n"
            "    log(campaign_lock)\n"
            "    return nullcontext()\n"
            "def put(cid):\n"
            "    with _helper(cid):\n"
            "        atomic.write_text(p, x)\n")
    _f, serializing, _m = _probe(sham)
    assert "put" not in serializing, "a nullcontext helper passed as a lock alias"

    wrong = ("def _helper(sid):\n"
             "    return locks.campaign_lock(sid)\n"
             "def put(cid, sid):\n"
             "    with _helper(sid):\n"
             "        atomic.write_text(p, x)\n")
    _f, serializing, _m = _probe(wrong)
    assert "put" not in serializing, "an alias keyed on another id passed"

    real = ("def _lock(cid):\n"
            "    return locks.campaign_lock(cid)\n"
            "def put(cid):\n"
            "    with _lock(cid):\n"
            "        atomic.write_text(p, x)\n")
    _f, serializing, _m = _probe(real)
    assert "put" in serializing, "the real audit-shaped alias regressed"


def test_a_lock_inside_a_callback_does_not_cover_the_enclosing_body():
    """`ast.walk` descends into nested defs, so a lock taken in a callback made
    the function around it read as serialized while its own write ran under
    nothing. This package persists through callbacks (`proposals` commits a
    narration that way), so the shape is not hypothetical."""
    src = ("def outer(cid):\n"
           "    def _cb():\n"
           "        with locks.campaign_lock(cid):\n"
           "            pass\n"
           "    atomic.write_text(p, x)\n"
           "    register(_cb)\n")
    _funcs, serializing, mutators = _probe(src)
    assert "outer" in mutators
    assert "outer" not in serializing, \
        "a nested closure's lock covered the enclosing function's own write"
    assert "_cb" in serializing, "the closure itself still locks"


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
                "def rehome(cid):\n    p.rename(q)\n",
                "def swap(cid):\n    os.replace(tmp, p)\n",
                "def drop2(cid):\n    os.remove(p)\n"):
        _funcs, _s, mutators = _probe(src)
        assert mutators, f"missed a removal/rename mutation: {src!r}"


def test_a_string_or_list_method_is_not_a_filesystem_mutation():
    """`remove` and `replace` are also ordinary builtin methods, and matching
    them receiver-blind classified `store.export` as a campaign mutator because
    `build_html` calls `doc.replace(...)` on a string. Two modules sat in the
    declaration on the strength of that."""
    for src, why in [
        ("def build_html(cid):\n    return doc.replace('a', 'b')\n", "str.replace"),
        ("def leave(cid, sid):\n    rec['scenes'].remove(sid)\n", "list.remove"),
        ("def prune(cid):\n    seen.remove(cid)\n", "set.remove"),
    ]:
        _funcs, _s, mutators = _probe(src)
        assert not mutators, f"{why} was read as a filesystem mutation ({src!r})"


def test_a_function_without_a_cid_is_not_campaign_scoped():
    """World- and module-scoped writers share these primitives and are not in
    this domain; without the `cid` test the guard would flag the whole store."""
    src = "def write_world(wid):\n    atomic.write_text(p, x)\n"
    funcs, _s, mutators = _probe(src)
    assert mutators == {"write_world"}
    assert not _takes_cid(funcs["write_world"][0])


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
    assert _exemption(src, funcs["seed"][0]) is not None
    assert _exemption(src, funcs["other"][0]) is None


def test_a_marker_above_a_decorator_attaches():
    """`FunctionDef.lineno` is the `def` line, so the comment block above a
    DECORATED function does not attach to it and the marker reads as absent.
    Every `scenes` mutator is decorated — the one domain module where writing a
    marker in the obvious place has to work."""
    src = (f"# {MARKER} a reason long enough to be a real one\n"
           "@_serialized\n"
           "def append_message(cid, text):\n"
           "    atomic.write_text(p, text)\n")
    funcs = _functions(ast.parse(src))
    assert _exemption(src, funcs["append_message"][0]) is not None


def test_a_marker_inside_a_nested_function_does_not_exempt_the_outer_one():
    """A function's span covers its whole body, so without `others` a marker
    written for an inner closure silently exempted the enclosing mutator."""
    src = ("def outer(cid):\n"
           "    def inner():\n"
           f"        pass  # {MARKER} this reason belongs to the closure\n"
           "    atomic.write_text(p, x)\n")
    funcs = _functions(ast.parse(src))
    others = [d for ds in funcs.values() for d in ds if d is not funcs["outer"][0]]
    assert _exemption(src, funcs["outer"][0], others) is None, \
        "the enclosing mutator inherited a nested function's marker"
    assert _exemption(src, funcs["inner"][0], [funcs["outer"][0]]) is not None
