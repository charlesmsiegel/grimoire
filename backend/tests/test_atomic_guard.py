"""Every store writer goes through store.atomic (#233).

The bug this guards was drift, not a mistake: six mechanics-era modules grew
the temp+replace pattern and the older core never got it, for about a year,
because nothing failed. A test fails.

Honest about its reach: this is a **regression check over the write APIs this
package actually uses**, not a proof that every writer is covered. Code can
still reach the disk through a library that takes an output path -- PIL was
exactly that -- and no static check catches those. What it does catch is the
specific drift that caused the issue: a new record writer added with a plain
``write_text``.
"""

from __future__ import annotations

import ast
import pathlib

import grimoire.store as store_pkg

STORE = pathlib.Path(store_pkg.__file__).parent

# Path methods that publish bytes straight to a live pathname.
_PATH_WRITERS = ("write_text", "write_bytes")
# Builtins that open a handle for writing.
_OPENERS = ("open",)
_WRITE_MODES = ("w", "wb", "wt", "a", "ab", "at", "w+", "r+")

MARKER = "atomic-ok:"


def _marker_reason(src: str, node: ast.AST) -> str | None:
    """The `# atomic-ok: <reason>` text attached to a call, if any.

    Looked for on the call's own line and on the lines just above it, so a
    reason long enough to be useful can sit on its own line.
    """
    lines = src.splitlines()
    end = getattr(node, "end_lineno", node.lineno)
    window = lines[max(0, node.lineno - 4):end]
    for line in window:
        _, sep, reason = line.partition(MARKER)
        if sep:
            return reason.strip()
    return None


def _write_calls(tree: ast.AST):
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Attribute) and f.attr in _PATH_WRITERS:
            # atomic.write_text(...) is the helper itself, not a raw write
            if isinstance(f.value, ast.Name) and f.value.id == "atomic":
                continue
            yield node, f"Path.{f.attr}"
        elif isinstance(f, ast.Attribute) and f.attr == "write" and \
                isinstance(f.value, ast.Name) and f.value.id == "os":
            yield node, "os.write"
        elif isinstance(f, ast.Name) and f.id in _OPENERS:
            mode = next((a.value for a in node.args[1:2]
                         if isinstance(a, ast.Constant)), None)
            mode = mode or next((k.value.value for k in node.keywords
                                 if k.arg == "mode" and isinstance(k.value, ast.Constant)), None)
            if isinstance(mode, str) and mode in _WRITE_MODES:
                yield node, f"open(..., {mode!r})"
        elif isinstance(f, ast.Attribute) and f.attr == "open" and \
                isinstance(f.value, ast.Name) and f.value.id == "io":
            yield node, "io.open"


def _offenders():
    for path in sorted(STORE.rglob("*.py")):
        if path.name == "atomic.py":
            continue  # the helper is where the raw writes are supposed to live
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node, kind in _write_calls(tree):
            reason = _marker_reason(src, node)
            if reason is None:
                yield f"{path.relative_to(STORE)}:{node.lineno}: {kind}"


def test_every_store_write_goes_through_atomic_or_is_marked():
    offenders = list(_offenders())
    assert not offenders, (
        "raw write(s) in store/ — route through store.atomic, or annotate the "
        "line with `# atomic-ok: <why this one is safe>`:\n  "
        + "\n  ".join(offenders))


def test_the_marker_is_not_a_rubber_stamp():
    """Exceptions must stay few and must say why. A bare `# atomic-ok:` with no
    reason, or a growing pile of them, means the rule is being routed around
    rather than applied."""
    marked = []
    for path in sorted(STORE.rglob("*.py")):
        if path.name == "atomic.py":
            continue
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node, kind in _write_calls(tree):
            reason = _marker_reason(src, node)
            if reason is not None:
                marked.append((f"{path.relative_to(STORE)}:{node.lineno}", reason))

    unexplained = [loc for loc, reason in marked if len(reason) < 15]
    assert not unexplained, f"`atomic-ok` with no real reason: {unexplained}"
    assert len(marked) <= 3, (
        f"{len(marked)} atomic-ok exemptions; each one is a hole in the "
        f"guarantee, so they need review rather than a raised limit: {marked}")


def test_the_guard_actually_detects_a_raw_write():
    """A guard that cannot fail is worse than none -- it reads as coverage."""
    tree = ast.parse("p.write_text('x', encoding='utf-8')\n")
    assert [kind for _n, kind in _write_calls(tree)] == ["Path.write_text"]

    tree = ast.parse("atomic.write_text(p, 'x')\n")
    assert list(_write_calls(tree)) == [], "helper calls must not be flagged"

    tree = ast.parse("with open(p, 'w') as f:\n    f.write('x')\n")
    assert [kind for _n, kind in _write_calls(tree)] == ["open(..., 'w')"]


def test_multi_line_calls_are_not_missed():
    """The reason this is an AST walk and not a grep."""
    tree = ast.parse("_meta_path(root, cid).write_text(\n    dump(meta),\n"
                     "    encoding='utf-8')\n")
    assert [kind for _n, kind in _write_calls(tree)] == ["Path.write_text"]
