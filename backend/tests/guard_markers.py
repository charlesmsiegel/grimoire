"""Shared marker parsing for the static architecture guards.

Both `test_atomic_guard.py` (#233) and `test_overlay_guard.py` (#248) work the
same way: walk the package's ASTs, flag calls that break a store-wide rule, and
let a human clear a genuinely-safe one with a `# <marker>: <reason>` comment.
The subtle part is *which* call a marker exempts, so it lives here once rather
than being copied into each guard and drifting.
"""

from __future__ import annotations

import ast


def marker_reason(marker: str, src: str, node: ast.AST) -> str | None:
    """The `# <marker>: <reason>` attached to THIS call, if any.

    Accepted in exactly two places: on one of the call's own lines, or in the
    unbroken comment block immediately above it. A fixed backward window (which
    the atomic guard used to use) let one marker cover a raw call added just
    below the one it was written for — the exemption would silently spread,
    which is the same invisible drift the guards exist to stop.
    """
    lines = src.splitlines()
    end = getattr(node, "end_lineno", node.lineno)

    for line in lines[node.lineno - 1:end]:
        _, sep, reason = line.partition(marker)
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
