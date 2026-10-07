"""Guard: every generation names a task, and every task has a route (#142).

`store/routing.py` maps a task string to the connection setting that decides
where that generation is sent. The map is code, because tasks are code — and
that is exactly how it rots: a new LLM call site is written, reviewed and
shipped, nothing fails, and one more generation quietly ignores the routing page
while appearing to obey it. The same failure `store/locks.py`'s prose domain list
had, and this guard is `test_lock_domain_guard.py`'s shape applied to it.

Four claims, held against the AST of `routes/`:

- every `require_inference(...)` call passes a **task literal**,
- that literal is claimed by exactly one route in `routing.ROUTES`,
- an `operation=` the call passes is a literal equal to that route's operation
  (absent means `"generate"`, which must equal it too), and
- `require_inference` is only ever *called*: a reference handed around as a
  value (`run_in_threadpool(require_inference, ...)`) carries its task as an
  argument nothing here reads.

Honest about its reach, in the house style:

- **It sees `require_inference`, not "a call to a provider".** A route that
  reached for `llm_connections.get_active()` itself, or hand-built a connection
  dict, would route nothing and this would not notice. Nor does it see a direct
  `store.inference.resolve.resolve(...)`: the display-only reads that ask "what
  would chat run on" (`config._send_images_reach`, the scene context view) go
  that way on purpose, because they must never refuse. That seam is the one
  every existing call site uses, and `test_the_only_way_into_a_provider_is_the_
  seam` below pins it so a second seam has to be declared rather than
  discovered.
- **A campaign override only reaches a call that hands over a campaign id**,
  and `cid` is not proof of one: `routes/characters.py` spells a CHARACTER id
  `cid`, so `require_inference("tagline", cid)` there would apply some other
  record's routing and read perfectly naturally. The check below is the
  decorated half -- a route handler passing a second argument must be mounted
  under `/campaigns/{cid}`. A helper with no decorator (`_draft_description`)
  is checked only for the argument's spelling, and its callers are on a human.
- **The task inventory is the literals in `routes/`**: a `require_inference`
  first argument, a `store.usage.meter(...)` first argument, and any `task=`
  keyword (which is how `_chat_stream` and `_ephemeral_stream` are told what
  they are streaming). A task assembled at runtime is invisible to all of it --
  `streaming.py` meters the `task` it was handed, and the literal that fed it
  lives at the caller, which is where this finds it.
- **It checks the literal, not the meaning.** Nothing here can tell that the
  scene-break route passes `"scene-break"` rather than `"rolling-summary"`; both
  are known tasks and both are in the `summary` route. What it catches is the
  unrouted call and the unclassified task.
- **A `task=` keyword that is not a generation's task would be miscounted.**
  Every one in `routes/` today names a task; a future keyword argument of that
  name meaning something else would have to be renamed or the inventory
  narrowed, and the failure says which literal it could not place.
- **A non-literal task is a failure**, not a pass: `require_inference(task)`
  where `task` is a variable cannot be checked here, so it has to be argued in
  review and marked `# routing-ok: <reason>` on the line, like every other guard
  in this tree. Markers are parsed by `guard_markers`, not by looking for the
  text: a marker quoted in a docstring (this one, for instance) exempts nothing.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from grimoire.store import routing

from . import guard_markers

ROUTES_DIR = pathlib.Path(__file__).resolve().parents[1] / "src" / "grimoire" / "routes"
#: The exemption marker, `# routing-ok: <reason>`. A marker with no reason fails,
#: the convention every guard in this tree shares.
MARKER = "# routing-ok:"


def _sources():
    for path in sorted(ROUTES_DIR.rglob("*.py")):
        yield path, path.read_text(encoding="utf-8")


def _calls(tree: ast.AST, name: str):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            called = (func.id if isinstance(func, ast.Name)
                      else func.attr if isinstance(func, ast.Attribute) else "")
            if called == name:
                yield node


def _reason(src: str, node: ast.AST, others=()) -> str | None:
    """The `# routing-ok: <reason>` attached to this call, if any."""
    return guard_markers.marker_reason(MARKER.rstrip(":").lstrip("# "), src, node, others)


def test_every_generation_names_a_task_and_every_task_has_a_route():
    unrouted: list[str] = []
    unknown: list[str] = []
    for path, text in _sources():
        tree = ast.parse(text)
        calls = list(_calls(tree, "require_inference"))
        for call in calls:
            where = f"{path.name}:{call.lineno}"
            if _reason(text, call, [c for c in calls if c is not call]) is not None:
                continue
            if not call.args:
                unrouted.append(where)
                continue
            first = call.args[0]
            if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
                unrouted.append(f"{where} (task is not a literal)")
                continue
            if first.value not in routing.TASK_ROUTE:
                unknown.append(f"{where} passes {first.value!r}")
    assert not unrouted, (
        "these generations resolve a connection without naming their task, so no "
        f"routing setting can reach them: {unrouted}")
    assert not unknown, (
        "these tasks are claimed by no route in store/routing.py ROUTES -- add "
        f"the task to the route it belongs to: {unknown}")


#: Both function forms. Nearly every generation route is `async def`, which is
#: an `AsyncFunctionDef` and not a subclass of `FunctionDef` -- checking only the
#: latter made the check below walk the handful of sync helpers and silently skip
#: every route it exists for. Caught by mutating a call site and watching the
#: guard stay green.
_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)


def _route_paths(fn) -> list[str]:
    """The URL paths `fn` is mounted at, from its `@router.<method>("...")`."""
    out = []
    for dec in fn.decorator_list:
        if isinstance(dec, ast.Call) and dec.args:
            first = dec.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                out.append(first.value)
    return out


def test_a_campaign_override_only_reaches_calls_that_pass_a_campaign():
    """The second argument is the campaign whose routing applies.

    A route not mounted under `/campaigns/{cid}` has no campaign to hand over,
    and the id it does have is something else wearing the same name -- which is
    how `routes/characters.py`, where `cid` is a character, would silently apply
    another record's settings.
    """
    wrong = []
    for path, text in _sources():
        tree = ast.parse(text)
        for fn in [n for n in ast.walk(tree) if isinstance(n, _FUNCTIONS)]:
            for call in _calls(fn, "require_inference"):
                if len(call.args) < 2:
                    continue
                second = call.args[1]
                where = f"{path.name}:{call.lineno} ({fn.name})"
                if not (isinstance(second, ast.Name) and second.id == "cid"):
                    wrong.append(f"{where}: second argument is not `cid`")
                    continue
                mounted = _route_paths(fn)
                if mounted and not any("/campaigns/{cid}" in p for p in mounted):
                    wrong.append(f"{where}: mounted at {mounted}, so `cid` is not a campaign")
    assert not wrong, (
        "these calls hand a campaign id to the routing cascade that is not one: "
        f"{wrong}")


def test_the_walk_finds_the_call_sites_and_not_the_definition():
    """A guard that passed because it found nothing is the failure mode here.

    Both halves matter. Finding nothing would make every assertion above
    vacuous; counting `def require_inference(...)` as a call site would make
    the whole suite fail on the one line that is not one.
    """
    seen = 0
    definitions = 0
    for _path, text in _sources():
        tree = ast.parse(text)
        seen += sum(1 for _ in _calls(tree, "require_inference"))
        for node in ast.walk(tree):
            if isinstance(node, _FUNCTIONS) and node.name == "require_inference":
                definitions += 1
                assert not any(c.lineno == node.lineno
                               for c in _calls(tree, "require_inference")), (
                    "the definition line is being read as a call site")
    assert seen >= 15, f"only {seen} call sites found; the walk is not finding them"
    assert definitions == 1, f"expected one definition of the seam, found {definitions}"


def test_the_only_way_into_a_provider_is_the_seam_this_guard_watches():
    """No route resolves a connection behind `require_inference`'s back.

    `get_active()` in `routes/` would be a generation the routing page cannot
    reach, and this guard would report nothing at all about it -- the exact
    invisibility the module docstring above admits to.
    """
    offenders = []
    for path, text in _sources():
        calls = list(_calls(ast.parse(text), "get_active"))
        offenders.extend(f"{path.name}:{call.lineno}" for call in calls
                         if _reason(text, call, [c for c in calls if c is not call]) is None)
    assert not offenders, (
        "these routes reach for the active connection directly instead of "
        f"require_inference, so no route setting applies to them: {offenders}")


def test_the_marker_is_not_a_rubber_stamp():
    """Every exemption is a generation the routing page cannot reach, so they
    stay few and they say why. A bare `# routing-ok:` is not a reason."""
    marked = []
    for path, text in _sources():
        tree = ast.parse(text)
        for name in ("require_inference", "get_active"):
            calls = list(_calls(tree, name))
            for call in calls:
                reason = _reason(text, call, [c for c in calls if c is not call])
                if reason is not None:
                    marked.append((f"{path.name}:{call.lineno}", reason))
        marked.extend((f"{path.name}:{node.lineno}", reason)
                      for node, reason in _value_references(text) if reason is not None)

    unexplained = [loc for loc, reason in marked if len(reason) < 15]
    assert not unexplained, f"`routing-ok` with no real reason: {unexplained}"
    assert len(marked) <= 3, (
        f"{len(marked)} routing-ok exemptions; each is a call no route setting "
        f"can reach, so they need review rather than a raised limit: {marked}")


def test_the_guard_actually_detects_an_unrouted_call():
    """A guard that cannot fail reads as coverage without being any."""
    tree = ast.parse("conn = require_inference()\n")
    call = next(_calls(tree, "require_inference"))
    assert not call.args
    tree = ast.parse('conn = require_inference("chat", cid)\n')
    call = next(_calls(tree, "require_inference"))
    assert call.args[0].value == "chat"
    # And a marker quoted in a string exempts nothing.
    src = 'x = """# routing-ok: not really"""\nconn = require_inference()\n'
    call = next(_calls(ast.parse(src), "require_inference"))
    assert _reason(src, call) is None


