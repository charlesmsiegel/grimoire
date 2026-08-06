"""Per-campaign plot threads: open/advanced/closed narrative threads, each with an
ordered list of dated beats. Stored at <campaign>/plot.json. Pure JSON IO, mirrors
relationships.py.
"""

from __future__ import annotations

import json
from pathlib import Path

from .. import prompts
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


def _field(value, fallback: str = "") -> str:
    """A stored field as text, or `fallback` for anything that is not a string.

    plot.json is hand-editable and read by a bare `json.loads`, so a record with
    an object-valued `title` reads fine and every consumer inherits it. The two
    that render (`routes.campaigns.get_ledger` -> `LedgerPanel`,
    `routes.scenes.get_briefing` -> the inspector's Briefing section) pass these
    straight to React, which refuses an object as a child and blanks the whole
    panel -- a failure no `try` around the READ can catch, because the read
    succeeds. `commitments._field` is the same helper for the same reason; the
    ledger route carried a local copy of it for this module because this
    projection did not coerce, and that copy is now belt-and-braces rather than
    the only guard.
    """
    return value.strip() if isinstance(value, str) else fallback


def open_threads(cid: str) -> list[dict]:
    # `isinstance(t, dict)`, and `_field` inside the sort key: a record that is
    # not a mapping has no `.get`, and a list-valued `last_scene` makes the
    # comparison raise -- either one costs every OTHER thread its row, since the
    # callers' tolerance is a `try` around the whole call. Mirrors
    # `commitments.open_commitments`, which learned both the same way.
    # Case-folded as well as stripped, the whole of `commitments`' rule rather
    # than half of it: every status this module WRITES is already lower-case
    # (`set_movement` only accepts a member of `STATUSES`), so folding can only
    # rescue a hand-edited `"Closed"` and cannot reinterpret anything the
    # pipeline produced. Stripping without folding was the gap that pass left.
    items = [(pid, t) for pid, t in read(cid).items()
             if isinstance(t, dict) and _field(t.get("status")).lower() != "closed"]
    items.sort(key=lambda kt: (_field(kt[1].get("last_scene")), kt[0]))
    out = []
    for pid, t in items:
        beats = t.get("beats")
        last = beats[-1] if isinstance(beats, list) and beats else None
        out.append({"id": pid, "title": _field(t.get("title"), pid),
                    "status": _field(t.get("status"), "open"),
                    "last_scene": _field(t.get("last_scene")),
                    "latest_beat": _field(last.get("text")) if isinstance(last, dict) else ""})
    return out


def render_open(cid: str, with_id: bool) -> list[str]:
    """Formatted lines for open/advanced threads, shared by the absorb prompt snapshot and
    the # Plot threads context block. `with_id=True` → absorb form (leads with the id so
    the model can reference the thread); `False` → context form. The line formats live in
    templates/snippets/plot_thread_line/. Tolerant of a garbled plot.json (returns [])."""
    try:
        threads = open_threads(cid)
    except Exception:  # noqa: BLE001 — garbled plot.json: omit, don't crash callers
        return []
    template = f"snippets/plot_thread_line/{'absorb' if with_id else 'context'}.j2"
    return [prompts.render(template, t=t) for t in threads]
