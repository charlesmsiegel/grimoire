"""Read-only cast queries: who has appeared, who is in a given scene, and
their display names -- all from the appearance record and the locked actor
root. Touches no scene state, unlike ``transitions.py``'s ``appear``/``leave``/
``suggestions``; that separation is what lets ``scenes`` import this module at
module scope once it is split.
"""

from __future__ import annotations

from pathlib import Path

from .. import characters, pcs
from . import paths, versions


def cast_detail(cid: str, sid: str, kind: str, actor_id: str) -> dict:
    """Read-only display info for an actor in a scene, from the campaign copy."""
    if not any(a["kind"] == kind and a["id"] == actor_id for a in scene_cast(cid, sid)):
        raise paths.AppearError(f"{kind}/{actor_id} is not in scene {sid}")
    aroot = paths.locked_actor_root(cid)
    vid = versions.locked_version(cid, kind, actor_id)
    if kind == "characters":
        data = characters.read_card(aroot, actor_id, vid)["data"]
        labelled = [("Description", "description"), ("Personality", "personality"), ("Scenario", "scenario")]
        body = "\n\n".join(f"**{lbl}**\n{data.get(f, '').strip()}"
                           for lbl, f in labelled if data.get(f, "").strip())
        name = data.get("name", actor_id)
    else:
        p = pcs.read_persona(aroot, actor_id, vid)
        body = "\n\n".join(x for x in (p.get("summary", "").strip(), p.get("description", "").strip()) if x)
        name = p.get("name", actor_id)
    return {"kind": kind, "id": actor_id, "name": name, "version": vid, "body": body}


def roster(cid: str) -> list[dict]:
    """Every actor that has ever appeared in this campaign, from the record alone.

    Deliberately *not* name-resolving: this runs over the whole record on every
    ``/appearances`` read, and a name costs a card read per actor at its locked
    version. Callers that need names go through ``roster_names`` (drift
    measurement) or ``scene_cast`` (the handful of actors on stage).
    """
    out = []
    for ref, r in sorted(paths.record(cid).items()):
        kind, actor_id = paths._split(ref)
        out.append({"kind": kind, "id": actor_id, "version": r["version"],
                    "role": r["role"], "scenes": r["scenes"]})
    return out


def roster_names(cid: str) -> list[str]:
    """Display names of every actor that has appeared anywhere in this campaign.

    Unlike scene_cast this retains actors after they leave a scene, which is what
    drift measurement needs: its window reaches back three turns, so a departed
    character still has blocks in it. Unreadable actors are skipped.
    """
    aroot = paths.locked_actor_root(cid)
    out = []
    for a in roster(cid):
        name = _actor_name(aroot, a["kind"], a["id"], a["version"])
        if name:
            out.append(name)
    return out


def _actor_name(aroot: Path, kind: str, actor_id: str, vid: str | None) -> str | None:
    """Display name from the campaign copy at the locked version; None if unreadable.

    `aroot` is a `locked_actor_root`: every actor reaching here comes from the
    appearance record, so the campaign-side copy exists.

    A card whose `data` is missing or is not an object falls back to the actor
    id rather than raising. Cards are stored as arbitrary dicts in a store the
    user owns and hand-edits, and this runs over WHOLE ROSTERS -- so indexing
    `data` here let one malformed card several scenes ago take out the caller
    for every actor at once, which is never the right blast radius for "this
    one card has no name in it"."""
    try:
        if kind == "pcs":
            return pcs.read_persona(aroot, actor_id, vid).get("name") or actor_id
        data = characters.read_card(aroot, actor_id, vid).get("data")
        return (data.get("name") if isinstance(data, dict) else None) or actor_id
    except (pcs.PCNotFound, pcs.PCVersionNotFound,
            characters.CharacterNotFound, characters.VersionNotFound):
        return None


def player_names(cid: str, scene_id: str) -> list[str]:
    """Display names of the scene's role=player cast (PCs or characters cast as players)."""
    aroot = paths.locked_actor_root(cid)
    out = []
    for a in players_in_scene(cid, scene_id):
        name = _actor_name(aroot, a["kind"], a["id"], a["version"])
        if name:
            out.append(name)
    return out


def scene_cast(cid: str, scene_id: str) -> list[dict]:
    aroot = paths.locked_actor_root(cid)
    out = []
    for ref, r in paths.record(cid).items():
        if scene_id in r["scenes"]:
            kind, actor_id = paths._split(ref)
            out.append({"kind": kind, "id": actor_id, "role": r["role"],
                        "name": _actor_name(aroot, kind, actor_id, r["version"]) or actor_id})
    return sorted(out, key=lambda a: (a["kind"], a["id"]))


def players_in_scene(cid: str, scene_id: str) -> list[dict]:
    out = []
    for ref, r in paths.record(cid).items():
        if scene_id in r["scenes"] and r["role"] == "player":
            kind, actor_id = paths._split(ref)
            out.append({"kind": kind, "id": actor_id, "version": r["version"]})
    return sorted(out, key=lambda a: (a["kind"], a["id"]))


def is_appeared(cid: str, kind: str, actor_id: str) -> bool:
    return paths._ref(kind, actor_id) in paths.record(cid)
