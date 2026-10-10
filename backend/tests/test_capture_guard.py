"""Guard: every `decide` call captures its decision (roadmap 01b-C1).

Decision capture is one helper, `routes.decision_capture`: a site opens a
capture scope and hands `decide` that scope's hook. Nothing else files a
decision, so a decide call that passes no `capture=` -- or passes one built
by hand -- is a decision nobody will be able to inspect. This walks the
whole package for `decide` operation calls, found by `test_operation_guard`'s
import-binding recogniser (so an alias is still found, and `Examination.decide`
and world-info activation's `self.decide` never are), and requires one of two
shapes:

- **in `routes/`**: `capture=<scope>.hook(...)`, where `<scope>` is the `as`
  target of an enclosing `async with <...>.capturing(...)` -- the scope the
  hook files into;
- **outside `routes/`**: `capture=<name>`, where `<name>` is a parameter of
  the enclosing function -- a pass-through, whose `routes/` caller supplies
  the hook (01g's Decision-as-tool handler is one by name; a store-level
  helper a later spec adds would be another).

Anything else needs `# capture-ok: <reason>` on the call, under the family's
rule: a marker with no reason fails, and at most `MAX_MARKERS` exist. So a
decide site added by a later spec cannot skip capture without saying why.

What it does not see: whether the scope the hook comes from is opened for
the right campaign, scene or task, or whether a pass-through's caller really
supplies a hook -- the routes-side rule covers the latter only where the
caller is itself a decide call.
"""

from __future__ import annotations

import ast

import pytest

from . import guard_markers
from .test_import_guard import SOURCES
from .test_operation_guard import _walk, decide_calls

MARKER = "capture-ok:"
#: The most exemptions the tree may carry (none today).
MAX_MARKERS = 2


def _parents(tree: ast.AST) -> dict[int, ast.AST]:
    return {id(child): node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}


def _ancestors(node: ast.AST, parents: dict[int, ast.AST]):
    while id(node) in parents:
        node = parents[id(node)]
        yield node


def _capture(call: ast.Call) -> ast.expr | None:
    return next((k.value for k in call.keywords if k.arg == "capture"), None)


def _scopes(call: ast.Call, parents: dict[int, ast.AST]) -> set[str]:
    """The names bound by every `async with <...>.capturing(...) as <name>`
    enclosing `call`."""
    names: set[str] = set()
    for node in _ancestors(call, parents):
        if not isinstance(node, ast.AsyncWith):
            continue
        for item in node.items:
            expr = item.context_expr
            func = expr.func if isinstance(expr, ast.Call) else None
            called = (func.attr if isinstance(func, ast.Attribute)
                      else func.id if isinstance(func, ast.Name) else "")
            if called == "capturing" and isinstance(item.optional_vars, ast.Name):
                names.add(item.optional_vars.id)
    return names


def _parameters(call: ast.Call, parents: dict[int, ast.AST]) -> set[str]:
    """The parameters of the function directly enclosing `call`."""
    for node in _ancestors(call, parents):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            a = node.args
            return {p.arg for p in [*a.posonlyargs, *a.args, *a.kwonlyargs,
                                    *([a.vararg] if a.vararg else []),
                                    *([a.kwarg] if a.kwarg else [])]}
    return set()


def _shaped(call: ast.Call, modname: str, parents: dict[int, ast.AST]) -> bool:
    capture = _capture(call)
    if modname.startswith("grimoire.routes."):
        return (isinstance(capture, ast.Call) and isinstance(capture.func, ast.Attribute)
                and capture.func.attr == "hook" and isinstance(capture.func.value, ast.Name)
                and capture.func.value.id in _scopes(call, parents))
    return isinstance(capture, ast.Name) and capture.id in _parameters(call, parents)


def capture_problems(tree: ast.AST, src: str, modname: str,
                     is_pkg: bool = False) -> tuple[list[str], int]:
    """`(problems, markers)` for one module's decide calls."""
    calls = decide_calls(tree, modname, is_pkg)
    parents = _parents(tree)
    out: list[str] = []
    markers = 0
    for call in calls:
        if _shaped(call, modname, parents):
            continue
        reason = guard_markers.marker_reason(MARKER, src, call,
                                             [c for c in calls if c is not call])
        if reason is None:
            out.append(f"{modname}:{call.lineno}: decide captures nothing through the "
                       "helper (decision_capture.capturing(...) as scope; "
                       "capture=scope.hook())")
        elif not reason:
            out.append(f"{modname}:{call.lineno}: # {MARKER} with no reason")
        else:
            markers += 1
    return out, markers


