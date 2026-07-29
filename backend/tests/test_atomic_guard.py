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

import grimoire
import grimoire.store as store_pkg

STORE = pathlib.Path(store_pkg.__file__).parent
# The whole package, not just store/: routes.py wrote a campaign's
# climate.json with a plain write_text, which a store-only scan missed
# entirely. Anything that writes a record belongs under the same rule.
PACKAGE = pathlib.Path(grimoire.__file__).parent

# Path methods that publish bytes straight to a live pathname.
_PATH_WRITERS = ("write_text", "write_bytes")
# Builtins that open a handle for writing.
_OPENERS = ("open",)


def _is_write_mode(mode) -> bool:
    """Any mode that can publish bytes to the named path. Enumerating literals
    ("w", "wb", ...) missed real forms — `open(p, "x")`, `"w+b"`, `"a+"` — so
    this tests for the characters that make a mode writable instead."""
    return isinstance(mode, str) and any(ch in mode for ch in "wax+")

MARKER = "atomic-ok:"


def _marker_reason(src: str, node: ast.AST) -> str | None:
    """The `# atomic-ok: <reason>` attached to THIS call, if any.

    Accepted in exactly two places: on one of the call's own lines, or in the
    unbroken comment block immediately above it. A fixed backward window (which
    this used to use) let one marker cover a raw write added just below the
    call it was written for — the exemption would silently spread, which is the
    same invisible drift the guard exists to stop.
    """
    lines = src.splitlines()
    end = getattr(node, "end_lineno", node.lineno)

    for line in lines[node.lineno - 1:end]:
        _, sep, reason = line.partition(MARKER)
        if sep:
            return reason.strip()

    # Walk up through contiguous comment lines only; a blank line or any code
    # ends the block and detaches the marker from this call.
    i = node.lineno - 2
    while i >= 0 and lines[i].lstrip().startswith("#"):
        _, sep, reason = lines[i].partition(MARKER)
        if sep:
            return reason.strip()
        i -= 1
    return None


def _write_calls(tree: ast.AST):
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Attribute) and f.attr in _PATH_WRITERS:
            # The helper itself is not a raw write. Match the receiver's last
            # name, so `store.atomic.write_text` counts as well as `atomic.` --
            # routes.py reaches it through the package.
            recv = f.value
            recv_name = recv.id if isinstance(recv, ast.Name) else (
                recv.attr if isinstance(recv, ast.Attribute) else None)
            if recv_name == "atomic":
                continue
            yield node, f"Path.{f.attr}"
        elif isinstance(f, ast.Attribute) and f.attr in ("write", "fdopen") and \
                isinstance(f.value, ast.Name) and f.value.id == "os":
            # os.fdopen is how you write a file while bypassing open() entirely
            # -- found by auditing the atlas, which cited exactly such a site.
            yield node, f"os.{f.attr}"
        elif isinstance(f, ast.Name) and f.id in _OPENERS:
            if _is_write_mode(_mode_arg(node)):
                yield node, f"open(..., {_mode_arg(node)!r})"
        elif isinstance(f, ast.Attribute) and f.attr == "open":
            # io.open(p, "w") and, importantly, Path.open("w") — the latter is
            # the most natural way to reintroduce exactly this bug.
            if isinstance(f.value, ast.Name) and f.value.id == "io":
                if _is_write_mode(_mode_arg(node)):
                    yield node, "io.open"
            else:
                # Receiver-agnostic: `p.open("w")` puts the mode first, but
                # `zipfile.open("member", "w")` puts it second. Check both, and
                # require something mode-SHAPED, so `svc.open("item")` is not
                # read as a mode. A false positive here is a loud test failure a
                # human clears with a marker; a false negative is the bug.
                for arg in (node.args[:1] + node.args[1:2]):
                    mode = arg.value if isinstance(arg, ast.Constant) else None
                    if _looks_like_mode(mode) and _is_write_mode(mode):
                        yield node, f"{'.open'}({mode!r})"
                        break
                else:
                    if _is_write_mode(_kw_mode(node)):
                        yield node, f".open(mode={_kw_mode(node)!r})"


def _looks_like_mode(mode) -> bool:
    """A real file mode is short and drawn from a tiny alphabet. Without this,
    any first string argument to any `.open()` reads as a mode."""
    return (isinstance(mode, str) and 0 < len(mode) <= 3
            and set(mode) <= set("rwaxbt+"))


def _kw_mode(node: ast.Call):
    return next((k.value.value for k in node.keywords
                 if k.arg == "mode" and isinstance(k.value, ast.Constant)), None)


def _mode_arg(node: ast.Call):
    """The mode of an open()-shaped call: second positional, else mode=."""
    if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
        return node.args[1].value
    return _kw_mode(node)


