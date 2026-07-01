"""Per-campaign record-change log: the latest write-back delta (previous -> current)
for each browsable record (characters/lore/locations). Stored at <campaign>/changes.json,
keyed by "<kind>/<id>". Pure JSON IO + a stdlib line diff. Written by absorb.apply_edits,
read by the GET /changes route.
"""

from __future__ import annotations

import difflib
import json
from pathlib import Path

from . import campaigns


def line_diff(before: str, after: str) -> list[dict]:
    """Tagged per-line diff of two text blobs. A `replace` span emits its deletes then
    its inserts, so the frontend can render removed-then-added lines."""
    a, b = before.splitlines(), after.splitlines()
    out: list[dict] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b).get_opcodes():
        if tag == "equal":
            out += [{"op": "equal", "text": t} for t in a[i1:i2]]
        elif tag == "delete":
            out += [{"op": "delete", "text": t} for t in a[i1:i2]]
        elif tag == "insert":
            out += [{"op": "insert", "text": t} for t in b[j1:j2]]
        else:  # replace
            out += [{"op": "delete", "text": t} for t in a[i1:i2]]
            out += [{"op": "insert", "text": t} for t in b[j1:j2]]
    return out
