"""Campaign play-state: the played-greeting set, availability bound to a
campaign, and starting a scene from a greeting."""

from __future__ import annotations

import json
from pathlib import Path

from . import atomic, characters, context, greetings, overlay, pcs
from .appearances import cast as appearances_cast, transitions as appearances_transitions, versions as appearances_versions
from .campaigns import paths as campaigns_paths
from .scenes import (lifecycle as scenes_lifecycle, read as scenes_read,
                     write as scenes_write)


class PlayError(Exception):
    pass


_MARK_KEYS = ("played", "completed", "skipped")


def _marks_path(cid: str) -> Path:
    return campaigns_paths.campaign_root(cid) / "played.json"


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
    atomic.write_text(_marks_path(cid), json.dumps(payload, indent=2) + "\n")


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
    overlay.read_greeting(cid, gid)  # raises GreetingNotFound
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
    out: set[str] = set()
    for a in appearances_cast.roster(cid):
        if a["role"] == "player" and a["kind"] == "pcs":
            try:
                out |= set(pcs.read_pc(overlay.pc_root(cid, a["id"]), a["id"])["meta"]["tags"])
            except pcs.PCNotFound:
                continue
    return out


def available_greetings(cid: str, after: str | None = None) -> list[dict]:
    plotmap = overlay.read_plotmap(cid)
    marks = read_marks(cid)
    out = greetings.availability(overlay.list_greetings(cid), plotmap,
                                 marks["played"] | marks["completed"],
                                 player_tags(cid), skipped=marks["skipped"])
    mark_of = {gid: "played" for gid in marks["played"]}
    mark_of.update({gid: "completed" for gid in marks["completed"]})
    for g in out:
        g["mark"] = mark_of.get(g["id"])
    unlocked: set[str] = set()
    if after:
        gid = scenes_read.read_scene(cid, after)["meta"].get("greeting", "")
        if gid:
            unlocked = set(greetings.edges_of(plotmap, gid)["leads_to"])
    for g in out:
        g["unlocked"] = g["id"] in unlocked
    out.sort(key=lambda g: not g["unlocked"])  # stable: unlocked first, rest keep order
    return out


def start_from_greeting(cid: str, sid: str, gid: str) -> str:
    g = overlay.read_greeting(cid, gid)["meta"]   # raises GreetingNotFound
    scene = scenes_read.read_scene(cid, sid)               # raises SceneNotFound
    if scene["messages"]:
        raise PlayError("scene already has messages")
    scene_pcless = scene["meta"].get("pcless") == "true"
    if scene_pcless and not g["pcless"]:
        raise PlayError("an offscreen scene must start from an offscreen greeting")
    if g["pcless"] and appearances_cast.players_in_scene(cid, sid):
        raise PlayError("an offscreen greeting cannot start a scene with players seated")
    if not {a["id"]: a["available"] for a in available_greetings(cid)}.get(gid, False):
        raise PlayError(f"greeting {gid} is not available")
    # Cast everyone present at the opener. A locked version always wins; otherwise
    # the primary uses the greeting's version and co-present characters their default.
    for actor in dict.fromkeys(a for a in [g["character"], *g["present"]] if a):
        version = appearances_versions.locked_version(cid, "characters", actor)
        if version is None:
            version = g["version"] if actor == g["character"] else \
                characters.read_character(overlay.char_root(cid, actor), actor)["meta"]["default_version"]
            # A materialized actor's version set is authoritative. If the
            # campaign has purged the version this inherited greeting names,
            # don't let the first-appearance lock revive it from the world.
            if actor == g["character"] and appearances_versions.actor_hash(
                    overlay.char_root(cid, actor), "characters", actor, version) is None:
                raise PlayError(
                    f"greeting {gid} needs version '{version}' of {actor}, "
                    f"which is no longer in this campaign")
        appearances_transitions.appear(cid, sid, "characters", actor, version, "npc")
    if g["pcless"] and not scene_pcless:
        scenes_write.set_pcless(cid, sid)  # before substitution: {{user}} needs the pcless fallback
    _mark_played(cid, gid)
    scenes_write.stamp_greeting(cid, sid, gid)
    text = context.expand_macros(overlay.read_greeting(cid, gid)["body"],
                                 context.scene_substitutions(cid, sid), cid, sid)
    # append_reply, not append_message: the greeting is authored rather than
    # generated, but it is the strongest length anchor the model has at the
    # start of a scene and it WILL be matched, so it records a turn like any
    # other model output.
    #
    # Split on the SAME marker grammar routes.streaming._persist_reply uses. Storing a
    # multi-block greeting as one segment records turn_sizes [1] while
    # _parse_messages re-splits it into N messages at read time; drift
    # segmentation would then measure only the trailing block of the very turn
    # that sets the scene's length anchor.
    scenes_write.append_reply(cid, sid, scenes_write.split_reply(
        text, frozenset(appearances_cast.player_names(cid, sid))))
    # retitle last: any earlier failure leaves the caller's sid valid for cleanup
    return scenes_lifecycle.rename_scene(cid, sid, g["name"])
