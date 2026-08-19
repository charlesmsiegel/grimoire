"""Per-campaign commitments: the promises, threats and foreshadowing a story
owes its reader, each with an ordered list of dated beats. Stored at
<campaign>/commitments.json. Pure JSON IO, mirrors plot.py's shape.

Deliberately NOT a flavour of plot thread (#115). A plot thread *moves* —
open, advanced, closed — and "closed" says nothing about how it ended. A
commitment *resolves*, and how it resolves is the whole point: a promise is
kept or broken, a threat lands or passes, a planted image pays off or is
dropped. Folding both into plot.json would make one status vocabulary mean two
things, and would push a due-by field onto every narrative thread that has no
deadline.

`due` is free text in the campaign's own reckoning — "before the harvest
moon", "by the third night". Not a scene id: the model naming a commitment can
read the fiction's deadline but cannot know which future scene will be the one.
Nothing here computes overdue or stale; `due` plus `last_scene` are what
aging (#103) reads, and this module only has to keep them.

Mutators serialize on `locks.campaign_lock(cid)`: commitments.json is
rewritten whole, so two unlocked read-modify-writes lose one of them.
"""

from __future__ import annotations

import json
from pathlib import Path

from .. import prompts
from . import atomic, fieldtext, locks
from .campaigns import paths as campaigns_paths

KINDS = ("promise", "threat", "foreshadowing")
STATUSES = ("open", "fulfilled", "broken", "expired")
#: The three ways a commitment stops being owed. Everything else is still open,
#: mirroring `plot.open_threads`' "not closed" rather than enumerating the
#: statuses that count as live -- a record written by an older version, or by
#: hand, stays visible instead of silently dropping out of the ledger.
RESOLVED = ("fulfilled", "broken", "expired")


def _path(cid: str) -> Path:
    return campaigns_paths.campaign_root(cid) / "commitments.json"


def read(cid: str) -> dict:
    p = _path(cid)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def _write(cid: str, data: dict) -> None:
    atomic.write_text(_path(cid), json.dumps(data, indent=2, sort_keys=True) + "\n")


def get(cid: str, mid: str) -> dict | None:
    return read(cid).get(mid)


def set_movement(cid: str, mid: str, title: str, kind: str, status: str,
                 due: str | None, beat_text: str, scene: str) -> None:
    """Create or advance one commitment. A blank `title` and an unrecognized
    `kind`/`status` preserve the stored value, so an absorb that only adds a
    beat cannot erase what the commitment was created with.

    `due` is three-valued because a deadline is the one field with a meaningful
    empty state. **None** preserves the stored deadline — what an absorb that
    never mentioned it must do, and what a payload predating this argument
    sends via `.get("due")`. **""** clears it, which is how a scene that lifts a
    deadline without resolving the commitment ("forget midnight, pay me
    whenever") gets recorded instead of leaving a stale date on the ledger
    forever. Text sets it.
    """
    with locks.campaign_lock(cid):
        data = read(cid)
        rec = data.get(mid)
        # `or` alone covers a missing record and a falsy one, and lets a TRUTHY
        # non-dict -- a hand-edited `{"x": [1]}` -- straight through to `.get`,
        # which raises. That failure is silent rather than loud: `materialize`
        # skips a non-dict record and stages the movement as new, so the row is
        # approved, and `apply_edits`' per-edit `except` then drops the write
        # with a 200 and no failure reported. A record that is not a mapping
        # holds nothing this module can read, so it is replaced by the default
        # structure rather than repaired -- same call as the malformed `beats`
        # below, and for the same reason: what has to survive is the movement
        # the reviewer actually approved.
        if not isinstance(rec, dict) or not rec:
            rec = {"title": "", "kind": "promise", "status": "open",
                   "due": "", "beats": [], "last_scene": ""}
        if title.strip():
            rec["title"] = title.strip()
        if not rec.get("title"):
            rec["title"] = mid
        if kind in KINDS:
            rec["kind"] = kind
        if status in STATUSES:
            rec["status"] = status
        if due is not None:
            rec["due"] = due.strip()
        if beat_text.strip():
            # `setdefault` alone only covers a MISSING key. A hand-edited
            # `"beats": {}` survives it and `.append` raises -- and everything
            # upstream tolerates that record (materialize and `commitment_line`
            # both coerce a non-list `beats` to empty), so the row stages, the
            # reviewer approves it, `apply_edits`' per-edit `except` drops the
            # write, and the panel closes on a 200 with the beat gone. A
            # malformed beat list is replaced rather than repaired: its contents
            # are unreadable by definition, and the alternative is losing the
            # movement that was actually approved.
            if not isinstance(rec.get("beats"), list):
                rec["beats"] = []
            rec["beats"].append({"scene": scene, "text": beat_text.strip()})
        rec["last_scene"] = scene
        data[mid] = rec
        _write(cid, data)


def restore(cid: str, mid: str, record: dict | None) -> None:
    """Put one commitment back to a recorded state, or remove it when there was
    none. `plot.restore`'s sibling, and there for the same reason: `set_movement`
    appends a beat and may move kind, status, deadline and title in the same
    call, so no argument list undoes it and `store/undo.py` snapshots the record
    instead (#31).

    Scoped to the one id rather than restoring the whole file, so commitments the
    reversal has no business touching keep whatever they have since become.
    """
    with locks.campaign_lock(cid):
        data = read(cid)
        if record is None:
            if data.pop(mid, None) is None:
                return
        else:
            data[mid] = record
        _write(cid, data)