def _operation_mismatches(tree: ast.AST, where: str) -> list[str]:
    """Calls whose `operation=` is not a literal equal to their route's.

    A call whose task is not a known literal is skipped: the task check above
    already fails it, and a second message for the same line says nothing new.
    """
    out = []
    for call in _calls(tree, "require_inference"):
        first = call.args[0] if call.args else None
        if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
            continue
        got = routing.route(first.value)
        if got is None:
            continue
        kws = [kw for kw in call.keywords if kw.arg == "operation"]
        if not kws:
            operation = "generate"
        elif isinstance(kws[0].value, ast.Constant) and isinstance(kws[0].value.value, str):
            operation = kws[0].value.value
        else:
            out.append(f"{where}:{call.lineno} (operation is not a literal)")
            continue
        if operation != got.operation:
            out.append(f"{where}:{call.lineno} passes operation {operation!r} for "
                       f"{first.value!r}, whose route {got.key!r} is {got.operation!r}")
    return out


def test_a_call_sites_operation_matches_its_route():
    """What a call says it is doing agrees with what its route says.

    `operation` picks how the call is served (a `decide` route answers a yes/no
    or a pick), so a call site that disagreed with its route would be resolved
    as one thing and used as another -- and nothing at runtime compares them.
    """
    wrong = []
    for path, text in _sources():
        wrong.extend(_operation_mismatches(ast.parse(text), path.name))
    assert not wrong, (
        "these calls name an operation their route does not have -- pass the "
        f"route's operation (store/routing.py), as a literal: {wrong}")


