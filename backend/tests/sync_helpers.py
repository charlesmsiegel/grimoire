"""Synthetic two-device fixtures for sync tests: a desktop and a phone copy of one small library."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from grimoire.core.identity import InstanceRef
from grimoire.store import LibraryStore, RecordEnvelope
from grimoire.sync import identify, sync

from store_helpers import HARBOR, OWNER, WREN_R1, harbor_manifest, instance, owner_handle, seed, wren

DESKTOP, PHONE = "desktop", "phone"
SCOPES = ("sources", "campaigns", "kept")
WREN_A = InstanceRef("c1", "character", "wren-a")
WREN_B = InstanceRef("c2", "character", "wren-b")
CHAT = InstanceRef("c1", "chat", "harbor-talk")
PATHS = {
    "wren_a": "campaigns/c1/instances/character/wren-a.json",
    "wren_b": "campaigns/c2/instances/character/wren-b.json",
    "chat": "campaigns/c1/instances/chat/harbor-talk.json",
    "manifest": "sources/world/harbor/_source/manifest.json",
    "r1": "sources/world/harbor/_source/records/character/wren/r1.json",
    "r2": "sources/world/harbor/_source/records/character/wren/r2.json",
}


def handle():
    return owner_handle(*SCOPES)


def handles():
    return {DESKTOP: handle(), PHONE: handle()}


def chat_record(participants: Iterable[str], world: str | None = None) -> RecordEnvelope:
    """A world-optional, campaign-local chat (107's shape, synthetic and opaque to the store)."""
    payload = {"participants": list(participants), "history": ["h1", "h2"]}
    if world is not None:
        payload["world"] = world
    return RecordEnvelope(CHAT, payload, name="Harbor talk")


def put(store: LibraryStore, envelope, expected="auto") -> None:
    txn = store.transaction()
    if expected == "auto":
        try:
            expected = store.read(envelope.address).etag
        except Exception:  # noqa: BLE001 - absent
            expected = None
    txn.put(envelope, expected)
    txn.commit(handle())


def edit_instance(store: LibraryStore, ref: InstanceRef, hp: int) -> None:
    put(store, instance(ref, WREN_R1, hp=hp))


def new_revision(store: LibraryStore, revision: str, bio: str) -> None:
    txn = store.transaction()
    txn.put(wren(revision, bio), None)
    txn.put(harbor_manifest({"character/wren": revision}), store.read_source(HARBOR).etag)
    txn.commit(handle())


def make_pair(root: Path, *, ops=None, locker=None) -> tuple[LibraryStore, LibraryStore]:
    """A seeded desktop, an identical phone (synced once), both identified."""
    desktop = LibraryStore.create(root / DESKTOP, ops=ops, locker=locker)
    seed(desktop, handle())
    put(desktop, chat_record(["character:wren"]), None)
    phone = LibraryStore.create(root / PHONE, ops=ops, locker=locker)
    identify(desktop, DESKTOP)
    identify(phone, PHONE)
    sync(desktop, phone, handles())
    return desktop, phone


def canonical(store: LibraryStore) -> dict[str, bytes]:
    return dict(store.scan().files)


def kept(store: LibraryStore) -> list[bytes]:
    return sorted((store.root / path).read_bytes() for path in store.conflicts())


__all__ = [
    "CHAT", "DESKTOP", "OWNER", "PATHS", "PHONE", "WREN_A", "WREN_B", "canonical", "chat_record",
    "edit_instance", "handle", "handles", "kept", "make_pair", "new_revision", "put",
]
