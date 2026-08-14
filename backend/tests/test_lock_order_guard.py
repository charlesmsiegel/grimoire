"""Guard: only ``locks.hold_all`` may hold more than one campaign lock (#267).

``store/locks.py`` states the ordering rule -- multi-campaign holders acquire in
sorted cid order, and ``hold_all`` is the single place that sorts -- and until
this file existed, that rule was checked *per holder*: ``test_locks_store.py``
spies on the registry and asserts the order each of the two known holders
actually asks for. Nothing checked a holder nobody had written yet, and that is
precisely how #267 happened: module publication and the world-module rebind
route were each written without knowledge of the other, each acquired every
campaign lock in its own order (recency vs cid), and each carried a comment
calling itself the only multi-lock holder. Two concurrent requests wedged
permanently. Both endpoints are plain ``def``, so FastAPI runs them in the
threadpool and they are genuinely concurrent.

A behavioural test cannot close that: it would have to lose a race to fail. So
this is a source guard, in the spirit of ``test_atomic_guard.py`` -- it walks
the package's ASTs and fails on the *shapes* that hold two campaign locks at
once, wherever a third holder is written.

What is flagged, inside one function body:

- an acquisition registered on an ``ExitStack``
  (``stack.enter_context(campaign_lock(c))``) -- the release outlives the
  statement, which is what makes accumulation possible and is the exact shape
  both #267 holders had. Including the form written a statement apart,
  ``lock = campaign_lock(cid)`` then ``stack.callback(lock.release)``, which no
  walk through the pushing call can see;
- an acquisition inside a ``for``/``while``/comprehension with no ``with``
  bounding it, so the loop can carry locks between iterations.
  ``for c in cids: with lock(c): ...`` is *not* flagged: that block releases
  before the next iteration, so it holds one lock at a time;
- two acquisitions open at once by lexical nesting, or two lock items in one
  ``with``, for two different campaign expressions. The same expression twice is
  reentrant (``campaign_lock`` returns an RLock) and is not flagged.

The one legitimate site is ``hold_all`` itself, which the package test names and
``test_hold_all_earns_its_exemption_by_sorting`` justifies rather than assumes.
Anything else needs a ``# lock-order-ok: <reason>`` marker, and those are capped.

Reach, stated rather than implied -- ``test_atomic_guard.py``'s house standard:

- **An acquisition is recognized by NAME**: a call whose callee is spelled with
  ``campaign_lock`` in it (``campaign_lock``, ``best_effort_campaign_lock``,
  ``campaign_lock_nowait``, ``module_edit._campaign_locks``) or is ``hold_all``,
  through any receiver. That is the opposite polarity from
  ``test_lock_domain_guard.py``, deliberately: that guard must *credit* an
  acquisition and so resolves every spelling to its binding, while this one must
  *notice* one, and an unrecognized acquisition here is a false negative. The
  cost is that ``cl = locks.campaign_lock`` and any lock reached through a
  wrapper object are invisible.
- **Only within one function body.** A function that holds a lock and calls
  another that takes a *different* campaign's is the same deadlock and is not
  seen here. Making it visible means resolving which campaign each callee locks,
  which is ``test_lock_domain_guard.py``'s whole apparatus, and it would flag
  the legitimate composition this package is built on: the rebind route holds
  ``hold_all(all_cids)`` and calls ``audit.clear_baselines(c)`` for each ``c``
  in that set, which reacquires reentrantly. A guard that flagged that would be
  turned off.
- **Lexical, not dynamic.** Locks handed to a thread, an executor, or a
  callback invoked later are not analyzed.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from . import guard_markers

# The trailing colon is load-bearing -- see test_pydantic_guard.py.
MARKER = "lock-order-ok:"
SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "grimoire"

#: The one function allowed to hold more than one campaign lock: it is the only
#: one that sorts. `test_hold_all_earns_its_exemption_by_sorting` checks that
#: claim instead of taking it, and a rename or a move fails this guard rather
#: than silently widening it.
SORTER = ("store/locks.py", "hold_all")

# The comprehension forms are here for the same reason as `for`: they iterate.
# `ast.comprehension` is deliberately not the node to look for -- the element
# expression is a child of the ListComp, not of the `comprehension` clause, so
# an ancestor walk never meets one.
_LOOPS = (ast.For, ast.AsyncFor, ast.While,
          ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
_WITHS = (ast.With, ast.AsyncWith)
#: `ExitStack` methods that defer a release past the enclosing statement.
_STACK_PUSHERS = frozenset({
    "enter_context", "enter_async_context", "push", "push_async_exit", "callback",
})


def _acquires(call: ast.Call) -> str:
    """The spelling, if this call reads as taking a campaign lock."""
    func = call.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
    if "campaign_lock" in name or name == "hold_all":
        return name
    return ""


def _campaign(call: ast.Call) -> str | None:
    """The campaign this acquisition is keyed on, as written.

    ``None`` when there is nothing comparable -- no argument, or ``hold_all``,
    whose argument is a *collection* and so is never "the same campaign" as a
    scalar one. Two acquisitions count as the same campaign only when both
    answer with the same text, so an unanswerable one is never assumed safe.
    """
    if _acquires(call) == "hold_all" or not call.args:
        return None
    return ast.unparse(call.args[0])


def _same_campaign(a: ast.Call, b: ast.Call) -> bool:
    ca, cb = _campaign(a), _campaign(b)
    return ca is not None and ca == cb


def _scopes(tree: ast.AST):
    """``(scope, [nodes])`` per function body; a nested ``def`` is its own scope.

    A closure defined inside a locked block does not run inside it -- the
    ``finalize``/``on_error`` callbacks in ``routes/streaming.py`` are defined
    beside a ``with`` and invoked long after it exits -- so charging their
    acquisitions to the enclosing function would be wrong in both directions.
    """
    out: list[tuple[ast.AST, list[ast.AST]]] = []

    def walk(node: ast.AST, own: list[ast.AST]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                  ast.Lambda, ast.ClassDef)):
                sub: list[ast.AST] = []
                out.append((child, sub))
                walk(child, sub)
            else:
                own.append(child)
                walk(child, own)

    root: list[ast.AST] = []
    out.append((tree, root))
    walk(tree, root)
    return out


def _ancestors(nodes: list[ast.AST]) -> dict[ast.AST, ast.AST]:
    parent: dict[ast.AST, ast.AST] = {}
    for node in nodes:
        for child in ast.iter_child_nodes(node):
            parent[child] = node
    return parent


def _chain(call: ast.AST, parent: dict[ast.AST, ast.AST]) -> list[ast.AST]:
    """Ancestors of `call` up to the top of its own scope, innermost first."""
    out = []
    cur = call
    while cur in parent:
        cur = parent[cur]
        out.append(cur)
    return out


def _deferred_releases(nodes: list[ast.AST]) -> set[ast.Call]:
    """Acquisitions bound to a name whose ``.release`` is handed to a stack.

    ``lock = campaign_lock(cid)`` / ``stack.callback(lock.release)`` is the
    accumulating form written one statement apart, so the acquisition is not
    inside the pushing call and the ancestor walk cannot see it. A *single*
    manual acquire/release pair is not flagged -- ``locks.py``'s own
    ``campaign_lock_nowait`` and ``best_effort_campaign_lock`` take exactly one
    lock and give it back in a ``finally`` -- because one lock, however it is
    spelled, cannot be half of a deadlock.
    """
    bound: dict[str, ast.Call] = {}
    for node in nodes:
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                and _acquires(node.value)):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    bound[target.id] = node.value

    out: set[ast.Call] = set()
    for node in nodes:
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in _STACK_PUSHERS):
            continue
        for arg in node.args:
            if (isinstance(arg, ast.Attribute) and arg.attr == "release"
                    and isinstance(arg.value, ast.Name) and arg.value.id in bound):
                out.add(bound[arg.value.id])
    return out


def _lock_items(with_node: ast.AST) -> list[ast.Call]:
    """The acquisitions this ``with`` statement enters directly."""
    return [item.context_expr for item in with_node.items
            if isinstance(item.context_expr, ast.Call)
            and _acquires(item.context_expr)]


def scan(src: str) -> list[tuple[ast.Call, str, str]]:
    """Flagged ``(node, idiom, scope name)`` for one module's source."""
    tree = ast.parse(src)
    found: list[tuple[ast.Call, str, str]] = []
    for scope, nodes in _scopes(tree):
        where = getattr(scope, "name", "<module>")
        parent = _ancestors(nodes)
        deferred = _deferred_releases(nodes)
        for call in [n for n in nodes if isinstance(n, ast.Call) and _acquires(n)]:
            chain = _chain(call, parent)
            # The `with` this call is an ITEM of, if any -- not merely the
            # nearest enclosing one, which for `with lock(a): x = lock(b)` is
            # `a`'s block and would hide the second acquisition inside it.
            enclosing = [w for w in chain if isinstance(w, _WITHS)]
            own_with = next((w for w in enclosing if call in _lock_items(w)), None)

            if call in deferred or any(
                    isinstance(a, ast.Call) and isinstance(a.func, ast.Attribute)
                    and a.func.attr in _STACK_PUSHERS for a in chain):
                found.append((call, "registered on an exit stack", where))
                continue
            if any(isinstance(a, _LOOPS) for a in chain) and own_with is None:
                found.append((call, "acquired in a loop, unbounded by a `with`", where))
                continue

            # Open at the same time as another acquisition: a sibling item of
            # the same `with`, or one whose block this sits inside.
            siblings = [c for c in _lock_items(own_with) if c is not call] \
                if own_with is not None else []
            outer = [c for w in enclosing if w is not own_with for c in _lock_items(w)]
            if any(not _same_campaign(call, other) for other in siblings + outer):
                found.append((call, "a second campaign lock held at the same time", where))
    return found


