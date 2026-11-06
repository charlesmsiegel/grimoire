"""Synthetic World Change Set fixtures (043): the harbor world with a guild, its hall and Wren."""

from __future__ import annotations

from pathlib import Path

from grimoire.changesets import ChangeSet
from grimoire.changesetstore import ChangeSetLibrary
from grimoire.core.identity import SourceRef
from grimoire.store import LibraryStore, RecordEnvelope, SourceManifest

from store_helpers import HARBOR, OWNER, owner_handle

GUILD = SourceRef(HARBOR, "organization", "ashen-guild")
HALL = SourceRef(HARBOR, "location", "old-hall")
DOCKS = SourceRef(HARBOR, "location", "saltmarch-docks")
WREN = SourceRef(HARBOR, "character", "wren")
OLD_HALL = str(HALL)


def record_path(ref: SourceRef) -> str:
    return f"sources/world/harbor/_source/records/{ref.kind}/{ref.entity_id}/{ref.revision}.json"


MANIFEST = "sources/world/harbor/_source/manifest.json"


def seed_world(store: LibraryStore) -> None:
    """harbor: the Ashen Guild seated at the Old Hall, and Wren; every head declared."""
    txn = store.transaction()
    txn.put(SourceManifest(HARBOR, OWNER, "Harbor",
                           {"organization/ashen-guild": "r1", "location/old-hall": "r1", "character/wren": "r1"},
                           extra={"x-notes": "kept verbatim"}), None)
    txn.put(RecordEnvelope(GUILD.at("r1"), {"seat": OLD_HALL, "motto": "ash remembers"}, name="Ashen Guild",
                           owner="human:author", extra={"x-review": {"checked": True}}), None)
    txn.put(RecordEnvelope(HALL.at("r1"), {"traits": {"spatial": {}}}, name="Old Hall"), None)
    txn.put(RecordEnvelope(WREN.at("r1"), {"bio": "a sailor"}, name="Wren"), None)
    txn.commit(owner_handle())


def library(store: LibraryStore, handle=None) -> ChangeSetLibrary:
    return ChangeSetLibrary(store, handle or owner_handle())


def move_the_guild(lib: ChangeSetLibrary, changeset_id: str = "cs-1") -> ChangeSet:
    """The proving change set: add the Saltmarch Docks and seat the guild there."""
    docks = lib.create(HARBOR, "location", "saltmarch-docks", {"traits": {"spatial": {}}}, name="Saltmarch Docks",
                       evidence=("session 12: the guild moved after the fire",))
    guild = lib.revise(HARBOR, "organization", "ashen-guild", {"seat": str(DOCKS), "motto": "ash remembers"},
                       evidence=("session 12 notes",))
    return ChangeSet(changeset_id, HARBOR, "Move the guild to the docks", "human:owner", (docks, guild),
                     rationale="The hall burned down in session 12.")


def hand_write(root: Path, relative: str, data: bytes) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
