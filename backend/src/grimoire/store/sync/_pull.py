"""sync: pull responsibilities."""

from __future__ import annotations

from .. import atomic, entities, greetings, journal, locks, overlay, pcs, revision
from ..appearances import paths as appearances_paths
from ..appearances import versions as appearances_versions
from ..campaigns import lifecycle as campaigns_lifecycle
from ..campaigns import paths as campaigns_paths
from ..campaigns import read as campaigns_read
from . import _pins, _read_shapes


def _advance_actor(cid: str, kind: str, actor_id: str, *, copy: bool) -> bool:
    wroot = campaigns_read.world_root_of(cid)
    croot = campaigns_paths.campaign_root(cid)
    rec = appearances_paths.record(cid).get(f"{kind}/{actor_id}")
    if rec is None:
        return False
    vid = rec["version"]
    world_h = appearances_versions.actor_hash(wroot, kind, actor_id, vid)
    if world_h is None or rec["base"] == world_h:
        return False  # not pending
    if copy:
        ext = "json" if kind == "characters" else "md"
        src = wroot / kind / actor_id / f"{vid}.{ext}"
        dst = croot / kind / actor_id / f"{vid}.{ext}"
        dst.parent.mkdir(parents=True, exist_ok=True)
        text = src.read_text(encoding="utf-8")
        if kind == "pcs":
            # Accepting the world's text over a diverged copy discards the
            # campaign's newest edit -- keep it, so history can return it (#67).
            pcs.keep_before_overwrite(croot, actor_id, vid, text)
        atomic.write_text(dst, text)
    appearances_versions.set_base(cid, kind, actor_id, world_h)
    return True


def _advance(cid: str, refs: list[dict], *, copy: bool) -> None:
    with locks.campaign_lock(cid):
        severed, _ = overlay.require_identity_ledgers(cid)
        journal.require_readable(cid)
        # Migrate first, so accept only ever runs against the overlay layout (#270).
        # Accepting drops a copy and its manifest ref, and to a campaign the
        # migration has not reached a ref whose copy is gone is a record the user
        # deleted -- so an interruption between the two writes would have it
        # tombstone an inherited record. Ordering the writes the other way round
        # there is not enough on its own: the stranded copy is then a record that
        # reads correctly and never syncs again, and no rule can tell it from one
        # the campaign owns. Getting the migration out of the way first leaves
        # neither. Nothing is lost when it cannot run (the world dir is missing):
        # every `world_h` below is then None and this function does nothing at all.
        # Cheap besides -- a migrated campaign costs one campaign.md read -- and the
        # sync routes already call it, so this only closes the gap between theirs
        # and ours.
        campaigns_lifecycle.ensure_campaign_slim(cid)
        # `incoming` filters detached refs, but accept/reject take theirs from the
        # request body -- a stale one submitted after the slug was recreated would
        # dematerialize the very copy detaching preserved (Codex review).
        gone = severed
        refs = [r for r in refs if not overlay.record_is_marked(cid, r["kind"], r["id"], gone)]
        # Pinned refs are dropped for the same reason detached ones are: `incoming`
        # no longer offers them, but accept/reject take their refs from the request
        # body, and a stale submission must not advance a base hash the pin exists
        # to hold still (#71). Unpinning restores the offer untouched.
        pins = _pins.pinned_refs(cid)
        refs = [r for r in refs if not overlay.record_is_marked(cid, r["kind"], r["id"], pins)]
        wroot = campaigns_read.world_root_of(cid)
        croot = campaigns_paths.campaign_root(cid)
        # A requested World alias must not advance/delete another owned
        # original. Validate the whole batch before its first effect (spec07).
        for ref in refs:
            overlay.require_world_identity_scope(wroot, ref["kind"], ref["id"], (cid,))
        manifest = campaigns_paths.read_manifest(cid)
        manifest_changed = False  # loc/lore manifest write
        touched = False           # any ref advanced → bump campaign.updated
        effect_attempted = False
        try:
            for ref in refs:
                kind, eid = ref["kind"], ref["id"]
                if kind == "plotmap":
                    world_h = greetings.plotmap_hash(wroot) if wroot.exists() else None
                    pending = ("plotmap" in manifest and world_h is not None
                               and manifest["plotmap"] != world_h)
                    if not pending:
                        continue
                    if copy:   # take world: drop our copy, revert to inherited
                        effect_attempted = True
                        (croot / "plotmap.json").unlink(missing_ok=True)
                        manifest.pop("plotmap", None)
                    else:
                        manifest["plotmap"] = world_h
                    manifest_changed = touched = True
                    continue
                if kind in appearances_paths.ACTOR_KINDS:
                    if appearances_paths.record(cid).get(_read_shapes._ref_str(kind, eid)) is not None:
                        if _advance_actor(cid, kind, eid, copy=copy):   # locked flow: unchanged
                            touched = True
                        continue
                    world_h = _read_shapes._dir_hash(wroot, kind, eid) if wroot.exists() else None
                    if world_h is None or manifest.get(_read_shapes._ref_str(kind, eid)) == world_h:
                        continue
                    if copy:
                        effect_attempted = True
                        overlay.dematerialize_actor(cid, kind, eid)
                        manifest.pop(_read_shapes._ref_str(kind, eid), None)
                    else:
                        manifest[_read_shapes._ref_str(kind, eid)] = world_h
                    manifest_changed = touched = True
                    continue
                world_h = entities.entity_hash(wroot, kind, eid) if wroot.exists() else None
                if world_h is None or manifest.get(_read_shapes._ref_str(kind, eid)) == world_h:
                    continue
                if copy:
                    effect_attempted = True
                    overlay.dematerialize(cid, _read_shapes._ref_str(kind, eid))
                    manifest.pop(_read_shapes._ref_str(kind, eid), None)
                else:
                    manifest[_read_shapes._ref_str(kind, eid)] = world_h
                manifest_changed = touched = True
            if manifest_changed:
                effect_attempted = True
                campaigns_paths.write_manifest(cid, manifest)
            if touched:
                campaigns_read.touch(cid)
        except Exception:
            if effect_attempted:
                # A take-world unlink may have returned successfully before the
                # final manifest writer failed. Stamp after that last possible
                # effect, under the same hold as retirement/removal (spec 07).
                revision.bump(cid)
            raise



def accept(cid: str, refs: list[dict]) -> None:
    _advance(cid, refs, copy=True)


def reject(cid: str, refs: list[dict]) -> None:
    _advance(cid, refs, copy=False)
