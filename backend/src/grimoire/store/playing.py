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


def _played_path(cid: str) -> Path:
    return campaigns.campaign_root(cid) / "played.json"


def read_played(cid: str) -> set[str]:
    p = _played_path(cid)
    if not p.exists():
        return set()
    return set(json.loads(p.read_text(encoding="utf-8")))


def _mark_played(cid: str, gid: str) -> None:
    played = read_played(cid)
    played.add(gid)
    _played_path(cid).write_text(json.dumps(sorted(played), indent=2) + "\n", encoding="utf-8")


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


def available_greetings(cid: str) -> list[dict]:
    wroot = _world_root(cid)
    return greetings.availability(wroot, greetings.read_plotmap(wroot),
                                  read_played(cid), player_tags(cid))


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
    text = context._substitute(greetings.read_greeting(wroot, gid)["body"],
                               context.scene_substitutions(cid, sid))
    scenes.append_message(cid, sid, "assistant", text)
