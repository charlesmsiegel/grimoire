"""Read a string-literal union out of `frontend/src/api/types.ts` (capstone §20).

The TS mirrors of the server's vocabularies are hand-written, and each is held
to its one Python tuple by a backend test that parses the file. Two suites do
that (`test_suggest_controls.py` for the chooser's vocabulary,
`test_continuity_graph.py` for the Story Graph's), so the parser lives here
rather than in either of them.
"""

from __future__ import annotations

import re
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"
TYPES = FRONTEND / "api" / "types.ts"
# One or more `"literal"`s joined by `|`, with an optional leading `|`.
STRINGS = r'\s*\|?\s*"[^"]*"(?:\s*\|\s*"[^"]*")*\s*'


def ts_union(src: str, name: str) -> tuple[str, ...]:
    """`export type NAME = "a" | "b" ...;`, in declaration order. A missing
    declaration, or one that is not a plain union of string literals, fails
    by name."""
    found = re.search(rf"export type {name}\s*=([^;]*);", src)
    assert found, f"types.ts declares no `export type {name}`"
    rhs = found.group(1)
    assert re.fullmatch(STRINGS, rhs), f"{name} is not a union of string literals: {rhs!r}"
    return tuple(re.findall(r'"([^"]*)"', rhs))
