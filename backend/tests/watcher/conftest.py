"""Fixtures for the FileWatcher tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.event_bus import Event, EventBus
from grimoire.library.config import LibraryConfig, LibraryIndexingConfig
from grimoire.state_store import StateStore
from grimoire.storage import Database, apply_migrations
from grimoire.watcher import FileWatcher


@pytest.fixture
async def store(tmp_path: Path):
    data_root = tmp_path / "data"
    (data_root / "library").mkdir(parents=True)
    (data_root / "campaigns").mkdir(parents=True)
    db = Database(tmp_path / "campaigns.sqlite", pool_size=2)
    await db.connect()
    await apply_migrations(db)
    s = StateStore(db, data_root)
    try:
        yield s
    finally:
        await db.close()


@pytest.fixture
async def bus() -> EventBus:
    return EventBus()


@pytest.fixture
async def watcher(store: StateStore, bus: EventBus):
    config = LibraryConfig(indexing=LibraryIndexingConfig(embed_on_index=True))
    w = FileWatcher(data_root=store.data_root, store=store, bus=bus, config=config)
    yield w
    await w.stop()


class EventCollector:
    """Subscribes to the bus and stores every event for assertion."""

    def __init__(self, bus: EventBus, *event_types: str) -> None:
        self.events: list[Event] = []
        for event_type in event_types:
            bus.subscribe(event_type, self._on_event)

    async def _on_event(self, event: Event) -> None:
        self.events.append(event)

    def of_type(self, event_type: str) -> list[Event]:
        return [e for e in self.events if e.type == event_type]
