"""Image writes, conditional links, per-slot copies, deletion and journalled avatar promotion."""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path

from .. import atomic, image_refs, image_store
from ..paths import safe_id
from . import (
    _contracts,
    _legacy_rescue,
    _locking,
    _lookup,
    _metadata,
    _placements,
    _recovery,
    _version_views,
)


def put_in(d: Path, name: str, data: bytes, ext: str, *,
           supported_only: bool = False, source_url: str | None = None) -> str:
    """Store `data` and place it as `name` in `d`, dropping the legacy siblings.

    Returns the extension of the blob actually kept, which is what the bytes
    sniff as (or `ext`, for bytes that sniff as nothing). `ext` is still
    validated against the allowlist first, so an unsupported one is the same
    `ValueError` it always was.
    """
    if not _contracts._safe_name(name):
        raise ValueError("unsafe image id")
    if not _contracts.norm_ext(ext):
        raise ValueError("unsupported image type")
    # Outside the name lock: ingest takes the store's own locks, and the
    # decode is the slow part of a write.
    obj = image_store.ingest(data, ext, source_url=source_url)
    with _locking._image_lock(d, name):
        _recovery._recover_before_write(d, name)
        _placements._place(d, name, obj.id, keep_focus=False, supported_only=supported_only)
    return obj.ext


def link_if_vacant(d: Path, name: str, image_id: str) -> bool:
    """`link_in`, but only when `name` holds no picture: a placement with an
    image, or a legacy file, is left as it is and the answer is False.

    The check and the placement run under the name's image lock, so an upload
    to the same name cannot land between them and be overwritten. An upload
    that comes after is an ordinary replacement, as `link_in` always allowed.
    `ValueError` for an unsafe name or an invalid id, as `link_in`.
    """
    if not _contracts._safe_name(name):
        raise ValueError("unsafe image id")
    with _locking._image_lock(d, name):
        # Recover first, so an interrupted promotion's slot is judged as it
        # will stand, not as the half-swap left it.
        _recovery._recover_before_write(d, name)
        if _lookup.slot_taken(d, name):
            return False
        _placements._place(d, name, image_id, keep_focus=False, supported_only=False)
        return True


def unlink_if_holds(d: Path, name: str, image_id: str) -> bool:
    """Remove placement `name` only while it still holds `image_id`.

    The check and the delete run under the name's image lock, so an upload
    that replaced the picture in the meantime is left as it is.
    """
    if not _contracts._safe_name(name):
        raise ValueError("unsafe image id")
    with _locking._image_lock(d, name):
        _recovery._recover_before_write(d, name)  # as every other writer: never act on a half-swap
        ref = image_refs.read(d, name)
        if ref is None or ref.image != image_id:
            return False
        return image_refs.delete(d, name)


def link_in(d: Path, name: str, image_id: str, *, keep_focus: bool = False) -> None:
    """Place an image already in the store as `name` in `d` -- a reference
    operation, no bytes moved -- and drop the name's legacy siblings.

    `ValueError` for an unsafe name or an invalid id. The object is not
    required to exist: a placement may arrive before its object does (sync),
    and readers treat that as unresolved until it does -- which is why the
    legacy siblings are dropped only once the placement resolves.
    """
    if not _contracts._safe_name(name):
        raise ValueError("unsafe image id")
    with _locking._image_lock(d, name):
        _recovery._recover_before_write(d, name)
        _placements._place(d, name, image_id, keep_focus=keep_focus, supported_only=False)