def _reason(src: str, node: ast.AST, others=()) -> str | None:
    return guard_markers.marker_reason(MARKER, src, node, others)


PROHIBITED = [
    # The #267 shape itself, in both holders' original spellings.
    ("exit stack in a loop",
     "def publish():\n"
     "    with ExitStack() as stack:\n"
     "        for c in campaigns.list_campaigns():\n"
     "            stack.enter_context(locks.campaign_lock(c['id']))\n"),
    ("exit stack, no loop",
     "def rebind(a, b):\n"
     "    with ExitStack() as stack:\n"
     "        stack.enter_context(locks.campaign_lock(a))\n"
     "        stack.enter_context(locks.campaign_lock(b))\n"),
    ("acquired in a comprehension",
     "def publish(cids, stack):\n"
     "    held = [stack.enter_context(locks.campaign_lock(c)) for c in cids]\n"),
    ("hand-rolled acquire in a loop",
     "def publish(cids):\n"
     "    for cid in cids:\n"
     "        campaign_lock(cid).acquire()\n"),
    ("release deferred by callback",
     "def publish(cid, stack):\n"
     "    lock = locks.campaign_lock(cid)\n"
     "    lock.acquire()\n"
     "    stack.callback(lock.release)\n"),
    ("two locks in one with",
     "def move(src, dst):\n"
     "    with locks.campaign_lock(src), locks.campaign_lock(dst):\n"
     "        pass\n"),
    ("nested with, two campaigns",
     "def move(src, dst):\n"
     "    with locks.campaign_lock(src):\n"
     "        with locks.campaign_lock(dst):\n"
     "            pass\n"),
    ("nested through an intervening block",
     "def move(src, dst):\n"
     "    with locks.campaign_lock(src):\n"
     "        if ok:\n"
     "            with store.locks.campaign_lock(dst):\n"
     "                pass\n"),
    # A best-effort or non-blocking acquisition is still an acquisition: holding
    # one while blocking on another is one side of the same ABBA.
    ("best-effort under a blocking hold",
     "def move(src, dst):\n"
     "    with locks.campaign_lock(src):\n"
     "        with locks.best_effort_campaign_lock(dst):\n"
     "            pass\n"),
    # Hand-rolled, so there is no second `with` to nest -- the second lock is
    # simply acquired inside the first one's block.
    ("a manual acquire inside another lock's block",
     "def move(src, dst):\n"
     "    with locks.campaign_lock(src):\n"
     "        other = locks.campaign_lock(dst)\n"
     "        other.acquire()\n"),
    ("hold_all under a single hold",
     "def rebind(cid, cids):\n"
     "    with locks.campaign_lock(cid):\n"
     "        with locks.hold_all(cids):\n"
     "            pass\n"),
    ("the multi-holder helper under a single hold",
     "def publish(cid):\n"
     "    with locks.campaign_lock(cid):\n"
     "        with _campaign_locks():\n"
     "            pass\n"),
]


