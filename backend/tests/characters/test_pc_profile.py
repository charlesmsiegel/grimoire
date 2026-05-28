"""Tests for PC profile path helpers, I/O, and rendering."""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.characters import CharactersService
from grimoire.characters.pc_profile import (
    PCProfile,
    list_pc_profile_revisions,
    read_pc_profile,
    read_pc_profile_revision,
    write_pc_profile,
)
from grimoire.characters.views import render_full_pc
from grimoire.library import LibraryService
from grimoire.mechanics import MechanicsConfig, MechanicsService
from grimoire.state_store import StateStore
from grimoire.state_store.paths import pc_profile_path, pc_profile_revisions_dir
from grimoire.storage import Database
from grimoire.testing.db_template import stamp_migrated_db
from grimoire.types.characters import (
    Character,
    CharacterData,
    CharacterRole,
    VoiceAnchor,
)
from grimoire.types.mechanics import Capability

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def test_pc_profile_path(tmp_path: Path) -> None:
    result = pc_profile_path(tmp_path, "camp-1", "alistair")
    assert result == tmp_path / "campaigns" / "camp-1" / "characters" / "alistair" / "profile.md"


def test_pc_profile_revisions_dir(tmp_path: Path) -> None:
    result = pc_profile_revisions_dir(tmp_path, "camp-1", "alistair")
    assert result == tmp_path / "campaigns" / "camp-1" / "characters" / "alistair" / "revisions"


# ---------------------------------------------------------------------------
# PCProfile model and I/O
# ---------------------------------------------------------------------------


def test_pc_profile_defaults() -> None:
    profile = PCProfile(character_ref="library:worlds/wod/characters/alistair")
    assert profile.goals == []
    assert profile.player_notes == ""
    assert profile.description == ""
    assert profile.updated_at is not None


def test_write_and_read_profile(tmp_path: Path) -> None:
    profile = PCProfile(
        character_ref="library:worlds/wod/characters/alistair",
        goals=["Find the lost artifact", "Protect the chantry"],
        player_notes="Lean into the mentor archetype.",
        description="A Tremere elder, calm and clinical.",
    )
    write_pc_profile(tmp_path, "camp-1", "alistair", profile)
    target = pc_profile_path(tmp_path, "camp-1", "alistair")
    assert target.exists()

    loaded = read_pc_profile(tmp_path, "camp-1", "alistair")
    assert loaded is not None
    assert loaded.character_ref == "library:worlds/wod/characters/alistair"
    assert loaded.goals == ["Find the lost artifact", "Protect the chantry"]
    assert loaded.player_notes == "Lean into the mentor archetype."
    assert loaded.description == "A Tremere elder, calm and clinical."


def test_read_missing_profile(tmp_path: Path) -> None:
    result = read_pc_profile(tmp_path, "camp-1", "nobody")
    assert result is None


def test_write_creates_revision(tmp_path: Path) -> None:
    profile_v1 = PCProfile(
        character_ref="library:worlds/wod/characters/alistair",
        goals=["Original goal"],
        description="Version one.",
    )
    write_pc_profile(tmp_path, "camp-1", "alistair", profile_v1)

    profile_v2 = PCProfile(
        character_ref="library:worlds/wod/characters/alistair",
        goals=["Updated goal"],
        description="Version two.",
    )
    write_pc_profile(tmp_path, "camp-1", "alistair", profile_v2)

    revisions = list_pc_profile_revisions(tmp_path, "camp-1", "alistair")
    assert len(revisions) == 1
    assert revisions[0].description == "Version one."
    assert revisions[0].goals == ["Original goal"]


def test_read_specific_revision(tmp_path: Path) -> None:
    profile_v1 = PCProfile(
        character_ref="library:worlds/wod/characters/alistair",
        goals=["Original goal"],
        description="Version one.",
    )
    write_pc_profile(tmp_path, "camp-1", "alistair", profile_v1)

    profile_v2 = PCProfile(
        character_ref="library:worlds/wod/characters/alistair",
        goals=["Updated goal"],
        description="Version two.",
    )
    write_pc_profile(tmp_path, "camp-1", "alistair", profile_v2)

    revisions = list_pc_profile_revisions(tmp_path, "camp-1", "alistair")
    assert len(revisions) == 1

    loaded = read_pc_profile_revision(tmp_path, "camp-1", "alistair", revisions[0].timestamp)
    assert loaded is not None
    assert loaded.description == "Version one."