def delete_in(d: Path, name: str, *, supported_only: bool = False) -> None:
    """Remove logical image `name` in `d`: its placement and every legacy file.

    Deleting a placement never deletes an object or a blob: they stay until
    the collector (`image_gc`, run from Settings) finds them unreferenced with
    their files at least an hour old, and sends the picture to the recycle
    bin. Failures are swallowed here, as they
    always were -- callers that need the removal *confirmed*
    (`covers.delete_cover`) re-resolve afterwards. The one exception is an
    `OSError` from `_recover_before_write`, raised while an unfinished
    promotion journal names this slot: a retryable refusal, because removing
    the slot's placement then could leave a promotion's only copy unreferenced.
    """
    if not _contracts._safe_name(name) or not d.exists():
        return
    # Same lock as put_in: a delete racing an upload must not remove the file
    # the upload just published and leave the caller thinking it wrote one, nor
    # half-remove a set the upload is mid-way through replacing.
    with _locking._image_lock(d, name):
        _recovery._recover_before_write(d, name)
        with suppress(OSError):     # swallowed like the legacy files below
            image_refs.delete(d, name)
        for p in _lookup.siblings(d, name, supported_only):
            with suppress(OSError):
                p.unlink()


def copy_slots(src: Path, dst: Path, *, skip: Callable[[str], bool] = lambda n: False,
               overwrite: bool = False) -> None:
    """Copy the image SLOTS of version folder `src` into `dst`, one logical
    name at a time -- never file by file (spec section 6, "tree copies are per
    logical name").

    A slot is every name `names_in(src)` reports plus every image-less
    placement (an occurrence override). A slot `skip` names is never copied:
    that is how a caller applies tombstones, which key on the logical name.

    - ``overwrite=False`` copies a slot only into a name `dst` holds no image
      under -- neither an image-bearing placement nor a legacy file. An
      image-less placement in `dst` does not count as an image: the source's
      image is written into it and the DESTINATION's focus kept (it is the
      crop somebody chose for this occurrence).
    - ``overwrite=True`` (a promotion into a fresh library record) replaces
      the slot in `dst`. An image-less source placement replaces only the
      crop, never the destination's image: it carries no image to replace one
      with.

    A placement is copied as a placement (same image, same focus) and a legacy
    file as a file of the same name, through `atomic`; a legacy file is copied
    only when the source holds no image-bearing placement that resolves --
    one that does not resolve yet is backed by the file meanwhile, so both
    travel. The promotion journal is never copied, and nothing else in `src`
    either: its sidecars (`descriptions.json`, `focus.json`, ...) are the
    caller's, which copies them by its own rules (`is_slot_file` tells them
    apart).

    `src` is repaired first (`_recover_promotion`), so a half-finished swap is
    not copied as a duplicate or a hole; each slot is written under `dst`'s
    name lock, serialized with uploads, after `dst`'s own repair.
    """
    if not src.is_dir():
        return
    _recovery._recover_promotion(src)
    refs = image_refs.scan(src)
    names = _lookup.names_in(src)[0] | {n for n in refs if _contracts._addressable_name(n)}
    for name in sorted(names):
        if skip(name) or not _contracts._addressable_name(name):
            continue
        ref = refs.get(name)
        legacy = (_lookup.legacy_path(src, name, True)
                  if ref is None or ref.image is None or image_refs.resolve_ref(ref) is None
                  else None)
        if ref is None and legacy is None:
            continue   # vanished since the scan
        dst.mkdir(parents=True, exist_ok=True)
        with _locking._image_lock(dst, name):
            _recovery._recover_before_write(dst, name)
            _copy_slot(src, dst, name, ref, legacy, overwrite)