@pytest.mark.parametrize("label,src", PROHIBITED, ids=[p[0] for p in PROHIBITED])
def test_multi_lock_shapes_are_flagged(label, src):
    assert scan(src), f"{label} slipped past the guard"


ALLOWED = [
    # The overwhelmingly common shape, and the reason the guard is not simply
    # "one acquisition per function".
    ("one lock",
     "def write(cid):\n    with locks.campaign_lock(cid):\n        pass\n"),
    ("one lock per iteration, released each time",
     "def sweep(cids):\n"
     "    for cid in cids:\n"
     "        with locks.campaign_lock(cid):\n"
     "            pass\n"),
    ("two locks in sequence, never overlapping",
     "def two(a, b):\n"
     "    with locks.campaign_lock(a):\n"
     "        pass\n"
     "    with locks.campaign_lock(b):\n"
     "        pass\n"),
    # Reentrant: the same RLock, taken twice by one thread.
    ("the same campaign nested",
     "def write(cid):\n"
     "    with locks.campaign_lock(cid):\n"
     "        with locks.campaign_lock(cid):\n"
     "            pass\n"),
    ("hold_all, which is the sanctioned form",
     "def rebind(cids):\n"
     "    with locks.hold_all(cids):\n"
     "        pass\n"),
    # The real spelling in `module_edit`: the generator is hold_all's ARGUMENT,
    # so the acquisition is not inside the iteration -- it is the one call that
    # consumes it, under the one sort.
    ("hold_all over a generator expression",
     "def publish():\n"
     "    with locks.hold_all(c['id'] for c in campaigns.list_campaigns()):\n"
     "        pass\n"),
    ("a loop inside hold_all that acquires nothing",
     "def rebind(cids):\n"
     "    with locks.hold_all(cids):\n"
     "        for c in cids:\n"
     "            audit.clear_baselines(c)\n"),
    # A closure is not entered by the block it is defined in.
    ("a callback defined beside a lock",
     "def turn(cid):\n"
     "    with locks.campaign_lock(cid):\n"
     "        pass\n"
     "    def finalize(other):\n"
     "        with locks.campaign_lock(other):\n"
     "            pass\n"),
    # Not this rule's business: neither is a campaign lock.
    ("the module-edit lock",
     "def publish():\n"
     "    with locks.module_edit_lock():\n"
     "        with locks.config_lock():\n"
     "            pass\n"),
    ("an unrelated stack push",
     "def read(paths):\n"
     "    with ExitStack() as stack:\n"
     "        for p in paths:\n"
     "            stack.enter_context(open(p))\n"),
]


