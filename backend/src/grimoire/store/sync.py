"""The push/sync engine: per-campaign incoming changes + accept/reject.

Compares three content hashes per ref (kind, id):
  world = world entity's current hash (or None)
  base  = campaign sync.md[ref]        (or None)
  mine  = campaign entity's current hash (or None)
An incoming change exists iff world is not None and world != base.
"""

from __future__ import annotations

from pathlib import Path

from . import appearances, atomic, campaigns, characters, entities, greetings, overlay, pcs, worlds


def _world_id(cid: str) -> str:
    return campaigns.read_campaign(cid)["meta"].get("world", "")


def _ref_str(kind: str, eid: str) -> str:
    return f"{kind}/{eid}"


def _entity_blob(root: Path, kind: str, eid: str) -> dict:
    if kind == "greetings":
        g = greetings.read_greeting(root, eid)
        return {"name": g["meta"].get("name", eid), "body": g["body"]}
    e = entities.read_entity(root, kind, eid)
    return {"name": e["meta"].get("name", eid), "body": e["body"]}


def incoming(cid: str) -> list[dict]:
    wid = _world_id(cid)  # raises CampaignNotFound if the campaign is missing
    wroot = worlds.world_root_or_missing(wid)
    croot = campaigns.campaign_root(cid)
    # read campaign.md / sync.md / appearances.json once and thread them through
    # the passes -- each used to re-read all three per pass
    manifest = campaigns.read_manifest(cid)
    locked = appearances.record(cid)

    refs: set[str] = set(manifest)

    out: list[dict] = []
    for ref in sorted(refs):
        kind, _, eid = ref.partition("/")
        if kind not in entities.SYNCED_KINDS:
            continue  # actor refs + plotmap are handled by their own passes
        world_h = entities.entity_hash(wroot, kind, eid) if wroot.exists() else None
        base_h = manifest.get(ref)
        if world_h is None or world_h == base_h:
            continue  # no incoming change (incl. world-side deletions, skipped)
        mine_h = entities.entity_hash(croot, kind, eid)
        if mine_h is None:
            continue  # copy gone since materialization: nothing to reconcile
        status = "update" if mine_h == base_h else "conflict"
        out.append({"ref": {"kind": kind, "id": eid}, "status": status,
                    "world": _entity_blob(wroot, kind, eid),
                    "mine": _entity_blob(croot, kind, eid)})
    return (out + _plotmap_incoming(wroot, croot, manifest)
            + _actor_incoming(wroot, croot, locked)
            + _unpicked_incoming(wroot, croot, manifest, locked))


def _plotmap_blob(root: Path) -> dict:
    p = root / "plotmap.json"
    return {"name": "Plot map", "body": p.read_text(encoding="utf-8") if p.exists() else ""}


def _plotmap_incoming(wroot: Path, croot: Path, manifest: dict) -> list[dict]:
    if "plotmap" not in manifest or not (croot / "plotmap.json").exists():
        return []
    world_h = greetings.plotmap_hash(wroot) if wroot.exists() else None
    base = manifest.get("plotmap")
    if world_h is None or world_h == base:
        return []
    mine_h = greetings.plotmap_hash(croot)
    status = "update" if mine_h == base else "conflict"
    return [{"ref": {"kind": "plotmap", "id": "plotmap"}, "status": status,
            "world": _plotmap_blob(wroot), "mine": _plotmap_blob(croot)}]


def _actor_blob(root: Path, kind: str, actor_id: str, vid: str) -> dict:
    if kind == "characters":
        card = characters.read_card(root, actor_id, vid)
        return {"name": card["data"].get("name", actor_id), "version": vid, "card": card}
    persona = pcs.read_persona(root, actor_id, vid)
    return {"name": persona.get("name", actor_id), "version": vid, "persona": persona}


def _actor_incoming(wroot: Path, croot: Path, locked: dict) -> list[dict]:
    out: list[dict] = []
    for ref, rec in sorted(locked.items()):
        kind, actor_id = ref.split("/", 1)
        vid = rec["version"]
        world_h = appearances.actor_hash(wroot, kind, actor_id, vid)
        if world_h is None or world_h == rec["base"]:
            continue  # world unchanged (or locked version deleted, which we skip)
        mine_h = appearances.actor_hash(croot, kind, actor_id, vid)
        status = "update" if mine_h == rec["base"] else "conflict"
        item = {"ref": {"kind": kind, "id": actor_id}, "status": status,
                "world": _actor_blob(wroot, kind, actor_id, vid)}
        if mine_h is not None:
            item["mine"] = _actor_blob(croot, kind, actor_id, vid)
        out.append(item)
    return out


