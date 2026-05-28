"""Fixtures for ImageGen tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.event_bus import EventBus
from grimoire.imagegen import (
    BackendRegistry,
    ImageGenService,
    InMemoryDiffusersBackend,
)
from grimoire.state_store import StateStore
from grimoire.storage import Database
from grimoire.testing.db_template import stamp_migrated_db


@pytest.fixture
async def store(tmp_path: Path):
    data_root = tmp_path / "data"
    data_root.mkdir()
    db = Database(stamp_migrated_db(tmp_path / "campaigns.sqlite"), pool_size=2)
    await db.connect()
    s = StateStore(db, data_root)
    try:
        yield s
    finally:
        await db.close()


@pytest.fixture
async def registry() -> BackendRegistry:
    reg = BackendRegistry()
    reg.register(InMemoryDiffusersBackend())
    return reg


async def _seed_scene(store: StateStore, *, campaign_id: str, scene_id: str) -> None:
    """Insert a minimal scenes row so FK references from images hold up."""
    await store.db.execute(
        """
        INSERT INTO scenes (
          id, campaign_id, ordinal, slug, file_path,
          location_ref, in_game_start, in_game_end, pov_character_ref,
          present_character_refs, present_pc_refs, summary, running_summary,
          key_beats, tags, emotional_arc, post_count, threads_introduced,
          threads_paid_off, title, greeting_id, closed, closed_at_turn
        )
        VALUES (?, ?, 1, ?, ?, NULL, NULL, NULL, NULL,
                '[]', '[]', '', '', '[]', '[]', '', 0, '[]', '[]', '', NULL, 0, NULL)
        """,
        (
            scene_id,
            campaign_id,
            scene_id,
            f"scenes/{scene_id}.md",
        ),
    )


@pytest.fixture
async def service(store: StateStore, registry: BackendRegistry):
    bus = EventBus()
    svc = ImageGenService(
        store=store,
        registry=registry,
        default_backend_id="diffusers-memory",
        event_bus=bus,
    )
    # Seed a campaign + one referenceable scene so foreign keys are happy.
    await store.upsert_campaign(campaign_id="camp-1", name="Test")
    await _seed_scene(store, campaign_id="camp-1", scene_id="scene-1")
    try:
        yield svc, bus
    finally:
        await svc.aclose()