def test_the_operation_check_flags_a_planted_mismatch():
    tree = ast.parse('r = require_inference("chat", cid, operation="decide")\n')
    assert _operation_mismatches(tree, "planted.py")
    tree = ast.parse('r = require_inference("chat", cid, operation=op)\n')
    assert _operation_mismatches(tree, "planted.py")
    route = routing.route("chat")
    assert route is not None
    tree = ast.parse(f'r = require_inference("chat", cid, operation="{route.operation}")\n'
                     'r = require_inference("chat", cid)\n')
    assert not _operation_mismatches(tree, "planted.py")


def _value_references(text: str) -> list[tuple[ast.AST, str | None]]:
    """Every `require_inference` named other than as a call's callee, with the
    `# routing-ok:` reason attached to it (None for none)."""
    tree = ast.parse(text)
    callees = {id(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)}
    refs = [node for node in ast.walk(tree)
            if ((isinstance(node, ast.Name) and node.id == "require_inference")
                or (isinstance(node, ast.Attribute) and node.attr == "require_inference"))
            and id(node) not in callees]
    return [(node, _reason(text, node, [r for r in refs if r is not node])) for node in refs]


def _bare_references(text: str, where: str) -> list[str]:
    """`require_inference` named anywhere but as the callee of a call, unmarked."""
    return [f"{where}:{node.lineno}" for node, reason in _value_references(text)
            if reason is None]