def _dir_hash(root: Path, kind: str, actor_id: str) -> str | None:
    return characters.dir_hash(root, actor_id) if kind == "characters" else pcs.dir_hash(root, actor_id)


def _actor_summary_blob(root: Path, kind: str, actor_id: str) -> dict:
    detail = (characters.read_character(root, actor_id) if kind == "characters"
              else pcs.read_pc(root, actor_id))
    versions = ", ".join(v["id"] for v in detail["versions"])
    return {"name": detail["meta"].get("name", actor_id), "body": f"versions: {versions}"}


def _unpicked_incoming(wroot: Path, croot: Path, manifest: dict, locked: dict) -> list[dict]:
    """Whole-actor diffs for materialized actors with no version lock: one item per
    changed actor; accept dematerializes (revert to inherited), reject advances the base."""
    refs = {r for r in manifest if r.partition("/")[0] in appearances.ACTOR_KINDS}
    out: list[dict] = []
    for ref in sorted(refs):
        if ref in locked:
            continue  # the per-locked-version pass owns this actor
        kind, _, aid = ref.partition("/")
        world_h = _dir_hash(wroot, kind, aid) if wroot.exists() else None
        if world_h is None or world_h == manifest.get(ref):
            continue  # no incoming change (incl. world-side deletions, skipped)
        mine_h = _dir_hash(croot, kind, aid)
        if mine_h is None:
            continue  # copy gone since materialization: nothing to reconcile
        status = "update" if mine_h == manifest.get(ref) else "conflict"
        out.append({"ref": {"kind": kind, "id": aid}, "status": status,
                    "world": _actor_summary_blob(wroot, kind, aid),
                    "mine": _actor_summary_blob(croot, kind, aid)})
    return out


def _advance_actor(cid: str, kind: str, actor_id: str, *, copy: bool) -> bool:
    wroot = worlds.world_root_or_missing(_world_id(cid))
    croot = campaigns.campaign_root(cid)
    rec = appearances.record(cid).get(f"{kind}/{actor_id}")
    if rec is None:
        return False
    vid = rec["version"]
    world_h = appearances.actor_hash(wroot, kind, actor_id, vid)
    if world_h is None or rec["base"] == world_h:
        return False  # not pending
    if copy:
        ext = "json" if kind == "characters" else "md"
        src = wroot / kind / actor_id / f"{vid}.{ext}"
        dst = croot / kind / actor_id / f"{vid}.{ext}"
        dst.parent.mkdir(parents=True, exist_ok=True)
        atomic.write_text(dst, src.read_text(encoding="utf-8"))
    appearances.set_base(cid, kind, actor_id, world_h)
    return True


def _advance(cid: str, refs: list[dict], *, copy: bool) -> None:
    wid = _world_id(cid)
    wroot = worlds.world_root_or_missing(wid)
    croot = campaigns.campaign_root(cid)
    manifest = campaigns.read_manifest(cid)
    manifest_changed = False  # loc/lore manifest write
    touched = False           # any ref advanced → bump campaign.updated
    for ref in refs:
        kind, eid = ref["kind"], ref["id"]
        if kind == "plotmap":
            world_h = greetings.plotmap_hash(wroot) if wroot.exists() else None
            pending = ("plotmap" in manifest and world_h is not None
                       and manifest["plotmap"] != world_h)
            if not pending:
                continue
            if copy:   # take world: drop our copy, revert to inherited
                (croot / "plotmap.json").unlink(missing_ok=True)
                manifest.pop("plotmap", None)
            else:
                manifest["plotmap"] = world_h
            manifest_changed = touched = True
            continue
        if kind in appearances.ACTOR_KINDS:
            if appearances.record(cid).get(_ref_str(kind, eid)) is not None:
                if _advance_actor(cid, kind, eid, copy=copy):   # locked flow: unchanged
                    touched = True
                continue
            world_h = _dir_hash(wroot, kind, eid) if wroot.exists() else None
            if world_h is None or manifest.get(_ref_str(kind, eid)) == world_h:
                continue
            if copy:
                overlay.dematerialize_actor(cid, kind, eid)
                manifest.pop(_ref_str(kind, eid), None)
            else:
                manifest[_ref_str(kind, eid)] = world_h
            manifest_changed = touched = True
            continue
        world_h = entities.entity_hash(wroot, kind, eid) if wroot.exists() else None
        if world_h is None or manifest.get(_ref_str(kind, eid)) == world_h:
            continue
        if copy:
            (croot / kind / f"{eid}.md").unlink(missing_ok=True)
            manifest.pop(_ref_str(kind, eid), None)
        else:
            manifest[_ref_str(kind, eid)] = world_h
        manifest_changed = touched = True
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
