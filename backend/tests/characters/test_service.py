"""Tests for CharactersService.

Covers CRUD, emergent, override, resolution cascade, multi-PC coordination,
tier pinning, drift detection, compressed views, relationships, capabilities,
promotion, and search.
"""

from __future__ import annotations

from datetime import UTC, datetime

from grimoire.characters import CharactersService
from grimoire.state_store import StateStore
from grimoire.types.characters import (
    CharacterData,
    CharacterRole,
    VoiceAnchor,
)
from grimoire.types.scene import AuthorKind, Post, Scene
from grimoire.types.state import ContextTier

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_setting(store: StateStore, setting_id: str) -> None:
    await store.write_library_file(
        library_id=f"settings/{setting_id}/setting/{setting_id}",
        frontmatter={"id": setting_id, "name": setting_id, "version": 1},
        body="",
        source="test:seed",
    )


async def _bind_campaign(store: StateStore, campaign_id: str, setting_id: str) -> None:
    await store.upsert_campaign(campaign_id=campaign_id, name=campaign_id)
    await store.upsert_setting_ref(
        campaign_id=campaign_id,
        setting_id=setting_id,
        priority=1,
        include=None,
        track_latest=True,
    )


def _voice(summary: str = "Crisp, formal, archaic.") -> VoiceAnchor:
    return VoiceAnchor(
        summary=summary,
        voice_register="formal",
        samples=[
            "Indeed, I shall not be coerced by such pedestrian threats.",
            "Pray, do not test my patience further.",
            "One does not simply ignore the Tremere.",
        ],
        speech_patterns=["uses 'one' instead of 'I'"],
        dos=["formal address"],
        donts=["modern slang"],
    )


def _character_data(
    asset_id: str = "alistair",
    role: CharacterRole = CharacterRole.MAJOR_NPC,
) -> CharacterData:
    return CharacterData(
        id=asset_id,
        name=asset_id.replace("-", " ").title(),
        role=role,
        aliases=["The Tremere"],
        tags=["vampire", "tremere"],
        voice=_voice(),
        description="A Tremere elder, calm and clinical.",
        body="# Background\n\nElder of the Tremere chantry in London.",
    )


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


async def test_create_and_get_character(characters: CharactersService, store: StateStore) -> None:
    await _seed_setting(store, "wod-london")
    created = await characters.create("wod-london", _character_data())

    assert created.id == "alistair"
    assert created.name == "Alistair"
    assert created.voice.samples
    assert created.voice.voice_register == "formal"
    assert created.setting_id == "wod-london"

    fetched = await characters.get("wod-london", "alistair")
    assert fetched.id == "alistair"
    assert fetched.role == CharacterRole.MAJOR_NPC


async def test_update_character(characters: CharactersService, store: StateStore) -> None:
    await _seed_setting(store, "wod-london")
    await characters.create("wod-london", _character_data())

    updated = await characters.update("wod-london", "alistair", {"age": "452"})
    assert updated.age == "452"


async def test_list_in_setting(characters: CharactersService, store: StateStore) -> None:
    await _seed_setting(store, "wod-london")
    await characters.create("wod-london", _character_data("alistair"))
    await characters.create("wod-london", _character_data("vivienne", role=CharacterRole.PC))

    listed = await characters.list_in_setting("wod-london")
    ids = {c.id for c in listed}
    assert ids == {"alistair", "vivienne"}


async def test_delete_character(characters: CharactersService, store: StateStore) -> None:
    await _seed_setting(store, "wod-london")
    await characters.create("wod-london", _character_data())

    await characters.delete("wod-london", "alistair")
    listed = await characters.list_in_setting("wod-london")
    assert listed == []


# ---------------------------------------------------------------------------
# Emergent + override
# ---------------------------------------------------------------------------