def test_first_write_no_revision(tmp_path: Path) -> None:
    profile = PCProfile(
        character_ref="library:worlds/wod/characters/alistair",
        goals=["First goal"],
        description="First version.",
    )
    write_pc_profile(tmp_path, "camp-1", "alistair", profile)
    revisions = list_pc_profile_revisions(tmp_path, "camp-1", "alistair")
    assert len(revisions) == 0


# ---------------------------------------------------------------------------
# Card rendering — render_full_pc
# ---------------------------------------------------------------------------


def _make_character(
    description: str = "A Tremere elder.",
    body: str = "",
) -> Character:
    return Character(
        id="alistair",
        name="Alistair",
        role=CharacterRole.PC,
        tags=["vampire", "tremere"],
        voice=VoiceAnchor(summary="Crisp and formal."),
        description=description,
        body=body,
    )


def test_render_full_pc_with_profile_and_capabilities() -> None:
    char = _make_character(description="A Tremere elder.")
    profile = PCProfile(
        character_ref="library:worlds/wod/characters/alistair",
        goals=["Find the lost artifact", "Protect the chantry"],
        player_notes="Lean into the mentor archetype.",
        description="Campaign-specific backstory details.",
    )
    capabilities = [
        Capability(
            id="wod.dominate.2",
            name="Dominate",
            kind="discipline",
            description="Mental domination",
        ),
        Capability(
            id="wod.auspex.1",
            name="Auspex",
            kind="discipline",
            description="Heightened senses",
        ),
    ]
    result = render_full_pc(char, profile=profile, capabilities=capabilities)
    assert "# Alistair" in result
    assert "A Tremere elder." in result
    assert "## Campaign Context" in result
    assert "Campaign-specific backstory details." in result
    assert "## Goals" in result
    assert "- Find the lost artifact" in result
    assert "- Protect the chantry" in result
    assert "## Capabilities" in result
    assert "Dominate" in result
    assert "Auspex" in result
    assert "## Voice" in result
    assert "## Player Notes" in result
    assert "Lean into the mentor archetype." in result


def test_render_full_pc_empty_library_desc_uses_profile_as_primary() -> None:
    char = _make_character(description="", body="")
    profile = PCProfile(
        character_ref="library:worlds/wod/characters/alistair",
        description="This is the primary description from the profile.",
    )
    result = render_full_pc(char, profile=profile, capabilities=[])
    assert "This is the primary description from the profile." in result
    assert "## Campaign Context" not in result


def test_render_full_pc_no_profile_no_capabilities() -> None:
    char = _make_character(description="A Tremere elder.")
    result = render_full_pc(char, profile=None, capabilities=[])
    assert "# Alistair" in result
    assert "A Tremere elder." in result
    assert "## Campaign Context" not in result
    assert "## Goals" not in result
    assert "## Capabilities" not in result
    assert "## Player Notes" not in result


def test_render_full_pc_section_ordering() -> None:
    """Verify the rendered card follows the spec ordering."""
    char = _make_character(
        description="Library desc.",
        body="# Background\nBackstory here.",
    )
    profile = PCProfile(
        character_ref="library:worlds/wod/characters/alistair",
        goals=["Goal A"],
        player_notes="Meta notes.",
        description="Campaign context.",
    )
    capabilities = [
        Capability(id="wod.dominate.2", name="Dominate", kind="discipline"),
    ]
    result = render_full_pc(char, profile=profile, capabilities=capabilities)
    desc_pos = result.index("Library desc.")
    ctx_pos = result.index("## Campaign Context")
    goals_pos = result.index("## Goals")
    cap_pos = result.index("## Capabilities")
    voice_pos = result.index("## Voice")
    notes_pos = result.index("## Player Notes")
    assert desc_pos < ctx_pos < goals_pos < cap_pos < voice_pos < notes_pos


def test_render_full_pc_profile_with_no_content() -> None:
    char = _make_character(description="A Tremere elder.")
    profile = PCProfile(
        character_ref="library:worlds/wod/characters/alistair",
    )
    result = render_full_pc(char, profile=profile, capabilities=[])
    assert "## Campaign Context" not in result
    assert "## Goals" not in result
    assert "## Player Notes" not in result


