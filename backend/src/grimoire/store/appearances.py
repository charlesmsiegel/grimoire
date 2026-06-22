"""Per-campaign character appearance state: which characters appeared, the locked
version, the sync base hash, and the scenes they're in. Source of truth for
characters in a campaign (the generic sync.md covers only locations/lore).

Stored as <campaign>/appearances.json:
  {"seraphine": {"version": "corrupted", "base": "<hash>", "scenes": ["the-docks"]}}
"""

from __future__ import annotations

import json
import re
import shutil

from . import campaigns, characters, worlds
from .frontmatter import dump_frontmatter, parse_frontmatter


class AppearError(Exception):
    pass


def _path(cid: str):
    return campaigns.campaign_root(cid) / "appearances.json"


def record(cid: str) -> dict:
    p = _path(cid)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def _write(cid: str, data: dict) -> None:
    _path(cid).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def set_base(cid: str, char_id: str, base: str) -> None:
    """Advance the recorded sync base hash for an appeared character (sync uses this)."""
    data = record(cid)
    if char_id in data:
        data[char_id]["base"] = base
        _write(cid, data)


def _world_id(cid: str) -> str:
    return campaigns.read_campaign(cid)["meta"].get("world", "")


def appear(cid: str, scene_id: str, char_id: str, version_id: str) -> None:
    data = record(cid)
    rec = data.get(char_id)
    if rec is not None:
        if rec["version"] != version_id:
            raise AppearError(f"{char_id} is locked to {rec['version']}, not {version_id}")
        if scene_id not in rec["scenes"]:
            rec["scenes"].append(scene_id)
            _write(cid, data)
        return

    wroot = worlds.world_root(_world_id(cid))
    croot = campaigns.campaign_root(cid)
    base = characters.card_hash(wroot, char_id, version_id)
    if base is None:
        raise AppearError(f"world has no {char_id}/{version_id}")
    # copy only the locked version card (+ assets) into the campaign
    src_dir = wroot / "characters" / char_id
    dst_dir = croot / "characters" / char_id
    dst_dir.mkdir(parents=True, exist_ok=True)
    (dst_dir / f"{version_id}.json").write_text(
        (src_dir / f"{version_id}.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    # container meta so campaign-side reads work; default_version must point at the
    # one version we copied (the world's default may be a version we didn't take).
    meta, _ = parse_frontmatter((src_dir / "character.md").read_text(encoding="utf-8"))
    meta["default_version"] = version_id
    (dst_dir / "character.md").write_text(dump_frontmatter(meta, ""), encoding="utf-8")
    if (src_dir / "assets").exists():
        shutil.copytree(src_dir / "assets", dst_dir / "assets", dirs_exist_ok=True)
    data[char_id] = {"version": version_id, "base": base, "scenes": [scene_id]}
    _write(cid, data)
    campaigns.touch(cid)


def roster(cid: str) -> list[dict]:
    data = record(cid)
    return [{"character": c, "version": r["version"], "scenes": r["scenes"]}
            for c, r in sorted(data.items())]


def scene_cast(cid: str, scene_id: str) -> list[str]:
    return sorted(c for c, r in record(cid).items() if scene_id in r["scenes"])


def is_appeared(cid: str, char_id: str) -> bool:
    return char_id in record(cid)


def locked_version(cid: str, char_id: str) -> str | None:
    rec = record(cid).get(char_id)
    return rec["version"] if rec else None


def suggestions(cid: str, scene_id: str) -> list[dict]:
    from . import scenes
    croot = campaigns.campaign_root(cid)
    wroot = worlds.world_root(_world_id(cid))
    appeared = set(record(cid))
    dismissed = set(scenes.get_dismissed(cid, scene_id))
    cast = scene_cast(cid, scene_id)
    candidates = [c for c in characters.list_characters(wroot)
                  if c["id"] not in appeared and c["id"] not in dismissed and c["id"] not in cast]

    # for each in-scene card, find which candidate names it mentions
    mentioned_by: dict[str, list[str]] = {}
    for char_id in cast:
        card = characters.read_card(croot, char_id, locked_version(cid, char_id))
        d = card.get("data", {})
        text = "\n".join(d.get(f) for f in ("description", "personality", "scenario", "first_mes", "mes_example")
                         if isinstance(d.get(f), str))
        for c in candidates:
            if re.search(rf"\b{re.escape(c['name'])}\b", text, re.IGNORECASE):
                mentioned_by.setdefault(c["id"], []).append(char_id)

    return [{"character": c["id"], "name": c["name"], "mentioned_by": sorted(set(mentioned_by[c["id"]]))}
            for c in candidates if c["id"] in mentioned_by]
