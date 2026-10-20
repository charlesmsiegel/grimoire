"""sync: incoming responsibilities."""

from __future__ import annotations

from pathlib import Path

from .. import entities, greetings, overlay
from ..appearances import paths as appearances_paths
from ..appearances import versions as appearances_versions
from ..campaigns import paths as campaigns_paths
from ..campaigns import read as campaigns_read
from . import _display_refs, _pins, _read_shapes


def incoming(cid: str) -> list[dict]:
    v = overlay.view(cid)
    wroot, croot = v.wroot, v.croot  # raises CampaignNotFound if the campaign is missing
    # read campaign.md / sync.md / appearances.json once and thread them through
    # the passes -- each used to re-read all three per pass
    manifest = campaigns_paths.read_manifest(cid)
    locked = appearances_paths.record(cid)
    # A detached record shares only a slug with whatever the world now holds
    # under its id, so nothing there is an update to it (#225). Dropping the
    # manifest ref covers the entity and unpicked-actor passes; a version lock
    # keeps its base in appearances.json, so that pass has to be told.
    gone = overlay.detached(cid)

    refs: set[str] = set(manifest) - gone

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
                    "world": _read_shapes._entity_blob(wroot, kind, eid),
                    "mine": _read_shapes._entity_blob(croot, kind, eid)})
        if kind in entities.ENTITY_KINDS:
            _read_shapes._align_entity_fields(out[-1]["world"], out[-1]["mine"], kind)
    items = (out + _plotmap_incoming(wroot, croot, manifest)
             + _actor_incoming(wroot, croot, locked, gone)
             + _unpicked_incoming(wroot, croot, manifest, locked, gone))
    # LAST, over the combined list, so a pin means the same thing for every
    # pass: a pinned ref is simply not offered (#71). The composition view is
    # where a pinned ref still reports the state this filter is hiding.
    pins = _pins.pinned_refs(cid)
    if pins:
        items = [i for i in items if _read_shapes._ref_str(i["ref"]["kind"], i["ref"]["id"]) not in pins]
    _display_refs._show_incoming_fields(cid, items, v)
    return items


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
            "world": _read_shapes._plotmap_blob(wroot), "mine": _read_shapes._plotmap_blob(croot)}]


def _actor_incoming(wroot: Path, croot: Path, locked: dict, detached: set[str]) -> list[dict]:
    out: list[dict] = []
    for ref, rec in sorted(locked.items()):
        if ref in detached:
            continue  # the lock's base outlived its world actor; see overlay.detached
        kind, actor_id = ref.split("/", 1)
        vid = rec["version"]
        world_h = appearances_versions.actor_hash(wroot, kind, actor_id, vid)
        if world_h is None or world_h == rec["base"]:
            continue  # world unchanged (or locked version deleted, which we skip)
        mine_h = appearances_versions.actor_hash(croot, kind, actor_id, vid)
        status = "update" if mine_h == rec["base"] else "conflict"
        item = {"ref": {"kind": kind, "id": actor_id}, "status": status,
                "world": _read_shapes._actor_blob(wroot, kind, actor_id, vid)}
        if mine_h is not None:
            item["mine"] = _read_shapes._actor_blob(croot, kind, actor_id, vid)
        out.append(item)
    return out


def _unpicked_incoming(wroot: Path, croot: Path, manifest: dict, locked: dict,
                       detached: set[str]) -> list[dict]:
    """Whole-actor diffs for materialized actors with no version lock: one item per
    changed actor; accept dematerializes (revert to inherited), reject advances the base."""
    refs = {r for r in manifest
            if r.partition("/")[0] in appearances_paths.ACTOR_KINDS and r not in detached}
    out: list[dict] = []
    for ref in sorted(refs):
        if ref in locked:
            continue  # the per-locked-version pass owns this actor
        kind, _, aid = ref.partition("/")
        world_h = _read_shapes._dir_hash(wroot, kind, aid) if wroot.exists() else None
        if world_h is None or world_h == manifest.get(ref):
            continue  # no incoming change (incl. world-side deletions, skipped)
        mine_h = _read_shapes._dir_hash(croot, kind, aid)
        if mine_h is None:
            continue  # copy gone since materialization: nothing to reconcile
        status = "update" if mine_h == manifest.get(ref) else "conflict"
        out.append({"ref": {"kind": kind, "id": aid}, "status": status,
                    "world": _read_shapes._actor_summary_blob(wroot, kind, aid),
                    "mine": _read_shapes._actor_summary_blob(croot, kind, aid)})
    return out


def campaigns_for_world(wid: str) -> list[dict]:
    out: list[dict] = []
    for c in campaigns_read.list_campaigns():
        if c.get("world") != wid:
            continue
        counts = {"new": 0, "update": 0, "conflict": 0}
        for p in incoming(c["id"]):
            counts[p["status"]] += 1
        out.append({"id": c["id"], "name": c["name"], "pending": counts})
    return out
