"""Tests for CharactersService.find_cast_ref (#464)."""

from __future__ import annotations

from grimoire.characters import CharactersService
from grimoire.characters.service import CastRef
from grimoire.state_store import StateStore
from grimoire.types.characters import CharacterData, CharacterRole


async def _seed_world(store: StateStore, world_id: str) -> None:
    await store.write_library_file(
        library_id=f"worlds/{world_id}/world/{world_id}",
        frontmatter={"id": world_id, "name": world_id, "version": 1},
        body="",
        source="test:seed",
    )


async def _bind_campaign(store: StateStore, campaign_id: str, world_id: str) -> None:
    await store.upsert_campaign(campaign_id=campaign_id, name=campaign_id)
    await store.upsert_world_ref(
        campaign_id=campaign_id, world_id=world_id, priority=1, include=None, track_latest=True
    )


async def _setup(characters: CharactersService, store: StateStore) -> str:
    await _seed_world(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")
    await characters.create(
        "wod-london", CharacterData(id="alistair", name="Alistair", role=CharacterRole.MAJOR_NPC)
    )
    await characters.create(
        "wod-london", CharacterData(id="vivienne", name="vivienne", role=CharacterRole.PC)
    )
    return "camp-1"


async def test_find_cast_ref_matches_npc_by_id(characters: CharactersService, store: StateStore):
    campaign_id = await _setup(characters, store)
    ref = await characters.find_cast_ref(campaign_id, "alistair")
    assert isinstance(ref, CastRef)
    assert ref.character_ref == "library:worlds/wod-london/characters/alistair"
    assert ref.is_pc is False
    assert ref.name == "Alistair"


async def test_find_cast_ref_matches_by_name(characters: CharactersService, store: StateStore):
    campaign_id = await _setup(characters, store)
    ref = await characters.find_cast_ref(campaign_id, "Alistair")
    assert ref is not None
    assert ref.character_ref.endswith("alistair")


async def test_find_cast_ref_flags_pc(characters: CharactersService, store: StateStore):
    campaign_id = await _setup(characters, store)
    ref = await characters.find_cast_ref(campaign_id, "vivienne")
    assert ref is not None
    assert ref.is_pc is True


async def test_find_cast_ref_unknown_returns_none(characters: CharactersService, store: StateStore):
    campaign_id = await _setup(characters, store)
    assert await characters.find_cast_ref(campaign_id, "nobody-xyz") is None
