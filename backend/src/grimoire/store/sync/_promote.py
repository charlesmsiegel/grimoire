"""sync: promote responsibilities."""

from __future__ import annotations

import shutil
from contextlib import suppress
from pathlib import Path

from .. import actor_names, atomic, entities, group_refs, locks, overlay
from .. import paths as _store_paths
from ..appearances import paths as appearances_paths
from ..campaigns import lifecycle as campaigns_lifecycle
from ..campaigns import paths as campaigns_paths
from ..campaigns import read as campaigns_read
from . import _move_policy, _publication, _read_shapes

# ---- promote --------------------------------------------------------------

def promote(cid: str, kind: str, eid: str) -> None:
    """Copy a campaign-local record up into the campaign's world, and record the
    sync base that makes the two one record from here on.

    The precondition is that **the world holds no record under that id** —
    deliberately a statement about the world rather than about the manifest
    ref, so the residue of a crashed promote (a base whose world record never
    landed) retries cleanly instead of being refused as already-materialized.
    See the design doc for why that residue is the one chosen.
    """
    campaigns_lifecycle.ensure_campaign_slim(cid)   # only ever run against the overlay layout
    _move_policy._safe_ref(kind, eid)
    # sync.md is rewritten WHOLE, so two library moves on the same campaign can
    # each read it and each republish it, and the later write drops the other's
    # ref -- leaving a promoted record no manifest names, which no world edit
    # ever reaches again (Codex review). One lock, held across the read and the
    # write, and the same one demote takes.
    with locks.campaign_lock(cid):
        # Resolve the destination with the source under this same hold. A
        # recreated/rebound campaign cannot publish into its former World
        # using a root captured while waiting for this lock (spec 07 review).
        wroot = _move_policy._require_world(campaigns_read.world_root_of(cid))
        overlay.require_identity_ledgers(cid)
        if kind in appearances_paths.ACTOR_KINDS:
            _promote_actor(cid, kind, eid, wroot)
        else:
            _move_policy._flat_kind(kind)
            _promote_flat(cid, kind, eid, wroot)
    _publication._touch_world(wroot)


def _promote_flat(cid: str, kind: str, eid: str, wroot: Path) -> None:
    # Publish the owned source's actual identity, as the actor path does.
    # With no owned source, preserve the existing World-only conflict order.
    with suppress(FileNotFoundError, NotADirectoryError):
        eid = _move_policy._world_file(campaigns_paths.campaign_root(cid), kind, eid).resolve(strict=True).stem
    # Collision before existence, on purpose. A record the campaign merely
    # inherits has no campaign-side file either, and answering *that* with "not
    # found" would be a lie about a record the user is looking at: it exists,
    # it is simply already library content. The 409 says so.
    if _move_policy._world_holds_flat(wroot, kind, eid):
        raise _move_policy.PromoteConflictError(f"the library already has {kind}/{eid}")
    text = overlay.record_text(cid, kind, eid)
    if text is None:
        raise entities.EntityNotFound(_read_shapes._ref_str(kind, eid))
    _move_policy._require_world_members(cid, wroot, text, kind, eid)
    if kind == "greetings":
        _move_policy._require_world_character(cid, wroot, text, eid)
    ref = _read_shapes._ref_str(kind, eid)
    dst = _move_policy._world_file(wroot, kind, eid)
    dst.parent.mkdir(parents=True, exist_ok=True)
    # Detaching comes off before the copy and goes back on if the copy fails --
    # see `_reattaching_on_failure` for both halves of why (#225, #247).
    # The base is hashed from the bytes we are writing, never re-read from the
    # destination: it has to describe the content that actually moved, even if
    # another writer touches that path in between (the argument
    # `_materialize_flat` makes in the opposite direction).
    with (_publication._reattaching_on_failure(cid, ref),
          overlay.recorded_base(cid, ref, entities.content_hash(text), dst)):
        atomic.write_text(dst, text)
    # Assets only. The rest of a record directory (a group's state.md) is
    # campaign-local state that has no business in the library.
    _publication._copy_extras("images", lambda: _publication._copy_tree(
        overlay.record_dir(cid, kind, eid) / "assets", wroot / kind / eid / "assets"))