def _copy_slot(src: Path, dst: Path, name: str, ref: image_refs.Ref | None,
               legacy: Path | None, overwrite: bool) -> None:
    """One slot of `copy_slots`: `ref` is the source placement, `legacy` the
    source file to copy (None when a resolving placement makes it redundant).
    Caller holds `_image_lock(dst, name)`."""
    held = image_refs.read(dst, name)
    held_image = held is not None and held.image is not None
    held_files = _placements._snapshot_siblings(dst, name, False)
    if held_image or held_files:
        if not overwrite:
            return     # the destination's own image wins
        if ref is not None and ref.image is None and legacy is None:
            # An occurrence override carries a crop and no image, so over an
            # image it replaces the crop alone.
            image_refs.write(dst, name, held.image if held is not None else None,
                             focus=ref.focus)
            return
    # Without `overwrite`, any placement `dst` still holds here is image-less:
    # an override whose crop is kept under the image copied into it.
    if held is not None and not overwrite:
        focus = held.focus
    else:
        focus = ref.focus if ref is not None else None
    if legacy is not None:
        target = dst / legacy.name
        atomic.write_bytes(target, legacy.read_bytes())
        if overwrite:
            # Replaced, not merged: another extension left behind would
            # outrank the copy by mtime.
            _placements._drop_snapshotted([s for s in held_files if s[0] != target])
    if ref is not None and ref.image is not None:
        image_refs.write(dst, name, ref.image, focus=focus)
        if overwrite and legacy is None:
            _placements._drop_if_placed(dst, name, ref.image, held_files)
    elif held is not None or focus is not None:
        # The copied slot is a legacy file: `dst`'s placement keeps only the
        # crop (or goes, with none), so it can never shadow the file.
        image_refs.write(dst, name, None, focus=focus)


def write_focus(root: Path, cid: str, vid: str, focus: int, base: str = "characters") -> None:
    """Set the avatar crop.

    - an avatar placement: rewritten with the new focus, its image kept;
    - a legacy avatar file: `focus.json`, as before placements;
    - neither: an image-less placement, the occurrence override a campaign
      writes over an avatar it inherits (`put_campaign_avatar_focus`) -- not
      an image to any reader, so the world's art keeps showing through.
    """
    if not (safe_id(cid) and safe_id(vid)):
        raise ValueError("unsafe image id")
    d = _contracts._dir(root, cid, vid, base)
    value = max(0, min(100, int(focus)))
    d.mkdir(parents=True, exist_ok=True)
    with _locking._image_lock(d, _contracts.AVATAR):
        _recovery._recover_before_write(d, _contracts.AVATAR)
        ref = image_refs.read(d, _contracts.AVATAR)
        if ref is None and _lookup.siblings(d, _contracts.AVATAR, False):
            atomic.write_text(d / _contracts.FOCUS_FILE, json.dumps({_contracts.AVATAR: value}))
            return
        image_refs.write(d, _contracts.AVATAR, ref.image if ref is not None else None, focus=value)


def put_image(root: Path, cid: str, vid: str, name: str, data: bytes, ext: str,
              base: str = "characters", *, source_url: str | None = None) -> str:
    """Store `data` as image `name` of this version; returns the blob's ext.

    A new avatar picture drops the crop: `put_in` keeps a placement's focus
    only when it already held this very image, so re-uploading the same
    picture keeps the crop and anything else clears it. A legacy
    `focus.json` is dropped either way -- the placement owns the crop now."""
    if not (safe_id(cid) and safe_id(vid)):
        raise ValueError("unsafe image id")
    d = _contracts._dir(root, cid, vid, base)
    ext = put_in(d, name, data, ext, source_url=source_url)
    if name == _contracts.AVATAR:
        with _locking._image_lock(d, _contracts.AVATAR):
            _metadata._unlink_focus_file(d)
    return ext


