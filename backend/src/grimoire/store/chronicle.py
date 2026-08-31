"""Per-campaign play chronicle: an append-only fact record of absorbed scenes plus a
running timeline. The recap read-forward reads from here.

<campaign>/chronicle.json — keyed by scene id:
  {"<sid>": {"id","one_line","summary","keywords":[...],"cast":[...],
             "location","date","absorbed"}}
<campaign>/timeline.md — append-only dated lines.

Pure file IO. The extraction prompt/parse now lives in the absorb package
(absorb/prompt.py, absorb/parse.py); the LLM call lives in the route layer — the split
every LLM-backed store module follows (see absorb/prompt.py,
suggest.py, dossiers.py).
"""

from __future__ import annotations

import json
from pathlib import Path

from .. import prompts
from . import atomic, entities, locks, overlay
from .appearances import cast as appearances_cast
from .campaigns import paths as campaigns_paths
from .paths import now_iso
from .scenes import read as scenes_read
from .scenes import serialize as scenes_serialize


def _chronicle_path(cid: str) -> Path:
    return campaigns_paths.campaign_root(cid) / "chronicle.json"


def _timeline_path(cid: str) -> Path:
    return campaigns_paths.campaign_root(cid) / "timeline.md"


def read_chronicle(cid: str) -> dict:
    p = _chronicle_path(cid)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def absorb(cid: str, record: dict) -> dict:
    """Insert or replace the record keyed by record['id']; stamp absorption time."""
    data = read_chronicle(cid)
    stored = {**record, "absorbed": now_iso()}
    data[record["id"]] = stored
    atomic.write_text(_chronicle_path(cid), json.dumps(data, indent=2, sort_keys=True) + "\n")
    return stored


def get_record(cid: str, sid: str) -> dict | None:
    """One scene's chronicle record, or None. The snapshot `store/undo.py`
    takes before a hand edit, and the shape `restore` puts back."""
    data = read_chronicle(cid)
    if not isinstance(data, dict):
        return None
    rec = data.get(sid)
    return rec if isinstance(rec, dict) else None


def set_line(cid: str, sid: str, one_line: str | None = None,
             date: str | None = None) -> bool:
    """Correct a scene's one-line recap or its in-fiction date. False when the
    scene has no chronicle record -- nothing has absorbed it yet.

    Only the two fields a reader sees on the ledger's Timeline. `summary`, the
    long form, and the absorb metadata around it are left alone: this is a
    typo fix on the line that shows, not a way to rewrite what a scene was
    absorbed as. Re-absorbing the scene is that, and it replaces the record.

    `absorbed` is deliberately NOT restamped. It records when the pass read the
    transcript, which a hand edit does not change, and moving it would make a
    corrected line look like a fresh extraction to anything reading the stamp.
    """
    with locks.campaign_lock(cid):
        data = read_chronicle(cid)
        if not isinstance(data, dict):
            return False
        rec = data.get(sid)
        if not isinstance(rec, dict):
            return False
        if one_line is not None:
            rec["one_line"] = one_line.strip()
        if date is not None:
            rec["date"] = date.strip()
        atomic.write_text(_chronicle_path(cid),
                          json.dumps(data, indent=2, sort_keys=True) + "\n")
        return True


def restore(cid: str, sid: str, record: dict | None) -> None:
    """Put one scene's record back, or drop it when there was none —
    `store/undo.py`'s reversal shape, matching `plot.restore`."""
    with locks.campaign_lock(cid):
        data = read_chronicle(cid)
        if not isinstance(data, dict):
            data = {}
        if record is None:
            if data.pop(sid, None) is None:
                return
        else:
            data[sid] = record
        atomic.write_text(_chronicle_path(cid),
                          json.dumps(data, indent=2, sort_keys=True) + "\n")


def forget(cid: str, sid: str) -> bool:
    """Drop one scene's chronicle record. Returns whether there was one (#75).

    The record IS the absorbed reading of a transcript, so a cut into that
    transcript leaves it describing posts that no longer exist — and it is keyed
    by scene id, which makes "which record belongs to this scene" the one
    question this store can answer exactly. Deleting rather than rewriting: what
    the remaining posts add up to is an extraction, not a subtraction, and the
    scene is left un-absorbed so the player can re-run it.

    `timeline.md` is deliberately untouched. It is an append-only dated log with
    no scene attribution at all — the same gap `relationships.json` has, and the
    same answer: nothing here may guess which lines came from which scene.
    """
    data = read_chronicle(cid)
    if data.pop(sid, None) is None:
        return False
    atomic.write_text(_chronicle_path(cid), json.dumps(data, indent=2, sort_keys=True) + "\n")
    return True


