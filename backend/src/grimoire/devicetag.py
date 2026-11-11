"""This library's device tag for new ids (028): one leaf module every allocator can import.

``chatstore`` (actors), ``possessionstore`` (items), ``scenestore`` (turns) and ``effectstore`` (accepted
events and the relations an acceptance begins) all qualify new ids with the same tag. It lives here, below
all of them, because ``chatstore`` reads the ids every other family reserves and so imports those
families: the tag cannot live in ``chatstore`` and also be imported by them (#028).
"""

from __future__ import annotations

from hashlib import sha256

from grimoire.library import LibraryPathError
from grimoire.store import LibraryStore, StoreError
from grimoire.sync.documents import DEVICE_FILE, parse_device


class DeviceIdentityUnusable(Exception):
    """This library has a ``.sync/device.json`` that cannot be read or is damaged: no new IDs."""


def device_tag(store: LibraryStore) -> str:
    """``-`` and eight hex digits naming this library's device, or ``""`` for a library with none.

    028: the first free number is only free on *this* device. Two paired devices that each create
    the same thing offline (a chat actor for one character, a unique item from one template) would
    both draw ``mara-1`` or ``longsword-1``, and 019 would then merge two different individuals
    (their knowledge, holdings, custody and evidence) into one file, or report a conflict on what
    are really two. Qualifying the number with a short digest of the device ID (``mara-3f2a91c0-1``)
    keeps the two apart without any coordination between the devices, which is the whole point of
    offline work. A library that was never given a device identity has never synced, so it keeps the
    original numbering (``mara-1``) and every existing library and test fixture reads exactly as
    before. The digest, not the ID itself, goes in the instance ID: device IDs are free-form within
    the segment rules and could be long, and an instance ID should not carry a label the user chose
    for a machine. Only new IDs change: an existing instance keeps the ID it was given.

    A damaged or unreadable identity file (a link or folder included) raises ``DeviceIdentityUnusable``
    rather than falling back to plain numbers, so a broken identity never quietly brings back the
    numbering that collides. A library whose ``.sync`` folder was deleted outright reads as never
    identified, until ``identify`` is run again.
    """
    try:
        data = store.read_sync_state(DEVICE_FILE)
    except (StoreError, LibraryPathError, OSError) as error:  # a link or folder too: never "no identity"
        raise DeviceIdentityUnusable(f"cannot read this library's device identity: {error}") from error
    if data is None:
        return ""
    device = parse_device(data)
    if device is None:
        raise DeviceIdentityUnusable("this library's device identity is damaged; repair .sync/device.json")
    return "-" + tag_of(device)


def tag_of(device: str) -> str:
    """The eight hex digits a device of that name tags its new ids with (a turn's ``device``, say), so a screen
    can say which paired device a tagged record came from. Naming only: nothing is decided by it."""
    return sha256(device.encode()).hexdigest()[:8]
