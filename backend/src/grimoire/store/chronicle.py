"""Per-campaign play chronicle: an append-only fact record of absorbed scenes plus a
running timeline. The recap read-forward reads from here.

<campaign>/chronicle.json — keyed by scene id:
  {"<sid>": {"id","one_line","summary","keywords":[...],"cast":[...],
             "location","date","absorbed"}}
<campaign>/timeline.md — append-only dated lines.

Pure file IO. The extraction prompt/parse now lives in absorb.py; the LLM call lives
in the route layer (mirrors briefs.py). No module-load import of scenes/appearances/
entities (cycle-free).
"""

from __future__ import annotations

import json
from pathlib import Path

from . import campaigns
from .paths import now_iso


def _chronicle_path(cid: str) -> Path:
    return campaigns.campaign_root(cid) / "chronicle.json"


def _timeline_path(cid: str) -> Path:
    return campaigns.campaign_root(cid) / "timeline.md"


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
    _chronicle_path(cid).write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return stored


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
    p.write_text(existing.rstrip() + "\n" + "\n".join(lines) + "\n", encoding="utf-8")


def scene_facts(cid: str, sid: str) -> dict:
    """Deterministic facts the LLM should not have to infer: present cast refs, the
    current location's display name, and the current native datetime."""
    from . import appearances, entities, scenes
    cast = [f"{a['kind']}/{a['id']}" for a in appearances.scene_cast(cid, sid)]
    loc_hist = scenes.get_location_history(cid, sid)
    location = ""
    if loc_hist:
        try:
            location = entities.read_entity(
                campaigns.campaign_root(cid), "locations", loc_hist[-1]
            )["meta"].get("name", loc_hist[-1])
        except entities.EntityNotFound:
            location = loc_hist[-1]
    time_hist = scenes.get_time_history(cid, sid)
    return {"cast": cast, "location": location, "date": time_hist[-1] if time_hist else ""}


def transcript_text(messages: list[dict]) -> str:
    from .scenes import ROLE_TO_LABEL
    return "\n\n".join(
        f"**{ROLE_TO_LABEL.get(m['role'], m['role'])}:** {m['content']}" for m in messages)