def repoint_scenes(cid: str, mapping: dict[str, str]) -> None:
    """Follow renamed scene ids: rewrite record keys and their id fields."""
    data = read_chronicle(cid)
    if not any(k in mapping for k in data):
        return
    out = {}
    for k, rec in data.items():
        if rec.get("id") in mapping:
            rec = {**rec, "id": mapping[rec["id"]]}
        out[mapping.get(k, k)] = rec
    atomic.write_text(_chronicle_path(cid), json.dumps(out, indent=2, sort_keys=True) + "\n")


def recent(cid: str, n: int) -> list[dict]:
    """The n highest-id (chronological-ish) records, ascending. n <= 0 -> [].

    `page` at offset zero, and `test_recent_is_page_at_offset_zero` holds the
    two to that. Kept as its own name because its callers inside this store
    want the newest few records and nothing else -- an offset argument they
    would all pass zero to is not clarity.
    """
    return page(cid, n)


def page(cid: str, limit: int, offset: int = 0) -> list[dict]:
    """`recent`, with a window that can start further back than the newest record.

    `offset` skips that many of the NEWEST records; the page still comes back
    ascending. Anchored at that end not because a chronicle is only read from
    there but because `recent` already anchored it there, and `recent` is what
    `GET /campaigns/{cid}/chronicle` has always returned -- a front-anchored
    window with the same default would hand that route the OLDEST 50 instead.

    An unusable window is empty rather than an error -- `limit <= 0`, or an
    `offset` past the oldest record. Range-checking a client's query belongs in
    the route (`routes.common._page_window`), which can answer 400; here, a
    caller that asked for nothing gets nothing, which is what `recent(cid, 0)`
    has always done.
    """
    if limit <= 0 or offset < 0:
        return []
    rows = sorted(read_chronicle(cid).values(), key=lambda r: r.get("id", ""))
    end = len(rows) - offset
    if end <= 0:
        return []
    return rows[max(0, end - limit):end]


def append_timeline(cid: str, events: list[dict]) -> None:
    if not events:
        return
    p = _timeline_path(cid)
    existing = p.read_text(encoding="utf-8") if p.exists() else "# Timeline\n"
    lines = [f"- **{e.get('date', '')}** {e.get('text', '').strip()}".rstrip()
             for e in events]
    atomic.write_text(p, existing.rstrip() + "\n" + "\n".join(lines) + "\n")


def scene_facts(cid: str, sid: str) -> dict:
    """Deterministic facts the LLM should not have to infer: present cast refs, the
    current location's display name, and the current native datetime."""
    cast = [f"{a['kind']}/{a['id']}" for a in appearances_cast.scene_cast(cid, sid)]
    loc_hist = scenes_read.get_location_history(cid, sid)
    location = ""
    if loc_hist:
        try:
            location = overlay.read_entity(
                cid, "locations", loc_hist[-1]
            )["meta"].get("name", loc_hist[-1])
        except entities.EntityNotFound:
            location = loc_hist[-1]
    time_hist = scenes_read.get_time_history(cid, sid)
    return {"cast": cast, "location": location, "date": time_hist[-1] if time_hist else ""}


def transcript_text(messages: list[dict]) -> str:
    """Render messages via transcript.j2. The transition tag is internal drift
    metadata (`scenes_serialize.TRANSITION_SPEAKER`), never a speaker a prompt should see —
    strip it here so every caller (app transcript, exports, and the mechanics
    audit/absorb LLM prompts) gets the same never-displayed guarantee, rather
    than relying on each caller to normalize raw `scenes_read.read_scene` messages
    itself. `ROLL_SPEAKER` is left untouched: manual dice-roll lines are real
    transcript content and their labelling is intentional.
    """
    normalized = [
        {**m, "speaker": None} if m.get("speaker") == scenes_serialize.TRANSITION_SPEAKER else m
        for m in messages
        # A director note is dropped, not unlabelled. Every caller of this is
        # either showing the reader what happened or asking a model to
        # summarise it, and a note is neither: absorbed as though it were
        # dialogue it would put the author's own instructions into the
        # chronicle, and cited as evidence for a proposal it would be the
        # reviewer's own words coming back as a finding.
        if not scenes_serialize.is_director_note(m)
    ]
    return prompts.render("snippets/transcript.j2", messages=normalized)
