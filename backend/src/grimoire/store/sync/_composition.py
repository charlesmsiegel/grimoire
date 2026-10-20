"""sync: composition responsibilities."""

from __future__ import annotations

from pathlib import Path

from .. import entities, greetings, overlay
from ..appearances import paths as appearances_paths
from ..appearances import versions as appearances_versions
from ..campaigns import paths as campaigns_paths
from ..campaigns import read as campaigns_read
from . import _pins, _read_shapes


def _composition_state(world_h: str | None, base: str | None, mine_h: str) -> str:
    """One state per ref, from the same three hashes the engine compares.

    A world hash of None is a world-side deletion (or a missing world), and it
    reads as "the world has not moved" here for the same reason `incoming`
    skips it: there is nothing to take, so the campaign's copy simply stands.
    """
    world_moved = world_h is not None and world_h != base
    mine_moved = mine_h != base
    if world_moved and mine_moved:
        return "conflict"
    if world_moved:
        return "update"
    if mine_moved:
        return "diverged"
    return "insync"


def _composition_row(kind: str, eid: str, name: str, state: str, pins: set[str],
                     lock: dict | None = None) -> dict:
    return {"ref": {"kind": kind, "id": eid}, "name": name, "state": state,
            "pinned": _read_shapes._ref_str(kind, eid) in pins, "lock": lock}


def composition(cid: str) -> list[dict]:
    """Every ref this campaign holds against the library, each with its state.

    The join that `incoming` and `diverged` are two projections of (#71): one row
    per ref from sync.md plus one per version-locked actor, with the state
    derived from the world/base/mine hash comparison -- including the two rows
    neither endpoint can report: a materialized ref with nothing pending
    (`insync`), and an actor edited here but never version-locked (`diverged`
    via the whole-dir hash). States and blob-less shape are deliberate: this is
    the overview, and the review panel still owns the diffs it acts on.

    Inherited records -- world records this campaign reads through the overlay
    without holding a copy -- are not rows: they are the world's, not part of
    what the campaign holds, and enumerating a whole library here would price
    the overview by the world's size instead of the campaign's.
    """
    wroot = campaigns_read.world_root_of(cid)
    croot = campaigns_paths.campaign_root(cid)
    manifest = campaigns_paths.read_manifest(cid)
    locked = appearances_paths.record(cid)
    gone = overlay.detached(cid)
    pins = _pins.pinned_refs(cid)
    return (_manifest_rows(cid, wroot, croot, manifest, locked, gone, pins)
            + _locked_rows(cid, wroot, croot, locked, gone, pins))


def _manifest_row(cid: str, wroot: Path, croot: Path, manifest: dict,
                  locked: dict, pins: set[str], ref: str) -> dict | None:
    """One manifest ref's row, or None where `incoming` would skip it too: a
    copy gone since materialization, an actor the locked pass owns, a kind the
    engine does not sync."""
    world_there = wroot.exists()
    kind, _, eid = ref.partition("/")
    if kind == "plotmap":
        mine_h = greetings.plotmap_hash(croot)
        if mine_h is None:
            return None
        world_h = greetings.plotmap_hash(wroot) if world_there else None
        return _composition_row(
            "plotmap", "plotmap", "Plot map",
            _composition_state(world_h, manifest[ref], mine_h), pins)
    if kind in appearances_paths.ACTOR_KINDS:
        if ref in locked:
            return None
        mine_h = _read_shapes._dir_hash(croot, kind, eid)
        if mine_h is None:
            return None
        world_h = _read_shapes._dir_hash(wroot, kind, eid) if world_there else None
        return _composition_row(
            kind, eid, _read_shapes._actor_name_or_id(croot, kind, eid),
            _composition_state(world_h, manifest[ref], mine_h), pins)
    if kind not in entities.SYNCED_KINDS:
        return None
    text = overlay.record_text(cid, kind, eid)
    if text is None:
        return None
    world_h = entities.entity_hash(wroot, kind, eid) if world_there else None
    return _composition_row(
        kind, eid, _read_shapes._blob_name(text, eid),
        _composition_state(world_h, manifest[ref], entities.content_hash(text)), pins)


def _manifest_rows(cid: str, wroot: Path, croot: Path, manifest: dict,
                   locked: dict, gone: set[str], pins: set[str]) -> list[dict]:
    rows = (_manifest_row(cid, wroot, croot, manifest, locked, pins, ref)
            for ref in sorted(set(manifest) - gone))
    return [r for r in rows if r is not None]


def _locked_rows(cid: str, wroot: Path, croot: Path, locked: dict,
                 gone: set[str], pins: set[str]) -> list[dict]:
    aroot = appearances_paths.locked_actor_root(cid)
    rows: list[dict] = []
    for ref, rec in sorted(locked.items()):
        if ref in gone:
            continue
        kind, aid = ref.split("/", 1)
        vid = rec["version"]
        world_h = appearances_versions.actor_hash(wroot, kind, aid, vid)
        mine_h = appearances_versions.actor_hash(croot, kind, aid, vid)
        if mine_h is None:
            continue  # locked copy gone from disk: nothing to report a state on
        rows.append(_composition_row(
            kind, aid, _read_shapes._actor_name_or_id(aroot, kind, aid),
            _composition_state(world_h, rec["base"], mine_h), pins,
            lock={"version": vid, "role": rec["role"], "scenes": rec["scenes"]}))
    return rows


def is_composition_ref(cid: str, kind: str, eid: str) -> bool:
    """Whether a ref is currently part of this campaign's composition -- in the
    sync manifest or the appearance record. What the pin route refuses on: a
    pin on a ref the engine never consults would sit in the file meaning
    nothing, and the 404 says so while somebody is there to read it.

    The plot map's manifest key is the bare "plotmap" while its ref -- and so
    its pin -- is spelled "plotmap/plotmap" like every other; this is the one
    place the two spellings meet."""
    if kind == "plotmap":
        return "plotmap" in campaigns_paths.read_manifest(cid)
    ref = _read_shapes._ref_str(kind, eid)
    return (ref in campaigns_paths.read_manifest(cid)
            or ref in appearances_paths.record(cid))