def promoted_intact(cid: str, kind: str, eid: str) -> bool:
    """Did a flat-record `promote` of this ref land in full?

    What a caller that journalled a promote as *attempted* and never saw it
    return asks before trying again (the chronicle commit's publish step, spec
    §9.4) -- asking `promote` instead would refuse the commit's own record as a
    collision. True only when the campaign holds a sync base for the ref, the
    world file exists, and that file's bytes hash to the base.

    The WORLD file is what is compared, never the campaign copy. `promote`
    records the base first and writes the world file second, both from the same
    bytes, so the pair agreeing is the one state only a completed promote of
    this record leaves behind. The campaign copy proves nothing either way: it
    is free to move after a publish, and it matched the base before the world
    file was ever written. A base beside a world file that hashes to something
    else is the residue of a promote that never landed plus a record somebody
    else put under the same id -- and that one `promote`'s own precondition
    refuses, which is the answer it should get. It is also what a promote that
    DID land looks like once the world record has been edited since; the two
    cannot be told apart from here, so both read as not published, which errs
    toward reporting a publish that happened rather than claiming one that did
    not. The same goes for the pre-slim ``""`` reservation `_recorded_base`
    writes before the real base: a crash between the two reads as not intact,
    and `promote` then refuses the world file it finds -- reported, never
    overwritten.
    """
    if kind not in entities.SYNCED_KINDS or not _store_paths.safe_id(eid):
        return False
    base = campaigns_paths.read_manifest(cid).get(_read_shapes._ref_str(kind, eid))
    if not isinstance(base, str) or not base:
        return False             # "" is the pre-slim reservation, not a base
    try:
        text = _move_policy._world_file(campaigns_read.world_root_of(cid), kind, eid).read_text(
            encoding="utf-8")
    except (OSError, ValueError):  # absent, unreadable, or not text this store wrote
        return False
    return entities.content_hash(text) == base


def _promote_actor(cid: str, kind: str, aid: str, wroot: Path) -> None:
    if not _store_paths.safe_id(aid):
        raise _move_policy._actor_missing(kind, aid)
    # Only the owned source resolves this id, under promote's hold. Its
    # spelling also keys the appearance lock and every sync leg (spec 07).
    # Without an owned path, retain conflict-first behavior for World-only
    # actors; the snapshot below diagnoses an absent source otherwise.
    with suppress(FileNotFoundError, NotADirectoryError):
        aid = overlay.record_dir(cid, kind, aid).resolve(strict=True).name
    if _move_policy._world_holds_actor(wroot, kind, aid):     # see `_promote_flat` on the order
        raise _move_policy.PromoteConflictError(f"the library already has {kind}/{aid}")
    taken = overlay.actor_snapshot(cid, kind, aid)
    if taken is None:
        raise _move_policy._actor_missing(kind, aid)
    # Promotion changes what every campaign of this world can see. Validate
    # every name in the promoted record before the first world-side write.
    detail = (overlay.read_character(cid, aid) if kind == "characters"
              else overlay.read_pc(cid, aid))
    names = [detail["meta"]["name"]]
    names.extend(actor_names.card_name(v["card"]) if kind == "characters"
                 else actor_names.persona_name(v["persona"])
                 for v in detail["versions"])
    world_id = campaigns_read.read_campaign(cid)["meta"]["world"]
    for name in names:
        actor_names.require_unique(name, scope="world", scope_id=world_id,
                                   actor_ref=f"{kind}:{aid}", ignore_campaign=cid)
    # A lock stored under another spelling cannot safely be rekeyed here:
    # historical role refs retain it, and a different World namespace may map
    # that spelling to another actor. Refuse before publication (spec 07).
    croot = campaigns_paths.campaign_root(cid)
    ref = _read_shapes._ref_str(kind, aid)
    for locked_ref in appearances_paths.record(cid):
        if locked_ref != ref and overlay.record_is_marked(
                cid, kind, aid, {locked_ref}, roots=(croot, croot)):
            raise group_refs.GroupIdentityConflict(
                "This actor's saved version lock uses an alias; resolve that alias before promoting it.")
    base, files = taken
    meta_name = _move_policy._actor_meta_name(kind)
    ref = _read_shapes._ref_str(kind, aid)
    dst = wroot / kind / aid
    dst.mkdir(parents=True, exist_ok=True)
    # No meta means no actor, so EVERYTHING here is residue -- from an
    # interrupted attempt, or from a delete that could not finish removing the
    # directory. The whole tree goes, not just the version files: `assets/`,
    # tagline.md and voice_anchor.md are only ever overwritten where this
    # campaign supplies one, so a slot it leaves empty would keep the dead
    # actor's face and voice and hand them to the promoted one -- and through
    # the overlay, to every campaign of this world (#225, Codex review).
    shutil.rmtree(dst, ignore_errors=True)
    dst.mkdir(parents=True, exist_ok=True)
    # The meta file is the commit point every reader keys an actor on, so it is
    # written last and is what `recorded_base` watches: version files landing
    # without it leave no actor at all — the harmless residue.
    with (_publication._reattaching_on_failure(cid, ref),
          overlay.recorded_base(cid, ref, base, dst / meta_name)):
        for name, text in files:
            if name != meta_name:
                atomic.write_text(dst / name, text)
        atomic.write_text(dst / meta_name, dict(files)[meta_name])
    _publication._advance_appearance_base(cid, kind, aid, wroot)
    src_dir = overlay.record_dir(cid, kind, aid)
    _publication._copy_extras("sidecars", lambda: _publication._copy_sidecars(src_dir, dst))
    _publication._copy_extras("images", lambda: _publication._copy_tree(src_dir / "assets", dst / "assets"))
