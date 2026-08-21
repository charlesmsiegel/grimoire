"""Per-campaign record-change log: the latest write-back delta (previous -> current)
for each browsable record — a character, or any of the five entity kinds, whose
body an absorb can evolve (#224). Stored at <campaign>/changes.json,
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


#: Longest DIFFERING span `line_diff` will align exactly. Past it the input is
#: handed back to difflib's `autojunk` heuristic, which is fast and imprecise
#: rather than precise and quadratic. Chosen by measurement: the worst case at
#: this length -- two maximally repetitive spans sharing neither end -- is
#: ~0.1s, and it is ~1s at 3000.
EXACT_DIFF_LIMIT = 1000


def line_diff(before: str, after: str) -> list[dict]:
    """Tagged per-line diff of two text blobs. A `replace` span emits its deletes then
    its inserts, so the frontend can render removed-then-added lines.

    The common prefix and suffix are matched off directly and only the middle
    goes to difflib. That is an optimisation for the record fields this was
    written for and a correctness fix for the prompt sections
    `context.compare` now feeds it (#130), because difflib's `autojunk`
    heuristic engages past 200 lines and then refuses to let any line occurring
    in more than 1% of the input ANCHOR a match. Prompt sections are full of
    exactly that -- a roster, a fact list, a set of world-info entries sharing a
    bullet or a speaker prefix -- and editing one item of a 300-line list came
    back as 150 deletions and 150 insertions.

    Turning the heuristic off outright was the first fix and it was wrong, in
    the way review caught: it is a bound as well as a filter, and without it
    SequenceMatcher goes quadratic on the same repetitive input. Measured, one
    edit in 10,000 identical lines: 13.7s. Trimming instead answers the same
    case in 0.011s AND exactly, because a one-line edit leaves a one-line
    middle. `EXACT_DIFF_LIMIT` covers what trimming cannot -- two long spans
    that genuinely differ throughout -- by handing those back to the heuristic,
    where an imprecise diff of two texts with nothing in common is no loss.
    """
    a, b = before.splitlines(), after.splitlines()
    head = 0
    while head < len(a) and head < len(b) and a[head] == b[head]:
        head += 1
    tail = 0
    while (tail < len(a) - head and tail < len(b) - head
           and a[len(a) - 1 - tail] == b[len(b) - 1 - tail]):
        tail += 1
    mid_a, mid_b = a[head:len(a) - tail], b[head:len(b) - tail]
    junk = max(len(mid_a), len(mid_b)) > EXACT_DIFF_LIMIT

    out: list[dict] = [{"op": "equal", "text": t} for t in a[:head]]
    matcher = difflib.SequenceMatcher(None, mid_a, mid_b, autojunk=junk)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            out += [{"op": "equal", "text": t} for t in mid_a[i1:i2]]
        elif tag == "delete":
            out += [{"op": "delete", "text": t} for t in mid_a[i1:i2]]
        elif tag == "insert":
            out += [{"op": "insert", "text": t} for t in mid_b[j1:j2]]
        else:  # replace
            out += [{"op": "delete", "text": t} for t in mid_a[i1:i2]]
            out += [{"op": "insert", "text": t} for t in mid_b[j1:j2]]
    out += [{"op": "equal", "text": t} for t in a[len(a) - tail:]]
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


def forget_scene(cid: str, sid: str) -> int:
    """Drop every delta this scene left. Returns how many went (#75).

    The panel's row says "this is what the last write-back did to this record".
    Once the scene that wrote it has been cut and its value put back, the row is
    describing a change the record no longer holds — and because this log is
    rolling, there is no earlier row to fall back to. No row at all is the honest
    state, and it is what a record that has never been absorbed already shows.
    """
    data = read(cid)
    doomed = [ref for ref, rec in data.items()
              if isinstance(rec, dict) and rec.get("scene") == sid]
    for ref in doomed:
        del data[ref]
    if doomed:
        atomic.write_text(_path(cid), json.dumps(data, indent=2, sort_keys=True) + "\n")
    return len(doomed)


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