async def test_create_emergent(characters: CharactersService, store: StateStore) -> None:
    await _seed_setting(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")

    ref = await characters.create_emergent(
        "camp-1", _character_data("the-bartender", role=CharacterRole.MINOR_NPC)
    )

    assert ref == "campaign:emergent/character/the-bartender"
    resolved = await characters.resolve(ref, "camp-1")
    assert resolved.character.id == "the-bartender"
    assert resolved.character.setting_id is None


async def test_upsert_override(characters: CharactersService, store: StateStore) -> None:
    await _seed_setting(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")
    await characters.create("wod-london", _character_data())

    ref = "library:settings/wod-london/characters/alistair"
    await characters.upsert_override(
        "camp-1", ref, {"description": "Newly turned softer in this chronicle."}
    )
    # Resolve should pick the override.
    resolved = await characters.resolve(ref, "camp-1")
    assert "softer" in resolved.character.description


# ---------------------------------------------------------------------------
# PCs and multi-PC
# ---------------------------------------------------------------------------


async def test_pc_lifecycle(characters: CharactersService, store: StateStore) -> None:
    await _seed_setting(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")
    await characters.create("wod-london", _character_data("vivienne", role=CharacterRole.PC))

    entry = await characters.add_pc(
        "camp-1", "library:settings/wod-london/characters/vivienne", "vivienne", owner="local"
    )
    assert entry.active is True

    listed = await characters.list_pcs("camp-1")
    assert len(listed) == 1
    assert listed[0].name == "vivienne"

    await characters.remove_pc("camp-1", "library:settings/wod-london/characters/vivienne")
    assert await characters.list_pcs("camp-1") == []


async def test_set_active_pc_rejects_unknown(
    characters: CharactersService, store: StateStore
) -> None:
    await _bind_campaign(store, "camp-1", "wod-london")
    try:
        await characters.set_active_pc("camp-1", "library:settings/wod/characters/ghost")
    except Exception as exc:
        assert "not a PC" in str(exc)
    else:
        raise AssertionError("expected CharactersError")


async def test_multi_pc_should_auto_respond(
    characters: CharactersService, store: StateStore
) -> None:
    await _seed_setting(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")
    pc1 = "library:settings/wod-london/characters/vivienne"
    pc2 = "library:settings/wod-london/characters/winifred"
    await characters.add_pc("camp-1", pc1, "vivienne")
    await characters.add_pc("camp-1", pc2, "winifred")

    scene = Scene(
        id="scene-1",
        campaign_id="camp-1",
        branch_id="camp-1:main",
        ordinal=1,
        slug="opening",
        file_path="scenes/0001-opening.md",
        present_pc_refs=[pc1, pc2],
        present_character_refs=[pc1, pc2],
    )

    # 2 present PCs → no auto respond
    assert await characters.should_auto_respond(scene) is False

    scene_solo = scene.model_copy(
        update={"present_pc_refs": [pc1], "present_character_refs": [pc1]}
    )
    assert await characters.should_auto_respond(scene_solo) is True


async def test_pending_pc_inputs_since_last_advance(
    characters: CharactersService,
) -> None:
    scene = Scene(
        id="scene-1",
        campaign_id="c",
        branch_id="c:main",
        ordinal=1,
        slug="s",
        file_path="x.md",
        last_advance_at_post=2,
    )
    posts = [
        Post(
            id=f"p{i}",
            scene_id="scene-1",
            order_in_scene=i,
            author_kind=AuthorKind.PC,
            body=f"line {i}",
            is_player=(i % 2 == 1),
            created_at=datetime.now(UTC),
            turn_id="t1",
        )
        for i in range(1, 6)
    ]
    pending = await characters.pending_pc_inputs_since_last_advance(scene, posts)
    # order > 2 (3,4,5) and is_player True (3,5)
    assert {p.order_in_scene for p in pending} == {3, 5}


# ---------------------------------------------------------------------------
# Per-PC scene
# ---------------------------------------------------------------------------


async def test_current_scene_for_pc(characters: CharactersService, store: StateStore) -> None:
    await _bind_campaign(store, "camp-1", "wod-london")
    ref = "library:settings/wod-london/characters/vivienne"
    assert await characters.current_scene_for_pc("camp-1", ref) is None
    await characters.set_current_scene_for_pc("camp-1", ref, "scene-7")
    assert await characters.current_scene_for_pc("camp-1", ref) == "scene-7"


# ---------------------------------------------------------------------------
# Tier
# ---------------------------------------------------------------------------


async def test_recommend_tiers(characters: CharactersService, store: StateStore) -> None:
    await _bind_campaign(store, "camp-1", "wod-london")
    scene = Scene(
        id="scene-1",
        campaign_id="camp-1",
        branch_id="camp-1:main",
        ordinal=1,
        slug="s",
        file_path="x.md",
        present_character_refs=["library:settings/wod-london/characters/alistair"],
    )
    tiers = await characters.recommend_tiers(scene)
    assert tiers["library:settings/wod-london/characters/alistair"] == ContextTier.SPOTLIGHT


async def test_pin_tier_overrides_recommendation(
    characters: CharactersService, store: StateStore
) -> None:
    await _bind_campaign(store, "camp-1", "wod-london")
    ref = "library:settings/wod-london/characters/alistair"
    await characters.pin_tier(ref, "camp-1", ContextTier.LOCK_IN)
    scene = Scene(
        id="scene-1",
        campaign_id="camp-1",
        branch_id="camp-1:main",
        ordinal=1,
        slug="s",
        file_path="x.md",
        present_character_refs=[ref],
    )
    tiers = await characters.recommend_tiers(scene)
    assert tiers[ref] == ContextTier.LOCK_IN


# ---------------------------------------------------------------------------
# Drift
# ---------------------------------------------------------------------------


async def test_drift_with_in_voice_recent(characters: CharactersService, store: StateStore) -> None:
    await _seed_setting(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")
    await characters.create("wod-london", _character_data())
    ref = "library:settings/wod-london/characters/alistair"

    posts = [
        Post(
            id="p1",
            scene_id="s",
            order_in_scene=1,
            author_kind=AuthorKind.NPC,
            author_npc_ref="alistair",
            body="Indeed, one shall not tolerate such pedestrian threats.",
            is_player=False,
            created_at=datetime.now(UTC),
            turn_id="t1",
        )
    ]
    report = await characters.check_drift(ref, "camp-1", recent_posts=posts)
    assert report.character_ref == "alistair"
    # State persisted
    state = await characters.get_state(ref, "camp-1")
    assert state.drift_score == report.drift_score


async def test_drift_with_forbidden_phrase(
    characters: CharactersService, store: StateStore
) -> None:
    await _seed_setting(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")
    await characters.create("wod-london", _character_data())
    ref = "library:settings/wod-london/characters/alistair"

    posts = [
        Post(
            id="p1",
            scene_id="s",
            order_in_scene=1,
            author_kind=AuthorKind.NPC,
            author_npc_ref="alistair",
            body="yo dude lol modern slang totally rad.",
            is_player=False,
            created_at=datetime.now(UTC),
            turn_id="t1",
        )
    ]
    report = await characters.check_drift(ref, "camp-1", recent_posts=posts)
    assert any("modern slang" in ev for ev in report.evidence)
    assert report.drift_score > 0.0


async def test_drift_corrective_context_below_threshold(
    characters: CharactersService, store: StateStore
) -> None:
    await _seed_setting(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")
    await characters.create("wod-london", _character_data())
    ref = "library:settings/wod-london/characters/alistair"

    # No recent posts → drift 0 → empty corrective.
    await characters.check_drift(ref, "camp-1", recent_posts=[])
    assert await characters.drift_corrective_context(ref, "camp-1") == ""


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


async def test_compressed_views(characters: CharactersService, store: StateStore) -> None:
    await _seed_setting(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")
    await characters.create("wod-london", _character_data())
    ref = "library:settings/wod-london/characters/alistair"

    full = await characters.get_full_card(ref, "camp-1")
    compressed = await characters.get_compressed_card(ref, "camp-1")
    voice_only = await characters.get_voice_only(ref, "camp-1")
    capsule = await characters.get_capsule(ref, "camp-1")

    assert "Alistair" in full and "## Voice" in full
    assert "Voice:" in compressed
    assert "Register: formal" in voice_only
    assert capsule.startswith("Alistair")


# ---------------------------------------------------------------------------
# Cross-setting variants
# ---------------------------------------------------------------------------


async def test_cross_setting_lookup(characters: CharactersService, store: StateStore) -> None:
    await _seed_setting(store, "wod-london")
    await _seed_setting(store, "wod-nyc")
    await characters.create("wod-london", _character_data("alistair"))
    await characters.create("wod-nyc", _character_data("alistair"))

    variants = await characters.cross_setting_lookup("alistair", exclude_setting="wod-london")
    assert [v.setting_id for v in variants] == ["wod-nyc"]


# ---------------------------------------------------------------------------
# Capabilities (null mechanics → empty)
# ---------------------------------------------------------------------------


async def test_capabilities_empty_when_null_mechanics(
    characters: CharactersService, store: StateStore
) -> None:
    await _bind_campaign(store, "camp-1", "wod-london")
    caps = await characters.capabilities_of(
        "library:settings/wod-london/characters/alistair", "camp-1"
    )
    assert caps == []


# ---------------------------------------------------------------------------
# Relationships
# ---------------------------------------------------------------------------


async def test_relationship_increment(characters: CharactersService, store: StateStore) -> None:
    await _bind_campaign(store, "camp-1", "wod-london")
    rel1 = await characters.update_relationship(
        "library:settings/wod-london/characters/vivienne",
        "library:settings/wod-london/characters/winifred",
        "camp-1",
        delta={"affection": 3, "trust": 2},
        types=["sibling"],
    )
    assert rel1["state"]["affection"] == 3
    assert rel1["types"] == ["sibling"]

    rel2 = await characters.update_relationship(
        "library:settings/wod-london/characters/vivienne",
        "library:settings/wod-london/characters/winifred",
        "camp-1",
        delta={"affection": -1},
    )
    assert rel2["state"]["affection"] == 2
    assert rel2["state"]["trust"] == 2

    listed = await characters.get_relationships(
        "library:settings/wod-london/characters/vivienne", "camp-1"
    )
    assert len(listed) == 1
    assert listed[0]["state"]["affection"] == 2


# ---------------------------------------------------------------------------
# Promotion
# ---------------------------------------------------------------------------


async def test_promote_emergent_to_library(
    characters: CharactersService, store: StateStore
) -> None:
    await _seed_setting(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")
    await characters.create_emergent(
        "camp-1", _character_data("the-bartender", role=CharacterRole.MINOR_NPC)
    )

    new_path = await characters.promote_to_library(
        "camp-1", "the-bartender", "wod-london", delete_emergent=True
    )
    assert "wod-london" in new_path

    # Now resolvable via the library.
    char = await characters.get("wod-london", "the-bartender")
    assert char.name == "The Bartender"


# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------


async def test_import_sillytavern_v2(characters: CharactersService, store: StateStore) -> None:
    await _seed_setting(store, "wod-london")
    card = (
        b'{"spec":"chara_card_v2","data":{'
        b'"name":"vivienne","description":"A witty Toreador.",'
        b'"personality":"Charming and sardonic.",'
        b'"first_mes":"\\"Darling, you look terrible.\\"",'
        b'"mes_example":"<START>\\n{{char}}: Darling, sit down.\\n{{char}}: Don\'t fuss.",'
        b'"tags":["vampire","toreador"]}}'
    )
    result = await characters.import_sillytavern(card, "wod-london")
    assert "vivienne" in result.created
    char = await characters.get("wod-london", "vivienne")
    assert char.voice.samples


async def test_import_plaintext(characters: CharactersService, store: StateStore) -> None:
    await _seed_setting(store, "wod-london")
    text = 'winifred\nA Tremere apprentice.\n"One does not simply leave the chantry."'
    result = await characters.import_plaintext(text, "wod-london")
    assert "winifred" in result.created
    char = await characters.get("wod-london", "winifred")
    assert "leave the chantry" in (char.voice.samples or [""])[0]


async def test_import_skip_existing(characters: CharactersService, store: StateStore) -> None:
    await _seed_setting(store, "wod-london")
    await characters.create("wod-london", _character_data("vivienne", role=CharacterRole.PC))

    card = (
        b'{"spec":"chara_card_v2","data":{"name":"vivienne",'
        b'"description":"existing","first_mes":""}}'
    )
    result = await characters.import_sillytavern(card, "wod-london")
    assert result.skipped == ["vivienne"]
    assert result.created == []


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


async def test_search_by_name(characters: CharactersService, store: StateStore) -> None:
    await _seed_setting(store, "wod-london")
    await characters.create("wod-london", _character_data("alistair"))
    await characters.create("wod-london", _character_data("vivienne", role=CharacterRole.PC))

    hits = await characters.search("char", setting_id="wod-london", scope="setting")
    assert {c.id for c in hits} == {"vivienne"}
