"""What a 035 played turn needs on a device before sync lands it there (028's turn merge, docs/SYNC.md).

A turn is a mutable record changed by compare-and-swap, so it follows the ordinary decision rows. It is one
turn of one chat's tree (``grimoire.scene.branches``), so beside what any campaign record needs (the actors it
names that the origin holds, the revisions its lines pin), a turn never lands without its chat record and the
campaign's play scope (a turn is history of one chat, and a later chat given that id would inherit it), the
turn it was played after (``follows``), and the turn a set-aside mark names as going on instead (``chosen``):
without them the receiver could not place it, and would read a gap or a choice that names nothing. These are
needed on the receiver whether or not the origin still holds them. A turn written before ``follows`` existed
is placed by the turn before it of its own device, else the untagged one, else the only one
(``branches.parent``): the one the origin places it after is needed too (``turn_named``), so it never lands
alone beside another device's turn of that number. Read structurally from the payload, without decoding the
turn (an unreadable payload is never copied: the scan blocks it).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from grimoire.core.identity import ChatRef, ScopeRef, TurnRef
from grimoire.scene.model import parse_turn_id
from grimoire.store import LibraryStore, StoreError, UnstorableIdentifier
from grimoire.store.layout import chat_parts, scope_parts, turn_parts
from grimoire.store.replica import parse_exact

from .bookkeeping import read_document
from .documents import KeepsView, named_paths, syncable
from .errors import NoDeviceIdentity, SyncInterrupted
from .identity import device_of
from .keeps import kept_slots, other_peers, read_keeps


def _payload(path: str, data: bytes | None) -> Mapping | None:  # type: ignore[type-arg]
    if data is None or syncable(path) != "turn":
        return None
    payload = getattr(parse_exact(tuple(path.split("/")), data), "payload", None)
    return payload if isinstance(payload, Mapping) else None


def turn_needs(path: str, data: bytes | None) -> list[str]:
    """The paths a turn at ``path`` needs on its receiver (nothing for any other record)."""
    payload = _payload(path, data)
    if payload is None:
        return []
    campaign = path.split("/")[1]
    needs = [_spell(lambda: scope_parts(ScopeRef(campaign)))]
    chat = payload.get("chat")
    if isinstance(chat, str):
        needs.append(_spell(lambda: chat_parts(ChatRef(campaign, chat))))
    mark = payload.get("set_aside")
    for turn in (payload.get("follows"), mark.get("chosen") if isinstance(mark, Mapping) else None):
        if isinstance(turn, str):
            needs.append(_spell(lambda turn=turn: turn_parts(TurnRef(campaign, turn))))
    # text that spells no path a library can store names nothing (as for 107's named actors): the turn itself is
    # then one this version cannot decode, which the receiver reports as a damaged turn of that chat
    return [need for need in needs if need is not None and need != path]


def turn_named(path: str, data: bytes | None, origin) -> list[str]:  # type: ignore[no-untyped-def]
    """For a turn written before ``follows`` existed, the turn the origin places it after (``origin``: its
    ``replica`` scan), by ``branches.parent``'s rule over file names: the one before it of its own device,
    else the untagged one, else the only one. Short of the first two, every turn of that number the origin has
    (readable or blocked) is needed, so the receiver places it as the origin does: after the only one, or
    nowhere (a gap on both) when there are several. A blocked file counts as present, so the turn waits."""
    payload = _payload(path, data)
    folder, found = path.rpartition("/")[0], _turn_name(path)
    if payload is None or found is None or payload.get("follows") is not None or found[1] < 2:
        return []
    chat, number, device = found

    def there(candidate: str) -> bool:
        return candidate in origin.files or origin.blocked_reason(candidate) is not None
    for tag in (device, None):
        candidate = f"{folder}/{chat}.{number - 1:06d}{'' if tag is None else '-' + tag}.json"
        if there(candidate):
            return [candidate]
    return sorted({other for other in (*origin.files, *origin.blocked) if other.rpartition("/")[0] == folder
                   and (_turn_name(other) or ())[:2] == (chat, number - 1)})


def _turn_name(path: str) -> tuple[str, int, str | None] | None:
    """``(chat_id, number, device)`` of a turn file's name, or ``None``."""
    name = path.rpartition("/")[2]
    return parse_turn_id(name[:-len(".json")]) if name.endswith(".json") else None