@pytest.mark.parametrize("label,src", ALLOWED, ids=[a[0] for a in ALLOWED])
def test_single_lock_shapes_are_not_flagged(label, src):
    assert not scan(src), f"{label} was flagged and should not be"


def test_a_valid_marker_exempts_the_site():
    src = ("def move(a, b):\n"
           "    with locks.campaign_lock(a):\n"
           "        with locks.campaign_lock(b):  # lock-order-ok: b is always < a\n"
           "            pass\n")
    node, _idiom, _where = scan(src)[0]
    assert _reason(src, node) == "b is always < a"


def test_a_reasonless_marker_does_not_exempt():
    src = ("def move(a, b):\n"
           "    with locks.campaign_lock(a):\n"
           "        with locks.campaign_lock(b):  # lock-order-ok:\n"
           "            pass\n")
    node, _idiom, _where = scan(src)[0]
    assert not _reason(src, node), "a bare marker must not silence the guard"


def test_a_marker_inside_a_string_does_not_exempt():
    src = ("def move(a, b):\n"
           "    msg = '# lock-order-ok: nope'\n"
           "    with locks.campaign_lock(a):\n"
           "        with locks.campaign_lock(b):\n"
           "            pass\n")
    node, _idiom, _where = scan(src)[0]
    assert not _reason(src, node)


