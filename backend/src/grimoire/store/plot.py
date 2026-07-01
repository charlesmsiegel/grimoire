"""Per-campaign plot threads: open/advanced/closed narrative threads, each with an
ordered list of dated beats. Stored at <campaign>/plot.json. Pure JSON IO, mirrors
relationships.py.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import campaigns

STATUSES = ("open", "advanced", "closed")


def _path(cid: str) -> Path:
    return campaigns.campaign_root(cid) / "plot.json"


def read(cid: str) -> dict:
    p = _path(cid)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def _write(cid: str, data: dict) -> None:
    _path(cid).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


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


def open_threads(cid: str) -> list[dict]:
    items = [(pid, t) for pid, t in read(cid).items() if t.get("status") != "closed"]
    items.sort(key=lambda kt: (kt[1].get("last_scene", ""), kt[0]))
    out = []
    for pid, t in items:
        beats = t.get("beats") or []
        out.append({"id": pid, "title": t.get("title", pid), "status": t.get("status", "open"),
                    "latest_beat": beats[-1]["text"] if beats else ""})
    return out