def delete_image(root: Path, cid: str, vid: str, name: str, base: str = "characters") -> None:
    if not (safe_id(cid) and safe_id(vid) and _contracts._safe_name(name)):
        return
    d = _contracts._dir(root, cid, vid, base)
    # The description goes with the bytes, the way the crop already does below:
    # a re-upload under this name is different art and must inherit neither.
    #
    # Only once the bytes have ACTUALLY gone. `delete_in` swallows an unlink
    # failure by design (a scanner holding the file on Windows, a read-only
    # directory), and dropping the sentence anyway loses what somebody wrote
    # about a picture that is still sitting there (PR review).
    #
    # Unlink, confirmation and sidecar decision under ONE hold of this slot's
    # lock -- `delete_in` takes and releases it, and in that gap an upload can
    # publish replacement bytes, which reads here as "the delete failed" and
    # leaves the removed picture's sentence captioning the new one. Reentrant,
    # so `delete_in` taking it again inside costs nothing.
    #
    # The sidecar lock is taken with it, up front (name, then sidecar: the
    # order everything takes them in), and the crop is cleared inside the same
    # hold. Both locks can time out (`StoreBusy`), and one that did between the
    # unlink and the caption drop would strand the sentence for good: a re-run
    # finds no image to delete and never reaches the drop, and the next upload
    # under this name inherits it.
    with _locking._image_locks_held(d, name), _locking.sidecar_lock(d, _contracts.DESCRIPTIONS_FILE):
        _recovery._recover_promotion(d)
        delete_in(d, name)
        if _version_views.image_path(root, cid, vid, name, base) is None:
            _metadata.drop_sidecar_entry(d, _contracts.DESCRIPTIONS_FILE, name)
        if name == _contracts.AVATAR:
            _metadata.clear_focus(root, cid, vid, base)


def delete_version_images(root: Path, cid: str, vid: str, base: str = "characters") -> None:
    """Drop the whole per-version asset folder, sidecar and all.

    For the deletion of a *version*, not of an image: once the version file is
    gone nothing can address `assets/<vid>/` again -- no listing enumerates it
    (both list endpoints walk the version ids that exist) and, since the image
    routes started refusing an id that names no version (#360), no delete route
    can name it either. Leaving it behind is how a record deletion quietly
    manufactures the orphaned bytes that issue is about.

    Unlocked, like the whole-record `shutil.rmtree` in `characters.delete_character`
    and `pcs.delete_pc`: the version it belongs to is already gone, so an upload
    racing this is writing art for a version that no longer exists either way.

    A linked folder loses the link, never what it points at. `rmtree` refuses
    both a symlink and (since bpo-37834) a Windows junction rather than
    following it, and this runs *after* the record file is already unlinked --
    so letting that `OSError` out is a 500 with the version already gone. A
    store on a synced folder is exactly where someone points an asset directory
    at an art library living somewhere else.
    """
    if not (safe_id(cid) and safe_id(vid)):
        return
    d = _contracts._dir(root, cid, vid, base)
    if d.is_symlink() or getattr(d, "is_junction", bool)():   # is_junction: 3.12+
        try:
            d.unlink()          # POSIX: removes either kind of link
        except OSError:
            d.rmdir()           # Windows: a directory link goes the way a directory does
    elif d.is_dir():
        shutil.rmtree(d)


