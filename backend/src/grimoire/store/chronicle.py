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
from . import atomic, entities, overlay
from .appearances import cast as appearances_cast
from .campaigns import paths as campaigns_paths
from .paths import now_iso
from .scenes import read as scenes_read, serialize as scenes_serialize


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
    """The n highest-id (chronological-ish) records, ascending. n <= 0 -> []."""
    if n <= 0:
        return []
    data = read_chronicle(cid)
    return sorted(data.values(), key=lambda r: r.get("id", ""))[-n:]


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
    ]
    return prompts.render("snippets/transcript.j2", messages=normalized)
