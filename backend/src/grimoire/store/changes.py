"""Per-campaign record-change log: the latest write-back delta (previous -> current)
for each browsable record (characters/lore/locations). Stored at <campaign>/changes.json,
keyed by "<kind>/<id>". Pure JSON IO + a stdlib line diff. Written by absorb.apply_edits,
read by the GET /changes route.
"""

from __future__ import annotations

import difflib
import json
from pathlib import Path

from . import atomic
from .campaigns import paths as campaigns_paths


#: The staged-edit kinds whose write-back lands here. Declared beside the log
#: rather than beside the writer, because it describes what this file covers and
#: two modules now need it: `absorb.apply`, which records, and `store.undo`,
#: which puts the latest write-back back and has to keep this in step with the
#: record it just moved. (`absorb.apply` re-exports it under its old name; a
#: second literal in the other caller is exactly how the two would drift.)
BROWSABLE_KINDS: tuple[str, ...] = (
    "character_state", "dossier", "lore", "authored", "new_character",
    "new_location", "new_lore")


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


def _path(cid: str) -> Path:
    return campaigns_paths.campaign_root(cid) / "changes.json"


def read(cid: str) -> dict:
    p = _path(cid)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def record(cid: str, sid: str, changes: dict[str, list[dict]]) -> None:
    """Upsert the touched records, replacing any prior entry (rolling: only the latest
    write-back per record is kept). No-op when nothing was recorded."""
    if not changes:
        return
    data = read(cid)
    for ref, fields in changes.items():
        data[ref] = {"scene": sid, "fields": fields}
    atomic.write_text(_path(cid), json.dumps(data, indent=2, sort_keys=True) + "\n")


def repoint_scenes(cid: str, mapping: dict[str, str]) -> None:
    """Follow renamed scene ids in each record's scene field."""
    data = read(cid)
    hit = False
    for rec in data.values():
        if rec.get("scene") in mapping:
            rec["scene"] = mapping[rec["scene"]]
            hit = True
    if hit:
        atomic.write_text(_path(cid), json.dumps(data, indent=2, sort_keys=True) + "\n")
