"""The push/sync engine: per-campaign incoming changes + accept/reject.

Compares three content hashes per ref (kind, id):
  world = world entity's current hash (or None)
  base  = campaign sync.md[ref]        (or None)
  mine  = campaign entity's current hash (or None)
An incoming change exists iff world is not None and world != base.
"""

from __future__ import annotations

from pathlib import Path

from . import appearances, campaigns, characters, entities, worlds


def _world_id(cid: str) -> str:
    return campaigns.read_campaign(cid)["meta"].get("world", "")


def _ref_str(kind: str, eid: str) -> str:
    return f"{kind}/{eid}"


def _entity_blob(root: Path, kind: str, eid: str) -> dict:
    e = entities.read_entity(root, kind, eid)
    return {"name": e["meta"].get("name", eid), "body": e["body"]}


def incoming(cid: str) -> list[dict]:
    wid = _world_id(cid)  # raises CampaignNotFound if the campaign is missing
    wroot = worlds.world_root(wid)
    croot = campaigns.campaign_root(cid)
    manifest = campaigns.read_manifest(cid)

    refs: set[str] = set(manifest)
    if wroot.exists():
        refs |= {_ref_str(k, e) for k, e in entities.all_refs(wroot)}
    refs |= {_ref_str(k, e) for k, e in entities.all_refs(croot)}

    out: list[dict] = []
    for ref in sorted(refs):
        kind, _, eid = ref.partition("/")
        if kind not in entities.ENTITY_KINDS:
            continue
        world_h = entities.entity_hash(wroot, kind, eid) if wroot.exists() else None
        base_h = manifest.get(ref)
        if world_h is None or world_h == base_h:
            continue  # no incoming change (incl. world-side deletions, skipped)
        mine_h = entities.entity_hash(croot, kind, eid)
        if mine_h is None:
            status = "new"
        elif mine_h == base_h:
            status = "update"
        else:
            status = "conflict"
        item: dict = {"ref": {"kind": kind, "id": eid}, "status": status,
                      "world": _entity_blob(wroot, kind, eid)}
        if mine_h is not None:
            item["mine"] = _entity_blob(croot, kind, eid)
        out.append(item)
    return out + _character_incoming(cid)


def _card_blob(root: Path, char_id: str, vid: str) -> dict:
    card = characters.read_card(root, char_id, vid)
    return {"name": card["data"].get("name", char_id), "version": vid, "card": card}


def _character_incoming(cid: str) -> list[dict]:
    wroot = worlds.world_root(_world_id(cid))
    croot = campaigns.campaign_root(cid)
    out: list[dict] = []
    for char_id, rec in sorted(appearances.record(cid).items()):
        vid = rec["version"]
        world_h = characters.card_hash(wroot, char_id, vid)
        if world_h is None or world_h == rec["base"]:
            continue  # world unchanged (or locked version deleted, which we skip)
        mine_h = characters.card_hash(croot, char_id, vid)
        status = "update" if mine_h == rec["base"] else "conflict"
        item = {"ref": {"kind": "characters", "id": char_id}, "status": status,
                "world": _card_blob(wroot, char_id, vid)}
        if mine_h is not None:
            item["mine"] = _card_blob(croot, char_id, vid)
        out.append(item)
    return out


def _advance_character(cid: str, char_id: str, *, copy: bool) -> bool:
    wroot = worlds.world_root(_world_id(cid))
    croot = campaigns.campaign_root(cid)
    rec = appearances.record(cid).get(char_id)
    if rec is None:
        return False
    vid = rec["version"]
    world_h = characters.card_hash(wroot, char_id, vid)
    if world_h is None or rec["base"] == world_h:
        return False  # not pending
    if copy:
        src = wroot / "characters" / char_id / f"{vid}.json"
        dst = croot / "characters" / char_id / f"{vid}.json"
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    appearances.set_base(cid, char_id, world_h)
    return True


def _advance(cid: str, refs: list[dict], *, copy: bool) -> None:
    wid = _world_id(cid)
    wroot = worlds.world_root(wid)
    croot = campaigns.campaign_root(cid)
    manifest = campaigns.read_manifest(cid)
    manifest_changed = False  # loc/lore manifest write
    touched = False           # any ref advanced → bump campaign.updated
    for ref in refs:
        kind, eid = ref["kind"], ref["id"]
        if kind == "characters":
            if _advance_character(cid, eid, copy=copy):
                touched = True
            continue
        world_h = entities.entity_hash(wroot, kind, eid) if wroot.exists() else None
        if world_h is None or manifest.get(_ref_str(kind, eid)) == world_h:
            continue  # not pending (no world file, or base already == world): no-op
        if copy:
            src = wroot / kind / f"{eid}.md"
            dst_dir = croot / kind
            dst_dir.mkdir(parents=True, exist_ok=True)
            (dst_dir / f"{eid}.md").write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        manifest[_ref_str(kind, eid)] = world_h
        manifest_changed = True
        touched = True
    if manifest_changed:
        campaigns.write_manifest(cid, manifest)
    if touched:
        campaigns.touch(cid)


def accept(cid: str, refs: list[dict]) -> None:
    _advance(cid, refs, copy=True)


def reject(cid: str, refs: list[dict]) -> None:
    _advance(cid, refs, copy=False)


def campaigns_for_world(wid: str) -> list[dict]:
    out: list[dict] = []
    for c in campaigns.list_campaigns():
        if c.get("world") != wid:
            continue
        counts = {"new": 0, "update": 0, "conflict": 0}
        for p in incoming(c["id"]):
            counts[p["status"]] += 1
        out.append({"id": c["id"], "name": c["name"], "pending": counts})
    return out