# ---------------------------------------------------------------------------
# Integration: get_full_card with profile
# ---------------------------------------------------------------------------


@pytest.fixture
async def store_for_service(tmp_path: Path):
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
async def characters_svc(
    store_for_service: StateStore,
    tmp_path: Path,
) -> CharactersService:
    library = LibraryService(store_for_service)
    mech_root = tmp_path / "mechanics"
    mech_root.mkdir()
    mechanics = MechanicsService(
        config=MechanicsConfig(root=mech_root),
        state_store=store_for_service,
    )
    return CharactersService(library, mechanics)


async def _setup_character_with_profile(store: StateStore, characters: CharactersService) -> str:
    await store.write_library_file(
        library_id="worlds/wod/world/wod",
        frontmatter={"id": "wod", "name": "WoD", "version": 1},
        body="",
        source="test",
    )
    await store.upsert_campaign(campaign_id="camp-1", name="Test Campaign")
    await store.upsert_world_ref(
        campaign_id="camp-1",
        world_id="wod",
        priority=1,
        include=None,
        track_latest=True,
    )
    data = CharacterData(
        id="alistair",
        name="Alistair",
        role=CharacterRole.PC,
        description="A Tremere elder.",
        voice=VoiceAnchor(summary="Formal."),
    )
    await characters.create("wod", data)
    ref = "library:worlds/wod/characters/alistair"
    await characters.add_pc("camp-1", ref, "Alistair", "local")

    profile = PCProfile(
        character_ref=ref,
        goals=["Find the artifact"],
        player_notes="Keep it dark.",
        description="Campaign-specific context.",
    )
    write_pc_profile(store.data_root, "camp-1", "alistair", profile)
    return ref


async def test_get_full_card_includes_profile(
    characters_svc: CharactersService, store_for_service: StateStore
) -> None:
    ref = await _setup_character_with_profile(store_for_service, characters_svc)
    card = await characters_svc.get_full_card(ref, "camp-1")
    assert "## Campaign Context" in card
    assert "Campaign-specific context." in card
    assert "## Goals" in card
    assert "- Find the artifact" in card
    assert "## Player Notes" in card
    assert "Keep it dark." in card


# ---------------------------------------------------------------------------
# Service CRUD methods
# ---------------------------------------------------------------------------


async def test_service_get_pc_profile(
    characters_svc: CharactersService, store_for_service: StateStore
) -> None:
    ref = await _setup_character_with_profile(store_for_service, characters_svc)
    profile = await characters_svc.get_pc_profile("camp-1", ref)
    assert profile is not None
    assert profile.goals == ["Find the artifact"]
    assert profile.player_notes == "Keep it dark."


async def test_service_save_pc_profile(
    characters_svc: CharactersService, store_for_service: StateStore
) -> None:
    ref = await _setup_character_with_profile(store_for_service, characters_svc)
    new_profile = PCProfile(
        character_ref=ref,
        goals=["New goal"],
        player_notes="Updated notes.",
        description="Updated description.",
    )
    await characters_svc.save_pc_profile("camp-1", ref, new_profile)
    loaded = await characters_svc.get_pc_profile("camp-1", ref)
    assert loaded is not None
    assert loaded.goals == ["New goal"]


async def test_service_list_revisions(
    characters_svc: CharactersService, store_for_service: StateStore
) -> None:
    ref = await _setup_character_with_profile(store_for_service, characters_svc)
    new_profile = PCProfile(
        character_ref=ref,
        goals=["Updated goal"],
        description="V2.",
    )
    await characters_svc.save_pc_profile("camp-1", ref, new_profile)
    revisions = await characters_svc.list_pc_profile_revisions("camp-1", ref)
    assert len(revisions) >= 1


async def test_service_get_profile_missing_returns_none(
    characters_svc: CharactersService, store_for_service: StateStore
) -> None:
    await store_for_service.upsert_campaign(
        campaign_id="camp-empty",
        name="Empty",
    )
    ref = "library:worlds/wod/characters/nobody"
    profile = await characters_svc.get_pc_profile("camp-empty", ref)
    assert profile is None
