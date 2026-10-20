"""Whole name and sidecar stripe sets, including nonblocking repair holds."""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from pathlib import Path

from .. import locks


def _image_lock(d: Path, name: str) -> locks.StripeLock:
    """Serialize writes to one logical image (all extensions of `name` in `d`).

    Cleanup is inherently multi-step -- publish the new extension, then remove
    the stale siblings -- and no filesystem offers an identity-conditional
    unlink, so "verify this is still the file I snapshotted, then delete it"
    has a gap no amount of care closes. Two concurrent uploads of different
    extensions could interleave through it and leave no image at all, which is
    the exact outcome the write-before-cleanup ordering exists to prevent
    (PR review). Serializing the sequence is what actually closes it.

    `locks.image_name_lock`: process-scoped (a second server on the store
    waits too), case-folded (``Gallery_1`` and ``gallery_1`` are one file on a
    case-insensitive filesystem, so they are one lock), and striped -- an
    unrelated name may share the stripe, which is a spurious wait and never a
    lost update, but does mean a thread holding this lock must not BLOCK on
    another name's outside one up-front set (`_image_locks_held`).

    Reentrant, matching ``locks.campaign_lock``: a non-reentrant lock turns
    any future same-thread nesting (a delete invoked from inside a put, say)
    into a deadlock, which is a worse failure than the race it guards. Raises
    ``StoreBusy`` past ``LOCK_TIMEOUT``.
    """
    return locks.image_name_lock(d, name)


@contextmanager
def _image_locks_held(d: Path, *names: str):
    """Hold the per-image locks of several logical images at once.

    ``promote_image`` mutates two of them (the promoted slot and the avatar
    slot), so it has to hold both for the whole swap or an upload can land in
    the middle of it.

    Taken as ONE set, all up front: the names' stripes, deduplicated (two names
    on one stripe are one acquisition) and in stripe order
    (`locks.image_name_locks`). Stripe order, not name order, because the
    stripe is the lock: two holders sorting by name could take the same two
    stripes in opposite orders. One global order is what makes every holder of
    several safe by construction rather than by review, and sorting the WHOLE
    set, never appending to a set already held, is the property that has to
    survive future edits.

    A stripe this thread takes while already holding another, outside such a
    set, is taken non-blocking (`_locks_if_free`): the read-side promotion
    recovery (`_recover_promotion`), which every writer and `image_path` enter
    under whatever their caller holds, and a heal reached nested
    (`_heal_stranded_promotion` from ``delete_image`` of the avatar, which looks
    the slot up while holding it, or from a listing under a sidecar lock).
    Blocking there could wedge against a holder that took the same two stripes
    the other way round.
    """
    with ExitStack() as stack:
        for lock in locks.image_name_locks(d, names):
            stack.enter_context(lock)
        yield


@contextmanager
def name_locks_held(*keyed: tuple[Path, str]):
    """Hold the complete name-stripe set for several directories at once.
    This is the source/destination counterpart of `_image_locks_held`;
    callers pass every name before acquiring any stripe."""
    with ExitStack() as stack:
        for lock in locks.image_name_locks_for(keyed):
            stack.enter_context(lock)
        yield


@contextmanager
def _heal_locks(d: Path, names: tuple[str, ...]):
    """Yield whether the heal holds `names`' stripes.

    From a thread holding no image stripe, it waits for them like any writer
    (`_image_locks_held`). Reached NESTED -- ``delete_image`` of the avatar
    looks the slot up while holding it, and a greeting's subject save lists its
    pictures under the sidecar lock -- it never waits (`_locks_if_free`): the
    set it needs can sort below a stripe the thread already holds, and waiting
    there wedges against a holder taking the two the other way round.

    Either way a failure to lock (busy past the timeout, an unusable lock
    directory) is "not now" rather than an error: the heal runs from reads,
    and the next scan retries."""
    if locks.image_stripes_held():
        with _locks_if_free(locks.image_name_locks(d, names)) as free:
            yield free
        return
    with ExitStack() as stack:
        try:
            stack.enter_context(_image_locks_held(d, *names))
        except (locks.StoreBusy, OSError):
            held = False
        else:
            held = True
        yield held


def sidecar_lock(d: Path, filename: str) -> locks.StripeLock:
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

    `locks.image_sidecar_lock`: a family of its own, taken after any name lock
    and never before one; process-scoped, case-folded and striped like the
    name locks (`_image_lock`). Reentrant, so a caller already holding it
    (promotion edits the sidecar inside its own image locks) pays nothing. A
    caller rewriting the sidecars of two directories takes both up front
    (`sidecar_locks_held`), never the second inside the first.
    """
    return locks.image_sidecar_lock(d, filename)


@contextmanager
def sidecar_locks_held(*keyed: tuple[Path, str]):
    """Hold the sidecar locks of several ``(directory, filename)`` pairs at
    once: deduplicated and in stripe order, all up front, as
    `_image_locks_held` holds names -- the only safe way to hold two."""
    with ExitStack() as stack:
        for lock in locks.image_sidecar_locks(keyed):
            stack.enter_context(lock)
        yield


@contextmanager
def _locks_if_free(wanted: list[locks.StripeLock]):
    """Yield whether every lock in `wanted` was free to take, holding them if
    so. Never waits: a read that repairs must not block behind the write it
    would repair, nor deadlock against a caller already holding one slot's
    lock (`delete_image` looks its slot up while holding it) -- a stripe taken
    here may sort below one the caller holds, which only a non-blocking attempt
    makes safe.

    Never raises either: a lock held elsewhere and an `OSError` from the lock
    file (an unusable lock directory) both mean "not free", and the repair is
    left for a later read. A lock listed twice is taken once."""
    unique = list({id(lk): lk for lk in wanted}.values())
    held: list[locks.StripeLock] = []
    try:
        for lk in unique:
            try:
                got = lk.acquire(blocking=False)
            except OSError:
                got = False
            if not got:
                break
            held.append(lk)
        free = len(held) == len(unique)
    except BaseException:
        for lk in reversed(held):
            lk.release()
        raise
    try:
        yield free
    finally:
        for lk in reversed(held):
            lk.release()
