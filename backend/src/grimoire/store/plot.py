"""Per-campaign plot threads: open/advanced/closed narrative threads, each with an
ordered list of dated beats. Stored at <campaign>/plot.json. Pure JSON IO, mirrors
relationships.py.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import atomic
from .campaigns import paths as campaigns_paths

STATUSES = ("open", "advanced", "closed")


def _path(cid: str) -> Path:
    return campaigns_paths.campaign_root(cid) / "plot.json"


def read(cid: str) -> dict:
    p = _path(cid)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def _write(cid: str, data: dict) -> None:
    atomic.write_text(_path(cid), json.dumps(data, indent=2, sort_keys=True) + "\n")


def get(cid: str, pid: str) -> dict | None:
    return read(cid).get(pid)


def set_movement(cid: str, pid: str, title: str, status: str, beat_text: str, scene: str) -> None:
    data = read(cid)
    thread = data.get(pid) or {"title": "", "status": "open", "beats": [], "last_scene": ""}
    if title.strip():
        thread["title"] = title.strip()
    if not thread.get("title"):
        thread["title"] = pid
    if status in STATUSES:
        thread["status"] = status
    if beat_text.strip():
        thread.setdefault("beats", []).append({"scene": scene, "text": beat_text.strip()})
    thread["last_scene"] = scene
    data[pid] = thread
    _write(cid, data)


def repoint_scenes(cid: str, mapping: dict[str, str]) -> None:
    """Follow renamed scene ids in beats and last_scene markers."""
    data = read(cid)
    hit = False
    for thread in data.values():
        if thread.get("last_scene") in mapping:
            thread["last_scene"] = mapping[thread["last_scene"]]
            hit = True
        for beat in thread.get("beats", []):
            if beat.get("scene") in mapping:
                beat["scene"] = mapping[beat["scene"]]
                hit = True
    if hit:
        _write(cid, data)


def open_threads(cid: str) -> list[dict]:
    items = [(pid, t) for pid, t in read(cid).items() if t.get("status") != "closed"]
    items.sort(key=lambda kt: (kt[1].get("last_scene", ""), kt[0]))
    out = []
    for pid, t in items:
        beats = t.get("beats") or []
        out.append({"id": pid, "title": t.get("title", pid), "status": t.get("status", "open"),
                    "last_scene": t.get("last_scene", ""),
                    "latest_beat": beats[-1]["text"] if beats else ""})
    return out


def render_open(cid: str, with_id: bool) -> list[str]:
    """Formatted lines for open/advanced threads, shared by the absorb prompt snapshot and
    the # Plot threads context block. `with_id=True` → absorb form (leads with the id so
    the model can reference the thread); `False` → context form. The line formats live in
    templates/snippets/plot_thread_line/. Tolerant of a garbled plot.json (returns [])."""
    from .. import prompts
    try:
        threads = open_threads(cid)
    except Exception:  # noqa: BLE001 — garbled plot.json: omit, don't crash callers
        return []
    template = f"snippets/plot_thread_line/{'absorb' if with_id else 'context'}.j2"
    return [prompts.render(template, t=t) for t in threads]
