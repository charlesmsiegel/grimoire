"""Identity-ledger removal and movement of proven captured sources under existing locks."""

from __future__ import annotations

import json

from .. import atomic, commits, group_proposals, group_refs, locks
from ..campaigns import paths as campaigns_paths
from . import _identity, _ledgers, _record_paths


def _undetach(cid: str, ref: str, *, source_refs: _identity.RecordIdentityRefs | None = None) -> None:
    """Drop a detachment when the record it describes goes.

    Detachment is a statement about a *record*, not about an id: it says this
    campaign's copy is its own. Delete that copy and the statement has no
    subject -- but it would keep suppressing the per-file resolvers, so a later
    world record of the same slug would list in the campaign with its images
    and sidecars hidden (Codex review).

    UNDER `campaign_lock`, for `add_detached`'s reason: this rewrites the whole
    ledger to remove one ref, so an unserialized run of it drops whatever
    markers landed since it read -- including an actor marker it has nothing to
    do with, since entity and greeting deletes come through here too. Locking
    only the writers that ADD would leave the file racing the ones that
    subtract."""
    with locks.campaign_lock(cid):
        severed, gone = _ledgers.require_identity_ledgers(cid)
        refs = _identity.capture_record_refs(cid, ref) if source_refs is None else source_refs
        keep = severed - refs.owned
        if keep != severed:
            atomic.write_text(_ledgers._detached_path(cid), json.dumps(sorted(keep), indent=2) + "\n")
        # Images deleted while detached tombstoned SLOTS of a record that is now
        # gone. Reattaching without clearing them inherits the world's replacement
        # with its avatar still hidden (Codex review) -- the same reasoning as the
        # sweep's own clearing, at the other end of a detachment.
        _ledgers._drop_deleted(cid, {r for r in gone
                            if any(r.startswith(f"assets/{source}/") for source in refs.world)})


def undetach(cid: str, ref: str) -> None:
    """Drop a detachment because the record has a world ancestor again.

    The inverse of what `forget_world_record` does on a world-side delete, and
    the only thing that legitimately reverses it: `detached` means "mine, and
    whatever holds that id in the world is a stranger", which stops being true
    the moment this campaign's own copy becomes that world record (#52)."""
    _undetach(cid, ref)


def _drop_manifest_ref(cid: str, ref: str, *, source_refs: _identity.RecordIdentityRefs | None = None) -> None:
    manifest = campaigns_paths.read_manifest(cid)
    refs = {ref} if source_refs is None else source_refs.owned
    keep = {r: base for r, base in manifest.items() if r not in refs}
    if keep != manifest:
        campaigns_paths.write_manifest(cid, keep)


def _moved_identity_marker(ref: str, sources: frozenset[str], destination: str) -> str:
    """A whole-record or asset-slot marker follows its proven moved original."""
    if ref in sources:
        return destination
    parts = ref.split("/", 3)
    if len(parts) == 4 and parts[0] == "assets" and "/".join(parts[1:3]) in sources:
        return f"assets/{destination}/{parts[3]}"
    return ref


def repoint_record(cid: str, old_ref: str, new_ref: str, *, keep_base: bool,
                   source_refs: _identity.RecordIdentityRefs | None = None) -> None:
    """Follow a reclassified record through the three ledgers that say what this
    campaign's copy *is* relative to its world: `sync.md`, `deleted.json` and
    `detached.json` (#119).

    They stay here rather than joining `record_refs`' fan-out because each is a
    statement about inheritance, and only the caller knows whether the world
    moved too -- which is exactly what `keep_base` says. **True** (a world-side
    reclassify) carries the sync base over to the new key: the world file's
    bytes did not change, only its kind directory, so the hash the base records
    still describes it and the campaign's copy is still in sync or still
    diverged, unchanged. **False** (a campaign-side one) drops it: the world
    record is still filed under the old kind, so there is no longer a world
    record at this ref for a base to be about, and a base left standing would
    have `sync.incoming` compare this record against whatever claims the old
    slug next.

    Both tombstone shapes move: the whole-record one, and the per-asset
    `assets/<kind>/<id>/<version>/<name>` slots, which hide by slot and would
    otherwise blank an image of the record under its new kind while un-hiding
    the one it was hidden under. A detachment moves for the same reason -- it is
    a statement about a record, and the record is still here.
    """
    with locks.campaign_lock(cid):
        # Repoint is also a ledger writer: validate its input even when no
        # current marker happens to name it (spec 07 review).
        if not all(_ledgers._valid_ledger_ref(r, visibility=False) for r in (old_ref, new_ref)):
            raise group_refs.GroupIdentityConflict("invalid record reference for repoint")
        if old_ref == new_ref:
            return
        severed, gone = _ledgers.require_identity_ledgers(cid)
        manifest = campaigns_paths.read_manifest(cid)
        # These aliases were proven BEFORE movement, under this same hold.
        # The vacated paths can no longer establish which keys belonged to it.
        captured = (_identity.RecordIdentityRefs(owned=frozenset({old_ref}), world=frozenset({old_ref}))
                    if source_refs is None else source_refs)
        refs = captured.owned
        if not all(_ledgers._valid_ledger_ref(r, visibility=False) for r in refs | captured.world):
            raise group_refs.GroupIdentityConflict("invalid captured record reference for repoint")
        bases = {r: manifest[r] for r in refs if r in manifest}
        if bases:
            base = bases.get(old_ref, bases[min(bases)])
            manifest = {r: value for r, value in manifest.items() if r not in refs}
            if keep_base:
                manifest[new_ref] = base
            campaigns_paths.write_manifest(cid, manifest)
        kept = {_moved_identity_marker(r, captured.world, new_ref) for r in gone}
        if kept != gone:
            atomic.write_text(_ledgers._deleted_path(cid), json.dumps(sorted(kept), indent=2) + "\n")
        moved = severed & refs
        if moved:
            atomic.write_text(_ledgers._detached_path(cid),
                              json.dumps(sorted((severed - moved) | {new_ref}), indent=2) + "\n")


def _retire_owned_reviews(cid: str, kind: str, rid: str) -> None:
    croot = _record_paths.croot_of(cid)
    group_proposals.require_readable(croot)
    commits.require_readable(cid)
    if kind == "groups":
        group_proposals.retire(cid, croot, group_proposals.matching_refs(croot, croot, rid))
    commits.retire_membership_targets(cid, kind, rid, root=croot)
