"""Campaign play-state: the played-greeting set, availability bound to a
campaign, and starting a scene from a greeting."""

from __future__ import annotations

import json
from pathlib import Path

from . import appearances, campaigns, characters, context, greetings, pcs, scenes, worlds


class PlayError(Exception):
    pass


def _world_root(cid: str) -> Path:
    return worlds.world_root(campaigns.read_campaign(cid)["meta"].get("world", ""))


_MARK_KEYS = ("played", "completed", "skipped")


def _marks_path(cid: str) -> Path:
    return campaigns.campaign_root(cid) / "played.json"


def read_marks(cid: str) -> dict[str, set[str]]:
    p = _marks_path(cid)
    if not p.exists():
        return {k: set() for k in _MARK_KEYS}
    data = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(data, list):  # legacy format: a bare list of played ids
        data = {"played": data}
    return {k: set(data.get(k, [])) for k in _MARK_KEYS}


def _write_marks(cid: str, marks: dict[str, set[str]]) -> None:
    payload = {k: sorted(marks[k]) for k in _MARK_KEYS}
    _marks_path(cid).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def read_played(cid: str) -> set[str]:
    return read_marks(cid)["played"]


def _mark_played(cid: str, gid: str) -> None:
    marks = read_marks(cid)
    marks["played"].add(gid)
    marks["completed"].discard(gid)  # actually playing supersedes an off-screen mark
    marks["skipped"].discard(gid)
    _write_marks(cid, marks)


def mark_greeting(cid: str, gid: str, status: str) -> None:
    """Set a greeting's off-screen mark: completed / skipped / none (clear)."""
    greetings.read_greeting(campaigns.campaign_root(cid), gid)  # raises GreetingNotFound
    if status not in ("completed", "skipped", "none"):
        raise PlayError(f"unknown mark status: {status}")
    marks = read_marks(cid)
    if gid in marks["played"]:
        raise PlayError("greeting was played in a scene; its mark cannot be changed")
    marks["completed"].discard(gid)
    marks["skipped"].discard(gid)
    if status != "none":
        marks[status].add(gid)
    _write_marks(cid, marks)


def player_tags(cid: str) -> set[str]:
    croot = campaigns.campaign_root(cid)
    out: set[str] = set()
    for a in appearances.roster(cid):
        if a["role"] == "player" and a["kind"] == "pcs":
            try:
                out |= set(pcs.read_pc(croot, a["id"])["meta"]["tags"])
            except pcs.PCNotFound:
                continue
    return out


def available_greetings(cid: str, after: str | None = None) -> list[dict]:
    wroot = _world_root(cid)
    plotmap = greetings.read_plotmap(wroot)
    out = greetings.availability(wroot, plotmap, read_played(cid), player_tags(cid))
    unlocked: set[str] = set()
    if after:
        gid = scenes.read_scene(cid, after)["meta"].get("greeting", "")
        if gid:
            unlocked = set(greetings.edges_of(plotmap, gid)["leads_to"])
    for g in out:
        g["unlocked"] = g["id"] in unlocked
    out.sort(key=lambda g: not g["unlocked"])  # stable: unlocked first, rest keep order
    return out


def start_from_greeting(cid: str, sid: str, gid: str) -> None:
    wroot = _world_root(cid)
    g = greetings.read_greeting(wroot, gid)["meta"]   # raises GreetingNotFound
    scene = scenes.read_scene(cid, sid)               # raises SceneNotFound
    if scene["messages"]:
        raise PlayError("scene already has messages")
    if not {a["id"]: a["available"] for a in available_greetings(cid)}.get(gid, False):
        raise PlayError(f"greeting {gid} is not available")
    # Cast everyone present at the opener: the primary at the greeting's version,
    # any co-present character at its own default version. Deduped, primary first.
    for cid_ in dict.fromkeys([g["character"], *g["present"]]):
        version = g["version"] if cid_ == g["character"] else \
            characters.read_character(wroot, cid_)["meta"]["default_version"]
        appearances.appear(cid, sid, "characters", cid_, version, "npc")
    _mark_played(cid, gid)
    scenes.stamp_greeting(cid, sid, gid)
    text = context._substitute(greetings.read_greeting(wroot, gid)["body"],
                               context.scene_substitutions(cid, sid))
    scenes.append_message(cid, sid, "assistant", text)
