"""Synthetic sources and a deterministic chat library for Sprint 107 tests (no real library)."""

from __future__ import annotations

from grimoire.chatstore import ChatLibrary
from grimoire.core.identity import SourceRef
from grimoire.store import LibraryStore, RecordEnvelope, SourceManifest

from store_helpers import OWNER, owner_handle

CARDS, OTHER, ME, WORLD = "catalog:cards", "catalog:other", "catalog:me", "world:harbor"
MARA = SourceRef(CARDS, "character", "mara", "r1")
ANYA = SourceRef(CARDS, "character", "anya", "r1")
LORE = SourceRef(CARDS, "lorebook", "harbor-notes", "r1")
STORM = SourceRef(CARDS, "scenario", "storm")  # follows its head (r2)
FOG = SourceRef(CARDS, "scenario", "fog", "r1")
NAMESAKE = SourceRef(OTHER, "character", "mara", "r1")  # same name, another source
SAM = SourceRef(ME, "character", "sam", "r1")
DOCKS = SourceRef(WORLD, "location", "docks", "r1")


def seed_sources(store: LibraryStore) -> None:
    txn = store.transaction()
    txn.put(SourceManifest(CARDS, OWNER, "Cards", {"scenario/storm": "r2"}), None)
    txn.put(RecordEnvelope(MARA, {"bio": "a smuggler"}, name="Mara"), None)
    txn.put(RecordEnvelope(ANYA, {"bio": "a harbor pilot"}, name="Anya"), None)
    txn.put(RecordEnvelope(LORE, {"entries": ["the lighthouse is dark"]}, name="Harbor notes"), None)
    txn.put(RecordEnvelope(STORM.at("r1"), {"opening": "rain"}, name="Storm"), None)
    txn.put(RecordEnvelope(STORM.at("r2"), {"opening": "thunder"}, name="Storm"), None)
    txn.put(RecordEnvelope(FOG, {"opening": "mist"}, name="Fog"), None)
    txn.put(SourceManifest(OTHER, OWNER, "Other cards"), None)
    txn.put(RecordEnvelope(NAMESAKE, {"bio": "a different Mara"}, name="Mara"), None)
    txn.put(SourceManifest(ME, OWNER, "Personas"), None)
    txn.put(RecordEnvelope(SAM, {"bio": "the player"}, name="Sam"), None)
    txn.put(SourceManifest(WORLD, OWNER, "Harbor"), None)
    txn.put(RecordEnvelope(DOCKS, {}, name="Docks"), None)
    txn.commit(owner_handle())


class Clock:
    """Deterministic UTC timestamps, one second apart."""

    def __init__(self) -> None:
        self.ticks = 0

    def __call__(self) -> str:
        self.ticks += 1
        minutes, seconds = divmod(self.ticks, 60)
        return f"2026-10-31T10:{minutes:02d}:{seconds:02d}Z"


class Ids:
    """Deterministic ids per prefix: q-1, q-2, t-1 ..."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    def __call__(self, prefix: str) -> str:
        self.counts[prefix] = self.counts.get(prefix, 0) + 1
        return f"{prefix}-{self.counts[prefix]}"


def chat_library(store: LibraryStore, handle=None) -> ChatLibrary:
    return ChatLibrary(store, handle or owner_handle(), clock=Clock(), new_id=Ids(), owner=OWNER)
