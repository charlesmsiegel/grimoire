"""Base reservation before publication, atomic copy-down and group-copy consent."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path

from .. import (
    assets,
    atomic,
    characters,
    commits,
    entities,
    group_proposals,
    image_refs,
    locks,
    pcs,
    revision,
)
from .. import paths as library_paths
from ..campaigns import paths as campaigns_paths
from ..campaigns import read as campaigns_read
from ..paths import safe_id
from . import _identity, _ledgers, _record_paths


@contextmanager
def _recorded_base(cid: str, ref: str, base: str, commit: Path):
    """Record `ref`'s sync base in sync.md, then make the copy it describes.
    `commit` is the copy's last write — the file whose existence *is* the
    materialization.

    Base first, copy second, because the two writes cannot be made one and a
    crash between them has to land on the harmless side (#247):

    - *base, no copy* — the record is still un-materialized, so it reads
      through to the world live and sync skips it (`mine_h is None`). The next
      materialization overwrites the base. Self-healing.
    - *copy, no base* — permanent silent divergence. Every ref the sync engine
      considers comes from the manifest, so that record never sees a world
      edit again, and nothing ever notices.

    The copy's own last write is therefore the commit point: for an actor that
    is character.md / pc.md, which is what `actor_root` keys on — version files
    landing without it leave the actor inherited, exactly as if the copy had
    not begun.

    All of which describes a campaign the slim migration has already reached.
    Before it, sync.md is that migration's inventory of the pre-overlay full
    copy, and *base, no copy* is the one thing it cannot survive: it reads as a
    record the user deleted and tombstones it (#270).

    Neither ordering is safe there, so the base is RESERVED before the copy and
    recorded after it. The reservation is the empty hash — the value the fork
    itself wrote for anything it did not copy — and `ensure_campaign_slim`
    already refuses to attribute one to a user, because nothing was ever copied
    for the user to have deleted. That makes both residues survivable:

    - *reserved, no copy* — the migration drops the ref, leaving the record
      inherited. Same self-healing shape as *base, no copy* past the migration.
    - *reserved, copy* — the migration keeps the record and its reservation, so
      it stays visible to sync, which offers it as a conflict to resolve. Noisy,
      and the point: swapping the writes instead would leave a copy no ref
      names, and once the world moved on the sweep could no longer recognize it
      as residue — permanent silent divergence, the very thing above (Codex
      review).

    An exception, unlike a crash, can unwind, so undo the base then. It restores
    what it displaced rather than dropping the ref: an earlier interrupted
    attempt may have left a base there.

    But undo only while `commit` is absent. An asynchronous exception —
    KeyboardInterrupt, a worker shutdown — can arrive after the commit write
    returned, and an unconditional undo would then strip the base off a copy
    that did land: the very state this ordering exists to prevent. A concurrent
    materialization of the same ref that finished while ours failed is the same
    case (Codex review).
    """
    reserving = campaigns_read.slim_pending(cid)
    manifest = campaigns_paths.read_manifest(cid)
    previous = manifest.get(ref)
    manifest[ref] = _record_paths.RESERVED_BASE if reserving else base
    campaigns_paths.write_manifest(cid, manifest)
    try:
        yield
    except BaseException:
        if not commit.exists():
            manifest = campaigns_paths.read_manifest(cid)
            if previous is None:
                manifest.pop(ref, None)
            else:
                manifest[ref] = previous
            try:
                campaigns_paths.write_manifest(cid, manifest)
            except Exception:  # noqa: BLE001 - the copy's failure is the one worth raising
                pass   # the copy's failure is the one worth raising
        elif reserving:
            # The copy landed and the exception arrived after it, so the
            # reservation now describes a real copy and has to be redeemed --
            # the same reasoning as leaving the base alone above.
            try:
                _put_base(cid, ref, base)
            except Exception:  # noqa: BLE001 - the copy's failure is the one worth raising
                pass
        raise
    if reserving:
        _put_base(cid, ref, base)


@contextmanager
def recorded_base(cid: str, ref: str, base: str, commit: Path):
    """`_recorded_base` for a copy that lands somewhere other than this campaign.

    Same contract, same crash ordering, one difference in what `commit` names:
    for a materialization it is the campaign file being written, and for a
    *promotion* (`sync.promote`) it is the world file. The reasoning survives
    the swap unchanged, because it only ever asks "did the copy this base
    describes actually land?" -- and the answer is read off `commit` either way.

    Public because the promoting caller lives in `store/sync.py`: base-hash
    discipline is the one part of moving a record that must not be re-derived
    at a second call site, which is the whole argument #52 makes for doing the
    move server-side at all.
    """
    with _recorded_base(cid, ref, base, commit):
        yield


def record_text(cid: str, kind: str, eid: str) -> str | None:
    """The bytes of the campaign's OWN copy of a flat record, or None when it
    has none and reads the world's. Deliberately unresolved: a promotion copies
    what this campaign holds, and inheriting content back up to the world it
    came from is the one thing it must never do."""
    if not safe_id(eid):
        return None
    p = _record_paths._flat_path(_record_paths.croot_of(cid), kind, eid)
    return p.read_text(encoding="utf-8") if p.exists() else None


def actor_snapshot(cid: str, kind: str, aid: str):
    """`characters.snapshot` / `pcs.snapshot` of the campaign's own actor dir,
    or None when the actor is still inherited. Unresolved for the same reason
    as `record_text`."""
    if not safe_id(aid):
        return None
    snap = characters.snapshot if kind == "characters" else pcs.snapshot
    return snap(_record_paths.croot_of(cid), aid)


def copy_record_dir_down(cid: str, kind: str, rid: str) -> None:
    """Give this campaign its own copy of everything filed beside a world
    record — its `assets/`, and for a greeting its localized images.

    `_materialize_flat` deliberately does not do this: assets overlay per file,
    so a campaign that merely diverged on TEXT should keep reading the world's
    pictures rather than forking them. That reasoning ends the moment the world
    record is about to be deleted (`sync.demote`), because the files it points
    at go with it — leaving the campaigns holding the demoted record with its
    text and none of its art, permanently and with nothing to say so.

    Two rules, both the overlay's own, and both the reason this lives here
    rather than in the caller:

    - **What the campaign already has wins**, and is not overwritten. That is
      the whole overlay rule, and `image_root` checks the campaign's own image
      *before* anything else, so copying over one would replace a picture this
      campaign chose with the world's. Images are judged per logical NAME
      (`assets.copy_slots`), not per file: a campaign's legacy `gallery_1.png`
      holds that slot against the world's `gallery_1` placement, and a
      campaign's image-less placement -- the crop it set on an avatar it
      inherited -- holds no image, so the world's is written into it under the
      campaign's crop. Every other file wins by path, as before.
    - **A tombstoned asset stays gone.** `image_root` checks the campaign file
      first and the tombstone second, so a blind copy would hand back exactly
      the image the user deleted here — the deletion undone by an operation
      aimed at a different record entirely. The tombstone names the slot, so
      it hides a placement and a legacy file alike.

    The promotion journal (``image-refs/.promote.json``) is never copied: it
    describes a swap in progress in the WORLD's folder.
    """
    with locks.campaign_lock(cid):
        if kind not in _record_paths.INHERITED_KINDS or not safe_id(rid):
            return
        src = _record_paths._record_dir(_record_paths.wroot_of(cid), kind, rid)
        if not src.is_dir():
            return
        dst = _record_paths._record_dir(_record_paths.croot_of(cid), kind, rid)
        _, gone = _ledgers.require_identity_ledgers(cid)
        # The images, per logical NAME (`assets.copy_slots`): a slot is a
        # placement or a legacy file, and which of them the campaign already holds
        # under that name is what decides -- never whether one particular FILE of
        # the world's exists here.
        assets_dir = src / "assets"
        if assets_dir.is_dir():
            for src_v in sorted(p for p in assets_dir.iterdir() if p.is_dir()):
                _copy_slots_down(kind, rid, src_v, dst / "assets" / src_v.name, gone)
        for p in sorted(src.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(src)
            if _image_slot_file(rel) or _tombstoned_asset(kind, rid, rel, gone):
                continue
            target = dst / rel
            if target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            # Through the helper, not `shutil.copy2`, for the reason
            # `sheets/tally.py` gives at the identical world->campaign copy: a
            # partial copy must never appear under a real name. Here that name is
            # an image slot the overlay resolves campaign-first, so a truncated
            # file would not merely be broken -- it would SHADOW the world's intact
            # one for as long as it sat there.
            atomic.write_bytes(target, p.read_bytes())


def _copy_slots_down(kind: str, rid: str, src_v: Path, dst_v: Path, gone: set[str]) -> None:
    """`copy_record_dir_down` for one version folder: every slot the campaign
    has not tombstoned BY NAME."""
    vid = src_v.name

    def tombstoned(name: str) -> bool:
        return _record_paths.asset_ref(kind, rid, vid, name) in gone

    assets.copy_slots(src_v, dst_v, skip=tombstoned)


def _image_slot_file(rel: Path) -> bool:
    """Is `rel` (relative to a record dir) part of an image slot that
    `assets.copy_slots` copies -- a legacy image file directly in
    `assets/<vid>/`, or anything under its `image-refs/` (placements and the
    promotion journal, which is never copied)?"""
    parts = rel.parts
    if len(parts) < 3 or parts[0] != "assets":
        return False
    if len(parts) == 3:
        return assets.is_slot_file(rel)
    return parts[2] == image_refs.REFS_DIR


def _tombstoned_asset(kind: str, rid: str, rel: Path, gone: set[str]) -> bool:
    """Does a per-asset tombstone hide `rel`, a file directly in
    `assets/<vid>/` that is not an image slot (`_image_slot_file` answered
    first)? Such a file -- one of an extension never accepted, say -- is
    still named by its stem, and `asset_ref` keys the tombstone on (base, id,
    vid, name) without the extension, because deleting an image and uploading
    a different format of it is the same slot.

    Placements are not asked about here: everything under `image-refs/` is an
    image slot, which `assets.copy_slots` copies with the tombstone check of
    its own (`_copy_slots_down`)."""
    parts = rel.parts
    if parts[:1] != ("assets",) or len(parts) != 3:
        return False
    return _record_paths.asset_ref(kind, rid, parts[1], Path(parts[2]).stem) in gone


def _put_base(cid: str, ref: str, base: str) -> None:
    manifest = campaigns_paths.read_manifest(cid)
    manifest[ref] = base
    campaigns_paths.write_manifest(cid, manifest)


def require_group_materialization(cid: str, kind: str, eid: str, *,
                                  source_text: str | None = None) -> None:
    """Preflight a default no-consent copy, without publishing anything.

    Demotion uses this for every actual first copy before its first asset or
    record effect. Frozen source_text is also passed to the subsequent copy,
    so validation cannot accidentally approve different World bytes.
    """
    with locks.campaign_lock(cid):
        if _record_paths._flat_path(_record_paths.croot_of(cid), kind, eid).exists():
            return   # already owned: its dangling refs are authored
        severed, gone = _ledgers.require_identity_ledgers(cid)
        if _identity.record_is_marked(cid, kind, eid, gone):
            raise entities.EntityNotFound(f"{kind}/{eid}")
        wroot = _record_paths.wroot_of(cid)
        # Greetings share the flat overlay layout but have their own reader;
        # entities.require_entity deliberately excludes their kind (#119).
        if kind not in entities.SYNCED_KINDS:
            raise entities.UnknownKind(kind)
        src = _record_paths._flat_path(wroot, kind, eid)
        if not safe_id(eid) or not src.exists():
            raise entities.EntityNotFound(f"{kind}/{eid}")
        text = src.read_text(encoding="utf-8") if source_text is None else source_text
        _identity._require_group_copy(_record_paths.croot_of(cid), wroot, text, text, severed, frozenset())


def _materialize_flat(cid: str, kind: str, eid: str, *,
                      prepare: Callable[[str], str] | None = None,
                      source_text: str | None = None,
                      reviewed_slots: frozenset[tuple[str, str]] = frozenset()) -> bool:
    """Publish one final safe copy and its original World sync base.

    The campaign hold spans identity validation and publication, so an actor
    birth cannot claim a missing World slug between them (spec 07). Assets
    continue to overlay per file. Already-owned records grant no new binding.
    """
    # Prepared publication bypasses the root CRUD writer, so it must retain
    # that writer's kind/id boundary BEFORE any native path probe (spec 07).
    if kind not in entities.SYNCED_KINDS:
        raise entities.UnknownKind(kind)
    if not safe_id(eid):
        raise entities.EntityNotFound(f"{kind}/{eid}")
    with locks.campaign_lock(cid):
        croot = _record_paths.croot_of(cid)
        if _record_paths._flat_path(croot, kind, eid).exists():
            return True
        severed, gone = _ledgers.require_identity_ledgers(cid)
        wroot = _record_paths.wroot_of(cid)
        src = _record_paths._flat_path(wroot, kind, eid)
        if not src.exists() or _identity.record_is_marked(cid, kind, eid, gone):
            return False
        copy_refs: set[str] = set()
        source_identity = owned_identity = None
        if kind == "groups":
            group_proposals.require_readable(croot)
            commits.require_readable(cid)
            source_identity = group_proposals.GroupIdentity(store=str(library_paths.home().resolve()),
                campaign=str(croot.resolve()), layer="world", world=str(wroot.resolve()))
            owned_identity = group_proposals.GroupIdentity(store=source_identity.store,
                campaign=source_identity.campaign, layer="campaign", world=None)
            copy_refs = group_proposals.matching_refs(croot, wroot, eid, world=source_identity.world)
        text = src.read_text(encoding="utf-8") if source_text is None else source_text
        final = text if prepare is None else prepare(text)
        _identity._require_group_copy(croot, wroot, text, final, severed, reviewed_slots)
        with revision.stamp_on_failure(cid):
            dst = _record_paths._flat_path(croot, kind, eid)
            dst.parent.mkdir(parents=True, exist_ok=True)
            # Hash the ORIGINAL selected bytes, not the prepared edit or a second
            # World read. Otherwise the edit could look like its own common base,
            # or a World race could hide an update (the earlier Codex review).
            with _recorded_base(cid, _record_paths.flat_ref(kind, eid), entities.content_hash(text), dst):
                atomic.write_text(dst, final)
            if source_identity is not None and owned_identity is not None:
                group_proposals.retained_copy(cid, croot, copy_refs, source_identity, owned_identity)
                commits.retain_membership_copy(cid, eid, world_root=wroot, receipt={
                    "source": group_proposals.identity_data(source_identity),
                    "owned": group_proposals.identity_data(owned_identity)})
            return True


def materialize_entity(cid: str, kind: str, eid: str) -> None:
    if not _materialize_flat(cid, kind, eid):
        raise entities.EntityNotFound(f"{kind}/{eid}")
