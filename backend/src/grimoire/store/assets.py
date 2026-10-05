"""Per-version image store: <base>/<cid>/assets/<vid>/<name>.<ext>.

**Placements.** Every write now lands in the content-addressed store
(`store.image_store`) and leaves a small placement record in the directory,
``<d>/image-refs/<name>.json`` (`store.image_refs`), instead of the bytes. The
legacy ``<name>.<ext>`` layout is still READ, as the fallback of every lookup
(spec section 6): a placement that resolves wins; one that does not (object or
blob not synced yet) falls through to a legacy file of that name when there is
one. Nothing here ever writes a legacy file again, and a write removes the
name's legacy siblings once its placement is published.

The default base is "characters"; entity kinds (locations, lore) pass base=kind
with vid="default" so records without versions get the same folder layout. The
avatar/primary image is the image named AVATAR. Other image kinds (gallery,
emotions, backgrounds, …) drop into the same per-version folder with no schema
change. Images are never hashed into the card, so character sync is untouched
by image edits.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
from collections.abc import Callable
from contextlib import ExitStack, contextmanager, suppress
from pathlib import Path

from . import atomic, image_hash, image_refs, image_store, statcache
from .paths import safe_id

AVATAR = "avatar"
FOCUS_FILE = "focus.json"
#: The per-image description sidecar (`store.image_descriptions`). Its NAME
#: lives here, with `FOCUS_FILE`, because this module owns the directories both
#: sit in and has to take an entry with a deleted image; its SEMANTICS -- what
#: an absent key means, what a write will accept -- live in the module named
#: after it. Spelling the string there instead would be one rule in two places.
DESCRIPTIONS_FILE = "descriptions.json"
_EXTS = {"png", "jpg", "jpeg", "gif", "webp"}
_PROMOTE_TMP = "promote-tmp"  # the temp name the pre-#253 three-rename swap used


def _addressable_name(name: str) -> bool:
    """Can this name be used as an image id at all?

    Reject "." (ambiguous with ext) and glob metacharacters (the cleanup/lookup
    globs name.*), on top of the shared id guard. Split out from `_safe_name`
    because listing and writing want different answers about `promote-tmp`:
    it is not writable, but a stranded one must stay visible (see list_images).
    """
    return (safe_id(name) and "." not in name
            and not any(c in name for c in "*?[]"))


def _safe_name(name: str) -> bool:
    #
    # `promote-tmp` is reserved, not rejected on a whim: the old three-rename
    # swap renamed onto that exact path, so an image stored under it would have
    # been clobbered (POSIX) or would have broken every promotion (Windows) --
    # the name was never usable. Reserving it is what lets
    # `_heal_stranded_promotion` treat such a file as crash residue rather than
    # as somebody's image (PR review).
    #
    # Case-folded, because the reservation has to hold on the filesystem's terms
    # rather than Python's: on Windows and macOS `Promote-Tmp.png` *is*
    # `promote-tmp.png`, so a case variant would otherwise slip an image into the
    # name the recovery scan claims (PR review).
    return _addressable_name(name) and name.casefold() != _PROMOTE_TMP


def storable(name: str) -> bool:
    """Is `name` one this module will both store under and resolve back?

    The public form of `_safe_name`, for a caller that has to filter a listing
    by what a write and a read will accept. `list_in` cannot answer this itself:
    it filters on `_addressable_name`, one name looser, because a stranded
    `promote-tmp` is shown on purpose (#253) -- and that exception belongs to a
    per-version folder, not to every directory built on these primitives. See
    `image_library.addressable` for what a caller without promotions does
    with it.
    """
    return _safe_name(name)


def _norm_ext(ext: str) -> str:
    ext = ext.lstrip(".").lower()
    return ext if ext in _EXTS else ""


def _dir(root: Path, cid: str, vid: str, base: str = "characters") -> Path:
    return root / base / cid / "assets" / vid


def version_dir(root: Path, cid: str, vid: str, base: str = "characters") -> Path:
    """The per-version image directory of record `cid`, version `vid` --
    `ValueError` for an unsafe id. For a caller that works at the directory
    level (`link_in`, `resolve`) on a record's images, so the layout is spelled
    once, here."""
    if not (safe_id(cid) and safe_id(vid)):
        raise ValueError("unsafe image id")
    return _dir(root, cid, vid, base)


_registry_guard = threading.Lock()
_image_locks: dict[str, threading.RLock] = {}


def _image_lock(d: Path, name: str) -> threading.RLock:
    """Serialize writes to one logical image (all extensions of `name` in `d`).

    Cleanup is inherently multi-step -- publish the new extension, then remove
    the stale siblings -- and no filesystem offers an identity-conditional
    unlink, so "verify this is still the file I snapshotted, then delete it"
    has a gap no amount of care closes. Two concurrent uploads of different
    extensions could interleave through it and leave no image at all, which is
    the exact outcome the write-before-cleanup ordering exists to prevent
    (PR review). Serializing the sequence is what actually closes it.

    In-process only, like every other lock in this app; two processes on one
    synced store still race, as they do everywhere else.

    Reentrant, matching ``locks.campaign_lock``: a non-reentrant lock turns
    any future same-thread nesting (a delete invoked from inside a put, say)
    into a deadlock, which is a worse failure than the race it guards.

    Get-or-create under a guard: a plain ``if key not in ...`` is a
    check-then-act race that hands two first-ever callers different locks.
    """
    key = str(d / name)
    with _registry_guard:
        return _image_locks.setdefault(key, threading.RLock())


@contextmanager
def _image_locks_held(d: Path, *names: str):
    """Hold the per-image locks of several logical images at once.

    ``promote_image`` mutates two of them (the promoted slot and the avatar
    slot), so it has to hold both for the whole swap or an upload can land in
    the middle of it.

    Sorted acquisition is discipline, not a fix for a cycle that exists today:
    every other caller takes exactly one image lock, and two promotions of
    different names share only ``avatar`` -- neither ever waits on the other's
    gallery lock -- so nothing can currently deadlock whatever order is used
    (PR review corrected the original claim here). It is the *second*
    multi-lock caller that would introduce a cycle, and a fixed global order
    means that caller is safe by construction rather than by review.

    That second caller already exists: ``_heal_stranded_promotion`` takes
    ``{promote-tmp, avatar, gallery_N}``, and can be entered NESTED --
    ``delete_image`` of the avatar holds ``avatar`` and looks the slot up
    through ``image_path``. No cycle, because every set sorts as a whole and
    contains ``avatar``. Sorting the whole set, not appending to a held set,
    is the property that has to survive future edits. ``promote_image`` runs
    both repairs before taking its own locks, and the read-side promotion
    recovery (``_recover_promotion``), which ``image_path`` enters for ANY
    name, never waits at all (``_locks_if_free``): ``delete_image`` of a
    gallery slot holds that slot while it looks, and a blocking acquisition
    of ``avatar`` there could wedge against a promotion of that very slot,
    holding ``avatar`` and waiting for the slot.

    What sorting cannot do is serialize names that differ only by case:
    ``_image_lock`` keys on the exact string, so on a case-insensitive
    filesystem ``gallery_1`` and ``Gallery_1`` are one file behind two locks.
    That is this module's own pre-existing gap -- two ``put_image`` calls
    spelled that way have raced since the lock was added in #233 -- not
    something promotion introduces, and closing it means re-keying every caller
    (PR review).
    """
    with ExitStack() as stack:
        for n in sorted(set(names)):
            stack.enter_context(_image_lock(d, n))
        yield


def _free_gallery(d: Path) -> str:
    """The lowest ``gallery_N`` no image in ``d`` already occupies -- a legacy
    file or a placement (`names_in`), so a healed stray never lands on a slot a
    placement holds.

    Occupancy is case-folded: on a case-insensitive filesystem an existing
    ``Gallery_1.png`` *is* ``gallery_1.png``, so a case-sensitive comparison
    would keep handing out a slot that cannot actually be claimed -- recovery
    would then find its target already there on every pass and give up without
    repairing anything (PR review). Skipping a case-variant name on a
    case-sensitive filesystem too is merely conservative: the next slot is
    equally good, and nothing else allocates these names.
    """
    used = {n.casefold() for n in names_in(d)[0]}
    n = 1
    while f"gallery_{n}" in used:
        n += 1
    return f"gallery_{n}"


def _newest_stranded(d: Path) -> Path | None:
    """The stranded temp to rescue next, newest first.

    Newest first for the same reason ``image_path`` breaks its ties that way,
    and because the issue said so outright: with more than one temp (two
    interrupted promotions), the freshest is the one the user was promoting, so
    it is the one that should become the avatar. Sorted-by-name would hand that
    slot to whichever extension happens to sort first (PR review).
    """
    strays = [p for p in d.glob(f"{_PROMOTE_TMP}.*") if _norm_ext(p.suffix)]
    return max(strays, key=lambda p: (_mtime_ns(p), p.name)) if strays else None


# Bounds LOST RACES only -- a successful rename does not spend it. Budgeting
# total passes instead would let a directory holding more temps than the budget
# keep one stranded after an uncontended scan (`_norm_ext` lowercases, so
# `promote-tmp.PNG` and `promote-tmp.png` are two eligible files on a
# case-sensitive filesystem: the count is not bounded by the five extensions --
# PR review). Successful renames strictly reduce the number of temps, so the
# loop terminates on progress alone; this only stops it spinning against a
# writer that keeps taking the slot, and the next scan resumes the repair.
_HEAL_RETRIES = 8


def _heal_stranded_promotion(d: Path) -> None:
    """Rescue an image a pre-#253 promotion left under ``promote-tmp<ext>``.

    That swap renamed the promoted file to a fixed temp name, then the old
    avatar into its slot, then the temp into the avatar slot. A crash
    mid-sequence stranded the temp, and nothing in the app looks for it:
    ``image_path`` globs ``<name>.*``, and the editor renders only ``avatar``
    and ``gallery_N`` (``CharacterEditor.tsx``, ``EntityEditor.tsx``). So the
    file sat on disk, invisible, and the user's only recovery was to re-upload.

    Promotion cannot produce this state any more -- there is no temp -- so this
    exists purely to repair stores damaged before the fix, and it does it where
    the issue asked for it: on the directory scan, completing or restoring the
    swap.

    - No avatar: the newest temp becomes the avatar. That was the swap's
      destination, so this finishes the promotion the user asked for.
    - Avatar present: the crash came before the avatar moved, and *which* slot
      the temp was taken from is unrecoverable (the fixed name never encoded
      it), so it lands in the next free gallery slot -- visible again, and the
      working avatar is left alone.

    Renames only, onto a name held under its own lock and verified free while
    held: an in-process upload cannot have its file replaced by this (choosing
    the slot unlocked and renaming onto it could, and on POSIX ``rename``
    replaces silently -- PR review). A second process racing the same directory
    is unguarded, as it is everywhere else in this module. Nothing is ever
    deleted, and a failure (read-only store, a sync client holding the file) is
    swallowed: a read must not fail over repair the next scan can retry.

    One caveat this cannot resolve: ``_safe_name`` accepted ``promote-tmp``
    before it was reserved, so a store *could* hold a genuine image somebody
    uploaded under that name, and nothing distinguishes it from crash residue.
    Adopting it is still the better outcome -- the bytes are kept, and the file
    moves from a name no part of the UI renders to one it does.
    """
    if not any(d.glob(f"{_PROMOTE_TMP}.*")):
        return  # the overwhelmingly common case: one glob, no locks, no writes
    retries = _HEAL_RETRIES
    while retries:
        stray = _newest_stranded(d)
        if stray is None:
            return
        slot = _heal_slot(d)
        # Both choices above were read unlocked, so lock every name they name --
        # in the module's one global order -- and only then act on them.
        with _image_locks_held(d, _PROMOTE_TMP, AVATAR, slot):
            target = d / f"{slot}{stray.suffix}"
            # Recompute the decision while holding the names it depends on: the
            # temp may have been rescued by another thread, an avatar may have
            # appeared, or the slot may have been taken. `target.exists()` is
            # the last-moment guard -- with the locks held nothing in this
            # process can have created it, but `rename` replaces silently on
            # POSIX, so a file another process put there must stop us.
            if stray.exists() and slot == _heal_slot(d) and not target.exists():
                try:
                    stray.rename(target)
                except OSError:
                    return  # read-only store or a held file; the next scan retries
                continue  # progress: only lost races spend the retry budget
        retries -= 1


def _heal_slot(d: Path) -> str:
    """Where `_heal_stranded_promotion` puts a stray: the avatar slot when there
    is no avatar, else the next free gallery slot.

    An image-bearing avatar placement counts as an avatar whether or not it
    resolves yet: its object or blob may be mid-sync, and a stray renamed into
    the slot now would sit there as a legacy file the placement shadows the
    moment it arrives -- hidden again, which is what the heal exists to undo."""
    ref = image_refs.read(d, AVATAR)
    if (ref is not None and ref.image is not None) or path_in(d, AVATAR) is not None:
        return _free_gallery(d)
    return AVATAR


def _mtime_ns(p: Path) -> int:
    """Sort key that tolerates the file vanishing mid-scan. put_in writes
    the new extension and then unlinks the stale sibling, so a concurrent
    reader can genuinely glob a path that is gone by the time it stats -- and
    the old `sorted(...)[0]` never stat'd at all, so raising here would be a
    regression, not a new safety check."""
    try:
        return p.stat().st_mtime_ns
    except OSError:
        return -1


def _siblings(d: Path, name: str, supported_only: bool) -> list[Path]:
    """Every file in `d` whose stem is `name`.

    `supported_only` narrows that to the extensions we actually accept. The
    cover directory is one a human browses and a sync client writes into, so a
    `cover.txt` left beside `cover.png` must neither win resolution (it would
    be served as octet-stream and packed into a book) nor be deleted by a
    replace or a remove -- it is not ours. Record images keep the unfiltered
    behaviour: `promote_image` raises `ValueError` for "an externally-placed
    file whose extension we never accepted", which requires `image_path` to
    still hand one back.
    """
    found = list(d.glob(f"{name}.*"))
    return [p for p in found if _norm_ext(p.suffix)] if supported_only else found


def _legacy_path(d: Path, name: str, supported_only: bool) -> Path | None:
    """The newest legacy ``<name>.*`` file in `d` -- `path_in`'s pre-placement
    rule, and its fallback."""
    matches = _siblings(d, name, supported_only)
    if not matches:
        return None
    return max(matches, key=lambda p: (_mtime_ns(p), p.name))


def resolve(d: Path, name: str) -> image_refs.ResolvedImage | None:
    """The placement `name` in `d`, resolved to its object and blob -- None
    when there is no image-bearing placement or it does not resolve (yet)."""
    if not _safe_name(name):
        return None
    return image_refs.resolve(d, name)


def path_in(d: Path, name: str, *, supported_only: bool = False) -> Path | None:
    """The current file for logical image `name` in directory `d`, or None.

    A placement that resolves answers with its blob path. Otherwise -- no
    placement, an image-less one, or one whose object or blob has not arrived
    -- the legacy rule below answers, so redundant data beats lost data.

    Legacy: newest wins, not alphabetically first: `put_in` used to write the
    new file before unlinking stale other-extension siblings (so a crash can't
    lose the image), which leaves both present for a moment -- and a plain
    `sorted()[0]` would hand back the stale one. Also self-heals if that
    unlink ever fails.

    Lock-agnostic: this takes a directory and no campaign identity, so a caller
    that mutates campaign-scoped state through `put_in`/`delete_in` is the one
    that must hold `locks.campaign_lock` (`store.covers` does).
    """
    if not _safe_name(name) or not d.exists():
        return None
    resolved = image_refs.resolve(d, name)
    if resolved is not None:
        return resolved.blob_path
    return _legacy_path(d, name, supported_only)


def _snapshot_siblings(d: Path, name: str,
                       supported_only: bool) -> list[tuple[Path, int, int]]:
    """Every legacy file of `name`, with its identity, for `_drop_snapshotted`."""
    stale = []
    for p in _siblings(d, name, supported_only):
        try:
            st = p.stat()
            stale.append((p, st.st_dev, st.st_ino))
        except OSError:
            pass  # vanished already; nothing to clean up
    return stale


def _drop_snapshotted(stale: list[tuple[Path, int, int]]) -> None:
    """Unlink exactly the files `_snapshot_siblings` saw: the lock keeps
    concurrent callers out, and the identity check keeps anything that reaches
    the directory another way (an external tool, a sync client) from having its
    file deleted by path alone."""
    for p, dev, ino in stale:
        try:
            st = p.stat()
            if (st.st_dev, st.st_ino) != (dev, ino):
                continue  # not the file we snapshotted; not ours to delete
            p.unlink()
        except OSError:
            pass  # a lost cleanup is harmless: the placement wins over it


def _drop_if_placed(d: Path, name: str, image_id: str,
                    stale: list[tuple[Path, int, int]]) -> None:
    """`_drop_snapshotted`, only once placement `name` RESOLVES to `image_id`.

    A placement whose object or blob has not arrived (a link to an image that
    is mid-sync) is not an image to any reader yet, and the legacy file is
    what `path_in` falls back to meanwhile: redundant data beats lost data
    (spec section 6). The file goes on a later write, or stays shadowed once
    the placement resolves."""
    placed = image_refs.resolve(d, name)
    if placed is not None and placed.image_id == image_id:
        _drop_snapshotted(stale)


def _place(d: Path, name: str, image_id: str, *, keep_focus: bool,
           supported_only: bool) -> None:
    """Publish placement `name` -> `image_id`, then drop the legacy siblings.

    Placement BEFORE the unlinks, as `put_in` always wrote before it cleaned
    up: a failure in between leaves a legacy file the placement shadows, never
    no image -- and the unlinks only once the placement resolves
    (`_drop_if_placed`). `keep_focus` keeps the old placement's focus;
    otherwise a focus survives only when the placement already held this very
    image. Caller holds `_image_lock(d, name)`.
    """
    stale = _snapshot_siblings(d, name, supported_only)
    old = image_refs.read(d, name)
    focus = (old.focus if old is not None and (keep_focus or old.image == image_id)
             else None)
    image_refs.write(d, name, image_id, focus=focus)
    _drop_if_placed(d, name, image_id, stale)


def put_in(d: Path, name: str, data: bytes, ext: str, *,
           supported_only: bool = False, source_url: str | None = None) -> str:
    """Store `data` and place it as `name` in `d`, dropping the legacy siblings.

    Returns the extension of the blob actually kept, which is what the bytes
    sniff as (or `ext`, for bytes that sniff as nothing). `ext` is still
    validated against the allowlist first, so an unsupported one is the same
    `ValueError` it always was.
    """
    if not _safe_name(name):
        raise ValueError("unsafe image id")
    if not _norm_ext(ext):
        raise ValueError("unsupported image type")
    # Outside the name lock: ingest takes the store's own locks, and the
    # decode is the slow part of a write.
    obj = image_store.ingest(data, ext, source_url=source_url)
    with _image_lock(d, name):
        _recover_promotion(d)
        _place(d, name, obj.id, keep_focus=False, supported_only=supported_only)
    return obj.ext


def link_in(d: Path, name: str, image_id: str, *, keep_focus: bool = False) -> None:
    """Place an image already in the store as `name` in `d` -- a reference
    operation, no bytes moved -- and drop the name's legacy siblings.

    `ValueError` for an unsafe name or an invalid id. The object is not
    required to exist: a placement may arrive before its object does (sync),
    and readers treat that as unresolved until it does -- which is why the
    legacy siblings are dropped only once the placement resolves.
    """
    if not _safe_name(name):
        raise ValueError("unsafe image id")
    with _image_lock(d, name):
        _recover_promotion(d)
        _place(d, name, image_id, keep_focus=keep_focus, supported_only=False)


def adopt_legacy(d: Path, name: str) -> str | None:
    """Turn legacy image `name` in `d` into a placement and return its id.

    - an image-bearing placement already there: its id, nothing written;
    - a legacy file: its bytes are ingested, the placement written and the
      legacy file(s) removed. The avatar's legacy crop (`focus.json`) moves
      onto the placement, unless a placement already carries one;
    - neither: None.

    `ValueError` for a legacy file of an extension this module never accepted
    (only an external tool can put one there), which is left untouched.
    """
    if not _safe_name(name) or not d.exists():
        return None
    with _image_lock(d, name):
        old = image_refs.read(d, name)
        if old is not None and old.image is not None:
            return old.image
        src = _legacy_path(d, name, False)
        if src is None:
            return None
        if not _norm_ext(src.suffix):
            raise ValueError(f"unsupported image type: {src.name}")
        obj = image_store.ingest(src.read_bytes(), src.suffix)
        focus = old.focus if old is not None else None
        if name == AVATAR and focus is None:
            focus = _read_focus_file(d)
        stale = _snapshot_siblings(d, name, False)
        image_refs.write(d, name, obj.id, focus=focus)
        _drop_if_placed(d, name, obj.id, stale)
        if name == AVATAR:
            # The placement answers for the crop from here on (`read_focus`),
            # so a `focus.json` left behind could only ever be stale.
            _unlink_focus_file(d)
        return obj.id


def delete_in(d: Path, name: str, *, supported_only: bool = False) -> None:
    """Remove logical image `name` in `d`: its placement and every legacy file.

    The object and blob stay in the store (GC is stage 4). Failures are
    swallowed here, as they always were -- callers that need the removal
    *confirmed* (`covers.delete_cover`) re-resolve afterwards.
    """
    if not _safe_name(name) or not d.exists():
        return
    # Same lock as put_in: a delete racing an upload must not remove the file
    # the upload just published and leave the caller thinking it wrote one, nor
    # half-remove a set the upload is mid-way through replacing.
    with _image_lock(d, name):
        _recover_promotion(d)
        with suppress(OSError):     # swallowed like the legacy files below
            image_refs.delete(d, name)
        for p in _siblings(d, name, supported_only):
            try:
                p.unlink()
            except OSError:
                pass


def is_slot_file(p: Path) -> bool:
    """Whether file `p`, directly inside a version folder, is a legacy image
    file -- a slot `copy_slots` answers for -- rather than one of the folder's
    other files (`descriptions.json`, `focus.json`, ...). The rule is
    `names_in`'s, so the two agree on what counts as a slot."""
    return bool(_norm_ext(p.suffix)) and _addressable_name(p.stem)


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
    _recover_promotion(src)
    refs = image_refs.scan(src)
    names = names_in(src)[0] | {n for n in refs if _addressable_name(n)}
    for name in sorted(names):
        if skip(name) or not _addressable_name(name):
            continue
        ref = refs.get(name)
        legacy = (_legacy_path(src, name, True)
                  if ref is None or ref.image is None or image_refs.resolve_ref(ref) is None
                  else None)
        if ref is None and legacy is None:
            continue   # vanished since the scan
        dst.mkdir(parents=True, exist_ok=True)
        with _image_lock(dst, name):
            _recover_promotion(dst)
            _copy_slot(src, dst, name, ref, legacy, overwrite)


def _copy_slot(src: Path, dst: Path, name: str, ref: image_refs.Ref | None,
               legacy: Path | None, overwrite: bool) -> None:
    """One slot of `copy_slots`: `ref` is the source placement, `legacy` the
    source file to copy (None when a resolving placement makes it redundant).
    Caller holds `_image_lock(dst, name)`."""
    held = image_refs.read(dst, name)
    held_image = held is not None and held.image is not None
    held_files = _snapshot_siblings(dst, name, False)
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
            _drop_snapshotted([s for s in held_files if s[0] != target])
    if ref is not None and ref.image is not None:
        image_refs.write(dst, name, ref.image, focus=focus)
        if overwrite and legacy is None:
            _drop_if_placed(dst, name, ref.image, held_files)
    elif held is not None or focus is not None:
        # The copied slot is a legacy file: `dst`'s placement keeps only the
        # crop (or goes, with none), so it can never shadow the file.
        image_refs.write(dst, name, None, focus=focus)


def image_path(root: Path, cid: str, vid: str, name: str, base: str = "characters") -> Path | None:
    if not (safe_id(cid) and safe_id(vid) and _safe_name(name)):
        return None
    d = _dir(root, cid, vid, base)
    if not d.exists():
        return None
    _recover_promotion(d)
    p = path_in(d, name)
    if p is None and name == AVATAR:
        # A promotion interrupted before #253 may have stranded the avatar under
        # `promote-tmp`; adopt it rather than serve a 404 over a file we have.
        _heal_stranded_promotion(d)
        p = path_in(d, name)
    return p


def names_in(d: Path, also: str = "") -> tuple[set[str], bool]:
    """Every logical image NAME in `d`: the legacy stems plus every placement
    that holds an image -- `list_in`'s name set, and nothing else.

    Placements are counted WITHOUT being resolved, which keeps a sweep to one
    directory read per folder (plus the placements' own small reads) rather
    than an object read and a blob stat per image. The price is a transient
    disagreement with `list_in`, accepted on purpose: mid-sync, a placement
    whose object or blob has not arrived yet is counted here but omitted
    there (unless a legacy file backs the name), until the sync completes.

    `list_in` additionally decides which of two files sharing a stem answers for
    it, and that tie-break is an mtime stat per file. A caller that only counts
    names, or asks whether one is present, never uses the answer — and on a
    sweep over every version of every record of every base, those stats are the
    bulk of the walk (`image_descriptions.undescribed_count`).

    The filter is `list_in`'s, deliberately identical: a name this admits and
    that one does not is a count that disagrees with the list behind it.

    `also` names one extra file to report the presence of, and it is here so a
    caller that wants both facts pays for one directory read instead of two.
    The sidecar beside a version's images is the case (`image_descriptions`):
    statting it separately doubles the traversals on a sweep over every version
    folder in the store. Returns (names, whether `also` was present); with no
    `also`, the flag is always False.
    """
    out: set[str] = set()
    found = False
    try:
        # `os.scandir`, not `iterdir()`: the entry carries its type from the
        # directory read, so this is one syscall for the directory rather than
        # one more stat per file in it. On the sweep above that is the
        # difference the function was added for.
        with os.scandir(d) as it:
            for e in it:
                if also and e.name == also:
                    found = True
                    continue
                stem, _, ext = e.name.rpartition(".")
                if stem and e.is_file() and _norm_ext("." + ext) and _addressable_name(stem):
                    out.add(stem)
    except OSError:
        return set(), False
    out.update(n for n in image_refs.image_names(d) if _addressable_name(n))
    return out, found


def list_in(d: Path) -> list[dict]:
    """One entry per logical image in directory `d`, newest sibling winning.

    The directory-level half of `list_images`, split out so a flat directory
    built on `path_in`/`put_in`/`delete_in` -- the campaign image library
    (`store.campaign_images`) -- enumerates by the very same rule the
    per-version folders do, rather than by a second copy of it that agrees
    right up until one of the two is fixed. What stays behind in `list_images`
    is only what a version has and a flat directory does not: ids to check,
    and a stranded promotion to repair.

    ONE ENTRY PER LOGICAL IMAGE, not per file. Two files can share a stem:
    `put_in` writes the new extension before dropping the old one, and
    `path_in` self-heals an unlink that never happened, so the state is
    reachable and can persist. Listing both double-counts galleries, hands the
    frontend two tiles under one key, and lets a caller read a cache token off
    the sibling the server will not serve -- which a `?v=` URL, answered
    `immutable, max-age=1y`, then pins for a year.

    Newest wins, the same rule and the same tie-break `path_in` resolves by,
    so the entry always describes the bytes the serve route returns.

    Placements: a name whose placement resolves is listed from it,
    ``{name, ext, v, image_id}`` with `v` the blob's sha256, and shadows any
    legacy file of that name; one that does not resolve is omitted unless a
    legacy file backs the name (the same fallback as `path_in`). Sorted by
    name. A listing never scans the global store -- each placement costs its
    object read and a blob stat.
    """
    found = _candidates(d)
    return _listing(found, _resolve_refs(found[1])[0] if found else None)


_Files = dict[str, list[tuple[str, int, int]]]


def _candidates(d: Path) -> tuple[_Files, dict[str, image_refs.Ref]] | None:
    """`(files, refs)`: every legacy file in `d` that could answer for a
    logical image, by stem, each as `(name, st_mtime_ns, st_size)`, and every
    placement in `d` (`image_refs.scan`, image-less ones included) -- or None
    when `d` is not there to list.

    ONE stat per file, and it is the one the listing needs anyway: `os.scandir`
    carries each entry's type from the directory read, so `is_file` costs
    nothing for a plain file, and the stat that ranks two siblings is the same
    stat the `v` token is formatted from. `iterdir` paid for the type check, a
    stat per comparison and a fresh stat for the token -- three per image on
    the listing every character grid row and describe queue is built from.

    The token coming from the ranking stat is a tightening, not only a saving:
    the two used to be taken apart, so a file rewritten between them could be
    ranked by one version of itself and tokened by another.

    The stem and extension split is `names_in`'s, which that function's
    docstring holds to this one: for every name either accepts, a
    `rpartition(".")` and pathlib's `stem`/`suffix` agree.
    """
    try:
        it = os.scandir(d)
    except OSError:
        # `d.exists()` was the old guard, and it reads a missing directory and
        # a missing PARENT the same way -- while a `d` that exists but cannot
        # be listed (a file, a permissions problem) raised out of `iterdir`.
        # Both halves are kept.
        if not d.exists():
            return None
        raise
    found: dict[str, list[tuple[str, int, int]]] = {}
    with it:
        for e in it:
            stem, _, ext = e.name.rpartition(".")
            # filter on addressability, not just the extension: a name
            # image_path could never resolve would advertise a gallery entry
            # that cannot be served, promoted or deleted (#259 review).
            # `promote-tmp` is the one deliberate exception -- unwritable, but a
            # stranded one is shown on purpose so failed recovery is visible
            # rather than silent (#253).
            if not (stem and _norm_ext(ext) and _addressable_name(stem)):
                continue
            try:
                if not e.is_file():
                    continue
                st = e.stat()
            except OSError:
                continue   # vanished mid-scan; a listing must not fail over one file
            found.setdefault(stem, []).append((e.name, st.st_mtime_ns, st.st_size))
    return found, image_refs.scan(d)


def _resolve_refs(refs: dict[str, image_refs.Ref]
                  ) -> tuple[dict[str, image_refs.ResolvedImage], bool]:
    """Each image-bearing placement that resolves, by name, and whether EVERY
    image-bearing placement did. Image-less ones (occurrence overrides) are not
    images and are skipped."""
    out: dict[str, image_refs.ResolvedImage] = {}
    complete = True
    for name, ref in refs.items():
        if ref.image is None or not _addressable_name(name):
            continue
        r = image_refs.resolve_ref(ref)
        if r is None:
            complete = False
        else:
            out[name] = r
    return out, complete


def _listing(found: tuple[_Files, dict[str, image_refs.Ref]] | None,
             resolved: dict[str, image_refs.ResolvedImage] | None = None) -> list[dict]:
    """`list_in`'s entries from `_candidates` and `_resolve_refs`: a resolved
    placement answers for its name; otherwise the newest legacy sibling per
    stem by (mtime, name), which is order-independent because names are
    unique -- so the directory's own enumeration order, unlike `iterdir`'s
    sorted one, cannot change which file wins."""
    files = found[0] if found else {}
    resolved = resolved or {}
    out: list[dict] = []
    for stem in sorted(set(files) | set(resolved)):
        r = resolved.get(stem)
        if r is not None:
            out.append({"name": stem, "ext": r.ext, "v": r.blob_sha256,
                        "image_id": r.image_id})
            continue
        name, mtime_ns, size = max(files[stem], key=lambda c: (c[1], c[0]))
        out.append({"name": stem, "ext": name.rpartition(".")[2].lower(),
                    "v": _token(mtime_ns, size)})
    return out


def list_images(root: Path, cid: str, vid: str, base: str = "characters") -> list[dict]:
    if not (safe_id(cid) and safe_id(vid)):
        return []
    d = _dir(root, cid, vid, base)
    if not d.exists():
        return []
    # An interrupted promotion is finished (or its stale journal dropped)
    # before anything is listed, so the listing never shows a half-swap.
    _recover_promotion(d)
    # The "asset directory scan" the recovery in #253 was asked for: a temp the
    # old promotion stranded gets a reachable name before the listing is built,
    # so it shows up in the editor instead of staying invisible forever.
    _heal_stranded_promotion(d)
    return list_in(d)


def image_version(p: Path) -> str:
    """Cache-busting token for an image file's current bytes; a `?v=` URL
    carrying it is served immutable, so the browser never revalidates.

    A blob's token is its sha256 -- its name says what its bytes are, so
    nothing is statted -- and a legacy file's is `_token` of its stat."""
    sha = image_store.blob_sha_of(p)
    if sha is not None:
        return sha
    st = p.stat()
    return _token(st.st_mtime_ns, st.st_size)


def _token(mtime_ns: int, size: int) -> str:
    """`image_version`'s token from a stat already in hand."""
    return f"{mtime_ns:x}-{size:x}"


def _stranded(found: _Files) -> bool:
    """Whether `_heal_stranded_promotion` could find something to rescue among
    these candidates. Case-folded, so it answers yes wherever the heal's glob
    might match (a case-insensitive filesystem): a false yes only costs a
    listing that is not memoized."""
    return any(stem.casefold() == _PROMOTE_TMP for stem in found)


def version_art(root: Path, cid: str, vid: str,
                base: str = "characters") -> tuple[list[dict], int | None, tuple | None]:
    """`(list_images(...), read_focus(...), stamps)` for one version folder.

    The same two answers those functions give, plus what vouches for them in
    `statcache.memo_stamped`'s terms, so a caller memoizing a row built from a
    version's art knows which stats will tell it the art moved:

    - the folder itself, stamped before it is listed -- its mtime covers every
      image arriving, leaving or being renamed, and `focus.json` appearing;
    - each file that could answer for `AVATAR`, whose bytes are the only ones
      a row reports on (`avatar_v`); for every other image only the NAME is
      used, and names are the folder's listing;
    - `focus.json` when there is one, stamped before it is read;
    - the placements folder (``image-refs/``) when there is one, whose listing
      covers a placement arriving, leaving or being rewritten (every write is
      an atomic rename), and ``image-refs/avatar.json`` when there is one --
      the avatar's placement names the bytes `avatar_v` reports and carries
      the crop. Both stamped before the placements are read;
    - for an image-bearing avatar placement, the store's object sidecar it
      names (stamped before it is read) and the blob that resolves to -- the
      global half of `avatar_v`, which an adoption or a vanished blob moves
      without touching this folder.

    A folder that is not there is vouched for by the nearest ancestor that is,
    up to `root`: creating it moves that directory's mtime.

    The stamps are None -- "compute again next time" -- when a file vanished
    mid-read, and when the folder holds a stranded promotion or a promotion
    journal: `list_images` heals and recovers on every scan, and a repair that
    failed (a read-only store, a held file, a promotion in flight) is meant to
    be retried by the next scan rather than remembered as the answer.
    Likewise while any image-bearing placement in the folder does not resolve:
    its object or blob may sync in later without moving anything stamped here,
    and that arrival must be noticed rather than remembered as an absence.
    """
    if not (safe_id(cid) and safe_id(vid)):
        return [], None, ()
    d = _dir(root, cid, vid, base)
    here = statcache.stamp(d)
    if here is None:
        # Walk up to whatever exists; `root` itself missing leaves nothing to
        # vouch with, and an empty answer that is recomputed every time.
        up = next((s for s in map(statcache.stamp, (d.parent, d.parent.parent,
                                                     d.parent.parent.parent, root))
                   if s is not None), None)
        # The folder may have been created since that stamp; whatever the
        # reads below see, the stamp no longer matches next time.
        return (list_images(root, cid, vid, base), read_focus(root, cid, vid, base),
                None if up is None else (up,))
    stamps = [here]
    _recover_promotion(d)            # as `list_images` does, before listing
    _heal_stranded_promotion(d)
    for p in (d / image_refs.REFS_DIR, image_refs.ref_path(d, AVATAR)):
        ref_stamp = statcache.stamp(p)
        if ref_stamp is not None:
            stamps.append(ref_stamp)
    found = _candidates(d)
    if found is None:
        return [], read_focus(root, cid, vid, base), None
    files, refs = found
    avatar_ok = _stamp_avatar_object(refs, stamps)
    resolved, complete = _resolve_refs(refs)
    avatar_ok = _stamp_avatar_blob(resolved, stamps) and avatar_ok
    cacheable = (_restamp_avatar(d, files, stamps) and not _stranded(files)
                 and complete and avatar_ok and image_refs.read_journal(d) is None)
    focus_stamp = statcache.stamp(d / FOCUS_FILE)
    if focus_stamp is not None:
        stamps.append(focus_stamp)
    focus = read_focus(root, cid, vid, base)
    return _listing(found, resolved), focus, (tuple(stamps) if cacheable else None)


def _stamp_avatar_object(refs: dict[str, image_refs.Ref], stamps: list) -> bool:
    """Stamp the store sidecar of the object the avatar placement names, onto
    `stamps`, BEFORE it is read (`_resolve_refs`). False when there is one to
    stamp and it cannot be statted.

    The sidecar is global state no version folder's stat covers: an adoption
    rewrites the blob it retains -- the bytes `avatar_v` names -- without
    moving anything under `d`."""
    ref = refs.get(AVATAR)
    if ref is None or ref.image is None:
        return True
    s = statcache.stamp(image_store.object_path(ref.image))
    if s is None:
        return False
    stamps.append(s)
    return True


def _stamp_avatar_blob(resolved: dict[str, image_refs.ResolvedImage], stamps: list) -> bool:
    """Stamp the blob a resolved avatar placement reports, onto `stamps`: its
    vanishing (a partial GC, a sync) must be noticed, not remembered as there.
    False when it cannot be statted. Only the avatar's: every other image
    contributes a NAME to a row, never bytes."""
    r = resolved.get(AVATAR)
    if r is None:
        return True
    s = statcache.stamp(r.blob_path)
    if s is None:
        return False
    stamps.append(s)
    return True


def _restamp_avatar(d: Path, found: _Files, stamps: list) -> bool:
    """Stamp every LEGACY file that could answer for `AVATAR`, onto `stamps`, and
    rebuild its candidates in `found` from those stamps. False when one of
    them vanished since the scan (the listing then simply lacks it).

    From the STAMPS, so the token a row reports and the stat that vouches for
    it are one stat: stamping after the scan instead would let a rewrite
    between the two store the new stamp beside the old token. Not the scan's
    own stat either: `DirEntry.stat()` has no inode on Windows, so it would
    never equal the `os.stat` a later call compares it with.
    """
    ok = True
    fresh = []
    for name, _mtime, _size in found.get(AVATAR, []):
        s = statcache.stamp(os.path.join(d, name))
        if s is None:
            ok = False
            continue
        stamps.append(s)
        fresh.append((name, s[1], s[3]))
    if fresh:
        found[AVATAR] = fresh
    else:
        found.pop(AVATAR, None)
    return ok


def _clamp_focus(val: object) -> int | None:
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        return None
    return max(0, min(100, int(val)))


def _read_focus_file(d: Path) -> int | None:
    """The legacy crop: `focus.json` in `d`, or None."""
    p = d / FOCUS_FILE
    if not p.exists():
        return None
    try:
        val = json.loads(p.read_text(encoding="utf-8")).get(AVATAR)
    except (json.JSONDecodeError, AttributeError):
        return None
    return _clamp_focus(val)


def _unlink_focus_file(d: Path) -> None:
    p = d / FOCUS_FILE
    if p.exists():
        p.unlink()


def _avatar_ref(d: Path) -> image_refs.Ref | None:
    """The avatar placement in `d`, statted before it is opened: most version
    folders have none, and a listing asks this of every row it rebuilds."""
    if not image_refs.ref_path(d, AVATAR).exists():
        return None
    return image_refs.read(d, AVATAR)


def owns_focus(root: Path, cid: str, vid: str, base: str = "characters") -> bool:
    """Whether this version folder carries a crop record of its own: an avatar
    placement (with or without an image -- an image-less one is the occurrence
    override a campaign sets on an avatar it inherits) or a legacy
    `focus.json`. `overlay.read_focus`, and the campaign listing that has to
    agree with it, read the crop from whichever root owns one."""
    if not (safe_id(cid) and safe_id(vid)):
        return False
    d = _dir(root, cid, vid, base)
    return _avatar_ref(d) is not None or (d / FOCUS_FILE).exists()


def read_focus(root: Path, cid: str, vid: str, base: str = "characters") -> int | None:
    """Avatar crop focus: 0-100 along the image's long axis; None = center.

    An avatar placement, image-bearing or not, owns the crop when there is
    one -- its `focus`, None included; otherwise the legacy `focus.json`."""
    if not (safe_id(cid) and safe_id(vid)):
        return None
    d = _dir(root, cid, vid, base)
    ref = _avatar_ref(d)
    if ref is not None:
        return ref.focus
    return _read_focus_file(d)


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
    d = _dir(root, cid, vid, base)
    value = max(0, min(100, int(focus)))
    d.mkdir(parents=True, exist_ok=True)
    with _image_lock(d, AVATAR):
        _recover_promotion(d)
        ref = image_refs.read(d, AVATAR)
        if ref is None and _siblings(d, AVATAR, False):
            atomic.write_text(d / FOCUS_FILE, json.dumps({AVATAR: value}))
            return
        image_refs.write(d, AVATAR, ref.image if ref is not None else None, focus=value)


def clear_focus(root: Path, cid: str, vid: str, base: str = "characters") -> None:
    """Drop the avatar crop: from the placement (an image-less placement goes
    entirely) and the legacy `focus.json`."""
    if not (safe_id(cid) and safe_id(vid)):
        return
    _clear_focus_in(_dir(root, cid, vid, base))


def _clear_focus_in(d: Path) -> None:
    """`clear_focus` for a directory already in hand."""
    if not d.exists():
        return
    with _image_lock(d, AVATAR):
        ref = image_refs.read(d, AVATAR)
        if ref is not None and ref.focus is not None:
            image_refs.write(d, AVATAR, ref.image)
        _unlink_focus_file(d)


def sidecar_lock(d: Path, filename: str) -> threading.RLock:
    """Serialize read-modify-writes of one sidecar file.

    A sidecar is rewritten whole, so every writer of it needs the SAME lock or
    the last one wins. That is not what the callers were holding: an image
    lifecycle event (`promote_image`, `delete_image`) takes `_image_lock` on the
    slots it moves, while a description save takes `locks.campaign_lock` --
    two different locks over one file, so a promotion could interleave with a
    save and leave the sentence attached to the slot the picture just left.

    Keyed on the FILE rather than on an image name, because that is the unit
    being rewritten: a save for `gallery_1` and a promotion of `gallery_2` both
    rewrite the whole mapping.

    In-process only, like every other lock in this module; two processes on one
    synced store still race, as they do everywhere else here. Reentrant, so a
    caller already holding it (promotion edits the sidecar inside its own image
    locks) pays nothing.
    """
    return _image_lock(d, f"\x00sidecar\x00{filename}")


def _read_sidecar(d: Path, filename: str) -> dict:
    """A ``{name: value}`` sidecar in `d`, or ``{}`` for missing or garbled."""
    p = d / filename
    if not p.exists():
        return {}
    try:
        cur = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return {}
    return cur if isinstance(cur, dict) else {}


def edit_sidecar(d: Path, filename: str, changes: dict[str, str | None]) -> None:
    """Apply `changes` to a ``{name: value}`` sidecar in `d`; ``None`` removes.

    Image lifecycle events have to take the sidecar entries with them, and this
    module is where they happen. It cannot call the sidecar's own module to do
    it: `image_descriptions` enumerates its directory through `assets.list_in`,
    so the import would be a cycle -- and a deferred import to dodge that is
    exactly what `tests/test_import_guard.py` exists to refuse.

    So the split is by *layer*, not by file: this edits a flat JSON mapping,
    knowing nothing about what the values mean, and the owning module keeps
    every rule about them.

    SEVERAL changes at once, because promotion needs two: a swap applied as two
    writes is a window in which one picture holds the other's description.

    Silent on a missing, garbled or unwritable sidecar, matching `clear_focus`:
    these run *after* the bytes have moved, and an image operation must not fail
    over the file that annotates it. A no-op change set never creates a file.
    """
    with sidecar_lock(d, filename):
        _edit_sidecar_locked(d, filename, changes)


def _edit_sidecar_locked(d: Path, filename: str, changes: dict[str, str | None]) -> None:
    cur = _read_sidecar(d, filename)
    wanted = {k: v for k, v in changes.items() if v is not None}
    dropped = {k for k, v in changes.items() if v is None}
    if not any(cur.get(k) != v for k, v in wanted.items()) and not (dropped & set(cur)):
        return
    cur = {k: v for k, v in cur.items() if k not in dropped}
    cur.update(wanted)
    p = d / filename
    try:
        if cur:
            atomic.write_text(p, json.dumps(cur, indent=2, sort_keys=True) + "\n")
        elif p.exists():
            p.unlink()
    except OSError:
        pass


def drop_sidecar_entry(d: Path, filename: str, key: str) -> None:
    """Remove `key` from a ``{name: value}`` sidecar in `d`, if it is there."""
    edit_sidecar(d, filename, {key: None})


def put_image(root: Path, cid: str, vid: str, name: str, data: bytes, ext: str,
              base: str = "characters", *, source_url: str | None = None) -> str:
    """Store `data` as image `name` of this version; returns the blob's ext.

    A new avatar picture drops the crop: `put_in` keeps a placement's focus
    only when it already held this very image, so re-uploading the same
    picture keeps the crop and anything else clears it. A legacy
    `focus.json` is dropped either way -- the placement owns the crop now."""
    if not (safe_id(cid) and safe_id(vid)):
        raise ValueError("unsafe image id")
    d = _dir(root, cid, vid, base)
    ext = put_in(d, name, data, ext, source_url=source_url)
    if name == AVATAR:
        with _image_lock(d, AVATAR):
            _unlink_focus_file(d)
    return ext


def image_id(root: Path, cid: str, vid: str, name: str,
             base: str = "characters") -> str | None:
    """The image id placement `name` of this version resolves to, or None
    (no placement, an image-less one, a legacy file, or not resolvable)."""
    if not (safe_id(cid) and safe_id(vid) and _safe_name(name)):
        return None
    r = resolve(_dir(root, cid, vid, base), name)
    return r.image_id if r is not None else None


def delete_image(root: Path, cid: str, vid: str, name: str, base: str = "characters") -> None:
    if not (safe_id(cid) and safe_id(vid) and _safe_name(name)):
        return
    d = _dir(root, cid, vid, base)
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
    with _image_locks_held(d, name):
        _recover_promotion(d)
        delete_in(d, name)
        if image_path(root, cid, vid, name, base) is None:
            drop_sidecar_entry(d, DESCRIPTIONS_FILE, name)
    if name == AVATAR:
        clear_focus(root, cid, vid, base)


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
    d = _dir(root, cid, vid, base)
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
    if name == AVATAR:
        return
    if not (safe_id(cid) and safe_id(vid) and _safe_name(name)):
        raise FileNotFoundError(name)  # no logical image can live under such a name
    d = _dir(root, cid, vid, base)
    if not d.exists():
        raise FileNotFoundError(name)
    # Unlocked repairs first, each taking its own locks: a journal an earlier
    # promotion left is finished before this one overwrites it, and a pre-#253
    # stray is given a slot before the slots are read.
    _recover_promotion(d)
    _heal_stranded_promotion(d)
    with _image_locks_held(d, name, AVATAR), sidecar_lock(d, DESCRIPTIONS_FILE):
        # Again under the locks: a promotion that failed between the repair
        # above and this hold can have left a journal. One still here after
        # that (its slot busy, or a write that keeps failing) is refused rather
        # than overwritten -- mid-swap, it is the only record of the picture it
        # moved out of the avatar slot.
        _recover_promotion(d)
        if image_refs.read_journal(d) is not None:
            raise OSError("an earlier promotion is still unfinished; retry")
        _check_promotable(d, name)
        name_id = adopt_legacy(d, name)
        if name_id is None:
            raise FileNotFoundError(name)
        avatar_id = adopt_legacy(d, AVATAR)
        described = _read_sidecar(d, DESCRIPTIONS_FILE)
        journal = {
            "name": name,
            "pre": {AVATAR: avatar_id, name: name_id},
            "post": {AVATAR: name_id, name: avatar_id},
            "desc": {AVATAR: described.get(AVATAR), name: described.get(name)},
        }
        image_refs.write_journal(d, journal)
        _finish_promotion(d, name, journal)


class ImageNotYetAvailableError(OSError):
    """A placement names an image whose object or blob has not arrived here
    yet (mid-sync). Not "no image": the routes answer it with the 404 an
    absent image gets, carrying this reason."""


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
    check_arrived(d, name)
    src = path_in(d, name)
    if src is None:
        raise FileNotFoundError(name)
    for p in (src, path_in(d, AVATAR)):
        if p is not None and not _norm_ext(p.suffix):
            raise ValueError(f"unsupported image type: {p.name}")
    check_arrived(d, AVATAR)


def check_arrived(d: Path, slot: str) -> None:
    """`ImageNotYetAvailableError` when `slot` holds an image-bearing placement
    that does not resolve yet. Public for `overlay.promote_image`, which must
    ask before it copies anything up."""
    ref = image_refs.read(d, slot)
    if ref is not None and ref.image is not None and image_refs.resolve(d, slot) is None:
        raise ImageNotYetAvailableError(f"image not yet available (still syncing?): {slot}")


def _finish_promotion(d: Path, name: str, journal: dict) -> None:
    """Steps 4-7 of a journalled promotion: write the post-state, drop the
    crop, clear the journal. Idempotent, so recovery can repeat it from any
    point the forward pass reached. Caller holds both image locks and the
    sidecar lock, and has validated `journal` (`_journal_ok`).

    The journal goes LAST: it is the only thing that sends a later read back
    to finish the job, so every step must be done before it is cleared."""
    post, desc = journal["post"], journal["desc"]
    _set_placement(d, AVATAR, post[AVATAR])
    if post[name] is not None:
        _set_placement(d, name, post[name])
    else:
        image_refs.delete(d, name)
        if path_in(d, name) is not None:
            raise OSError(f"promoted image could not be cleared: {name}")
    # `None` on either side removes that key rather than leaving the slot's
    # previous description behind to caption its new occupant.
    edit_sidecar(d, DESCRIPTIONS_FILE, {
        AVATAR: desc.get(name),
        name: desc.get(AVATAR) if post[name] is not None else None,
    })
    _clear_focus_in(d)
    image_refs.clear_journal(d)


def _set_placement(d: Path, name: str, image_id: str) -> None:
    """Point `name` at `image_id` with no focus, unless it already does --
    a roll-forward repeats the forward pass's writes, and a read that repairs
    should not rewrite what is already right."""
    if image_refs.read(d, name) != image_refs.Ref(name, image_id, None):
        image_refs.write(d, name, image_id)


def _journal_ok(journal: dict) -> str | None:
    """The slot name a well-formed promotion journal names, or None.

    Well-formed: a promotable `name`; `pre` and `post` each naming exactly
    the avatar and that slot, with image ids or null; `pre`'s slot never null
    (a promotion always has a source) and `post` exactly `pre` swapped -- a
    roll-forward writes `post`, so any other one would place what no
    promotion asked for; and a `desc` object whose two slots' sentences are
    strings or null, since recovery writes them into the sidecar as they
    stand."""
    name = journal.get("name")
    if not isinstance(name, str) or name == AVATAR or not _safe_name(name):
        return None
    for key in ("pre", "post"):
        sides = journal.get(key)
        if not isinstance(sides, dict) or set(sides) != {AVATAR, name}:
            return None
        if not all(v is None or (isinstance(v, str) and image_hash.is_image_id(v))
                   for v in sides.values()):
            return None
    pre, post = journal["pre"], journal["post"]
    if pre[name] is None or post != {AVATAR: pre[name], name: pre[AVATAR]}:
        return None   # a promotion always has a source, and `post` is `pre` swapped
    desc = journal.get("desc")
    if not isinstance(desc, dict):
        return None
    if not all(desc.get(n) is None or isinstance(desc.get(n), str) for n in (AVATAR, name)):
        return None
    return name


@contextmanager
def _locks_if_free(locks: list[threading.RLock]):
    """Yield whether every lock in `locks` was free to take, holding them if
    so. Never waits: a read that repairs must not block behind the write it
    would repair, nor deadlock against a caller already holding one slot's
    lock (`delete_image` looks its slot up while holding it)."""
    held: list[threading.RLock] = []
    try:
        for lk in locks:
            if not lk.acquire(blocking=False):
                break
            held.append(lk)
        yield len(held) == len(locks)
    finally:
        for lk in reversed(held):
            lk.release()


def recover_promotions_in(d: Path) -> None:
    """Finish or discard every promotion journal under `d` (`_recover_promotion`
    for each ``image-refs/`` folder holding one), for a caller about to read
    placements as a whole -- a world bundle export, whose walk would otherwise
    pack a half-swapped pair and miss the picture only the journal still names.

    Best-effort exactly as each recovery is: a slot busy with a promotion in
    flight is left for that promotion. Symlinks are not followed."""
    for dirpath, dirnames, filenames in os.walk(d, followlinks=False):
        if Path(dirpath).name != image_refs.REFS_DIR:
            continue
        dirnames[:] = []
        if image_refs.JOURNAL in filenames:
            _recover_promotion(Path(dirpath).parent)


def _recover_promotion(d: Path) -> None:
    """Finish or discard the promotion journal in `d`, if there is one.

    The journal is finished only when every slot still holds its `pre` or its
    `post` image -- the states the swap itself passes through. Anything else
    means the store moved on (later edits, or a stale journal that synced in
    after them), and replaying it would undo somebody's work, so it is
    discarded and nothing else changes. A malformed journal is discarded too.

    Runs from reads (`image_path`, `list_images`, `version_art`), so it never
    raises and never waits: when the slots are busy -- a promotion in flight --
    or a write fails, the journal stays for the next read to finish.

    And from the writers (`put_in`, `link_in`, `delete_in`, `delete_image`,
    `write_focus`), under their own name lock, before they write: a write
    landing first would move a slot out of its `pre`/`post` states, and
    recovery would then discard the journal as stale -- and, mid-swap, with
    it the only record of the old avatar's picture.
    """
    journal = image_refs.read_journal(d)
    if journal is None:
        return   # the overwhelmingly common case: one failed open, no locks
    name = _journal_ok(journal)
    if name is None:
        # Under the sidecar lock, which a promotion holds throughout, so this
        # cannot remove the journal of a promotion that has just written one.
        with _locks_if_free([sidecar_lock(d, DESCRIPTIONS_FILE)]) as free:
            if free and image_refs.read_journal(d) == journal:
                image_refs.clear_journal(d)
        return
    locks = [_image_lock(d, n) for n in sorted({name, AVATAR})]
    with _locks_if_free([*locks, sidecar_lock(d, DESCRIPTIONS_FILE)]) as free:
        if not free or image_refs.read_journal(d) != journal:
            return   # busy, or another reader got here first
        try:
            for slot in (AVATAR, name):
                ref = image_refs.read(d, slot)
                held = ref.image if ref is not None else None
                if held not in (journal["pre"][slot], journal["post"][slot]):
                    image_refs.clear_journal(d)
                    return
            _finish_promotion(d, name, journal)
        except (OSError, ValueError):
            pass     # a read must not fail over a repair the next one retries
