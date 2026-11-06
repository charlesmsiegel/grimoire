"""Dismissing a kept version (028): the supported way to say "I have seen the other device's version".

Keep-both delivers the peer's version of a conflict under ``.conflicts/``, and 019 delivers it
again whenever its slot is empty, so deleting a kept file by hand never sticks. ``dismiss`` records
the decision instead, in the pair's kept-version record (``keeps``). Like every 019 decision it is
made from bytes, never from timestamps:

- only a version a **sync wrote here from the peer**, of a path still in conflict, can be
  dismissed: the record lists those etags,
  recorded in the commit that wrote the keep, and only for the first copy of those bytes at the
  path. This device's own version is refused, and so is any version ``resolve`` held here (perhaps
  its only copy anywhere), which every pair's record lists apart as held: a slot a sync merely
  found standing, or a second copy beside one, is never recorded as delivered;
- a sync never keeps a dismissed version for that device again while the conflict stands on the
  archive base this device's archive held when it was dismissed, and reports it in
  ``Conflict.dismissed``; a **different** version from the peer (other bytes) is kept as usual;
- it never touches the other device: the peer's file stays its canonical record, the conflict is
  still reported until a human ends it, and only this device's keep is withheld;
- a dismissal ends with its conflict: once a completed sync finds the path no longer in conflict,
  or its base moved, or ``resolve`` ends it, the dismissal is dropped, so the same bytes in a later
  conflict are kept again.

The record is written in one 010 transaction, authorized like a keep: ``APPLY`` on the kept
version's own address (``kept/<hash16>/<digest12>/<name>``), so a handle that may not write a
kept version may not dismiss one either, and an approval policy applies as it does to any write.
It is journaled as ``.sync`` state, so a crash leaves it either written or not. The kept files
themselves are not deleted (010 commits never delete); ``Dismissal.slots`` lists the ones holding
those bytes: bytes a peer's sync delivered, held by no ``resolve`` here, so deleting them loses
nothing the peer did not send.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from grimoire.store import LibraryStore, NameCollision, StaleWrite, StoreError, WriteConflict, WriteDenied, etag
from grimoire.store.jsonfmt import MAX_FILE_BYTES, is_etag
from grimoire.store.layout import kept_address

from .documents import KeptEntry, check_device_id, keeps_name, render_keeps, syncable
from .errors import DismissRefused, SyncInterrupted
from .keeps import held_anywhere, kept_slots, read_keeps
from .identity import device_of


@dataclass(frozen=True)
class Dismissal:
    """What ``dismiss`` recorded: ``wrote`` is false when it was already dismissed. ``slots`` are the
    kept-version files on this device that hold the dismissed bytes: the peer's version as a sync
    wrote it, never a hold, so the app may delete them, and no sync writes them again."""

    peer: str
    path: str
    etag: str
    slots: tuple[str, ...]
    wrote: bool


def dismiss(store: LibraryStore, peer: str, path: str, kept: str, handle: object,
            approvals: Mapping[str, str] | None = None) -> Dismissal:
    """Never keep the version of ``path`` whose etag is ``kept`` on this device again, from ``peer``.

    Refused (``DismissRefused``, nothing written): a ``peer`` that is not another device, a path
    that is not a synchronized record that can have kept versions (a blob never has one), an etag
    that is not one, this device's own current version of ``path``, any version no sync delivered
    here from ``peer``, a path no longer in conflict with ``peer`` (as the last sync found it, or
    since ``resolve`` ended it), any version ``resolve`` held here (by
    any pair's record, or possibly: one this device cannot read as valid), a record that would
    exceed 010's file cap, or a write the handle may not make. A record that is unreadable stops it
    (``SyncInterrupted``).
    """
    device = device_of(store)
    try:
        check_device_id(peer)
    except ValueError as error:
        raise DismissRefused(str(error)) from None
    if peer == device:
        raise DismissRefused(f"{peer} is this device; a kept version comes from another device")
    kind = syncable(path)
    if kind is None or kind == "blob":
        raise DismissRefused(f"{path} is not a synchronized record with kept versions")
    if not is_etag(kept):
        raise DismissRefused("kept must be the etag of the kept version's bytes")
    parts = tuple(path.split("/"))
    try:
        mine = store.read_exact(path)
    except (NameCollision, StoreError) as error:
        raise DismissRefused(f"{path} cannot be read here: {error}") from error
    if mine is not None and etag(mine) == kept:
        raise DismissRefused(f"{kept} is this device's own version of {path}, not a kept one")
    devices: tuple[str, str] = tuple(sorted((device, peer)))  # type: ignore[assignment]
    view = read_keeps(store, peer, devices)
    entry = view.entries.get(path) if view.state == "valid" else None
    if entry is None or kept not in entry.delivered:
        raise DismissRefused(f"{kept} is not a version of {path} that a sync kept here from {peer}")
    if held_anywhere(store, device, path, kept):  # another pair's resolve held the same bytes here
        raise DismissRefused(f"{kept} is (or may be) a version of {path} that resolve held on this device, "
                             "perhaps its only copy; it is never dismissed")
    if entry.base is None:  # the devices agree again: a kept copy may be the last copy of that version
        raise DismissRefused(f"{path} is no longer in conflict with {peer}; its kept versions are not dismissed")
    slots = kept_slots(store, path, kept)
    if kept in entry.dismissed:
        return Dismissal(peer, path, kept, slots, False)
    entries = {**view.entries, path: KeptEntry(entry.base, entry.delivered, entry.dismissed | {kept}, entry.held)}
    try:
        data = render_keeps(devices, entries)
    except StoreError as error:  # larger than any reader accepts
        raise DismissRefused(f"the kept-version record would be too large: {error}") from error
    if len(data) > MAX_FILE_BYTES:
        raise DismissRefused("the kept-version record would exceed the file size limit")
    txn = store.transaction()
    txn.put_sync_state(keeps_name(peer), data, None if view.data is None else etag(view.data),
                       authorize=kept_address(parts, kept))
    try:
        txn.commit(handle, dict(approvals or {}))
    except WriteDenied as error:
        raise DismissRefused(f"dismissing {path} was not authorized: {error}") from error
    except (StaleWrite, WriteConflict) as error:  # a sync rewrote the record meanwhile: nothing was written
        raise SyncInterrupted(f"the dismissal record changed while it was written; try again: {error}") from error
    return Dismissal(peer, path, kept, slots, True)

