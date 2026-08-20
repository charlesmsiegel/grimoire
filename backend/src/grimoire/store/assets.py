"""Per-version image store: <base>/<cid>/assets/<vid>/<name>.<ext>.

The default base is "characters"; entity kinds (locations, lore) pass base=kind
with vid="default" so records without versions get the same folder layout. The
avatar/primary image is the image named AVATAR. Other image kinds (gallery,
emotions, backgrounds, …) drop into the same per-version folder with no schema
change. Images are never hashed into the card, so character sync is untouched
by image edits.
"""

from __future__ import annotations

import json
import shutil
import threading
from contextlib import ExitStack, contextmanager
from pathlib import Path

from . import atomic
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
    `campaign_images.addressable` for what a caller without promotions does
    with it.
    """
    return _safe_name(name)


def _norm_ext(ext: str) -> str:
    ext = ext.lstrip(".").lower()
    return ext if ext in _EXTS else ""


def _dir(root: Path, cid: str, vid: str, base: str = "characters") -> Path:
    return root / base / cid / "assets" / vid


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

    That second caller already exists, nested: ``promote_image`` holds
    ``{name, avatar}`` and calls ``image_path``, which can enter
    ``_heal_stranded_promotion`` and take ``{promote-tmp, avatar, gallery_N}``.
    No cycle, because both sort their *whole* set and every set contains
    ``avatar`` -- so no thread can hold a later name while waiting for an
    earlier one. Sorting the whole set, not appending to a held set, is the
    property that has to survive future edits.

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
    """The lowest ``gallery_N`` no file in ``d`` already occupies.

    Occupancy is case-folded: on a case-insensitive filesystem an existing
    ``Gallery_1.png`` *is* ``gallery_1.png``, so a case-sensitive comparison
    would keep handing out a slot that cannot actually be claimed -- recovery
    would then find its target already there on every pass and give up without
    repairing anything (PR review). Skipping a case-variant name on a
    case-sensitive filesystem too is merely conservative: the next slot is
    equally good, and nothing else allocates these names.
    """
    used = {p.stem.casefold() for p in d.iterdir() if p.is_file()}
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
        slot = AVATAR if not any(d.glob(f"{AVATAR}.*")) else _free_gallery(d)
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
            if (stray.exists()
                    and slot == (AVATAR if not any(d.glob(f"{AVATAR}.*"))
                                 else _free_gallery(d))
                    and not target.exists()):
                try:
                    stray.rename(target)
                except OSError:
                    return  # read-only store or a held file; the next scan retries
                continue  # progress: only lost races spend the retry budget
        retries -= 1


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


def path_in(d: Path, name: str, *, supported_only: bool = False) -> Path | None:
    """The current file for logical image `name` in directory `d`, or None.

    Newest wins, not alphabetically first: `put_in` writes the new file before
    unlinking stale other-extension siblings (so a crash can't lose the image),
    which leaves both present for a moment -- and a plain `sorted()[0]` would
    hand back the stale one. Also self-heals if that unlink ever fails.

    Lock-agnostic: this takes a directory and no campaign identity, so a caller
    that mutates campaign-scoped state through `put_in`/`delete_in` is the one
    that must hold `locks.campaign_lock` (`store.covers` does).
    """
    if not _safe_name(name) or not d.exists():
        return None
    matches = _siblings(d, name, supported_only)
    if not matches:
        return None
    return max(matches, key=lambda p: (_mtime_ns(p), p.name))


def put_in(d: Path, name: str, data: bytes, ext: str, *,
           supported_only: bool = False) -> str:
    """Publish `data` as `<name>.<ext>` in `d`, dropping the stale siblings."""
    if not _safe_name(name):
        raise ValueError("unsafe image id")
    ext = _norm_ext(ext)
    if not ext:
        raise ValueError("unsupported image type")
    d.mkdir(parents=True, exist_ok=True)
    written = d / f"{name}.{ext}"
    with _image_lock(d, name):
        # Write BEFORE dropping prior-extension files. The reverse order (which
        # this used to do) loses the image outright if anything fails between
        # the unlink and the write -- atomicity alone cannot fix an ordering
        # bug. path_in() breaks the resulting momentary tie by mtime.
        #
        # Snapshot the siblings' IDENTITY before writing, and delete only those
        # exact files: the lock keeps concurrent callers out, and the identity
        # check keeps anything that reaches the directory another way (an
        # external tool, a sync client) from having its file deleted by path
        # alone.
        stale = []
        for p in _siblings(d, name, supported_only):
            if p == written:
                continue
            try:
                st = p.stat()
                stale.append((p, st.st_dev, st.st_ino))
            except OSError:
                pass  # vanished already; nothing to clean up
        atomic.write_bytes(written, data)
        for p, dev, ino in stale:
            try:
                st = p.stat()
                if (st.st_dev, st.st_ino) != (dev, ino):
                    continue  # not the file we snapshotted; not ours to delete
                p.unlink()
            except OSError:
                pass  # a lost cleanup self-heals: path_in prefers the newest
    return ext


def delete_in(d: Path, name: str, *, supported_only: bool = False) -> None:
    """Remove every file for logical image `name` in `d`.

    Failures are swallowed here, as they always were -- callers that need the
    removal *confirmed* (`covers.delete_cover`) re-resolve afterwards.
    """
    if not _safe_name(name) or not d.exists():
        return
    # Same lock as put_in: a delete racing an upload must not remove the file
    # the upload just published and leave the caller thinking it wrote one, nor
    # half-remove a set the upload is mid-way through replacing.
    with _image_lock(d, name):
        for p in _siblings(d, name, supported_only):
            try:
                p.unlink()
            except OSError:
                pass


def image_path(root: Path, cid: str, vid: str, name: str, base: str = "characters") -> Path | None:
    if not (safe_id(cid) and safe_id(vid) and _safe_name(name)):
        return None
    d = _dir(root, cid, vid, base)
    if not d.exists():
        return None
    p = path_in(d, name)
    if p is None and name == AVATAR:
        # A promotion interrupted before #253 may have stranded the avatar under
        # `promote-tmp`; adopt it rather than serve a 404 over a file we have.
        _heal_stranded_promotion(d)
        p = path_in(d, name)
    return p


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
    """
    if not d.exists():
        return []
    best: dict[str, Path] = {}
    for p in sorted(d.iterdir()):
        # filter on addressability, not just the extension: a name image_path
        # could never resolve would advertise a gallery entry that cannot be
        # served, promoted or deleted (#259 review). `promote-tmp` is the one
        # deliberate exception -- unwritable, but a stranded one is shown on
        # purpose so failed recovery is visible rather than silent (#253).
        if p.is_file() and _norm_ext(p.suffix) and _addressable_name(p.stem):
            cur = best.get(p.stem)
            if cur is None or (_mtime_ns(p), p.name) > (_mtime_ns(cur), cur.name):
                best[p.stem] = p
    out: list[dict] = []
    for name in sorted(best):
        p = best[name]
        try:
            out.append({"name": name, "ext": p.suffix.lstrip(".").lower(),
                        "v": image_version(p)})
        except OSError:
            continue   # vanished mid-scan; a listing must not fail over one file
    return out


def list_images(root: Path, cid: str, vid: str, base: str = "characters") -> list[dict]:
    if not (safe_id(cid) and safe_id(vid)):
        return []
    d = _dir(root, cid, vid, base)
    if not d.exists():
        return []
    # The "asset directory scan" the recovery in #253 was asked for: a temp the
    # old promotion stranded gets a reachable name before the listing is built,
    # so it shows up in the editor instead of staying invisible forever.
    _heal_stranded_promotion(d)
    return list_in(d)


def image_version(p: Path) -> str:
    """Cache-busting token for an image file's current bytes; a `?v=` URL
    carrying it is served immutable, so the browser never revalidates."""
    st = p.stat()
    return f"{st.st_mtime_ns:x}-{st.st_size:x}"


def read_focus(root: Path, cid: str, vid: str, base: str = "characters") -> int | None:
    """Avatar crop focus: 0-100 along the image's long axis; None = center."""
    if not (safe_id(cid) and safe_id(vid)):
        return None
    p = _dir(root, cid, vid, base) / FOCUS_FILE
    if not p.exists():
        return None
    try:
        val = json.loads(p.read_text(encoding="utf-8")).get(AVATAR)
    except (json.JSONDecodeError, AttributeError):
        return None
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        return None
    return max(0, min(100, int(val)))


def write_focus(root: Path, cid: str, vid: str, focus: int, base: str = "characters") -> None:
    if not (safe_id(cid) and safe_id(vid)):
        raise ValueError("unsafe image id")
    d = _dir(root, cid, vid, base)
    d.mkdir(parents=True, exist_ok=True)
    atomic.write_text(d / FOCUS_FILE, json.dumps({AVATAR: max(0, min(100, int(focus)))}))


def clear_focus(root: Path, cid: str, vid: str, base: str = "characters") -> None:
    if not (safe_id(cid) and safe_id(vid)):
        return
    p = _dir(root, cid, vid, base) / FOCUS_FILE
    if p.exists():
        p.unlink()


def drop_sidecar_entry(d: Path, filename: str, key: str) -> None:
    """Remove `key` from a ``{name: value}`` sidecar in `d`, if it is there.

    Deleting an image has to take its sidecar entries with it, and this module
    is where deletion happens. It cannot call the sidecar's own module to do it:
    `image_descriptions` enumerates its directory through `assets.list_in`, so
    the import would be a cycle -- and a deferred import to dodge that is
    exactly what `tests/test_import_guard.py` exists to refuse.

    So the split is by *layer*, not by file: this drops a key from a flat JSON
    mapping, knowing nothing about what the values mean, and the owning module
    keeps every rule about them. Removing the KEY rather than blanking the value
    is the point -- for descriptions an absent key means "never reviewed", which
    is what a name with no image behind it now is.

    Silent on a missing, garbled or unwritable sidecar, matching `clear_focus`:
    this runs *after* the bytes are gone, and an image deletion must not fail
    over the file that annotates it.
    """
    p = d / filename
    if not p.exists():
        return
    try:
        cur = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return
    if not isinstance(cur, dict) or key not in cur:
        return
    del cur[key]
    try:
        atomic.write_text(p, json.dumps(cur, indent=2, sort_keys=True) + "\n")
    except OSError:
        pass


def put_image(root: Path, cid: str, vid: str, name: str, data: bytes, ext: str,
              base: str = "characters") -> str:
    if not (safe_id(cid) and safe_id(vid)):
        raise ValueError("unsafe image id")
    ext = put_in(_dir(root, cid, vid, base), name, data, ext)
    if name == AVATAR:
        clear_focus(root, cid, vid, base)
    return ext


def delete_image(root: Path, cid: str, vid: str, name: str, base: str = "characters") -> None:
    if not (safe_id(cid) and safe_id(vid) and _safe_name(name)):
        return
    d = _dir(root, cid, vid, base)
    delete_in(d, name)
    # The description goes with the bytes, the way the crop already does below:
    # a re-upload under this name is different art and must inherit neither.
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

    Publish each side through ``put_image``; do not shuffle the two files
    through a temp name. This used to be three renames through a fixed
    ``promote-tmp<ext>`` (#253): each rename was atomic, the *sequence* was
    not, so a crash after the second one left the promoted image parked under a
    name nothing ever looks for and **no avatar at all** -- silent, never
    self-healing, and the fixed temp name meant two concurrent promotions in
    one process fought over one path. No amount of write atomicity fixes that;
    every individual step already succeeded.

    Republishing removes the temp, and with it the interval in which the avatar
    is unresolvable: the avatar slot holds either the old image or the new one
    at every instant, and each ``put_image`` writes before it drops the
    other-extension sibling it replaces. The residue of a crash between the two
    publishes is a duplicate rather than a hole -- the promoted image is the
    avatar, and its gallery slot still holds a copy of it instead of receiving
    the demoted one. Keeping *both* copies across a crash would need a third
    slot (a same-extension swap cannot avoid one) and therefore a temp-file
    recovery protocol; a duplicate image is worth less than that.

    Both locks are held across the whole swap, so an upload to either slot
    cannot interleave with it. What that does *not* buy is an atomic two-slot
    swap: reads take no locks anywhere in this module, so a concurrent reader
    between the two publishes sees the promoted image in both slots (PR
    review). Same as before this change -- except the old sequence showed such
    a reader no avatar at all, which is the failure worth closing.
    """
    if name == AVATAR:
        return
    if not (safe_id(cid) and safe_id(vid) and _safe_name(name)):
        raise FileNotFoundError(name)  # no logical image can live under such a name
    d = _dir(root, cid, vid, base)
    with _image_locks_held(d, name, AVATAR):
        src = image_path(root, cid, vid, name, base)
        if src is None:
            raise FileNotFoundError(name)
        cur = image_path(root, cid, vid, AVATAR, base)
        # Both extensions are checked before anything is written: put_image
        # rejects a non-allowlisted one, and discovering that halfway through
        # would leave a half-swap. Only an externally-placed file can have one.
        for p in (src, cur):
            if p is not None and not _norm_ext(p.suffix):
                raise ValueError(f"unsupported image type: {p.name}")
        promoted = (src.read_bytes(), src.suffix)
        demoted = (cur.read_bytes(), cur.suffix) if cur is not None else None
        put_image(root, cid, vid, AVATAR, promoted[0], promoted[1], base)
        if demoted is None:
            # Nothing to swap back in, so the promoted image has to LEAVE this
            # slot, matching the rename this replaced. `delete_image` swallows
            # unlink failures by design (a lost cleanup self-heals there), but
            # here the unlink IS the operation: `overlay.promote_image` reads
            # this slot's emptiness to decide whether to tombstone an inherited
            # image, so a silently-kept source becomes a visible duplicate.
            # Confirm it, rather than report a move that did not happen.
            delete_image(root, cid, vid, name, base)
            if image_path(root, cid, vid, name, base) is not None:
                raise OSError(f"promoted image could not be cleared: {name}")
        else:
            put_image(root, cid, vid, name, demoted[0], demoted[1], base)
    clear_focus(root, cid, vid, base)
