"""sync: publication responsibilities."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path

from .. import assets, atomic, image_refs, overlay
from ..appearances import paths as appearances_paths
from ..appearances import versions as appearances_versions
from ..campaigns import paths as campaigns_paths
from ..worlds import lifecycle as worlds_lifecycle
from ..worlds import paths as worlds_paths
from . import _read_shapes

log = logging.getLogger('grimoire.store.sync')


#: The sidecars a promoted actor carries. Both are world-level identity by the
#: overlay's own rule (they resolve per file, from the world), which is exactly
#: what makes them part of the record being promoted. Everything else in an
#: actor directory -- dossier.md, state.md, voice_drift.md -- is campaign-local
#: by definition and stays behind.
ACTOR_WORLD_SIDECARS: tuple[str, ...] = ("tagline.md", "voice_anchor.md")


@contextmanager
def _reattaching_on_failure(cid: str, ref: str):
    """Undetach `ref`, and put the detachment back if the promotion does not land.

    Detaching has to come off BEFORE the copy — while it is on, the sync engine
    skips the ref, so a crash between the two writes would leave a promoted
    record that never syncs again, silently.

    But an *exception* can unwind where a crash cannot, and leaving the
    detachment off after a failed promotion is the #225 bug through its own
    door: the campaign is attached to a world record that was never written, so
    whatever claims that slug next supplies this campaign's record with its
    updates, its images and its sidecars by coincidence of id (Codex review).

    Only on the way out through an exception. A kill still lands on the
    documented residue, which is the harmless one and self-heals on retry.
    """
    severed, _ = overlay.require_identity_ledgers(cid)
    was_detached = severed & overlay.capture_record_refs(cid, ref).owned
    try:
        # Inside the try, not before it. `undetach` is itself two writes -- the
        # marker, then the asset tombstones the detachment kept -- so a failure
        # in the second one used to escape this handler entirely, leaving the
        # campaign attached with no world record written (Codex review).
        overlay.undetach(cid, ref)
        yield
    except BaseException:
        if was_detached:
            try:
                for marker in sorted(was_detached):
                    overlay.add_detached(cid, marker)
            except Exception:  # noqa: BLE001 - the copy's failure is the one worth raising
                log.warning("could not restore the detachment on %s after a failed "
                            "promotion; a world record reusing that id would be "
                            "inherited by campaign %s", ref, cid)
        raise


def _copy_extras(what: str, copy) -> None:
    """The part of a promotion that runs AFTER the record has committed.

    A record's assets and world-level sidecars are filed beside it rather than
    in it, so they cannot be part of the single write the sync base describes.
    By the time they are copied the promotion is already complete and correct
    -- the world holds the record, the base describes it -- so a failure here
    must not be reported as a failed promote: the caller would retry, and the
    retry now refuses as a collision with the record it just made.

    Logged rather than swallowed, because a promoted record whose picture did
    not come with it is a thing the user can see and cannot otherwise explain.
    """
    try:
        copy()
    except (OSError, ValueError) as exc:
        # ValueError covers UnicodeDecodeError: a sidecar this store never wrote
        # (hand-placed, or written in another encoding) would otherwise escape a
        # handler that exists precisely so a committed promotion is not reported
        log.warning("promoted the record but could not copy its %s (%s) -- "
                    "the promotion stands; the files are still campaign-side", what, exc)


def _copy_tree(src: Path, dst: Path) -> None:
    """Copy a record's asset tree (`<record>/assets`) into a fresh library
    record, replacing what is there.

    The images go per logical NAME, one version folder at a time
    (`assets.copy_slots`, ``overwrite=True``): a placement is copied as a
    placement, so the library record names the very images the campaign does
    and no blob is written, and a legacy file as a file. The promotion journal
    never travels. Every other file is copied file by file and atomically --
    not `shutil.copytree`, for the reason `sheets/tally.py` gives at the
    identical copy in the other direction: a partial copy must never appear
    under a real name. The destination here is a LIVE world record's asset
    directory, which every campaign of that world reads through the overlay the
    instant a file lands, so a torn image is served rather than merely stored.
    """
    if not src.is_dir():
        return
    for src_v in sorted(p for p in src.iterdir() if p.is_dir()):
        assets.copy_slots(src_v, dst / src_v.name, overwrite=True)
    for p in sorted(src.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(src)
        if len(rel.parts) == 2 and assets.is_slot_file(rel):
            continue    # a legacy image file: `copy_slots` copied its slot
        if len(rel.parts) >= 3 and rel.parts[1] == image_refs.REFS_DIR:
            continue    # placements and the journal: likewise
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic.write_bytes(target, p.read_bytes())


def _put_base(cid: str, ref: str, base: str) -> None:
    manifest = campaigns_paths.read_manifest(cid)
    manifest[ref] = base
    campaigns_paths.write_manifest(cid, manifest)


def _touch_world(wroot: Path) -> None:
    """Stamp the world as edited, without letting the stamp fail the edit.

    A library edit really is a change to the world (#53 asks for this, and #55
    reads it), but the record it describes has already landed by the time we
    get here -- reporting a 500 for a sort key would undo nothing and lose the
    user's work.

    The id comes back out of the directory name because the campaign-scoped
    callers only ever hold a root: `campaigns.read.world_root_of` resolves a
    campaign.md reference straight to a path. `canonical_id` is the same
    normalization `world_root` applied on the way in, so the round trip holds
    for every id that named a directory in the first place."""
    try:
        worlds_lifecycle.touch(worlds_paths.canonical_id(wroot.name))
    except Exception as exc:   # noqa: BLE001 - a sort key never fails a write
        log.warning("could not stamp world %s as updated (%s)", wroot, exc)


def _advance_appearance_base(cid: str, kind: str, aid: str, wroot: Path) -> None:
    """Point a locked actor's appearance base at the world version just written.

    The flagship #60 case runs straight through this: an NPC who walks on
    mid-scene is created campaign-local and then CAST, and casting locks a
    version in appearances.json with an empty base -- there was no world actor
    to hash. `_actor_incoming` reads that record in preference to sync.md, so
    without this the promotion that just succeeded reports itself as a
    *conflict* to the very campaign that made it (Codex review).

    The manifest ref `recorded_base` wrote stays and is inert: the unpicked
    pass skips any ref that also appears in `locked`.

    Best-effort, and loudly so. The actor is already in the library by now, so
    raising would report a failed promotion for one that landed and send the
    retry into a collision. A stale base here is visible and recoverable --
    it surfaces as a conflict the user can reject, which advances it.
    """
    rec = appearances_paths.record(cid).get(_read_shapes._ref_str(kind, aid))
    if rec is None:
        return          # not locked: sync.md's whole-actor base is the only one
    try:
        world_h = appearances_versions.actor_hash(wroot, kind, aid, rec["version"])
        if world_h is not None:
            appearances_versions.set_base(cid, kind, aid, world_h)
    except (OSError, ValueError) as exc:
        log.warning("promoted %s/%s but could not advance its appearance base (%s) -- "
                    "the campaign will offer it as a conflict until that is rejected",
                    kind, aid, exc)


def _copy_sidecars(src_dir: Path, dst: Path) -> None:
    for sidecar in ACTOR_WORLD_SIDECARS:
        if (src_dir / sidecar).exists():
            atomic.write_text(dst / sidecar, (src_dir / sidecar).read_text(encoding="utf-8"))
