"""Synthetic two-device fixtures for sync tests: a desktop and a phone copy of one small library."""

from __future__ import annotations

import atexit
import functools
import json
import shutil
import tempfile
from collections.abc import Iterable
from pathlib import Path

from grimoire.core.identity import InstanceRef
from grimoire.store import LibraryStore, RecordEnvelope, RecordNotFound
from grimoire.sync import identify, sync
from grimoire.testing.fileops import Tree

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
        except RecordNotFound:
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
    """A seeded desktop, an identical phone (synced once), both identified.

    Built once per test session and copied (plus opened, which is 010 recovery of a clean library)
    for each caller: the bytes are exactly what building it gives, at a fraction of the cost. A
    caller passing its own ``ops`` or ``locker`` (the fault harness) gets a fresh build through them.
    """
    if ops is not None or locker is not None:
        return _build_pair(root, ops=ops, locker=locker)
    template = _template()
    for name in (DESKTOP, PHONE):
        shutil.copytree(template / name, root / name)
    return LibraryStore.open(root / DESKTOP), LibraryStore.open(root / PHONE)


@functools.cache
def _template() -> Path:
    folder = Path(tempfile.mkdtemp(prefix="grimoire-sync-pair-"))
    atexit.register(shutil.rmtree, folder, True)
    desktop, phone = _build_pair(folder)
    desktop.close()
    phone.close()
    return folder


def _build_pair(root: Path, *, ops=None, locker=None) -> tuple[LibraryStore, LibraryStore]:
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


def _delivered(tree: Tree, key: str) -> dict[str, set[str]]:
    data = tree.get(key)
    entries = json.loads(data)["entries"] if data is not None else {}
    return {path: set(entry["delivered"]) for path, entry in entries.items()}


def _held(data: bytes | None) -> bytes | None:
    entries = json.loads(data)["entries"] if data is not None else {}
    held = {path: entry["held"] for path, entry in entries.items() if entry["held"]}
    return json.dumps(held, sort_keys=True).encode() if held else None


def converged(recovered: Tree, completed: Tree) -> str | None:
    """S9's convergence (a restart and one sync reach exactly what an uninterrupted sync reaches),
    except that a kept-version record may list fewer delivered versions (028): a keep a crash left
    unrecorded is found standing by the next sync, which is no delivery (a standing slot may be a
    hold), so it only stays undismissable. Every held version is recorded exactly the same."""
    records = {key for key in {*recovered, *completed} if key.endswith("/keeps.json")}
    for key in sorted(records):
        before, after = _delivered(recovered, key), _delivered(completed, key)
        if any(not tags <= after.get(path, set()) for path, tags in before.items()):
            return f"{key} records a delivered version the uninterrupted sync does not"
    keys = sorted(key for key in {*recovered, *completed}
                  if (_held(recovered.get(key)) != _held(completed.get(key)) if key in records
                      else recovered.get(key, "absent") != completed.get(key, "absent")))
    return f"differs from the uninterrupted sync at {keys[:4]}" if keys else None
