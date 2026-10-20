"""Idempotent promotion journal validation and roll-forward, with retryable writer refusal."""

from __future__ import annotations

import os
from pathlib import Path

from .. import image_hash, image_refs, locks
from . import _contracts, _locking, _lookup, _metadata, _placements


class ImageNotYetAvailableError(OSError):
    """A placement names an image whose object or blob has not arrived here
    yet (mid-sync). Not "no image": the routes answer it with the 404 an
    absent image gets, carrying this reason."""


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
    _placements._set_placement(d, _contracts.AVATAR, post[_contracts.AVATAR])
    if post[name] is not None:
        _placements._set_placement(d, name, post[name])
    else:
        image_refs.delete(d, name)
        if _lookup.path_in(d, name) is not None:
            raise OSError(f"promoted image could not be cleared: {name}")
    # `None` on either side removes that key rather than leaving the slot's
    # previous description behind to caption its new occupant.
    _metadata.edit_sidecar(d, _contracts.DESCRIPTIONS_FILE, {
        _contracts.AVATAR: desc.get(name),
        name: desc.get(_contracts.AVATAR) if post[name] is not None else None,
    })
    _metadata._clear_focus_in(d)
    image_refs.clear_journal(d)


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
    if not isinstance(name, str) or name == _contracts.AVATAR or not _contracts._safe_name(name):
        return None
    for key in ("pre", "post"):
        sides = journal.get(key)
        if not isinstance(sides, dict) or set(sides) != {_contracts.AVATAR, name}:
            return None
        if not all(v is None or (isinstance(v, str) and image_hash.is_image_id(v))
                   for v in sides.values()):
            return None
    pre, post = journal["pre"], journal["post"]
    if pre[name] is None or post != {_contracts.AVATAR: pre[name], name: pre[_contracts.AVATAR]}:
        return None   # a promotion always has a source, and `post` is `pre` swapped
    desc = journal.get("desc")
    if not isinstance(desc, dict):
        return None
    if not all(desc.get(n) is None or isinstance(desc.get(n), str) for n in (_contracts.AVATAR, name)):
        return None
    return name


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


def recover_promotion(d: Path, *, required: bool = False) -> None:
    """Finish or discard an interrupted promotion in image directory `d`.

    Ordinary readers retain best-effort recovery: no waiting or exceptions.
    A writer checking vacancy or captured tokens passes `required=True` while
    holding the avatar name stripe through its check AND publication. A valid
    journal left busy or unwritable must refuse that operation: recovery in a
    later writer could otherwise change the binding after its check succeeded.
    Malformed journals cannot replay and retain their existing read semantics.
    """
    _recover_promotion(d)
    if required:
        journal = image_refs.read_journal(d)
        if journal is not None and _journal_ok(journal) is not None:
            raise locks.StoreBusy("promotion", "image promotion")


def _recover_before_write(d: Path, name: str) -> None:
    """`_recover_promotion` for a writer about to change slot `name`, under
    that slot's lock; `OSError` when a promotion journal naming this slot is
    still there afterwards.

    The recovery never waits, so it can be left undone by a lock it could not
    take -- with striped locks, one held by a write to an unrelated name that
    shares the stripe. Writing then would move this slot out of its `pre` and
    `post` states, the next recovery would discard the journal as stale, and
    mid-swap that journal is the only record of the picture it moved out of the
    avatar slot. So the writer refuses, as `promote_image` refuses to overwrite
    a journal it could not finish, and a retry finds the swap done. A journal
    naming other slots is left to its own recovery: this write cannot make it
    stale. A malformed one is never replayed, so it blocks nothing."""
    _recover_promotion(d)
    journal = image_refs.read_journal(d)
    if journal is None:
        return
    slot = _journal_ok(journal)
    if slot is not None and name.casefold() in {slot.casefold(), _contracts.AVATAR}:
        raise OSError("an earlier promotion is still unfinished; retry")


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
    `write_focus`, `copy_slots`), under their own name lock, before they write:
    a write landing first would move a slot out of its `pre`/`post` states,
    and recovery would then discard the journal as stale -- and, mid-swap,
    with it the only record of the old avatar's picture. Since this can skip,
    those writers go through `_recover_before_write`, which refuses rather
    than write over a journal still naming their slot.
    """
    journal = image_refs.read_journal(d)
    if journal is None:
        return   # the overwhelmingly common case: one failed open, no locks
    name = _journal_ok(journal)
    if name is None:
        # Under the sidecar lock, which a promotion holds throughout, so this
        # cannot remove the journal of a promotion that has just written one.
        with _locking._locks_if_free([_locking.sidecar_lock(d, _contracts.DESCRIPTIONS_FILE)]) as free:
            if free and image_refs.read_journal(d) == journal:
                image_refs.clear_journal(d)
        return
    names = locks.image_name_locks(d, {name, _contracts.AVATAR})
    with _locking._locks_if_free([*names, _locking.sidecar_lock(d, _contracts.DESCRIPTIONS_FILE)]) as free:
        if not free or image_refs.read_journal(d) != journal:
            return   # busy, or another reader got here first
        try:
            for slot in (_contracts.AVATAR, name):
                ref = image_refs.read(d, slot)
                held = ref.image if ref is not None else None
                if held not in (journal["pre"][slot], journal["post"][slot]):
                    image_refs.clear_journal(d)
                    return
            _finish_promotion(d, name, journal)
        except (OSError, ValueError):
            pass     # a read must not fail over a repair the next one retries
