"""Synthetic paired devices for 028 peer-transfer tests: two libraries, each with its own durable authority.

Nothing here reaches a real library or the network: every server listens on loopback, and the
libraries live under the test's temporary directory.
"""

from __future__ import annotations

import contextlib
import threading
from pathlib import Path

from grimoire.chats.model import Launch
from grimoire.chatstore import ChatLibrary
from grimoire.images import Crop, Placement
from grimoire.imagestore import ImageLibrary
from grimoire.peersync import PeerServer, new_pairing_key, pair
from grimoire.security import Grant, Permission, ResourcePath
from grimoire.store import LibraryStore, RecordEnvelope, SourceManifest
from grimoire.sync import identify

from chat_fixtures import ANYA, CARDS, MARA, Clock
from image_fixtures import PNG
from ledger_helpers import open_authority
from store_helpers import OWNER

DESKTOP, PHONE = "desktop", "phone"
OWNER_SCOPES = ("sources", "campaigns", "assets", "kept")


class DeviceIds:
    """Chat and scope ids that name their device (``t-phone-1``): real ids are random, so two devices
    never pick the same one; deterministic per-device ids keep that property in a test."""

    def __init__(self, device: str) -> None:
        self.device, self.counts = device, {}  # type: ignore[var-annotated]

    def __call__(self, prefix: str) -> str:
        self.counts[prefix] = self.counts.get(prefix, 0) + 1
        return f"{prefix}-{self.device}-{self.counts[prefix]}"


class Device:
    """One library, identified, bound to its durable authority, with an owner handle over its records."""

    def __init__(self, root: Path, name: str, *, create: bool = True) -> None:
        self.name = name
        self.store = LibraryStore.create(root / name) if create else LibraryStore.open(root / name)
        if create:
            identify(self.store, name)
        self.authority = open_authority(self.store)
        if create:
            for number, scope in enumerate(OWNER_SCOPES):
                self.authority.add_grant(Grant(f"owner-{number}", OWNER, frozenset({Permission.APPLY}),
                                               ResourcePath.parse(scope), OWNER))
        self.handle = self.authority.handle_for(OWNER, trusted=True)
        self.chats = ChatLibrary(self.store, self.handle, clock=Clock(), new_id=DeviceIds(name), owner=OWNER)
        self.images = ImageLibrary(self.store, self.handle, owner=OWNER)

    @classmethod
    def reopen(cls, root: Path, name: str) -> Device:
        """The same device's library, opened again (after another process served it)."""
        return cls(root, name, create=False)

    def close(self) -> None:
        self.authority.close()
        self.store.close()


def paired(root: Path) -> tuple[Device, Device, bytes]:
    desktop, phone = Device(root, DESKTOP), Device(root, PHONE)
    key = new_pairing_key()
    pair(desktop.store, desktop.authority, PHONE, key, granted_by=OWNER)
    pair(phone.store, phone.authority, DESKTOP, key, granted_by=OWNER)
    return desktop, phone, key


def seed_cards(device: Device) -> None:
    """The cards source: Mara and Anya (chat_fixtures' synthetic characters)."""
    txn = device.store.transaction()
    txn.put(SourceManifest(CARDS, OWNER, "Cards"), None)
    txn.put(RecordEnvelope(MARA, {"bio": "a smuggler"}, name="Mara"), None)
    txn.put(RecordEnvelope(ANYA, {"bio": "a harbor pilot"}, name="Anya"), None)
    txn.commit(device.handle)


def start_scene(device: Device):  # type: ignore[no-untyped-def]
    """A world-less scene: Mara's chat, her campaign actor, and a picture placed on that actor."""
    seed_cards(device)
    started = device.chats.start_chat(Launch("character", MARA)).commit()
    actor = started.actors[0]
    batch = device.images.change()
    image = batch.add_image(f"campaign:{actor.campaign_id}", "portrait", PNG, {"origin": "test"})
    batch.place(actor, "avatar", Placement("set", "avatar", image, Crop(0, 0, 5000, 5000)), None)
    batch.commit()
    return started, image


@contextlib.contextmanager
def serving(device: Device, **options):  # type: ignore[no-untyped-def]
    """Serve ``device`` on loopback from a thread for the block's duration."""
    server = PeerServer(device.store, device.authority, **options)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.close()
        thread.join(10)