def _package_files() -> list[pathlib.Path]:
    files = sorted(SRC.rglob("*.py"))
    assert len(files) > 50, f"the scan found only {len(files)} files -- glob broken?"
    return files


def test_the_guard_still_recognizes_the_package_s_own_acquisitions():
    """A guard that has stopped matching reads exactly like a guard that passes.

    So count what it *sees*, not only what it flags: rename or rewrap the lock
    and this fails loud instead of going quietly vacuous.
    """
    seen = [f"{p.relative_to(SRC).as_posix()}:{n.lineno}"
            for p in _package_files()
            for n in ast.walk(ast.parse(p.read_text(encoding="utf-8")))
            if isinstance(n, ast.Call) and _acquires(n)]
    assert len(seen) > 30, f"the matcher sees only {len(seen)} acquisitions: {seen}"
    assert any(s.startswith("routes/worlds.py") for s in seen), seen
    assert any(s.startswith("store/module_edit/") for s in seen), seen


def test_only_hold_all_holds_more_than_one_campaign_lock():
    """The real scan: every multi-lock site is `hold_all` or carries a reason."""
    violations: list[str] = []
    exempted: list[str] = []
    for path in _package_files():
        src = path.read_text(encoding="utf-8")
        found = scan(src)
        nodes = [n for n, _i, _w in found]
        rel = path.relative_to(SRC).as_posix()
        for node, idiom, where in found:
            if (rel, where) == SORTER:
                continue
            if _reason(src, node, [o for o in nodes if o is not node]):
                exempted.append(f"{rel}:{node.lineno} {idiom}")
            else:
                violations.append(f"{rel}:{node.lineno} in {where}(): {idiom}")

    assert not violations, (
        "a second multi-campaign lock holder (#267): acquire through "
        "`locks.hold_all`, which is the only place that sorts, or annotate the "
        "line with `# lock-order-ok: <why this one cannot deadlock>`:\n  "
        + "\n  ".join(violations))
    assert len(exempted) <= 1, (
        f"{len(exempted)} lock-order-ok exemptions; each is a holder outside the "
        f"one sorted path, so they need review rather than a raised limit: {exempted}")


def test_the_sorter_is_where_the_guard_says_it_is():
    """The exemption is keyed on a file and a function name, so a `hold_all`
    that moves or is renamed must move this constant with it -- rather than
    leaving a hole named after a function that no longer exists."""
    rel, name = SORTER
    src = (SRC / rel).read_text(encoding="utf-8")
    found = scan(src)
    assert [w for _n, _i, w in found if w == name], (
        f"{rel}:{name} no longer reads as a multi-lock holder -- if `hold_all` "
        f"moved or was renamed, move SORTER with it; the exemption is otherwise "
        f"a hole named after a function that no longer exists")
    nodes = [n for n, _i, _w in found]
    others = [f"{w}():{n.lineno}" for n, _i, w in found if w != name
              and not _reason(src, n, [o for o in nodes if o is not n])]
    assert not others, (
        f"{rel} grew a second multi-lock holder beside {name}: {others}")


def test_hold_all_earns_its_exemption_by_sorting():
    """Why `hold_all` is exempt and nothing else is: it is the one holder that
    imposes an order. Structural companion to
    `test_locks_store.test_hold_all_acquires_in_sorted_order`, which proves the
    order behaviourally -- this one proves the sort is in the acquisition path
    rather than somewhere else in the function.
    """
    rel, name = SORTER
    tree = ast.parse((SRC / rel).read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
              and n.name == name)
    loops = [n for n in ast.walk(fn) if isinstance(n, (ast.For, ast.AsyncFor))
             if any(isinstance(c, ast.Call) and _acquires(c) for c in ast.walk(n))]
    assert loops, f"{name} no longer acquires in a loop -- re-read this guard"
    for loop in loops:
        sorts = any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                    and n.func.id == "sorted" for n in ast.walk(loop.iter))
        assert sorts, (
            f"{name} acquires over an unsorted iterable at line {loop.lineno}: "
            f"{ast.unparse(loop.iter)} -- the sort IS the fix for #267")
