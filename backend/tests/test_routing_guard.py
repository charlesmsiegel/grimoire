"""Guard: every generation names a task, and every task has a route (#142).

`store/routing.py` maps a task string to the connection setting that decides
where that generation is sent. The map is code, because tasks are code — and
that is exactly how it rots: a new LLM call site is written, reviewed and
shipped, nothing fails, and one more generation quietly ignores the routing page
while appearing to obey it. The same failure `store/locks.py`'s prose domain list
had, and this guard is `test_lock_domain_guard.py`'s shape applied to it.

Two claims, held against the AST of `routes/`:

- every `_require_connection(...)` call passes a **task literal**, and
- that literal is claimed by exactly one route in `routing.ROUTES`.

Honest about its reach, in the house style:

- **It sees `_require_connection`, not "a call to a provider".** A route that
  reached for `llm_connections.get_active()` itself, or hand-built a connection
  dict, would route nothing and this would not notice. That seam is the one
  every existing call site uses, and `test_the_only_way_into_a_provider_is_the_
  seam` below pins it so a second seam has to be declared rather than
  discovered.
- **The task inventory is the literals in `routes/`**: a `_require_connection`
  first argument, a `store.usage.meter(...)` first argument, and any `task=`
  keyword (which is how `_chat_stream` and `_ephemeral_stream` are told what
  they are streaming). A task assembled at runtime is invisible to all of it --
  `streaming.py` meters the `task` it was handed, and the literal that fed it
  lives at the caller, which is where this finds it.
- **It checks the literal, not the meaning.** Nothing here can tell that the
  scene-break route passes `"scene-break"` rather than `"rolling-summary"`; both
  are known tasks and both are in the `summary` route. What it catches is the
  unrouted call and the unclassified task.
- **A non-literal task is a failure**, not a pass: `_require_connection(task)`
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
        calls = list(_calls(tree, "_require_connection"))
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


def test_the_definition_itself_is_not_counted_as_a_call_site():
    """A guard that passed because it found nothing is the failure mode here."""
    seen = 0
    for _path, text in _sources():
        seen += sum(1 for _ in _calls(ast.parse(text), "_require_connection"))
    assert seen >= 15, f"only {seen} call sites found; the walk is not finding them"


def test_the_only_way_into_a_provider_is_the_seam_this_guard_watches():
    """No route resolves a connection behind `_require_connection`'s back.

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
        f"_require_connection, so no route setting applies to them: {offenders}")


def test_the_marker_is_not_a_rubber_stamp():
    """Every exemption is a generation the routing page cannot reach, so they
    stay few and they say why. A bare `# routing-ok:` is not a reason."""
    marked = []
    for path, text in _sources():
        tree = ast.parse(text)
        for name in ("_require_connection", "get_active"):
            calls = list(_calls(tree, name))
            for call in calls:
                reason = _reason(text, call, [c for c in calls if c is not call])
                if reason is not None:
                    marked.append((f"{path.name}:{call.lineno}", reason))

    unexplained = [loc for loc, reason in marked if len(reason) < 15]
    assert not unexplained, f"`routing-ok` with no real reason: {unexplained}"
    assert len(marked) <= 3, (
        f"{len(marked)} routing-ok exemptions; each is a call no route setting "
        f"can reach, so they need review rather than a raised limit: {marked}")


def test_the_guard_actually_detects_an_unrouted_call():
    """A guard that cannot fail reads as coverage without being any."""
    tree = ast.parse("conn = _require_connection()\n")
    call = next(_calls(tree, "_require_connection"))
    assert not call.args
    tree = ast.parse('conn = _require_connection("chat", cid)\n')
    call = next(_calls(tree, "_require_connection"))
    assert call.args[0].value == "chat"
    # And a marker quoted in a string exempts nothing.
    src = 'x = """# routing-ok: not really"""\nconn = _require_connection()\n'
    call = next(_calls(ast.parse(src), "_require_connection"))
    assert _reason(src, call) is None


def _task_literals() -> dict[str, set[str]]:
    """Every task string `routes/` names, and where it was named.

    Three shapes, because a task is spelled at three seams: resolving a
    connection, metering the call, and telling a streamer what it is streaming.
    """
    found: dict[str, set[str]] = {}
    for path, text in _sources():
        tree = ast.parse(text)
        for name in ("_require_connection", "meter"):
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
    `_require_connection` walk cannot: a new call site that resolves its
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
