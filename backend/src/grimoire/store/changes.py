"""Per-campaign record-change log: the latest write-back delta (previous -> current)
for each browsable record — a character, or any of the five entity kinds, whose
body an absorb can evolve (#224). Stored at <campaign>/changes.json,
keyed by "<kind>/<id>". Pure JSON IO + a stdlib line diff. Written by absorb.apply_edits,
read by the GET /changes route.
"""

from __future__ import annotations

import bisect
import difflib
import json
from collections import Counter
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


#: Longest span `line_diff` will hand to difflib in one piece. Past it the span
#: is split on anchor lines first (see `_anchors`), and only what falls between
#: those anchors reaches the quadratic matcher. Chosen by measurement: two
#: maximally repetitive spans of this length sharing neither end cost ~0.1s.
EXACT_DIFF_LIMIT = 1000


def line_diff(before: str, after: str) -> list[dict]:
    """Tagged per-line diff of two text blobs. A `replace` span emits its deletes then
    its inserts, so the frontend can render removed-then-added lines.

    Bounded, which a bare `difflib.SequenceMatcher` is not. That matters here
    because `context.compare` feeds this whole prompt sections (#130) rather
    than the record fields it was written for, and both of difflib's failure
    modes are reachable from user-authored content:

    - **Precision.** Its `autojunk` heuristic engages past 200 lines and then
      refuses to let any line occurring in more than 1% of the input ANCHOR a
      match. Prompt sections are full of that -- a roster, a fact list, entries
      sharing a bullet -- and editing one item of a 300-line list came back as
      150 deletions and 150 insertions.
    - **Cost.** Turning it off was the first fix and it was wrong: the heuristic
      is a bound as well as a filter. One edit in 10,000 identical lines took
      13.7s with it off.

    Neither setting is the answer, and neither is a size cap: review measured
    8,000 DISTINCT lines reordered in adjacent pairs at 4.5s, where `autojunk`
    discards nothing and bounds nothing -- while the shape this feature exists
    for (a transcript with its front trimmed by the packer and an exchange
    appended) shares neither end, so a cap that coarsened long spans would
    report the entire scene as replaced.

    So: match the common prefix and suffix off directly, then split what is left
    on lines that occur exactly ONCE on each side and agree in order. Those are
    unambiguous alignment points -- the patience-diff idea -- and finding them
    is a sort rather than a search. Each gap between two anchors goes through
    the same treatment again, which review showed is not optional: uniqueness is
    measured within a span, so a block that repeats on either side of a
    separator is unique nowhere until that separator has split it, and diffing
    the gaps directly reported 2,402 deletions for two edits. Only a span that
    is small enough reaches difflib; a span with no anchors at all is
    duplicate-dominated by definition, and there the honest answer is the coarse
    one -- the whole span replaced, in linear time.
    """
    out: list[dict] = []
    # An explicit stack rather than recursion: a span splits into gaps that are
    # each re-anchored, and how deep that goes is a property of the INPUT. Rows
    # ride the same stack so everything lands in order.
    stack: list[tuple[str, list, list]] = [("span", before.splitlines(), after.splitlines())]
    while stack:
        kind, left, right = stack.pop()
        if kind == "rows":
            out += left
        else:
            stack += reversed(_split(left, right))
    return out


def _tagged(op: str, lines: list[str]) -> list[dict]:
    return [{"op": op, "text": line} for line in lines]


def _rows(rows: list[dict]) -> tuple[str, list, list]:
    return ("rows", rows, [])