def promote_image(root: Path, cid: str, vid: str, name: str, base: str = "characters") -> None:
    """Make <name> the avatar; the old avatar takes <name>'s slot (swap, nothing lost).

    A swap of two PLACEMENTS, not of bytes (spec section 8): no blob is read or
    written. Both slots are adopted first (`adopt_legacy`), so a legacy file on
    either side becomes a placement before anything moves, and the swap itself
    is then a sequence of small placement writes -- which no filesystem makes
    atomic as a whole. So the sequence is journalled: the intent
    (``image-refs/.promote.json``, `image_refs.write_journal`) lands before the
    first write and is cleared after the last, and a read that finds one
    finishes the swap (`_recover_promotion`). A crash anywhere in between is
    rolled forward on the next listing or lookup, never left as a duplicate or
    a hole.

    With no avatar to swap back, the promoted image LEAVES its slot, matching
    the rename this replaced. Here the removal IS the operation:
    `overlay.promote_image` reads this slot's emptiness to decide whether to
    tombstone an inherited image, so a source that would not clear is an
    `OSError` rather than a move reported but not made.

    A description is a claim about particular pictures, so it travels with
    them: the journal carries both slots' descriptions as they were before
    anything moved, and the post-swap sidecar is written FROM it -- never by
    re-reading and swapping, which a roll-forward after the sidecar write would
    turn into a second swap. The crop goes, as with any new avatar picture.

    Both image locks and the sidecar lock are held across the whole swap, so an
    upload, a delete or a description save cannot interleave with it. Reads
    take no locks, so a concurrent reader can still see the swap half done;
    a reader that finds the journal and can take the locks finishes it first.

    `ImageNotYetAvailableError` (an `OSError`) when either side's image is placed
    but has not arrived yet, `FileNotFoundError` when `name` holds no image,
    `ValueError` when either side is an externally placed file of an
    extension never accepted -- all before anything is written
    (`_check_promotable`). `OSError` when an earlier promotion's journal could
    not be finished first (it is never overwritten), or when a write fails.
    """
    if name == _contracts.AVATAR:
        return
    if not (safe_id(cid) and safe_id(vid) and _contracts._safe_name(name)):
        raise FileNotFoundError(name)  # no logical image can live under such a name
    d = _contracts._dir(root, cid, vid, base)
    if not d.exists():
        raise FileNotFoundError(name)
    # Unlocked repairs first, each taking its own locks: a journal an earlier
    # promotion left is finished before this one overwrites it, and a pre-#253
    # stray is given a slot before the slots are read.
    _recovery._recover_promotion(d)
    _legacy_rescue._heal_stranded_promotion(d)
    with _locking._image_locks_held(d, name, _contracts.AVATAR), _locking.sidecar_lock(d, _contracts.DESCRIPTIONS_FILE):
        # Again under the locks: a promotion that failed between the repair
        # above and this hold can have left a journal. One still here after
        # that (its slot busy, or a write that keeps failing) is refused rather
        # than overwritten -- mid-swap, it is the only record of the picture it
        # moved out of the avatar slot.
        _recovery._recover_promotion(d)
        if image_refs.read_journal(d) is not None:
            raise OSError("an earlier promotion is still unfinished; retry")
        _check_promotable(d, name)
        name_id = _placements.adopt_legacy(d, name)
        if name_id is None:
            raise FileNotFoundError(name)
        avatar_id = _placements.adopt_legacy(d, _contracts.AVATAR)
        described = _metadata._read_sidecar(d, _contracts.DESCRIPTIONS_FILE)
        journal = {
            "name": name,
            "pre": {_contracts.AVATAR: avatar_id, name: name_id},
            "post": {_contracts.AVATAR: name_id, name: avatar_id},
            "desc": {_contracts.AVATAR: described.get(_contracts.AVATAR), name: described.get(name)},
        }
        image_refs.write_journal(d, journal)
        _recovery._finish_promotion(d, name, journal)


def _check_promotable(d: Path, name: str) -> None:
    """Everything that refuses a promotion, checked before anything is
    written -- adoption included -- since finding it halfway through would
    leave a half-swap. Caller holds both image locks.

    - `ImageNotYetAvailableError` (an `OSError`): either side is an image-bearing
      placement whose object or blob has not arrived (mid-sync, or a `link_in`
      ahead of its object) -- with or without a legacy file beside it. The
      swap would move an id no reader can show yet, and the legacy file
      `_drop_if_placed` kept beside it would keep a cleared source slot
      occupied -- a journal that could never finish.
    - `FileNotFoundError`: `name` holds no image at all.
    - `ValueError`: either side is an externally placed file of an extension
      never accepted.

    Asked in that order for `name` -- not arrived, then absent, then the
    extension -- and the avatar's arrival last, so a slot that does not
    exist is "not found" and an unsupported file a bad request whatever state
    the avatar is in.
    """
    _recovery.check_arrived(d, name)
    src = _lookup.path_in(d, name)
    if src is None:
        raise FileNotFoundError(name)
    for p in (src, _lookup.path_in(d, _contracts.AVATAR)):
        if p is not None and not _contracts.norm_ext(p.suffix):
            raise ValueError(f"unsupported image type: {p.name}")
    _recovery.check_arrived(d, _contracts.AVATAR)