def open_conflicts(store: LibraryStore, folder: str, *, ended: Callable[[str], bool] = lambda peer: False,
                   ) -> tuple[dict[str, tuple[bytes, ...]], bool]:
    """The files under ``folder`` that a pair's last sync found in conflict on this device and that no
    ``resolve`` or later sync has ended (019's keep-both, as this device's kept-version records hold it),
    each with the peer's versions kept here that can be read; and whether the records are complete.

    A path is in conflict with a peer while that pair's entry names a version of it that this device
    has not dismissed: the one the last sync kept (``open``, whatever the conflict's base and whoever's
    keep first wrote those bytes here), or one a sync delivered in the conflict that stands. Dismissing
    every such version is this device's local "keep mine": that pair's conflict no longer holds the path
    here, though the next sync with the peer still reports it (019). A peer whose pairing was ``ended``
    (``unpair``) holds nothing. ``False`` when a record of a peer that still counts cannot be honoured
    (damaged, or unreadable) and that pair may have synced ``folder``'s campaign (its archive, or the
    record itself, names a file of it, or the archive cannot be read): any path may be in conflict then,
    so callers fail closed. A library that never synced has none."""
    try:
        device = device_of(store)
    except NoDeviceIdentity:
        return {}, True
    except SyncInterrupted:
        return {}, False
    found: dict[str, tuple[bytes, ...]] = {}
    complete = True
    try:
        peers = other_peers(store, device)
    except (StoreError, OSError, SyncInterrupted):
        return {}, False
    for peer in peers:
        if ended(peer):
            continue
        devices: tuple[str, str] = tuple(sorted((device, peer)))  # type: ignore[assignment]
        try:
            view = read_keeps(store, peer, devices)
        except (StoreError, OSError, ValueError, SyncInterrupted):  # an unreadable ``.sync`` file
            view = None
        if view is None or view.state == "ignored":
            complete = complete and not _synced(store, peer, devices, folder, view)
            continue
        for path, entry in (view.entries.items() if view.state == "valid" else ()):
            standing = (({entry.open} - {None}) | entry.delivered) - entry.dismissed
            if not standing or not path.startswith(folder + "/"):
                continue
            kept = list(found.get(path, ()))
            for tag in sorted(standing):  # type: ignore[type-var]
                slots, unreadable = kept_slots(store, path, tag)  # type: ignore[arg-type]
                complete = complete and not unreadable
                kept += [data for slot in slots if (data := _read(store, slot)) is not None]
            found[path] = tuple(kept)
    return found, complete


def _synced(store: LibraryStore, peer: str, devices: tuple[str, str], folder: str, view: KeepsView | None) -> bool:
    """Whether the pair with ``peer`` may have synced ``folder``'s campaign, for a kept-version record that
    cannot be honoured: its archive names a file of the campaign or cannot be honoured, or the record names one.
    A pair folder with neither (a stray or newer record of a pair that never synced it) holds no chat."""
    campaign = folder.rpartition("/")[0] + "/"
    named = named_paths(view.data) if view is not None else frozenset()
    try:
        archive = read_document(store, "archive", peer, devices)
    except (StoreError, OSError, ValueError, SyncInterrupted):
        return True
    if archive.state == "ignored":  # what it names is read best effort: it may hold anything
        return True
    return any(path.startswith(campaign) for path in (*named, *archive.entries))


def _read(store: LibraryStore, slot: str) -> bytes | None:
    try:
        return store.read_exact(slot)
    except (StoreError, OSError, ValueError):
        return None


def _spell(parts) -> str | None:  # type: ignore[no-untyped-def]
    try:
        return "/".join(parts())
    except (ValueError, TypeError, UnstorableIdentifier):
        return None


__all__ = ["open_conflicts", "turn_named", "turn_needs"]
