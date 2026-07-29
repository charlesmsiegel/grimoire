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


def _comment_text(line: str) -> str | None:
    """The comment part of a source line, or None if it has no real comment.

    Tokenized rather than split on "#", so a marker sitting inside a string
    literal is not mistaken for an exemption -- an assertion message or a
    docstring that happens to quote the marker must not silence the guard.
    Unterminated multi-line constructs make a single line untokenizable; those
    yield None, which fails closed (no exemption).
    """
    try:
        for tok in tokenize.generate_tokens(io.StringIO(line).readline):
            if tok.type == tokenize.COMMENT:
                return tok.string
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return None
    return None


def marker_reason(marker: str, src: str, node: ast.AST) -> str | None:
    """The `# <marker>: <reason>` attached to THIS call, if any.

    Accepted in exactly two places: on one of the call's own lines, or in the
    unbroken comment block immediately above it. A fixed backward window (which
    the atomic guard used to use) let one marker cover a raw call added just
    below the one it was written for — the exemption would silently spread,
    which is the same invisible drift the guards exist to stop.

    The marker must appear in an actual comment. Matching raw line text let a
    string literal containing the marker exempt a call, which is both a way to
    silence a guard by accident and a way to do it on purpose.
    """
    lines = src.splitlines()
    end = getattr(node, "end_lineno", node.lineno)

    for line in lines[node.lineno - 1:end]:
        comment = _comment_text(line)
        if comment is not None:
            _, sep, reason = comment.partition(marker)
            if sep:
                return reason.strip()

    # Walk up through contiguous comment lines only; a blank line or any code
    # ends the block and detaches the marker from this call.
    i = node.lineno - 2
    while i >= 0 and lines[i].lstrip().startswith("#"):
        _, sep, reason = lines[i].partition(marker)
        if sep:
            return reason.strip()
        i -= 1
    return None
