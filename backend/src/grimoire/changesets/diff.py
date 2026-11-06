"""Human-readable per-file diffs of the exact bytes a change set writes.

The store writes deterministic JSON (sorted keys, two-space indent, trailing newline), so a
line diff of two record files is a readable field-by-field diff. The diff is for reading
only; what is approved and committed is always the exact bytes, never the diff.
"""

from __future__ import annotations

import difflib


def _lines(data: bytes | None) -> list[str]:
    if data is None:
        return []
    return data.decode("utf-8", errors="replace").splitlines(keepends=True)


def unified(old_path: str | None, old: bytes | None, new_path: str, new: bytes) -> str:
    """A unified diff from ``old`` (``None``: no file) to ``new``, labelled with logical paths.

    A revision is diffed against the revision it replaces as head, so a reviewer sees what
    changed rather than a whole new file. Every line of a file without a final newline is
    still shown; such a line is marked as the standard ``\\ No newline at end of file``.
    """
    before, after = _lines(old), _lines(new)
    out = []
    for line in difflib.unified_diff(before, after, fromfile="/dev/null" if old_path is None else f"a/{old_path}",
                                     tofile=f"b/{new_path}", n=3):
        out.append(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n")
    return "".join(out)
