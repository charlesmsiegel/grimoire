"""Per-campaign actor appearance state: which actors (characters or PCs) appeared, the
locked version, role, sync base hash, and the scenes they're in. Source of truth for
actors in a campaign (the generic sync.md covers only locations/lore).

Stored as <campaign>/appearances.json, keyed "<kind>/<id>":
  {"characters/seraphine": {"version":"corrupted","base":"<h>","scenes":["s1"],"role":"npc"},
   "pcs/elara":            {"version":"default","base":"<h>","scenes":["s1"],"role":"player"}}
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from . import campaigns, characters, pcs, worlds
from .frontmatter import dump_frontmatter, parse_frontmatter

ACTOR_KINDS = ("characters", "pcs")


class AppearError(Exception):
    pass


def _ref(kind: str, actor_id: str) -> str:
    return f"{kind}/{actor_id}"


def _split(ref: str) -> tuple[str, str]:
    kind, _, actor_id = ref.partition("/")
    return kind, actor_id


def _path(cid: str) -> Path:
    return campaigns.campaign_root(cid) / "appearances.json"


def record(cid: str) -> dict:
    p = _path(cid)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def _write(cid: str, data: dict) -> None:
    _path(cid).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def set_base(cid: str, kind: str, actor_id: str, base: str) -> None:
    """Advance the recorded sync base hash for an appeared actor (sync uses this)."""
    data = record(cid)
    ref = _ref(kind, actor_id)
    if ref in data:
        data[ref]["base"] = base
        _write(cid, data)


def _world_id(cid: str) -> str:
    return campaigns.read_campaign(cid)["meta"].get("world", "")


def actor_hash(root: Path, kind: str, actor_id: str, vid: str) -> str | None:
    if kind == "characters":
        return characters.card_hash(root, actor_id, vid)
    return pcs.version_hash(root, actor_id, vid)


def _version_ext(kind: str) -> str:
    return "json" if kind == "characters" else "md"


def _meta_name(kind: str) -> str:
    return "character.md" if kind == "characters" else "pc.md"


def _copy_actor(wroot: Path, croot: Path, kind: str, actor_id: str, vid: str) -> None:
    src_dir = wroot / kind / actor_id
    dst_dir = croot / kind / actor_id
    dst_dir.mkdir(parents=True, exist_ok=True)
    ext = _version_ext(kind)
    (dst_dir / f"{vid}.{ext}").write_text((src_dir / f"{vid}.{ext}").read_text(encoding="utf-8"), encoding="utf-8")
    # container meta so campaign-side reads work; default_version points at the copied version
    meta, _ = parse_frontmatter((src_dir / _meta_name(kind)).read_text(encoding="utf-8"))
    meta["default_version"] = vid
    (dst_dir / _meta_name(kind)).write_text(dump_frontmatter(meta, ""), encoding="utf-8")
    if kind == "characters":
        if (src_dir / "assets").exists():
            shutil.copytree(src_dir / "assets", dst_dir / "assets", dirs_exist_ok=True)
        if (src_dir / "brief.md").exists():
            (dst_dir / "brief.md").write_text(
                (src_dir / "brief.md").read_text(encoding="utf-8"), encoding="utf-8")


def appear(cid: str, scene_id: str, kind: str, actor_id: str, version_id: str, role: str) -> None:
    data = record(cid)
    ref = _ref(kind, actor_id)
    rec = data.get(ref)
    if rec is not None:
        if rec["version"] != version_id:
            raise AppearError(f"{ref} is locked to version {rec['version']}, not {version_id}")
        if rec["role"] != role:
            raise AppearError(f"{ref} is locked to role {rec['role']}, not {role}")
        if scene_id not in rec["scenes"]:
            rec["scenes"].append(scene_id)
            _write(cid, data)
        return

    wroot = worlds.world_root(_world_id(cid))
    croot = campaigns.campaign_root(cid)
    base = actor_hash(wroot, kind, actor_id, version_id)
    if base is None:
        # Campaign-local overlay actor: no world source, so it must already exist
        # in the campaign. Record an empty world-base; sync skips refs the world
        # lacks, so a local actor never surfaces as an incoming change.
        if actor_hash(croot, kind, actor_id, version_id) is None:
            raise AppearError(f"no {ref}/{version_id} in world or campaign")
        base = ""
    else:
        _copy_actor(wroot, croot, kind, actor_id, version_id)
    data[ref] = {"version": version_id, "base": base, "scenes": [scene_id], "role": role}
    _write(cid, data)
    campaigns.touch(cid)


def cast_detail(cid: str, sid: str, kind: str, actor_id: str) -> dict:
    """Read-only display info for an actor in a scene, from the campaign copy."""
    if not any(a["kind"] == kind and a["id"] == actor_id for a in scene_cast(cid, sid)):
        raise AppearError(f"{kind}/{actor_id} is not in scene {sid}")
    croot = campaigns.campaign_root(cid)
    vid = locked_version(cid, kind, actor_id)
    if kind == "characters":
        data = characters.read_card(croot, actor_id, vid)["data"]
        labelled = [("Description", "description"), ("Personality", "personality"), ("Scenario", "scenario")]
        body = "\n\n".join(f"**{lbl}**\n{data.get(f, '').strip()}"
                           for lbl, f in labelled if data.get(f, "").strip())
        name = data.get("name", actor_id)
    else:
        p = pcs.read_persona(croot, actor_id, vid)
        body = "\n\n".join(x for x in (p.get("summary", "").strip(), p.get("description", "").strip()) if x)
        name = p.get("name", actor_id)
    return {"kind": kind, "id": actor_id, "name": name, "version": vid, "body": body}


def roster(cid: str) -> list[dict]:
    out = []
    for ref, r in sorted(record(cid).items()):
        kind, actor_id = _split(ref)
        out.append({"kind": kind, "id": actor_id, "version": r["version"], "role": r["role"], "scenes": r["scenes"]})
    return out


def scene_cast(cid: str, scene_id: str) -> list[dict]:
    out = []
    for ref, r in record(cid).items():
        if scene_id in r["scenes"]:
            kind, actor_id = _split(ref)
            out.append({"kind": kind, "id": actor_id, "role": r["role"]})
    return sorted(out, key=lambda a: (a["kind"], a["id"]))


def players_in_scene(cid: str, scene_id: str) -> list[dict]:
    out = []
    for ref, r in record(cid).items():
        if scene_id in r["scenes"] and r["role"] == "player":
            kind, actor_id = _split(ref)
            out.append({"kind": kind, "id": actor_id, "version": r["version"]})
    return sorted(out, key=lambda a: (a["kind"], a["id"]))


def is_appeared(cid: str, kind: str, actor_id: str) -> bool:
    return _ref(kind, actor_id) in record(cid)


def locked_version(cid: str, kind: str, actor_id: str) -> str | None:
    rec = record(cid).get(_ref(kind, actor_id))
    return rec["version"] if rec else None


def suggestions(cid: str, scene_id: str) -> list[dict]:
    from . import scenes
    croot = campaigns.campaign_root(cid)
    wroot = worlds.world_root(_world_id(cid))
    appeared_chars = {actor_id for ref in record(cid) for k, actor_id in [_split(ref)] if k == "characters"}
    dismissed = set(scenes.get_dismissed(cid, scene_id))
    in_scene_chars = [a["id"] for a in scene_cast(cid, scene_id) if a["kind"] == "characters"]
    candidates = [c for c in characters.list_characters(wroot)
                  if c["id"] not in appeared_chars and c["id"] not in dismissed and c["id"] not in in_scene_chars]

    mentioned_by: dict[str, list[str]] = {}
    for char_id in in_scene_chars:
        card = characters.read_card(croot, char_id, locked_version(cid, "characters", char_id))
        d = card.get("data", {})
        text = "\n".join(d.get(f) for f in ("description", "personality", "scenario", "first_mes", "mes_example")
                         if isinstance(d.get(f), str))
        for c in candidates:
            if re.search(rf"\b{re.escape(c['name'])}\b", text, re.IGNORECASE):
                mentioned_by.setdefault(c["id"], []).append(char_id)

    return [{"character": c["id"], "name": c["name"], "mentioned_by": sorted(set(mentioned_by[c["id"]]))}
            for c in candidates if c["id"] in mentioned_by]
