"""Guard: a tool never writes the store, takes a blocking lock or starts a run
(01g spec 3.6, "Who executes tools, and what they may do").

A tool is the function a `ToolSpec(...)` is built with (`fn=`, or its fourth
positional argument), and so is every executor handed to a tool loop
(`run_tools(..., execute=...)` / `stream_tools`): a caller's executor is a tool
in all but name. Each such function, resolved by name to a `def` in the same
module, fails when its module imports `store.atomic`, or when its body calls
`campaign_lock`, `hold_all`, `start_detached` or a `run_*` of `runs`.

The accounting a tool may file -- ledger rows (`usage`), error-store entries
(`errors`) and a prompt capture (`_record_prompt`, which takes
`campaign_lock_nowait` and drops on contention) -- is allowed by name: none
of them is in the list above.

Honest about its reach, as `test_atomic_guard.py` is: it sees direct calls in
the tool's own body, not a write three helpers down, and a tool passed as an
expression it cannot resolve to a `def` in its module is not checked. A
genuinely safe call is cleared with `# tool-ok: <reason>` on the `def` line,
capped at `MARKER_CAP`. No tool exists in the package yet (12 and 02-C4 add
them), so the planted cases below are what prove the check.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

import grimoire

from . import guard_markers

PACKAGE = pathlib.Path(grimoire.__file__).parent
MARKER = "tool-ok"
MARKER_CAP = 3

#: Calls a tool may not make, by the name called.
FORBIDDEN = frozenset({"campaign_lock", "hold_all", "start_detached"})
#: The loop's doors, whose `execute=` is a tool.
LOOP_DOORS = frozenset({"run_tools", "stream_tools"})


def _called(node: ast.Call) -> str:
    func = node.func
    return func.id if isinstance(func, ast.Name) else (
        func.attr if isinstance(func, ast.Attribute) else "")


def _receiver(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return func.value.id
    return ""


def _tool_names(tree: ast.AST) -> set[str]:
    """The names of every function handed to `ToolSpec(...)` as its `fn`,
    and to a loop door as `execute=`."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        called = _called(node)
        found: list[ast.AST] = []
        if called == "ToolSpec":
            found += [k.value for k in node.keywords if k.arg == "fn"]
            found += node.args[3:4]
        elif called in LOOP_DOORS:
            found += [k.value for k in node.keywords if k.arg == "execute"]
        names |= {n.id for n in found if isinstance(n, ast.Name)}
    return names


def _imports_atomic(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.endswith("atomic") or any(a.name == "atomic" for a in node.names):
                return True
        if isinstance(node, ast.Import) and any(a.name.endswith(".atomic")
                                                for a in node.names):
            return True
    return False


def _forbidden_calls(fn: ast.AST) -> list[str]:
    out = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        called = _called(node)
        if called in FORBIDDEN or (called.startswith("run_") and _receiver(node) == "runs"):
            out.append(f"{called} at line {node.lineno}")
    return out


def problems(src: str, where: str) -> tuple[list[str], int]:
    """`(problems, markers used)` for one module's tools."""
    tree = ast.parse(src)
    names = _tool_names(tree)
    if not names:
        return [], 0
    atomic = _imports_atomic(tree)
    out: list[str] = []
    used = 0
    defs = [n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
    for fn in defs:
        found = _forbidden_calls(fn)
        if atomic:
            found.append("its module imports store.atomic")
        if not found:
            continue
        reason = guard_markers.marker_reason(MARKER, src, fn)
        if reason:
            used += 1
            continue
        out.append(f"{where}:{fn.lineno}: tool {fn.name} -- {', '.join(found)}")
    return out, used


def test_no_tool_writes_locks_or_starts_a_run():
    found: list[str] = []
    markers = 0
    for path in sorted(PACKAGE.rglob("*.py")):
        got, used = problems(path.read_text(encoding="utf-8"),
                             path.relative_to(PACKAGE).as_posix())
        found += got
        markers += used
    assert not found, "tools must not write, lock or start a run:\n  " + "\n  ".join(found)
    assert markers <= MARKER_CAP, f"{markers} `# tool-ok:` markers; the cap is {MARKER_CAP}"


_PRELUDE = "from ..tool_calls import ToolSpec\nfrom .. import inference as operations\n"


@pytest.mark.parametrize("src", [
    # A tool that takes the campaign lock.
    _PRELUDE + ("def lookup(args, ctx):\n"
                "    with store.locks.campaign_lock(ctx.campaign):\n"
                "        return read()\n"
                "SPEC = ToolSpec('lookup', 'd', {}, lookup)\n"),
    # One that starts a run, handed in by keyword.
    _PRELUDE + ("async def later(args, ctx):\n"
                "    runs.run_draft(app, subject, work)\n"
                "SPEC = ToolSpec(name='later', description='d', parameters={}, fn=later)\n"),
    # A writer's module.
    ("from ..store import atomic\nfrom ..tool_calls import ToolSpec\n"
     "def save(args, ctx):\n    return 1\n"
     "SPEC = ToolSpec('save', 'd', {}, save)\n"),
    # A caller's executor is a tool too.
    _PRELUDE + ("async def execute(call, ctx):\n"
                "    with store.locks.hold_all([ctx.campaign]):\n"
                "        return out\n"
                "async def go():\n"
                "    await operations.run_tools('x', m, execute=execute, resolved=r)\n"),
])
def test_the_guard_flags_a_planted_writer(src):
    found, _ = problems(src, "planted.py")
    assert found, src


@pytest.mark.parametrize("src", [
    # A reader that files its accounting.
    _PRELUDE + ("def lookup(args, ctx):\n"
                "    with store.usage.meter('x', run_id=ctx.run_id) as m:\n"
                "        store.errors.record('x', 'k', 'd')\n"
                "    _record_prompt(ctx.campaign, 'x', {})\n"
                "    return read(ctx.root)\n"
                "SPEC = ToolSpec('lookup', 'd', {}, lookup)\n"),
    # A forbidden call outside any tool is not this guard's business.
    _PRELUDE + "def elsewhere():\n    store.locks.campaign_lock('c')\n",
    # A cleared one.
    _PRELUDE + ("def lookup(args, ctx):  # tool-ok: planted -- the lock is a test double\n"
                "    store.locks.campaign_lock(ctx.campaign)\n"
                "SPEC = ToolSpec('lookup', 'd', {}, lookup)\n"),
])
def test_the_guard_passes_a_planted_reader(src):
    found, _ = problems(src, "planted.py")
    assert found == [], src
