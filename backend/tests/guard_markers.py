"""Shared marker parsing for the static architecture guards.

Both `test_atomic_guard.py` (#233) and `test_overlay_guard.py` (#248) work the
same way: walk the package's ASTs, flag calls that break a store-wide rule, and
let a human clear a genuinely-safe one with a `# <marker>: <reason>` comment.
The subtle part is *which* call a marker exempts, so it lives here once rather
than being copied into each guard and drifting.
"""

from __future__ import annotations

import ast
import io
import tokenize


def _comments_by_line(src: str) -> dict[int, str]:
    """{lineno: comment text} for the whole source.

    Tokenized rather than split on "#", so a marker sitting inside a string
    literal is not mistaken for an exemption -- an assertion message or a
    docstring quoting the marker must not silence a guard.

    The whole file is tokenized in one pass, deliberately. Tokenizing each
    physical line on its own is not equivalent: a line *inside* a triple-quoted
    string that happens to start with "#" tokenizes as a comment when read
    alone, so a docstring could still hand out exemptions. Only the full-file
    pass knows it is inside a string. A file that will not tokenize yields no
    comments at all, which fails closed.
    """
    out: dict[int, str] = {}
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT:
                out[tok.start[0]] = tok.string
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return {}
    return out


def _spans(node: ast.AST) -> tuple[int, int]:
    return node.lineno, getattr(node, "end_lineno", node.lineno)


def _extent(node: ast.AST) -> tuple[tuple[int, int], tuple[int, int]]:
    """((start line, start col), (end line, end col)) — the node's real extent.

    Line numbers alone cannot order two calls written on one physical line;
    columns can, which is what `_strictly_inside` needs."""
    return ((node.lineno, node.col_offset),
            (getattr(node, "end_lineno", node.lineno),
             getattr(node, "end_col_offset", node.col_offset)))


def _strictly_inside(inner: ast.AST, outer: ast.AST) -> bool:
    """`inner` is contained by `outer` and is not the same extent.

    Comparing line-span *width* looked equivalent and was not: two calls nested
    on a single line have identical spans, so the width test was false for both
    and a marker meant for the inner one exempted the outer one as well.
    """
    i_start, i_end = _extent(inner)
    o_start, o_end = _extent(outer)
    return o_start <= i_start and i_end <= o_end and (i_start, i_end) != (o_start, o_end)


def _stated_reason(marker: str, comment: str) -> str | None:
    """The reason, if this comment *is* the marker rather than mentions it.

    `partition` matched the marker anywhere in the comment, so
    `# do not add overlay-ok: this is still unsafe` read as a valid exemption
    with the reason "this is still unsafe" -- a warning about the marker turned
    the guard off. The documented form is `# <marker>: <reason>`, so require it
    at the start of the comment body.
    """
    body = comment.lstrip("#").strip()
    if not body.startswith(marker):
        return None
    return body[len(marker):].strip()


def _shares_first_line(a: ast.AST, b: ast.AST) -> bool:
    """Two nodes begin on the same line and neither contains the other."""
    return (a.lineno == b.lineno
            and not _strictly_inside(a, b) and not _strictly_inside(b, a))


def _sole_owner(node: ast.AST, lineno: int, others) -> bool:
    """Whether `node` alone can claim an inline marker on `lineno`.

    It cannot if something strictly inside it also spans that line (the inner
    call owns its own marker), and it cannot if a *sibling* does either:
    `(read(croot), image(croot))  # marker` names neither, so honouring it for
    both would exempt two calls on one reason. Ambiguous placement wins nothing
    — the author splits the line and says which they meant.
    """
    for o in others:
        if not (_spans(o)[0] <= lineno <= _spans(o)[1]):
            continue
        if _strictly_inside(o, node) or _shares_first_line(o, node):
            return False
    return True


def marker_reason(marker: str, src: str, node: ast.AST, others=()) -> str | None:
    """The `# <marker>: <reason>` attached to THIS call, if any.

    Accepted in exactly two places: on one of the call's own lines, or in the
    unbroken comment block immediately above it. A fixed backward window (which
    the atomic guard used to use) let one marker cover a raw call added just
    below the one it was written for — the exemption would silently spread,
    which is the same invisible drift the guards exist to stop.

    The marker must appear in an actual comment. Matching raw line text let a
    string literal containing the marker exempt a call, which is both a way to
    silence a guard by accident and a way to do it on purpose.

    `others` is the rest of the flagged nodes in the same file. An inline marker
    belongs to the *innermost* flagged node containing it, so a marker written
    for an inner call — `read_card(croot, image_path(croot, ...))  # marker`,
    whether split over lines or written on one — cannot also exempt the outer
    one spanning the same comment. Ownership is decided by AST containment
    (line *and* column), because on one physical line the line spans are equal.
    """
    comments = _comments_by_line(src)
    start, end = _spans(node)

    for lineno in range(start, end + 1):
        reason = _stated_reason(marker, comments.get(lineno, ""))
        if reason is None:
            continue
        # Someone else, strictly inside this node, owns this comment -- or two
        # siblings both do, in which case nobody does (see _sole_owner).
        if not _sole_owner(node, lineno, others):
            continue
        return reason

    # A comment block above a statement attaches to the statement, so it belongs
    # to the OUTERMOST flagged node starting there. Without this, a marker
    # written for `dst.write_bytes(...)` also exempted a call nested inside it on
    # the same first line -- the enclosing-direction twin of the inline case.
    # Siblings sharing that first line are ambiguous the same way.
    if any(_strictly_inside(node, o) or _shares_first_line(node, o) for o in others):
        return None

    # Walk up through contiguous comment lines only; a blank line or any code
    # ends the block and detaches the marker from this call. A comment line is
    # one the tokenizer calls a comment AND that holds nothing else.
    lines = src.splitlines()
    i = node.lineno - 1                      # 1-based line above the node
    while i >= 1 and i in comments and lines[i - 1].strip() == comments[i]:
        reason = _stated_reason(marker, comments[i])
        if reason is not None:
            return reason
        i -= 1
    return None
