"""Tests for PC role_tags feature (issue #470).

Covers role_tags on Character/CharacterData models, Greeting model,
WorldMeta model, and the campaign_pcs round-trip through
CharactersService.add_pc / list_pcs.
"""

from __future__ import annotations

from grimoire.characters import CharactersService
from grimoire.library.service import _greeting_from_row, _world_meta_from_row
from grimoire.state_store import StateStore
from grimoire.types.characters import Character, CharacterData, CharacterRole, PCEntry
from grimoire.types.composition import Greeting, WorldMeta

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
        campaign_id=campaign_id,
        world_id=world_id,
        priority=1,
        include=None,
        track_latest=True,
    )


# ---------------------------------------------------------------------------
# Model construction
# ---------------------------------------------------------------------------


def test_character_role_tags_default_empty() -> None:
    c = Character(id="x", name="X", role=CharacterRole.PC)
    assert c.role_tags == []


def test_character_role_tags_populated() -> None:
    c = Character(
        id="vivienne",
        name="vivienne",
        role=CharacterRole.PC,
        role_tags=["debutante", "vivienne"],
    )
    assert c.role_tags == ["debutante", "vivienne"]


def test_character_data_role_tags() -> None:
    cd = CharacterData(
        id="vivienne",
        name="vivienne",
        role=CharacterRole.PC,
        role_tags=["debutante"],
    )
    assert cd.role_tags == ["debutante"]


def test_greeting_role_tags_default_empty() -> None:
    g = Greeting(id="g1", world_id="w1", name="G1", starting_location=None, starting_time=None)
    assert g.role_tags == []


def test_greeting_role_tags_populated() -> None:
    g = Greeting(
        id="g1",
        world_id="w1",
        name="G1",
        starting_location=None,
        starting_time=None,
        role_tags=["vivienne", "debutante"],
    )
    assert g.role_tags == ["vivienne", "debutante"]


def test_world_meta_pc_role_tags_default_empty() -> None:
    wm = WorldMeta(id="w1", name="W1")
    assert wm.pc_role_tags == []


def test_world_meta_pc_role_tags_populated() -> None:
    wm = WorldMeta(id="w1", name="W1", pc_role_tags=["vivienne", "winifred-husband"])
    assert wm.pc_role_tags == ["vivienne", "winifred-husband"]


def test_pc_entry_role_tags_default_empty() -> None:
    entry = PCEntry(character_ref="ref", name="N", owner="local")
    assert entry.role_tags == []


def test_pc_entry_role_tags_populated() -> None:
    entry = PCEntry(character_ref="ref", name="N", owner="local", role_tags=["transfer-student"])
    assert entry.role_tags == ["transfer-student"]


# ---------------------------------------------------------------------------
# Parser functions
# ---------------------------------------------------------------------------


def test_greeting_from_row_parses_role_tags() -> None:
    row = {
        "asset_id": "arrival",
        "world_id": "w1",
        "frontmatter": {
            "id": "arrival",
            "name": "Arrival",
            "role_tags": ["vivienne", "debutante"],
        },
        "body": "Welcome.",
    }
    g = _greeting_from_row(row)
    assert g.role_tags == ["vivienne", "debutante"]


def test_greeting_from_row_missing_role_tags() -> None:
    row = {
        "asset_id": "arrival",
        "world_id": "w1",
        "frontmatter": {"id": "arrival", "name": "Arrival"},
        "body": "",
    }
    g = _greeting_from_row(row)
    assert g.role_tags == []


def test_world_meta_from_row_parses_pc_role_tags() -> None:
    row = {
        "asset_id": "ashgrove-regency",
        "frontmatter": {
            "id": "ashgrove-regency",
            "name": "ashgrove Regency",
            "pc_role_tags": ["vivienne", "winifred-husband", "giselle-husband"],
        },
    }
    wm = _world_meta_from_row(row)
    assert wm.pc_role_tags == ["vivienne", "winifred-husband", "giselle-husband"]


