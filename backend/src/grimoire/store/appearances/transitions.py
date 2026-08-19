"""Scene-facing appearance transitions: entering/leaving a scene's cast, and
suggesting characters not yet appeared. Unlike ``cast.py``'s read-only
queries, these touch scene state (narration messages), which is why they --
not the cast readers -- are the ones that need ``scenes``.
"""

from __future__ import annotations

import re

from .. import characters, overlay
from ..campaigns import read as campaigns_read

# Only the read/write/serialize leaves, never the `scenes` facade: `scenes/
# read.py` imports `cast.py` from this package, so binding whole packages in
# both directions would close a cycle these file-level edges do not.
from ..scenes import paths as scenes_paths
from ..scenes import read as scenes_read
from ..scenes import serialize as scenes_serialize
from ..scenes import write as scenes_write
from . import cast, paths, versions


def appear(cid: str, scene_id: str, kind: str, actor_id: str, version_id: str, role: str,
           narrate: bool = True) -> None:
    data = paths.record(cid)
    ref = paths._ref(kind, actor_id)
    rec = data.get(ref)
    if rec is not None:
        if rec["version"] != version_id:
            raise paths.AppearError(f"{ref} is locked to version {rec['version']}, not {version_id}")
        if rec["role"] != role:
            raise paths.AppearError(f"{ref} is locked to role {rec['role']}, not {role}")
        if scene_id not in rec["scenes"]:
            rec["scenes"].append(scene_id)
            paths._write(cid, data)
        else:
            return  # already in this scene: no-op, no narration
    else:
        base = versions._lock(cid, kind, actor_id, version_id)  # lazy pick: first appearance locks
        data[ref] = {"version": version_id, "base": base, "scenes": [scene_id], "role": role}
        paths._write(cid, data)
        campaigns_read.touch(cid)

    if not narrate:
        return
    try:
        has_messages = bool(scenes_read.read_scene(cid, scene_id)["messages"])
    except scenes_paths.SceneNotFound:
        return  # synthetic/test scene id with no backing file: nothing to narrate into
    if has_messages:
        name = cast._actor_name(paths.locked_actor_root(cid), kind, actor_id, version_id) or actor_id
        scenes_write.append_message(cid, scene_id, "assistant", f"*{name} joins the scene.*",
                              speaker=scenes_serialize.TRANSITION_SPEAKER)


def leave(cid: str, scene_id: str, kind: str, actor_id: str) -> None:
    """Drop `scene_id` from the actor's appearance record. The actor stays
    appeared campaign-wide (other scenes, roster) -- only this scene's cast
    loses them. Narrates a transition line once the scene already has
    messages; silent while the scene is still in pre-first-message setup,
    matching appear()'s silent-first-add.

    Idempotent: an actor already absent from this scene's cast (never cast,
    or a repeat call after a lost response / retry) is a silent no-op, not
    an error -- a retried DELETE must not fail just because the first
    attempt already landed."""
    data = paths.record(cid)
    ref = paths._ref(kind, actor_id)
    rec = data.get(ref)
    if rec is None or scene_id not in rec.get("scenes", []):
        return
    version = rec["version"]
    rec["scenes"].remove(scene_id)
    paths._write(cid, data)
    try:
        has_messages = bool(scenes_read.read_scene(cid, scene_id)["messages"])
    except scenes_paths.SceneNotFound:
        return
    if has_messages:
        name = cast._actor_name(paths.locked_actor_root(cid), kind, actor_id, version) or actor_id
        scenes_write.append_message(cid, scene_id, "assistant", f"*{name} leaves the scene.*",
                              speaker=scenes_serialize.TRANSITION_SPEAKER)


def suggestions(cid: str, scene_id: str) -> list[dict]:
    appeared_chars = {actor_id for ref in paths.record(cid) for k, actor_id in [paths._split(ref)] if k == "characters"}
    dismissed = set(scenes_read.get_dismissed(cid, scene_id))
    in_scene_chars = [a["id"] for a in cast.scene_cast(cid, scene_id) if a["kind"] == "characters"]
    candidates = [c for c in overlay.list_characters(cid)
                  if c["id"] not in appeared_chars and c["id"] not in dismissed and c["id"] not in in_scene_chars]

    mentioned_by: dict[str, list[str]] = {}
    for char_id in in_scene_chars:
        card = characters.read_card(overlay.char_root(cid, char_id), char_id,
                                    versions.locked_version(cid, "characters", char_id))
        d = card.get("data", {})
        text = "\n".join(d.get(f) for f in ("description", "personality", "scenario", "first_mes", "mes_example")
                         if isinstance(d.get(f), str))
        for c in candidates:
            if re.search(rf"\b{re.escape(c['name'])}\b", text, re.IGNORECASE):
                mentioned_by.setdefault(c["id"], []).append(char_id)

    return [{"character": c["id"], "name": c["name"], "mentioned_by": sorted(set(mentioned_by[c["id"]]))}
            for c in candidates if c["id"] in mentioned_by]