def forget_scene(cid: str, sid: str) -> int:
    """Drop every beat this scene contributed. Returns how many went (#75).

    `plot.forget_scene`'s sibling, with the same record shape and the same rules
    — that docstring carries the reasoning for both, including why an emptied
    commitment is left standing rather than deleted. Takes the campaign lock, as
    every mutator in this module does.
    """
    with locks.campaign_lock(cid):
        data = read(cid)
        gone, dirty = 0, False
        for rec in data.values():
            if not isinstance(rec, dict):
                continue
            beats = rec.get("beats")
            kept = [b for b in beats
                    if not (isinstance(b, dict) and b.get("scene") == sid)] \
                if isinstance(beats, list) else None
            if kept is not None and len(kept) != len(beats):
                gone += len(beats) - len(kept)
                rec["beats"] = kept
                dirty = True
            # Repaired whether or not a beat went — `set_movement` stamps
            # `last_scene` on every call and appends a beat only when given text.
            # See `plot.forget_scene`.
            if rec.get("last_scene") == sid:
                survivors = kept if kept is not None else []
                last = survivors[-1] if survivors else None
                rec["last_scene"] = last.get("scene", "") if isinstance(last, dict) else ""
                dirty = True
        if dirty:
            _write(cid, data)
        return gone


def repoint_scenes(cid: str, mapping: dict[str, str]) -> None:
    """Follow renamed scene ids in beats and last_scene markers.

    An unreadable file and malformed records are both stepped over rather than
    trusted. This file is hand-editable and read by a bare `json.loads`, and
    this runs from `scene_refs.repoint` AFTER the scene file has already been
    renamed — so raising here 500s the rename and leaves the stores *after*
    this one in the sweep pointing at an id that no longer exists. A record it
    cannot read keeps its stale scene id, which is the same outcome as never
    having been repointed and strictly better than aborting the sweep.

    Unparseable and wrong-shape are the same rule, deliberately caught in two
    places: `json.loads` raises on the first and returns happily on the second,
    so a `try` alone leaves `[]` to crash on `.values()` and a shape check alone
    leaves `{ no` to crash on the read.
    """
    with locks.campaign_lock(cid):
        try:
            data = read(cid)
        except Exception:  # noqa: BLE001 — unparseable commitments.json: skip this store
            return         # (the shape check below is the valid-JSON half of the same rule)
        if not isinstance(data, dict):
            return
        hit = False
        for rec in data.values():
            if not isinstance(rec, dict):
                continue
            # `isinstance(..., str)` before `in mapping`: a list-valued id is
            # unhashable and a membership test on it raises rather than missing.
            last = rec.get("last_scene")
            if isinstance(last, str) and last in mapping:
                rec["last_scene"] = mapping[last]
                hit = True
            beats = rec.get("beats")
            for beat in beats if isinstance(beats, list) else []:
                scene = beat.get("scene") if isinstance(beat, dict) else None
                if isinstance(scene, str) and scene in mapping:
                    beat["scene"] = mapping[scene]
                    hit = True
        if hit:
            _write(cid, data)


def _field(value, fallback: str = "") -> str:
    """A stored field as text, or `fallback` for anything that is not a string.

    Every projected field goes through this. `open_commitments` is the ledger
    route's source, and that route's tolerance only covers the read *raising* --
    a record with an object-valued `title` reads fine, passes the boundary, and
    reaches `LedgerPanel`, where React refuses to render an object as a child
    and the whole panel goes blank. One hand-edited record must not be able to
    do that, so the projection is the place the types are made true.

    One rule, in `fieldtext.text`: this used to be its own copy of the same
    three lines, as it was in eight other modules.
    """
    return fieldtext.text(value, fallback)


def open_commitments(cid: str) -> list[dict]:
    # `_field` here too, not the raw value: every row this returns is projected
    # through it, so `"status": " fulfilled "` would be *shown* as fulfilled
    # while this predicate read it as something unrecognized and kept it on the
    # ledger. A resolved commitment that keeps appearing in the snapshot is one
    # the model can move or resolve a second time.
    #
    # Case-folded for the same reason, and it took a second pass to see it: the
    # whitespace fix left `"Fulfilled"` reading as unrecognized. Every status
    # this module WRITES is already lower-case (`set_movement` only accepts a
    # member of `STATUSES`), so folding can only ever rescue a hand-edited one
    # -- it cannot reinterpret anything the pipeline produced.
    items = [(mid, c) for mid, c in read(cid).items()
             if isinstance(c, dict) and _field(c.get("status")).lower() not in RESOLVED]
    items.sort(key=lambda mc: (_field(mc[1].get("last_scene")), mc[0]))
    out = []
    for mid, c in items:
        beats = c.get("beats")
        last = beats[-1] if isinstance(beats, list) and beats else None
        out.append({"id": mid, "title": _field(c.get("title"), mid),
                    "kind": _field(c.get("kind"), "promise"),
                    "status": _field(c.get("status"), "open"),
                    "due": _field(c.get("due")), "last_scene": _field(c.get("last_scene")),
                    "latest_beat": _field(last.get("text")) if isinstance(last, dict) else ""})
    return out


def render_open(cid: str, with_id: bool) -> list[str]:
    """Formatted lines for unresolved commitments, shared by the absorb prompt
    snapshot and the # Commitments context block. `with_id=True` → absorb form
    (leads with the id so the model can resolve the right commitment); `False` →
    context form. The line formats live in templates/snippets/commitment_line/.
    Tolerant of a garbled commitments.json (returns []) -- same policy as
    `plot.render_open`, for the same reason: a broken file must cost the model
    one context block, not the whole turn."""
    try:
        items = open_commitments(cid)
    except Exception:  # noqa: BLE001 — garbled commitments.json: omit, don't crash callers
        return []
    template = f"snippets/commitment_line/{'absorb' if with_id else 'context'}.j2"
    return [prompts.render(template, c=c) for c in items]