def test_world_meta_from_row_missing_pc_role_tags() -> None:
    row = {
        "asset_id": "w1",
        "frontmatter": {"id": "w1", "name": "W1"},
    }
    wm = _world_meta_from_row(row)
    assert wm.pc_role_tags == []


# ---------------------------------------------------------------------------
# Service round-trip: add_pc with role_tags → list_pcs returns them
# ---------------------------------------------------------------------------


async def test_add_pc_with_role_tags(characters: CharactersService, store: StateStore) -> None:
    await _seed_world(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")
    await characters.create(
        "wod-london",
        CharacterData(id="vivienne", name="vivienne", role=CharacterRole.PC),
    )

    entry = await characters.add_pc(
        "camp-1",
        "library:worlds/wod-london/characters/vivienne",
        "vivienne",
        owner="local",
        role_tags=["debutante", "vivienne"],
    )
    assert entry.role_tags == ["debutante", "vivienne"]

    listed = await characters.list_pcs("camp-1")
    assert len(listed) == 1
    assert listed[0].role_tags == ["debutante", "vivienne"]


async def test_add_pc_without_role_tags(characters: CharactersService, store: StateStore) -> None:
    await _seed_world(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")
    await characters.create(
        "wod-london",
        CharacterData(id="winifred", name="winifred", role=CharacterRole.PC),
    )

    entry = await characters.add_pc(
        "camp-1",
        "library:worlds/wod-london/characters/winifred",
        "winifred",
    )
    assert entry.role_tags == []

    listed = await characters.list_pcs("camp-1")
    assert len(listed) == 1
    assert listed[0].role_tags == []


async def test_add_pc_upsert_updates_role_tags(
    characters: CharactersService, store: StateStore
) -> None:
    await _seed_world(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")
    await characters.create(
        "wod-london",
        CharacterData(id="vivienne", name="vivienne", role=CharacterRole.PC),
    )
    ref = "library:worlds/wod-london/characters/vivienne"

    await characters.add_pc("camp-1", ref, "vivienne", role_tags=["debutante"])
    await characters.add_pc("camp-1", ref, "vivienne", role_tags=["vivienne", "spy"])

    listed = await characters.list_pcs("camp-1")
    assert len(listed) == 1
    assert listed[0].role_tags == ["vivienne", "spy"]


async def test_add_pc_upsert_without_role_tags_preserves_existing(
    characters: CharactersService, store: StateStore
) -> None:
    """A legacy caller that omits role_tags must not erase stored tags.

    Both the persisted row and the returned PCEntry must reflect the
    preserved tags, so clients that update their state from the POST
    response don't temporarily lose the data.
    """
    await _seed_world(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")
    await characters.create(
        "wod-london",
        CharacterData(id="vivienne", name="vivienne", role=CharacterRole.PC),
    )
    ref = "library:worlds/wod-london/characters/vivienne"

    await characters.add_pc("camp-1", ref, "vivienne", role_tags=["debutante"])
    # Re-add without role_tags (None → preserve existing).
    returned = await characters.add_pc("camp-1", ref, "vivienne")

    assert returned.role_tags == ["debutante"]
    listed = await characters.list_pcs("camp-1")
    assert len(listed) == 1
    assert listed[0].role_tags == ["debutante"]


async def test_add_pc_upsert_with_empty_role_tags_clears(
    characters: CharactersService, store: StateStore
) -> None:
    """An explicit empty list intentionally clears stored tags."""
    await _seed_world(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")
    await characters.create(
        "wod-london",
        CharacterData(id="vivienne", name="vivienne", role=CharacterRole.PC),
    )
    ref = "library:worlds/wod-london/characters/vivienne"

    await characters.add_pc("camp-1", ref, "vivienne", role_tags=["debutante"])
    await characters.add_pc("camp-1", ref, "vivienne", role_tags=[])

    listed = await characters.list_pcs("camp-1")
    assert len(listed) == 1
    assert listed[0].role_tags == []