def _offenders():
    for path in sorted(PACKAGE.rglob("*.py")):
        if path.name == "atomic.py":
            continue  # the helper is where the raw writes are supposed to live
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node, kind in _write_calls(tree):
            reason = _marker_reason(src, node)
            if reason is None:
                yield f"{path.relative_to(PACKAGE)}:{node.lineno}: {kind}"


def test_every_store_write_goes_through_atomic_or_is_marked():
    offenders = list(_offenders())
    assert not offenders, (
        "raw write(s) in the grimoire package — route through store.atomic, "
        "or annotate the "
        "line with `# atomic-ok: <why this one is safe>`:\n  "
        + "\n  ".join(offenders))


def test_the_marker_is_not_a_rubber_stamp():
    """Exceptions must stay few and must say why. A bare `# atomic-ok:` with no
    reason, or a growing pile of them, means the rule is being routed around
    rather than applied."""
    marked = []
    for path in sorted(PACKAGE.rglob("*.py")):
        if path.name == "atomic.py":
            continue
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node, kind in _write_calls(tree):
            reason = _marker_reason(src, node)
            if reason is not None:
                marked.append((f"{path.relative_to(PACKAGE)}:{node.lineno}", reason))

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

    tree = ast.parse("store.atomic.write_text(p, 'x')\n")
    assert list(_write_calls(tree)) == [], "package-qualified helper calls too"

    tree = ast.parse("with open(p, 'w') as f:\n    f.write('x')\n")
    assert [kind for _n, kind in _write_calls(tree)] == ["open(..., 'w')"]


def test_the_guard_catches_the_less_obvious_write_forms():
    """Enumerating mode literals missed all of these; a review caught it."""
    for src, why in [
        ("p.open('w').write('x')", "Path.open — the most natural reintroduction"),
        ("open(p, 'x').write('b')", "exclusive create"),
        ("open(p, 'w+b').write(b'')", "multi-char mode"),
        ("open(p, 'a+').write('x')", "append-update"),
        ("open(p, mode='wb').write(b'')", "mode as a keyword"),
        ("os.fdopen(fd, 'wb')", "bypasses open() entirely"),
    ]:
        assert list(_write_calls(ast.parse(src))), f"missed: {why} ({src})"

    for src in ["open(p).read()", "open(p, 'r').read()", "p.open('rb').read()"]:
        assert not list(_write_calls(ast.parse(src))), f"false positive: {src}"


def test_the_guard_reads_the_mode_not_just_the_first_string():
    """`.open` is receiver-agnostic, so a zip member name in the first slot
    used to be mistaken for a mode -- hiding the real mode in the second."""
    assert list(_write_calls(ast.parse("archive.open('item', 'w')"))), \
        "mode in the second slot was missed"
    assert not list(_write_calls(ast.parse("svc.open('inventory')"))), \
        "a non-mode string argument was read as a mode"


def test_multi_line_calls_are_not_missed():
    """The reason this is an AST walk and not a grep."""
    tree = ast.parse("_meta_path(root, cid).write_text(\n    dump(meta),\n"
                     "    encoding='utf-8')\n")
    assert [kind for _n, kind in _write_calls(tree)] == ["Path.write_text"]


def test_a_marker_exempts_only_its_own_call():
    """A fixed backward window let one `atomic-ok` cover a raw write added just
    below the call it was written for, so the exemption spread silently -- the
    same invisible drift the guard exists to stop."""
    src = (
        "# atomic-ok: staging dir, published by rename\n"
        "dest.write_bytes(payload)\n"
        "other.write_text(sneaky, encoding='utf-8')\n"
    )
    tree = ast.parse(src)
    calls = list(_write_calls(tree))
    assert len(calls) == 2
    exempt, unexempt = calls[0], calls[1]
    assert _marker_reason(src, exempt[0]) is not None, "the marked call lost its marker"
    assert _marker_reason(src, unexempt[0]) is None, \
        "the next write inherited the marker above its neighbour"


def test_a_marker_survives_a_multi_line_comment_block():
    """Real reasons need more than one line; the block above the call counts."""
    src = (
        "# atomic-ok: unpublished staging tree, published as a\n"
        "# unit by _publish's single rename\n"
        "dest.write_bytes(payload)\n"
    )
    tree = ast.parse(src)
    node, _kind = next(iter(_write_calls(tree)))
    assert _marker_reason(src, node) == "unpublished staging tree, published as a"


def test_a_blank_line_detaches_the_marker():
    src = "# atomic-ok: this reason belongs to something else\n\ndest.write_bytes(x)\n"
    tree = ast.parse(src)
    node, _kind = next(iter(_write_calls(tree)))
    assert _marker_reason(src, node) is None