def _split(a: list[str], b: list[str]) -> list[tuple[str, list, list]]:
    """One span, decomposed into finished rows and the sub-spans still to do.

    Shared ends come off first -- cheap, and it is what makes an appended
    exchange or a trimmed front almost free. What is left is either small
    enough for difflib, or gets anchored and split again.
    """
    head = 0
    while head < len(a) and head < len(b) and a[head] == b[head]:
        head += 1
    tail = 0
    while (tail < len(a) - head and tail < len(b) - head
           and a[len(a) - 1 - tail] == b[len(b) - 1 - tail]):
        tail += 1
    mid_a, mid_b = a[head:len(a) - tail], b[head:len(b) - tail]

    parts: list[tuple[str, list, list]] = []
    if head:
        parts.append(_rows(_tagged("equal", a[:head])))
    parts += _split_middle(mid_a, mid_b)
    if tail:
        parts.append(_rows(_tagged("equal", a[len(a) - tail:])))
    return parts


def _split_middle(a: list[str], b: list[str]) -> list[tuple[str, list, list]]:
    """A span sharing neither end: difflib it, or anchor it and split again."""
    if not a or not b:
        return [_rows(_tagged("delete", a) + _tagged("insert", b))]
    if max(len(a), len(b)) <= EXACT_DIFF_LIMIT:
        return [_rows(_exact(a, b))]
    anchors = _anchors(a, b)
    if not anchors:
        # No line is unique to both sides anywhere in this span, so nothing can
        # be placed without searching, which is the cost this bounds. The whole
        # span replaced, in linear time.
        return [_rows(_tagged("delete", a) + _tagged("insert", b))]

    # Each gap goes back on the stack as a SPAN, not straight to difflib, and
    # review is why: uniqueness was measured across the whole span, so a block
    # repeating on either side of a separator is unique NOWHERE globally and
    # unique EVERYWHERE once the separator has split it. Coarsening the gaps
    # here reported 2,402 deletions and 2,402 insertions for two edits.
    parts: list[tuple[str, list, list]] = []
    i = j = 0
    for ai, bj in anchors:
        parts.append(("span", a[i:ai], b[j:bj]))
        parts.append(_rows([{"op": "equal", "text": a[ai]}]))
        i, j = ai + 1, bj + 1
    parts.append(("span", a[i:], b[j:]))
    return parts


def _exact(a: list[str], b: list[str]) -> list[dict]:
    """difflib on a span small enough to afford it, with `autojunk` off so it
    cannot refuse to anchor on a repeated line."""
    out: list[dict] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b,
                                                       autojunk=False).get_opcodes():
        if tag == "equal":
            out += _tagged("equal", a[i1:i2])
        elif tag == "delete":
            out += _tagged("delete", a[i1:i2])
        elif tag == "insert":
            out += _tagged("insert", b[j1:j2])
        else:  # replace -- deletes then inserts, so the frontend can pair them
            out += _tagged("delete", a[i1:i2]) + _tagged("insert", b[j1:j2])
    return out


def _anchors(a: list[str], b: list[str]) -> list[tuple[int, int]]:
    """Positions of lines occurring exactly ONCE in each side, in the longest
    order-preserving run of them.

    Uniqueness is what makes an alignment unambiguous: a line appearing once on
    each side can only correspond to itself, so it needs no search to place.
    The longest increasing subsequence over their partner positions is then the
    largest set of them that can all be right at once -- everything else is a
    genuine reordering, and lands in a gap.
    """
    once_a, once_b = Counter(a), Counter(b)
    at = {line: j for j, line in enumerate(b) if once_b[line] == 1}
    pairs = [(i, at[line]) for i, line in enumerate(a)
             if once_a[line] == 1 and line in at]

    tails: list[int] = []            # smallest tail partner-index per length
    ends: list[int] = []             # which pair sits at each of those tails
    prev = [-1] * len(pairs)
    for n, (_i, j) in enumerate(pairs):
        k = bisect.bisect_left(tails, j)
        prev[n] = ends[k - 1] if k else -1
        if k == len(tails):
            tails.append(j)
            ends.append(n)
        else:
            tails[k] = j
            ends[k] = n
    if not ends:
        return []
    run, n = [], ends[-1]
    while n != -1:
        run.append(pairs[n])
        n = prev[n]
    run.reverse()
    return run


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
