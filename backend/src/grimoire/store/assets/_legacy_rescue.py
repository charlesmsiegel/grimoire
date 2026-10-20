"""Best-effort rescue of pre-journal promotion temps into unoccupied slots."""

from __future__ import annotations

from pathlib import Path

from .. import image_refs
from . import _contracts, _locking, _lookup


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
    used = {n.casefold() for n in _lookup.names_in(d)[0]}
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
    strays = [p for p in d.glob(f"{_contracts._PROMOTE_TMP}.*") if _contracts.norm_ext(p.suffix)]
    return max(strays, key=lambda p: (_lookup.mtime_ns(p), p.name)) if strays else None


# Bounds LOST RACES only -- a successful rename does not spend it. Budgeting
# total passes instead would let a directory holding more temps than the budget
# keep one stranded after an uncontended scan (`norm_ext` lowercases, so
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
    if not any(d.glob(f"{_contracts._PROMOTE_TMP}.*")):
        return  # the overwhelmingly common case: one glob, no locks, no writes
    retries = _HEAL_RETRIES
    while retries:
        stray = _newest_stranded(d)
        if stray is None:
            return
        slot = _heal_slot(d)
        # Both choices above were read unlocked, so lock every name they name --
        # in the module's one global order -- and only then act on them.
        with _locking._heal_locks(d, (_contracts._PROMOTE_TMP, _contracts.AVATAR, slot)) as held:
            if not held:
                return  # busy, nested or not lockable: the next scan retries
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
    ref = image_refs.read(d, _contracts.AVATAR)
    if (ref is not None and ref.image is not None) or _lookup.path_in(d, _contracts.AVATAR) is not None:
        return _free_gallery(d)
    return _contracts.AVATAR