def test_every_decide_call_captures_through_the_helper():
    problems: list[str] = []
    markers = 0
    for modname, tree, is_pkg in _walk():
        found, used = capture_problems(tree, SOURCES[modname][1], modname, is_pkg)
        problems += found
        markers += used
    assert not problems, "\n  ".join(["every decide call must capture its decision:",
                                      *problems])
    assert markers <= MAX_MARKERS, f"{markers} # {MARKER} markers, past the cap"


def test_the_guard_sees_the_five_sites():
    """Vacuity insurance: the speaker pick, scene-break, voice drift, the
    duplicate check and the reconciliation sweep."""
    seen = sum(len(decide_calls(tree, modname, is_pkg)) for modname, tree, is_pkg in _walk())
    assert seen >= 5


# ---- planted cases ----

_HEAD = "from .. import inference as operations\nfrom . import decision_capture\n"


def _planted(src: str, modname: str = "grimoire.routes.scenes") -> tuple[list[str], int]:
    full = _HEAD + src
    return capture_problems(ast.parse(full), full, modname)


@pytest.mark.parametrize("src", [
    # No capture at all.
    ("async def f(c, r):\n"
     "    await operations.decide('scene-break', items, client=c, resolved=r)\n"),
    # A capture built by hand.
    ("async def f(c, r):\n"
     "    await operations.decide('scene-break', items, client=c, resolved=r,\n"
     "                            capture=lambda m, o, t: None)\n"),
    # A hook off a scope that is not the enclosing one.
    ("async def f(c, r, scope):\n"
     "    await operations.decide('scene-break', items, client=c, resolved=r,\n"
     "                            capture=scope.hook())\n"),
    # In routes/, a pass-through parameter is not enough: routes open the scope.
    ("async def f(c, r, capture):\n"
     "    await operations.decide('scene-break', items, client=c, resolved=r,\n"
     "                            capture=capture)\n"),
    # Through an alias.
    ("from ..inference import decide as pick\n"
     "async def f(c, r):\n"
     "    await pick('scene-break', items, client=c, resolved=r)\n"),
    # A marker with no reason.
    ("async def f(c, r):\n"
     "    # capture-ok:\n"
     "    await operations.decide('scene-break', items, client=c, resolved=r)\n"),
])
def test_the_guard_flags_planted_cases(src):
    assert _planted(src)[0], src


def test_the_guard_flags_a_store_module_that_drops_its_capture():
    src = ("from ... import inference as operations\n"
           "async def f(c, r, capture):\n"
           "    await operations.decide('scene-break', items, client=c, resolved=r)\n")
    assert capture_problems(ast.parse(src), src, "grimoire.store.continuity.helper")[0]


_IN_A_SCOPE = (
    "async def f(c, r):\n"
    "    async with decision_capture.capturing(cid, sid, 'scene-break') as scope:\n"
    "        await operations.decide('scene-break', items, client=c, resolved=r,\n"
    "                                capture=scope.hook())\n")
_PER_PART = (
    "async def f(c, r):\n"
    "    async with decision_capture.capturing(cid, sid, 'voice-drift') as scope:\n"
    "        for aid in todo:\n"
    "            await operations.decide('scene-break', items, client=c, resolved=r,\n"
    "                                    capture=scope.hook(aid))\n")
#: Outside routes/, the caller's own capture handed through.
_PASSED_THROUGH = (
    "from ... import inference as operations\n"
    "async def f(c, r, *, capture=None):\n"
    "    await operations.decide('scene-break', items, client=c, resolved=r,\n"
    "                            capture=capture)\n")


@pytest.mark.parametrize("src", [_IN_A_SCOPE, _PER_PART])
def test_the_guard_passes_a_hook_off_its_enclosing_scope(src):
    assert _planted(src) == ([], 0), src


def test_the_guard_passes_a_pass_through_outside_routes():
    assert capture_problems(ast.parse(_PASSED_THROUGH), _PASSED_THROUGH,
                            "grimoire.store.continuity.helper") == ([], 0)


def test_a_marker_with_a_reason_clears_a_call_and_counts():
    problems, markers = _planted(
        "async def f(c, r):\n"
        "    # capture-ok: an eval harness with no campaign to file into\n"
        "    await operations.decide('scene-break', items, client=c, resolved=r)\n")
    assert (problems, markers) == ([], 1)