def test_the_seam_is_only_ever_called():
    """A reference passed as a value hides its task from every check above.

    `run_in_threadpool(require_inference, "tracker-update", cid)` resolves
    exactly as the direct call would, and reads to this guard as a call site
    that does not exist: its task is an argument to something else. Wrap it in a
    lambda instead, so the call -- and its literal -- is where the walk looks.
    """
    bare = []
    for path, text in _sources():
        bare.extend(_bare_references(text, path.name))
    assert not bare, (
        "these hand `require_inference` around rather than calling it, so its "
        "task literal is invisible to the routing guard -- call it inside a "
        f"lambda instead: {bare}")


def test_the_reference_check_flags_a_planted_value():
    planted = 'conn = await run_in_threadpool(require_inference, "tracker-update", cid)\n'
    assert _bare_references(planted, "planted.py") == ["planted.py:1"]
    planted = "seam = common.require_inference\n"
    assert _bare_references(planted, "planted.py") == ["planted.py:1"]
    called = ('conn = await run_in_threadpool(lambda: require_inference("t", cid).conn)\n'
              'conn = common.require_inference("t", cid).conn\n')
    assert _bare_references(called, "planted.py") == []
    marked = "seam = require_inference  # routing-ok: a planted reference, argued here\n"
    assert _bare_references(marked, "planted.py") == []


def _task_literals() -> dict[str, set[str]]:
    """Every task string `routes/` names, and where it was named.

    Three shapes, because a task is spelled at three seams: resolving a
    connection, metering the call, and telling a streamer what it is streaming.
    """
    found: dict[str, set[str]] = {}
    for path, text in _sources():
        tree = ast.parse(text)
        for name in ("require_inference", "meter"):
            for call in _calls(tree, name):
                if call.args and isinstance(call.args[0], ast.Constant) \
                        and isinstance(call.args[0].value, str):
                    found.setdefault(call.args[0].value, set()).add(f"{path.name}:{call.lineno}")
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                for kw in node.keywords:
                    if kw.arg == "task" and isinstance(kw.value, ast.Constant) \
                            and isinstance(kw.value.value, str):
                        found.setdefault(kw.value.value, set()).add(f"{path.name}:{node.lineno}")
    return found


def test_every_task_the_routes_name_is_classified():
    """A metered generation no route claims is one the routing page cannot reach.

    The broadest of the three checks here, and the one that catches the case the
    `require_inference` walk cannot: a new call site that resolves its
    connection through an existing one (a phase of absorb, a director turn) but
    meters under a task of its own.
    """
    unclassified = {task: sorted(where) for task, where in _task_literals().items()
                    if task not in routing.TASK_ROUTE}
    assert not unclassified, (
        "these task strings are metered or streamed but belong to no route in "
        f"store/routing.py: {unclassified}")


@pytest.mark.parametrize("task", sorted(routing.TASK_ROUTE))
def test_every_registered_task_is_actually_named_by_a_call_site(task):
    """The registry may not accumulate phantoms either.

    A task nobody names is a route covering nothing -- the same lie in the other
    direction, and the reason `test_lock_domain_guard` makes its lists shrink
    when the code does.
    """
    assert task in _task_literals(), (
        f"routing.ROUTES claims task {task!r}, but nothing in routes/ names it")
