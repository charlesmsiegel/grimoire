"""Tests for CharactersService.

Covers CRUD, emergent, override, resolution cascade, multi-PC coordination,
tier pinning, drift detection, compressed views, relationships, capabilities,
promotion, and search.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

import pytest

from grimoire.characters import CharactersService
from grimoire.characters.config import (
    CacheConfig,
    CharactersConfig,
    DriftConfig,
)
from grimoire.characters.errors import CharactersError, PromotionError
from grimoire.library import LibraryService
from grimoire.mechanics import MechanicsService
from grimoire.state_store import StateStore
from grimoire.types.characters import (
    CapsuleDraft,
    Character,
    CharacterData,
    CharacterRole,
    PromotionProposal,
    VoiceAnchor,
)
from grimoire.types.scene import AuthorKind, Post, Scene
from grimoire.types.state import ContextTier

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
    await _seed_world(store, "wod-london")
    created = await characters.create("wod-london", _character_data())

    assert created.id == "alistair"
    assert created.name == "Alistair"
    assert created.voice.samples
    assert created.voice.voice_register == "formal"
    assert created.world_id == "wod-london"

    fetched = await characters.get("wod-london", "alistair")
    assert fetched.id == "alistair"
    assert fetched.role == CharacterRole.MAJOR_NPC


async def test_update_character(characters: CharactersService, store: StateStore) -> None:
    await _seed_world(store, "wod-london")
    await characters.create("wod-london", _character_data())

    updated = await characters.update("wod-london", "alistair", {"age": "452"})
    assert updated.age == "452"


async def test_list_in_world(characters: CharactersService, store: StateStore) -> None:
    await _seed_world(store, "wod-london")
    await characters.create("wod-london", _character_data("alistair"))
    await characters.create("wod-london", _character_data("vivienne", role=CharacterRole.PC))

    listed = await characters.list_in_world("wod-london")
    ids = {c.id for c in listed}
    assert ids == {"alistair", "vivienne"}


async def test_delete_character(characters: CharactersService, store: StateStore) -> None:
    await _seed_world(store, "wod-london")
    await characters.create("wod-london", _character_data())

    await characters.delete("wod-london", "alistair")
    listed = await characters.list_in_world("wod-london")
    assert listed == []


# ---------------------------------------------------------------------------
# Emergent + override
# ---------------------------------------------------------------------------


async def test_create_emergent(characters: CharactersService, store: StateStore) -> None:
    await _seed_world(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")

    ref = await characters.create_emergent(
        "camp-1", _character_data("the-bartender", role=CharacterRole.MINOR_NPC)
    )

    assert ref == "campaign:emergent/character/the-bartender"
    resolved = await characters.resolve(ref, "camp-1")
    assert resolved.character.id == "the-bartender"
    assert resolved.character.world_id is None


async def test_upsert_override(characters: CharactersService, store: StateStore) -> None:
    await _seed_world(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")
    await characters.create("wod-london", _character_data())

    ref = "library:worlds/wod-london/characters/alistair"
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
    await _seed_world(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")
    await characters.create("wod-london", _character_data("vivienne", role=CharacterRole.PC))

    entry = await characters.add_pc(
        "camp-1", "library:worlds/wod-london/characters/vivienne", "vivienne", owner="local"
    )
    assert entry.active is True

    listed = await characters.list_pcs("camp-1")
    assert len(listed) == 1
    assert listed[0].name == "vivienne"

    await characters.remove_pc("camp-1", "library:worlds/wod-london/characters/vivienne")
    assert await characters.list_pcs("camp-1") == []


async def test_list_pcs_logs_warning_when_state_load_fails(
    characters: CharactersService,
    store: StateStore,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """#587: a corrupt state row must not silently read as "PC has no scene".

    The entry still degrades to scene/location ``None``, but the failure is
    logged at WARNING so it is distinguishable from an idle PC.
    """

    await _seed_world(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")
    await characters.create("wod-london", _character_data("vivienne", role=CharacterRole.PC))
    await characters.add_pc("camp-1", "library:worlds/wod-london/characters/vivienne", "vivienne")

    async def boom(**_kwargs):
        raise RuntimeError("corrupt state row")

    monkeypatch.setattr(characters.store, "resolve_character_state", boom)

    with caplog.at_level(logging.WARNING, logger="grimoire.characters.service"):
        listed = await characters.list_pcs("camp-1")

    assert len(listed) == 1
    assert listed[0].current_scene_id is None
    assert listed[0].current_location_ref is None
    assert any(
        "failed to load state" in r.message and "camp-1" in r.message for r in caplog.records
    )


async def test_remove_pc_matches_stored_shorthand_ref(
    characters: CharactersService, store: StateStore
) -> None:
    """A PC registered under a wizard shorthand is removable by canonical ref.

    The frontend builds the canonical ``library:worlds/.../characters/...`` ref,
    but the wizard often registers PCs under the ``<world>/<id>`` shorthand. The
    store deletes by exact match, so remove_pc must resolve the stored spelling
    by canonical form or the delete affects zero rows (#517).
    """
    await _seed_world(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")
    await characters.create("wod-london", _character_data("vivienne", role=CharacterRole.PC))

    # Registered under the bare shorthand the wizard uses.
    await characters.add_pc("camp-1", "wod-london/vivienne", "vivienne", owner="local")
    assert len(await characters.list_pcs("camp-1")) == 1

    # Removed by the canonical ref the frontend sends.
    await characters.remove_pc("camp-1", "library:worlds/wod-london/characters/vivienne")
    assert await characters.list_pcs("camp-1") == []


async def test_set_active_pc_rejects_unknown(
    characters: CharactersService, store: StateStore
) -> None:
    await _bind_campaign(store, "camp-1", "wod-london")
    try:
        await characters.set_active_pc("camp-1", "library:worlds/wod/characters/ghost")
    except Exception as exc:
        assert "not a PC" in str(exc)
    else:
        raise AssertionError("expected CharactersError")


async def test_list_pcs_single_active_when_cache_cold(
    characters: CharactersService, store: StateStore
) -> None:
    """Regression for spec 2026-05-17 §14.

    When the in-process active-PC cache is empty (fresh worker) and multiple
    persisted rows have ``active=1`` (legacy data / pre-fix writes),
    ``list_pcs`` must still return exactly one entry with ``active=True``.
    """
    await _seed_world(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")
    pc1 = "library:worlds/wod-london/characters/vivienne"
    pc2 = "library:worlds/wod-london/characters/winifred"
    pc3 = "library:worlds/wod-london/characters/genevieve"
    await characters.add_pc("camp-1", pc1, "vivienne")
    await characters.add_pc("camp-1", pc2, "winifred")
    await characters.add_pc("camp-1", pc3, "Genevieve")

    # Simulate legacy / corrupted DB state where every row has active=1.
    await store.db.execute(
        "UPDATE campaign_pcs SET active = 1 WHERE campaign_id = ?",
        ("camp-1",),
    )

    # Simulate a fresh worker process: cache is cold.
    characters._active_pc.pop("camp-1", None)

    listed = await characters.list_pcs("camp-1")
    assert len(listed) == 3
    active_entries = [pc for pc in listed if pc.active]
    assert len(active_entries) == 1, (
        f"expected exactly one active PC, got {len(active_entries)}: "
        f"{[pc.character_ref for pc in active_entries]}"
    )

    characters._active_pc.pop("camp-1", None)
    assert await characters.active_pc("camp-1") == active_entries[0].character_ref


async def test_add_pc_persists_inactive_for_subsequent_pcs(
    characters: CharactersService, store: StateStore
) -> None:
    """The store guards the at-most-one-active invariant at write time.

    `add_pc` writes `active=1` only for the first PC in a campaign; subsequent
    inserts default to `active=0`. Without that guarantee the cold-cache fix
    in `_seed_active_pc_from_rows` would have nothing to anchor on.
    """
    await _seed_world(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")
    pc1 = "library:worlds/wod-london/characters/vivienne"
    pc2 = "library:worlds/wod-london/characters/winifred"
    await characters.add_pc("camp-1", pc1, "vivienne")
    await characters.add_pc("camp-1", pc2, "winifred")

    rows = await store.list_pcs("camp-1")
    active_rows = [r for r in rows if bool(r["active"])]
    assert len(active_rows) == 1
    assert active_rows[0]["character_ref"] == pc1


async def test_list_pcs_picks_first_row_when_no_row_is_active(
    characters: CharactersService, store: StateStore
) -> None:
    """Cold cache + zero `active=1` rows still yields exactly one active PC.

    Defends against a regression where a manual DB edit or future migration
    leaves every row with `active=0` — the fallback must still pick one (the
    earliest-added row) instead of returning all PCs as inactive.
    """
    await _seed_world(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")
    pc1 = "library:worlds/wod-london/characters/vivienne"
    pc2 = "library:worlds/wod-london/characters/winifred"
    await characters.add_pc("camp-1", pc1, "vivienne")
    await characters.add_pc("camp-1", pc2, "winifred")

    await store.db.execute(
        "UPDATE campaign_pcs SET active = 0 WHERE campaign_id = ?",
        ("camp-1",),
    )
    characters._active_pc.pop("camp-1", None)

    listed = await characters.list_pcs("camp-1")
    active_entries = [pc for pc in listed if pc.active]
    assert len(active_entries) == 1
    assert active_entries[0].character_ref == pc1


async def test_multi_pc_should_auto_respond(
    characters: CharactersService, store: StateStore
) -> None:
    await _seed_world(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")
    pc1 = "library:worlds/wod-london/characters/vivienne"
    pc2 = "library:worlds/wod-london/characters/winifred"
    await characters.add_pc("camp-1", pc1, "vivienne")
    await characters.add_pc("camp-1", pc2, "winifred")

    scene = Scene(
        id="scene-1",
        campaign_id="camp-1",
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
    ref = "library:worlds/wod-london/characters/vivienne"
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
        ordinal=1,
        slug="s",
        file_path="x.md",
        present_character_refs=["library:worlds/wod-london/characters/alistair"],
    )
    tiers = await characters.recommend_tiers(scene)
    assert tiers["library:worlds/wod-london/characters/alistair"] == ContextTier.SPOTLIGHT


async def test_pin_tier_overrides_recommendation(
    characters: CharactersService, store: StateStore
) -> None:
    await _bind_campaign(store, "camp-1", "wod-london")
    ref = "library:worlds/wod-london/characters/alistair"
    await characters.pin_tier(ref, "camp-1", ContextTier.LOCK_IN)
    scene = Scene(
        id="scene-1",
        campaign_id="camp-1",
        ordinal=1,
        slug="s",
        file_path="x.md",
        present_character_refs=[ref],
    )
    tiers = await characters.recommend_tiers(scene)
    assert tiers[ref] == ContextTier.LOCK_IN


async def test_recommend_tiers_promotes_recent_mentions_to_background(
    characters: CharactersService, store: StateStore
) -> None:
    """Spec characters-remaining §1: characters mentioned in recent posts → BACKGROUND."""
    await _seed_world(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")
    await characters.create("wod-london", _character_data("alistair"))
    alistair_ref = "library:worlds/wod-london/characters/alistair"
    scene = Scene(
        id="scene-1",
        campaign_id="camp-1",
        ordinal=1,
        slug="s",
        file_path="x.md",
        present_character_refs=[],  # not present
    )
    posts = [
        Post(
            id="p1",
            scene_id="s",
            order_in_scene=1,
            author_kind=AuthorKind.NARRATOR,
            body="The Tremere has been watching from the shadows.",  # matches alias
            is_player=False,
            created_at=datetime.now(UTC),
            turn_id="t_p1",
        )
    ]
    tiers = await characters.recommend_tiers(scene, recent_posts=posts)
    assert tiers.get(alistair_ref) == ContextTier.BACKGROUND


async def test_recommend_tiers_promotes_open_commitment_targets_to_background(
    characters: CharactersService, store: StateStore
) -> None:
    """Spec characters-remaining §1: characters with open commitments to PCs → ≥ BACKGROUND."""
    await _seed_world(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")
    await characters.create("wod-london", _character_data("alistair"))
    alistair_ref = "library:worlds/wod-london/characters/alistair"
    scene = Scene(
        id="scene-1",
        campaign_id="camp-1",
        ordinal=1,
        slug="s",
        file_path="x.md",
        present_character_refs=[],
    )
    tiers = await characters.recommend_tiers(scene, commitments_targeting_pcs={alistair_ref})
    assert tiers.get(alistair_ref) == ContextTier.BACKGROUND


async def test_recommend_tiers_demotes_inactive_characters(
    characters: CharactersService, store: StateStore
) -> None:
    """Spec characters-remaining §1: inactivity → demotion over time."""
    await _seed_world(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")
    await characters.create("wod-london", _character_data("alistair"))
    alistair_ref = "library:worlds/wod-london/characters/alistair"
    # Mark screen time at turn t_old, then advance the post log past the
    # background/archive thresholds without seeing alistair again.
    await characters.mark_screen_time(alistair_ref, "camp-1", "t_old")
    scene = Scene(
        id="scene-1",
        campaign_id="camp-1",
        ordinal=1,
        slug="s",
        file_path="x.md",
        present_character_refs=[],
    )
    # Five turns since alistair was on-screen; background threshold defaults
    # to 3 turns of silence.
    posts = [
        Post(
            id=f"p{i}",
            scene_id="s",
            order_in_scene=i,
            author_kind=AuthorKind.NARRATOR,
            body="Nothing about that character.",
            is_player=False,
            created_at=datetime.now(UTC),
            turn_id=f"t_{i}",
        )
        for i in range(1, 6)
    ]
    tiers = await characters.recommend_tiers(scene, recent_posts=posts)
    assert tiers.get(alistair_ref) == ContextTier.BACKGROUND


async def test_recommend_tiers_archives_long_inactive_characters(
    characters: CharactersService, store: StateStore
) -> None:
    """After more turns than the archive threshold, demotion goes all the way."""
    await _seed_world(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")
    await characters.create("wod-london", _character_data("alistair"))
    alistair_ref = "library:worlds/wod-london/characters/alistair"
    await characters.mark_screen_time(alistair_ref, "camp-1", "t_old")
    scene = Scene(
        id="scene-1",
        campaign_id="camp-1",
        ordinal=1,
        slug="s",
        file_path="x.md",
        present_character_refs=[],
    )
    # Twelve turns since alistair was on-screen; archive threshold defaults
    # to 10 turns of silence.
    posts = [
        Post(
            id=f"p{i}",
            scene_id="s",
            order_in_scene=i,
            author_kind=AuthorKind.NARRATOR,
            body="Nothing relevant.",
            is_player=False,
            created_at=datetime.now(UTC),
            turn_id=f"t_{i}",
        )
        for i in range(1, 13)
    ]
    tiers = await characters.recommend_tiers(scene, recent_posts=posts)
    assert tiers.get(alistair_ref) == ContextTier.ARCHIVE


async def test_recommend_tiers_presence_beats_inactivity_demotion(
    characters: CharactersService, store: StateStore
) -> None:
    """Being present in the scene wins over any inactivity demotion."""
    await _seed_world(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")
    await characters.create("wod-london", _character_data("alistair"))
    alistair_ref = "library:worlds/wod-london/characters/alistair"
    await characters.mark_screen_time(alistair_ref, "camp-1", "t_old")
    scene = Scene(
        id="scene-1",
        campaign_id="camp-1",
        ordinal=1,
        slug="s",
        file_path="x.md",
        present_character_refs=[alistair_ref],
    )
    posts = [
        Post(
            id=f"p{i}",
            scene_id="s",
            order_in_scene=i,
            author_kind=AuthorKind.NARRATOR,
            body="Nothing relevant.",
            is_player=False,
            created_at=datetime.now(UTC),
            turn_id=f"t_{i}",
        )
        for i in range(1, 13)
    ]
    tiers = await characters.recommend_tiers(scene, recent_posts=posts)
    assert tiers[alistair_ref] == ContextTier.SPOTLIGHT


# ---------------------------------------------------------------------------
# Drift
# ---------------------------------------------------------------------------


async def test_drift_with_in_voice_recent(characters: CharactersService, store: StateStore) -> None:
    await _seed_world(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")
    await characters.create("wod-london", _character_data())
    ref = "library:worlds/wod-london/characters/alistair"

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
    await _seed_world(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")
    await characters.create("wod-london", _character_data())
    ref = "library:worlds/wod-london/characters/alistair"

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
    await _seed_world(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")
    await characters.create("wod-london", _character_data())
    ref = "library:worlds/wod-london/characters/alistair"

    # No recent posts → drift 0 → empty corrective.
    await characters.check_drift(ref, "camp-1", recent_posts=[])
    assert await characters.drift_corrective_context(ref, "camp-1") == ""


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


async def test_compressed_views(characters: CharactersService, store: StateStore) -> None:
    await _seed_world(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")
    await characters.create("wod-london", _character_data())
    ref = "library:worlds/wod-london/characters/alistair"

    full = await characters.get_full_card(ref, "camp-1")
    compressed = await characters.get_compressed_card(ref, "camp-1")
    voice_only = await characters.get_voice_only(ref, "camp-1")
    capsule = await characters.get_capsule(ref, "camp-1")

    assert "Alistair" in full and "## Voice" in full
    assert "Voice:" in compressed
    assert "Register: formal" in voice_only
    assert capsule.startswith("Alistair")


def _voice_many_samples() -> VoiceAnchor:
    # 7 samples — exceeds the max_samples=5 cap used by _render_voice so
    # rotation order is observable through the slice.
    return VoiceAnchor(
        summary="Crisp, formal, archaic.",
        voice_register="formal",
        samples=[
            "Sample one: indeed.",
            "Sample two: pray.",
            "Sample three: one does not.",
            "Sample four: quite.",
            "Sample five: peculiar.",
            "Sample six: hardly.",
            "Sample seven: a curious notion.",
        ],
        speech_patterns=["uses 'one' instead of 'I'"],
    )


def _character_data_many_samples() -> CharacterData:
    return CharacterData(
        id="verbose",
        name="Verbose",
        role=CharacterRole.MAJOR_NPC,
        voice=_voice_many_samples(),
        description="A character with many sample dialogues.",
    )


async def test_get_full_card_rotates_samples_with_seed(
    characters: CharactersService, store: StateStore
) -> None:
    await _seed_world(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")
    await characters.create("wod-london", _character_data_many_samples())
    ref = "library:worlds/wod-london/characters/verbose"

    card_seed_0 = await characters.get_full_card(ref, "camp-1", seed=0)
    card_seed_1 = await characters.get_full_card(ref, "camp-1", seed=1)
    card_seed_3 = await characters.get_full_card(ref, "camp-1", seed=3)

    # Different seeds rotate the sample list so the first surfaced sample differs.
    assert card_seed_0 != card_seed_1
    assert card_seed_1 != card_seed_3
    # seed=0 → offset 0, so sample one comes first.
    assert "Sample one" in card_seed_0
    # seed=1 → offset 1, so sample two comes first (sample one drops out of the [:5] slice).
    first_block_seed_1 = card_seed_1.split("Dos:")[0] if "Dos:" in card_seed_1 else card_seed_1
    assert "Sample two" in first_block_seed_1
    # Same seed is deterministic.
    assert card_seed_0 == await characters.get_full_card(ref, "camp-1", seed=0)


async def test_get_full_card_seed_none_is_noop(
    characters: CharactersService, store: StateStore
) -> None:
    await _seed_world(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")
    await characters.create("wod-london", _character_data_many_samples())
    ref = "library:worlds/wod-london/characters/verbose"

    default_card = await characters.get_full_card(ref, "camp-1")
    explicit_none = await characters.get_full_card(ref, "camp-1", seed=None)
    seed_zero = await characters.get_full_card(ref, "camp-1", seed=0)

    # No-seed == explicit-None.
    assert default_card == explicit_none
    # seed=0 is also a no-op rotation (offset 0) so output matches the no-seed
    # case — proves the seed plumbing didn't accidentally change the cap or
    # reorder unconditionally.
    assert default_card == seed_zero


async def test_get_compressed_and_voice_only_honor_seed(
    characters: CharactersService, store: StateStore
) -> None:
    await _seed_world(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")
    await characters.create("wod-london", _character_data_many_samples())
    ref = "library:worlds/wod-london/characters/verbose"

    compressed_0 = await characters.get_compressed_card(ref, "camp-1", seed=0)
    compressed_1 = await characters.get_compressed_card(ref, "camp-1", seed=1)
    # render_compressed surfaces only sample[0]; rotation should change which line shows.
    assert "Sample one" in compressed_0
    assert "Sample two" in compressed_1

    voice_0 = await characters.get_voice_only(ref, "camp-1", seed=0)
    voice_2 = await characters.get_voice_only(ref, "camp-1", seed=2)
    assert voice_0 != voice_2

    # Capsule has no samples but should still accept the kwarg.
    capsule = await characters.get_capsule(ref, "camp-1", seed=4)
    assert capsule.startswith("Verbose")


# ---------------------------------------------------------------------------
# Compressed-view caching (spec 2026-05-17 §5)
# ---------------------------------------------------------------------------


def _patch_render_counters(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """Wrap the four view renderers and return a counter dict.

    Service-side calls are intercepted by patching the names imported into
    ``grimoire.characters.service``; cache hits skip the renderer entirely
    so counts only bump on a miss.
    """
    from grimoire.characters import service as svc

    counts = {"full": 0, "compressed": 0, "voice_only": 0, "capsule": 0}
    real_full = svc.render_full
    real_compressed = svc.render_compressed
    real_voice = svc.render_voice_only
    real_capsule = svc.render_capsule

    def _full(*args, **kwargs):
        counts["full"] += 1
        return real_full(*args, **kwargs)

    def _compressed(*args, **kwargs):
        counts["compressed"] += 1
        return real_compressed(*args, **kwargs)

    def _voice(*args, **kwargs):
        counts["voice_only"] += 1
        return real_voice(*args, **kwargs)

    def _capsule(*args, **kwargs):
        counts["capsule"] += 1
        return real_capsule(*args, **kwargs)

    monkeypatch.setattr(svc, "render_full", _full)
    monkeypatch.setattr(svc, "render_compressed", _compressed)
    monkeypatch.setattr(svc, "render_voice_only", _voice)
    monkeypatch.setattr(svc, "render_capsule", _capsule)
    return counts


async def test_get_full_card_is_cached(
    characters: CharactersService,
    store: StateStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_world(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")
    await characters.create("wod-london", _character_data())
    ref = "library:worlds/wod-london/characters/alistair"

    counts = _patch_render_counters(monkeypatch)
    first = await characters.get_full_card(ref, "camp-1")
    second = await characters.get_full_card(ref, "camp-1")

    assert first == second
    assert counts["full"] == 1  # second call served from cache


async def test_cache_distinguishes_views_and_seeds(
    characters: CharactersService,
    store: StateStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_world(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")
    await characters.create("wod-london", _character_data_many_samples())
    ref = "library:worlds/wod-london/characters/verbose"

    counts = _patch_render_counters(monkeypatch)

    # Different seeds → distinct cache slots → distinct renders.
    await characters.get_full_card(ref, "camp-1", seed=0)
    await characters.get_full_card(ref, "camp-1", seed=1)
    await characters.get_full_card(ref, "camp-1", seed=0)
    assert counts["full"] == 2

    # Different views key independently.
    await characters.get_compressed_card(ref, "camp-1", seed=0)
    await characters.get_compressed_card(ref, "camp-1", seed=0)
    assert counts["compressed"] == 1

    await characters.get_voice_only(ref, "camp-1", seed=0)
    await characters.get_capsule(ref, "camp-1", seed=0)
    assert counts["voice_only"] == 1
    assert counts["capsule"] == 1


async def test_update_state_invalidates_cache(
    characters: CharactersService,
    store: StateStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_world(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")
    await characters.create("wod-london", _character_data())
    ref = "library:worlds/wod-london/characters/alistair"

    counts = _patch_render_counters(monkeypatch)
    await characters.get_full_card(ref, "camp-1")
    state = await characters.get_state(ref, "camp-1")
    state.emotional_state = "wary"
    await characters.update_state(ref, "camp-1", state)

    await characters.get_full_card(ref, "camp-1")
    assert counts["full"] == 2  # invalidated → re-rendered


async def test_upsert_override_invalidates_cache(
    characters: CharactersService,
    store: StateStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_world(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")
    await characters.create("wod-london", _character_data())
    ref = "library:worlds/wod-london/characters/alistair"

    counts = _patch_render_counters(monkeypatch)
    first = await characters.get_full_card(ref, "camp-1")
    await characters.upsert_override(
        "camp-1", ref, {"description": "A softer Tremere in this chronicle."}
    )
    second = await characters.get_full_card(ref, "camp-1")

    assert counts["full"] == 2
    assert "softer" in second
    assert first != second


async def test_pin_tier_invalidates_cache(
    characters: CharactersService,
    store: StateStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_world(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")
    await characters.create("wod-london", _character_data())
    ref = "library:worlds/wod-london/characters/alistair"

    counts = _patch_render_counters(monkeypatch)
    await characters.get_full_card(ref, "camp-1")
    await characters.pin_tier(ref, "camp-1", ContextTier.LOCK_IN)
    await characters.get_full_card(ref, "camp-1")

    assert counts["full"] == 2


async def test_update_emergent_invalidates_cache(
    characters: CharactersService,
    store: StateStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_world(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")
    emergent_ref = await characters.create_emergent(
        "camp-1", _character_data("the-bartender", role=CharacterRole.MINOR_NPC)
    )

    counts = _patch_render_counters(monkeypatch)
    await characters.get_full_card(emergent_ref, "camp-1")
    await characters.update_emergent("camp-1", "the-bartender", {"description": "Now scarred."})
    await characters.get_full_card(emergent_ref, "camp-1")

    assert counts["full"] == 2


async def test_delete_emergent_invalidates_cache(
    characters: CharactersService,
    store: StateStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_world(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")
    emergent_ref = await characters.create_emergent(
        "camp-1", _character_data("the-bartender", role=CharacterRole.MINOR_NPC)
    )
    counts = _patch_render_counters(monkeypatch)
    await characters.get_full_card(emergent_ref, "camp-1")
    assert counts["full"] == 1

    await characters.delete_emergent("camp-1", "the-bartender")
    # The cached entry should be gone; we can't re-render (resolve will fail)
    # but the invalidation hook itself must have cleared the slot. Recreate
    # and confirm the new render is fresh.
    await characters.create_emergent(
        "camp-1", _character_data("the-bartender", role=CharacterRole.MINOR_NPC)
    )
    await characters.get_full_card(emergent_ref, "camp-1")
    assert counts["full"] == 2


async def test_update_library_character_invalidates_cache(
    characters: CharactersService,
    store: StateStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_world(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")
    await characters.create("wod-london", _character_data())
    ref = "library:worlds/wod-london/characters/alistair"

    counts = _patch_render_counters(monkeypatch)
    await characters.get_full_card(ref, "camp-1")
    await characters.update("wod-london", "alistair", {"description": "Now older."})
    await characters.get_full_card(ref, "camp-1")

    assert counts["full"] == 2


async def test_cache_respects_max_size(
    library: LibraryService,
    mechanics: MechanicsService,
    store: StateStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chars = CharactersService(
        library, mechanics, config=CharactersConfig(cache=CacheConfig(max_size=2))
    )
    await _seed_world(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")
    await characters_create_many(chars)
    ref_a = "library:worlds/wod-london/characters/cache-a"
    ref_b = "library:worlds/wod-london/characters/cache-b"
    ref_c = "library:worlds/wod-london/characters/cache-c"

    counts = _patch_render_counters(monkeypatch)
    await chars.get_full_card(ref_a, "camp-1")  # cache: [a]
    await chars.get_full_card(ref_b, "camp-1")  # cache: [a, b]
    await chars.get_full_card(ref_c, "camp-1")  # cache: [b, c] (a evicted)
    assert counts["full"] == 3

    # Touching b should hit cache (still resident).
    await chars.get_full_card(ref_b, "camp-1")
    assert counts["full"] == 3

    # Re-rendering a forces a fresh render (was evicted) and pushes b out.
    await chars.get_full_card(ref_a, "camp-1")
    assert counts["full"] == 4


async def characters_create_many(chars: CharactersService) -> None:
    for suffix in ("a", "b", "c"):
        await chars.create(
            "wod-london",
            _character_data(asset_id=f"cache-{suffix}", role=CharacterRole.MINOR_NPC),
        )


# ---------------------------------------------------------------------------
# Cross-world variants
# ---------------------------------------------------------------------------


async def test_cross_world_lookup(characters: CharactersService, store: StateStore) -> None:
    await _seed_world(store, "wod-london")
    await _seed_world(store, "wod-nyc")
    await characters.create("wod-london", _character_data("alistair"))
    await characters.create("wod-nyc", _character_data("alistair"))

    variants = await characters.cross_world_lookup("alistair", exclude_world="wod-london")
    assert [v.world_id for v in variants] == ["wod-nyc"]


def test_characters_config_defaults_match_spec() -> None:
    """Spec characters-remaining §6: documented defaults from spec 08 §Configuration."""
    config = CharactersConfig()
    assert config.drift.threshold == 0.4
    assert config.drift.check_every_n_appearances == 5
    assert config.tiers.demote_to_background_after_turns == 3
    assert config.tiers.demote_to_archive_after_turns == 10
    assert config.voice_anchor.sample_dialogue_rotation is True
    assert config.voice_anchor.max_samples == 5
    assert config.capsules.auto_generate is True
    assert config.promotion.require_confirmation is True
    assert config.cross_world_lookup.case_sensitive is False
    assert config.multi_pc.auto_advance_with_single_pc is True
    assert config.multi_pc.require_advance_with_multiple_pcs is True
    assert config.cache.max_size == 256


async def test_cross_world_lookup_is_case_insensitive_by_default(
    library: LibraryService,
    mechanics: MechanicsService,
    store: StateStore,
) -> None:
    """Spec characters-remaining §7: case_sensitive=False slug-normalizes the id."""
    chars = CharactersService(library, mechanics)
    await _seed_world(store, "wod-london")
    await _seed_world(store, "wod-nyc")
    await chars.create("wod-london", _character_data("alistair-hyde-smythe"))
    await chars.create("wod-nyc", _character_data("alistair-hyde-smythe"))

    # Mixed-case query slug-normalizes to "alistair-hyde-smythe".
    variants = await chars.cross_world_lookup("Alistair Hyde Smythe")
    assert {v.world_id for v in variants} == {"wod-london", "wod-nyc"}


async def test_cross_world_lookup_respects_case_sensitive_flag(
    library: LibraryService,
    mechanics: MechanicsService,
    store: StateStore,
) -> None:
    """When case_sensitive=True the raw id is passed through verbatim."""
    from grimoire.characters.config import CrossWorldLookupConfig

    chars = CharactersService(
        library,
        mechanics,
        config=CharactersConfig(cross_world_lookup=CrossWorldLookupConfig(case_sensitive=True)),
    )
    await _seed_world(store, "wod-london")
    await chars.create("wod-london", _character_data("alistair-hyde-smythe"))

    # Different case → no match (no slugification applied).
    variants = await chars.cross_world_lookup("Alistair Hyde Smythe")
    assert variants == []


# ---------------------------------------------------------------------------
# Capabilities (null mechanics → empty)
# ---------------------------------------------------------------------------


async def test_capabilities_empty_when_null_mechanics(
    characters: CharactersService, store: StateStore
) -> None:
    await _bind_campaign(store, "camp-1", "wod-london")
    caps = await characters.capabilities_of(
        "library:worlds/wod-london/characters/alistair", "camp-1"
    )
    assert caps == []


# ---------------------------------------------------------------------------
# Relationships (read paths; rows are written by the extractor delta path)
# ---------------------------------------------------------------------------


async def _seed_relationship(
    store: StateStore,
    *,
    campaign_id: str,
    from_ref: str,
    to_ref: str,
    types: list[str] | str | None = None,
    state: dict | str | None = None,
    history: list[dict] | str | None = None,
    row_id: str = "rel-1",
) -> None:
    """Insert a relationship row as production does (RELATIONSHIP_UPDATE deltas
    upsert rows through the state store; CharactersService only reads them).
    Pass a raw string for ``types``/``state``/``history`` to seed malformed JSON.
    """

    def _column(value: list | dict | str | None, default: list | dict) -> str:
        if isinstance(value, str):
            return value
        return json.dumps(value if value is not None else default)

    await store.db.execute(
        """
        INSERT INTO relationships (
          id, campaign_id, from_character_ref, to_character_ref,
          types, state, updated_at_turn, history
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            row_id,
            campaign_id,
            from_ref,
            to_ref,
            _column(types, []),
            _column(state, {}),
            "turn-001",
            _column(history, []),
        ),
    )


async def test_get_relationships_matches_either_direction(
    characters: CharactersService, store: StateStore
) -> None:
    await _bind_campaign(store, "camp-1", "wod-london")
    await _seed_relationship(
        store,
        campaign_id="camp-1",
        from_ref="library:worlds/wod-london/characters/vivienne",
        to_ref="library:worlds/wod-london/characters/winifred",
        types=["sibling"],
        state={"affection": 2, "trust": 2},
    )

    for ref in (
        "library:worlds/wod-london/characters/vivienne",
        "library:worlds/wod-london/characters/winifred",
    ):
        listed = await characters.get_relationships(ref, "camp-1")
        assert len(listed) == 1
        assert listed[0]["types"] == ["sibling"]
        assert listed[0]["state"]["affection"] == 2
        assert listed[0]["state"]["trust"] == 2

    # Rows are campaign-scoped.
    assert (
        await characters.get_relationships(
            "library:worlds/wod-london/characters/vivienne", "camp-2"
        )
        == []
    )


async def test_get_relationship_history_preserves_stored_order(
    characters: CharactersService, store: StateStore
) -> None:
    await _bind_campaign(store, "camp-1", "wod-london")
    await _seed_relationship(
        store,
        campaign_id="camp-1",
        from_ref="library:worlds/wod-london/characters/vivienne",
        to_ref="library:worlds/wod-london/characters/winifred",
        history=[
            {"in_post": "post-001", "summary": "first event", "delta": {"affection": 1}},
            {"in_post": "post-002", "summary": "second event", "delta": {"trust": 1}},
            {"in_post": "post-003", "summary": "third event", "delta": {"affection": -1}},
        ],
    )

    history = await characters.get_relationship_history(
        "library:worlds/wod-london/characters/vivienne",
        "library:worlds/wod-london/characters/winifred",
        "camp-1",
    )
    assert [e["summary"] for e in history] == [
        "first event",
        "second event",
        "third event",
    ]
    assert [e["in_post"] for e in history] == ["post-001", "post-002", "post-003"]


async def test_get_relationship_history_empty_when_no_row(
    characters: CharactersService, store: StateStore
) -> None:
    await _bind_campaign(store, "camp-1", "wod-london")
    history = await characters.get_relationship_history(
        "library:worlds/wod-london/characters/vivienne",
        "library:worlds/wod-london/characters/winifred",
        "camp-1",
    )
    assert history == []


async def test_relationship_row_with_malformed_json_degrades_to_defaults(
    characters: CharactersService, store: StateStore
) -> None:
    await _bind_campaign(store, "camp-1", "wod-london")
    await _seed_relationship(
        store,
        campaign_id="camp-1",
        from_ref="library:worlds/wod-london/characters/vivienne",
        to_ref="library:worlds/wod-london/characters/winifred",
        types="{not json",
        state="{not json",
        history="{not json",
    )

    listed = await characters.get_relationships(
        "library:worlds/wod-london/characters/vivienne", "camp-1"
    )
    assert len(listed) == 1
    assert listed[0]["types"] == []
    assert listed[0]["state"]["affection"] == 0  # RelationshipState defaults
    assert listed[0]["history"] == []

    history = await characters.get_relationship_history(
        "library:worlds/wod-london/characters/vivienne",
        "library:worlds/wod-london/characters/winifred",
        "camp-1",
    )
    assert history == []


# ---------------------------------------------------------------------------
# Promotion
# ---------------------------------------------------------------------------


async def test_promote_emergent_to_library(
    characters: CharactersService, store: StateStore
) -> None:
    await _seed_world(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")
    await characters.create_emergent(
        "camp-1", _character_data("the-bartender", role=CharacterRole.MINOR_NPC)
    )

    new_path = await characters.promote_to_library(
        "camp-1", "the-bartender", "wod-london", delete_emergent=True, confirm=True
    )
    assert "wod-london" in new_path

    # Now resolvable via the library.
    char = await characters.get("wod-london", "the-bartender")
    assert char.name == "The Bartender"


# ---------------------------------------------------------------------------
# Promotion confirmation flow (spec characters-remaining §9)
# ---------------------------------------------------------------------------


async def test_propose_promotion_returns_preview_without_writing(
    characters: CharactersService, store: StateStore
) -> None:
    """§9: propose_promotion returns a preview and does NOT write the file."""
    await _seed_world(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")
    await characters.create_emergent(
        "camp-1", _character_data("the-bartender", role=CharacterRole.MINOR_NPC)
    )

    proposal = await characters.propose_promotion("camp-1", "the-bartender", "wod-london")

    assert isinstance(proposal, PromotionProposal)
    assert proposal.target_world_id == "wod-london"
    assert proposal.target_library_id == "worlds/wod-london/characters/the-bartender"
    assert proposal.frontmatter.get("id") == "the-bartender"
    assert "the-bartender.md" in proposal.target_path
    # No write happened — library lookup must still 404.
    from grimoire.library.errors import LibraryNotFoundError

    with pytest.raises(LibraryNotFoundError):
        await characters.get("wod-london", "the-bartender")


async def test_propose_promotion_flags_id_collision(
    characters: CharactersService, store: StateStore
) -> None:
    """§9: target id already exists in the world → warning."""
    await _seed_world(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")
    # An existing library character with the same id.
    await characters.create("wod-london", _character_data("the-bartender"))
    await characters.create_emergent(
        "camp-1", _character_data("the-bartender", role=CharacterRole.MINOR_NPC)
    )

    proposal = await characters.propose_promotion("camp-1", "the-bartender", "wod-london")
    assert any("already has a character" in w for w in proposal.warnings)


async def test_propose_promotion_flags_missing_voice_and_description(
    characters: CharactersService, store: StateStore
) -> None:
    """§9: missing voice anchor / description fire warnings."""
    await _seed_world(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")
    sparse = CharacterData(
        id="ghost",
        name="Ghost",
        role=CharacterRole.MINOR_NPC,
        voice=VoiceAnchor(),
        description="",
    )
    await characters.create_emergent("camp-1", sparse)

    proposal = await characters.propose_promotion("camp-1", "ghost", "wod-london")
    assert any("voice" in w for w in proposal.warnings)
    assert any("description" in w for w in proposal.warnings)


async def test_promote_to_library_without_confirm_raises_when_warnings(
    characters: CharactersService, store: StateStore
) -> None:
    """§9: confirm=False + warnings → PromotionError."""
    await _seed_world(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")
    sparse = CharacterData(
        id="ghost",
        name="Ghost",
        role=CharacterRole.MINOR_NPC,
        voice=VoiceAnchor(),
        description="",
    )
    await characters.create_emergent("camp-1", sparse)

    with pytest.raises(PromotionError):
        await characters.promote_to_library("camp-1", "ghost", "wod-london")


async def test_promote_to_library_without_confirm_raises_even_when_clean(
    characters: CharactersService, store: StateStore
) -> None:
    """§9: confirm=False is a safety gate; commit requires explicit confirm=True."""
    await _seed_world(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")
    await characters.create_emergent(
        "camp-1", _character_data("the-bartender", role=CharacterRole.MINOR_NPC)
    )

    with pytest.raises(PromotionError):
        await characters.promote_to_library("camp-1", "the-bartender", "wod-london")


async def test_promote_to_library_accepts_pregenerated_proposal(
    characters: CharactersService, store: StateStore
) -> None:
    """§9: callers can pass a previously rendered proposal."""
    await _seed_world(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")
    await characters.create_emergent(
        "camp-1", _character_data("the-bartender", role=CharacterRole.MINOR_NPC)
    )

    proposal = await characters.propose_promotion("camp-1", "the-bartender", "wod-london")
    new_path = await characters.promote_to_library(
        "camp-1",
        "the-bartender",
        "wod-london",
        confirm=True,
        proposal=proposal,
    )
    assert "wod-london" in new_path
    char = await characters.get("wod-london", "the-bartender")
    assert char.name == "The Bartender"


# ---------------------------------------------------------------------------
# Promote-with-sheet-migration (spec characters-remaining §13)
# ---------------------------------------------------------------------------


class _RecordingSheetMigrator:
    """Test double for ``SheetMigrator``: captures invocations."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    async def migrate_sheet(
        self,
        campaign_id: str,
        character_ref: str,
        target_library_id: str,
    ) -> None:
        self.calls.append((campaign_id, character_ref, target_library_id))


class _FailingSheetMigrator:
    async def migrate_sheet(
        self,
        campaign_id: str,
        character_ref: str,
        target_library_id: str,
    ) -> None:
        raise RuntimeError("mechanics blew up")


async def test_promote_invokes_sheet_migrator_after_write(
    library: LibraryService,
    mechanics: MechanicsService,
    store: StateStore,
) -> None:
    """§13: when a sheet_migrator is wired, migrate_sheet runs post-write."""
    await _seed_world(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")
    migrator = _RecordingSheetMigrator()
    chars = CharactersService(library, mechanics, sheet_migrator=migrator)
    await chars.create_emergent(
        "camp-1", _character_data("the-bartender", role=CharacterRole.MINOR_NPC)
    )

    await chars.promote_to_library("camp-1", "the-bartender", "wod-london", confirm=True)

    assert migrator.calls == [
        (
            "camp-1",
            "campaign:emergent/character/the-bartender",
            "worlds/wod-london/characters/the-bartender",
        )
    ]


async def test_promote_without_sheet_migrator_works(
    characters: CharactersService, store: StateStore
) -> None:
    """§13: sheet migration is optional — no migrator wired, no problem."""
    await _seed_world(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")
    await characters.create_emergent(
        "camp-1", _character_data("the-bartender", role=CharacterRole.MINOR_NPC)
    )

    new_path = await characters.promote_to_library(
        "camp-1", "the-bartender", "wod-london", confirm=True
    )
    assert "wod-london" in new_path


async def test_promote_sheet_migrator_failure_raises_promotion_error(
    library: LibraryService,
    mechanics: MechanicsService,
    store: StateStore,
) -> None:
    """§13: migrator exceptions bubble up as PromotionError (no silent swallow)."""
    await _seed_world(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")
    chars = CharactersService(library, mechanics, sheet_migrator=_FailingSheetMigrator())
    await chars.create_emergent(
        "camp-1", _character_data("the-bartender", role=CharacterRole.MINOR_NPC)
    )

    with pytest.raises(PromotionError):
        await chars.promote_to_library("camp-1", "the-bartender", "wod-london", confirm=True)


# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------


async def test_import_sillytavern_v2(characters: CharactersService, store: StateStore) -> None:
    await _seed_world(store, "wod-london")
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
    await _seed_world(store, "wod-london")
    text = 'winifred\nA Tremere apprentice.\n"One does not simply leave the chantry."'
    result = await characters.import_plaintext(text, "wod-london")
    assert "winifred" in result.created
    char = await characters.get("wod-london", "winifred")
    assert "leave the chantry" in (char.voice.samples or [""])[0]


async def test_import_skip_existing(characters: CharactersService, store: StateStore) -> None:
    await _seed_world(store, "wod-london")
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
    await _seed_world(store, "wod-london")
    await characters.create("wod-london", _character_data("alistair"))
    await characters.create("wod-london", _character_data("vivienne", role=CharacterRole.PC))

    hits = await characters.search("char", world_id="wod-london", scope="world")
    assert {c.id for c in hits} == {"vivienne"}


# ---------------------------------------------------------------------------
# §10 — auto-capsule for sparse emergent NPCs
# ---------------------------------------------------------------------------


class FakeCapsuleDrafter:
    """Test double for the LLMCapsuleDrafter protocol."""

    def __init__(self, draft: CapsuleDraft) -> None:
        self.draft = draft
        self.calls: list[CharacterData] = []

    async def __call__(self, payload: CharacterData) -> CapsuleDraft:
        self.calls.append(payload)
        return self.draft


def _sparse_character_data(asset_id: str = "the-bartender") -> CharacterData:
    """Emergent payload with no description and no tags."""
    return CharacterData(
        id=asset_id,
        name=asset_id.replace("-", " ").title(),
        role=CharacterRole.MINOR_NPC,
    )


async def test_create_emergent_auto_capsule_fills_sparse_payload(
    library: LibraryService, mechanics: MechanicsService, store: StateStore
) -> None:
    await _seed_world(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")

    drafter = FakeCapsuleDrafter(
        CapsuleDraft(
            summary_line="A jaded barkeep who pours bourbon and rumors in equal measure.",
            tags=["bartender", "rumormonger"],
        )
    )
    svc = CharactersService(library, mechanics, auto_capsule_llm=drafter)

    ref = await svc.create_emergent("camp-1", _sparse_character_data("the-bartender"))

    assert ref == "campaign:emergent/character/the-bartender"
    assert len(drafter.calls) == 1
    assert drafter.calls[0].id == "the-bartender"

    resolved = await svc.resolve(ref, "camp-1")
    assert "jaded barkeep" in resolved.character.description
    assert set(resolved.character.tags) == {"bartender", "rumormonger"}


async def test_create_emergent_auto_capsule_skipped_when_not_sparse(
    library: LibraryService, mechanics: MechanicsService, store: StateStore
) -> None:
    await _seed_world(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")

    drafter = FakeCapsuleDrafter(CapsuleDraft(summary_line="x"))
    svc = CharactersService(library, mechanics, auto_capsule_llm=drafter)

    # Has a description → not sparse → drafter must NOT be invoked.
    await svc.create_emergent(
        "camp-1", _character_data("the-bartender", role=CharacterRole.MINOR_NPC)
    )

    assert drafter.calls == []


async def test_create_emergent_no_auto_capsule_drafter_works_as_before(
    characters: CharactersService, store: StateStore
) -> None:
    """Backwards compat: with no drafter wired, sparse payloads pass through untouched."""
    await _seed_world(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")

    ref = await characters.create_emergent("camp-1", _sparse_character_data("ghost"))
    resolved = await characters.resolve(ref, "camp-1")
    assert resolved.character.description == ""
    assert resolved.character.tags == []


# ---------------------------------------------------------------------------
# §11 — auto-draft voice anchor for emergent characters
# ---------------------------------------------------------------------------


class FakeVoiceAnchorDrafter:
    """Test double for the LLMVoiceAnchorDrafter protocol."""

    def __init__(self, anchor: VoiceAnchor) -> None:
        self.anchor = anchor
        self.calls: list[tuple[Character, list[Post]]] = []

    async def __call__(self, character: Character, recent_posts: list[Post]) -> VoiceAnchor:
        self.calls.append((character, list(recent_posts)))
        return self.anchor


def _post(
    post_id: str,
    body: str,
    *,
    order: int = 1,
    scene_id: str = "scene-1",
) -> Post:
    return Post(
        id=post_id,
        scene_id=scene_id,
        order_in_scene=order,
        author_kind=AuthorKind.NARRATOR,
        body=body,
        is_player=False,
        created_at=datetime.now(UTC),
        turn_id=f"t-{post_id}",
    )


async def test_draft_voice_anchor_calls_drafter_with_mentioning_posts(
    library: LibraryService, mechanics: MechanicsService, store: StateStore
) -> None:
    await _seed_world(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")

    bartender = CharacterData(
        id="the-bartender",
        name="The Bartender",
        role=CharacterRole.MINOR_NPC,
        aliases=["Barkeep"],
    )

    fetched_posts = [
        _post("p1", "The Bartender wipes a glass and grunts.", order=1),
        _post("p2", "vivienne sips her drink in silence.", order=2),  # no mention
        _post("p3", "Barkeep, another round?", order=3),
        _post("p4", '"Coming right up," he says.', order=4),  # no mention
    ]

    async def fetcher(scene_id: str) -> list[Post]:
        assert scene_id == "scene-bar"
        return fetched_posts

    drafted_anchor = VoiceAnchor(
        summary="Gruff, terse, world-weary.",
        voice_register="low",
        samples=["Coming right up."],
    )
    drafter = FakeVoiceAnchorDrafter(drafted_anchor)
    svc = CharactersService(
        library,
        mechanics,
        post_fetcher=fetcher,
        voice_anchor_llm=drafter,
    )

    ref = await svc.create_emergent("camp-1", bartender)
    await svc.set_current_scene_for_pc("camp-1", ref, "scene-bar")

    anchor = await svc.draft_voice_anchor(ref, "camp-1")

    assert anchor == drafted_anchor
    assert len(drafter.calls) == 1
    sent_character, sent_posts = drafter.calls[0]
    assert sent_character.id == "the-bartender"
    # Only the two posts that mention the character (by name or alias) are passed.
    sent_ids = [p.id for p in sent_posts]
    assert sent_ids == ["p1", "p3"]


async def test_draft_voice_anchor_raises_when_not_configured(
    characters: CharactersService, store: StateStore
) -> None:
    await _seed_world(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")

    ref = await characters.create_emergent("camp-1", _sparse_character_data("nobody"))

    try:
        await characters.draft_voice_anchor(ref, "camp-1")
    except CharactersError as exc:
        assert "voice_anchor_llm" in str(exc)
    else:
        raise AssertionError("expected CharactersError when no voice_anchor_llm is wired")


# ---------------------------------------------------------------------------
# Delta log integration (spec characters-remaining §8)
# ---------------------------------------------------------------------------


async def _character_state_deltas(store: StateStore, campaign_id: str) -> list:
    log = await store.get_delta_log(campaign_id=campaign_id)
    return [d for d in log if d.target_table == "character_state"]


async def test_update_state_records_character_state_delta(
    characters: CharactersService, store: StateStore
) -> None:
    await _seed_world(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")
    await characters.create("wod-london", _character_data())
    ref = "library:worlds/wod-london/characters/alistair"

    state = await characters.get_state(ref, "camp-1")
    state.emotional_state = "wary"
    await characters.update_state(ref, "camp-1", state, source="test:update")

    deltas = await _character_state_deltas(store, "camp-1")
    assert len(deltas) == 1
    delta = deltas[0]
    assert delta.kind == "character_state_update"
    assert delta.target_scope == "campaign-sqlite"
    assert delta.target_id == ref
    assert delta.source == "test:update"
    assert delta.after["character_ref"] == ref
    assert delta.after["emotional_state"] == "wary"
    # And the row is actually persisted (apply_delta writes through).
    reloaded = await characters.get_state(ref, "camp-1")
    assert reloaded.emotional_state == "wary"


async def test_mark_screen_time_records_delta_with_turn_id(
    characters: CharactersService, store: StateStore
) -> None:
    await _seed_world(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")
    await characters.create("wod-london", _character_data())
    ref = "library:worlds/wod-london/characters/alistair"

    await characters.mark_screen_time(ref, "camp-1", "t_42")

    deltas = await _character_state_deltas(store, "camp-1")
    assert len(deltas) == 1
    delta = deltas[0]
    assert delta.kind == "character_state_update"
    assert delta.turn_id == "t_42"
    assert delta.target_id == ref
    assert delta.after["last_screen_time_turn"] == "t_42"


async def test_check_drift_records_delta(characters: CharactersService, store: StateStore) -> None:
    await _seed_world(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")
    await characters.create("wod-london", _character_data())
    ref = "library:worlds/wod-london/characters/alistair"

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

    deltas = await _character_state_deltas(store, "camp-1")
    assert len(deltas) == 1
    delta = deltas[0]
    assert delta.kind == "character_state_update"
    assert delta.source == "characters:drift-check"
    assert delta.after["drift_score"] == report.drift_score


async def test_pin_tier_does_not_record_delta(
    characters: CharactersService, store: StateStore
) -> None:
    """pin_tier is a UI choice — it should survive an undo_turn replay,
    so we deliberately skip the delta-log write (record_in_delta_log=False).
    """
    await _seed_world(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")
    await characters.create("wod-london", _character_data())
    ref = "library:worlds/wod-london/characters/alistair"

    await characters.pin_tier(ref, "camp-1", ContextTier.LOCK_IN)

    deltas = await _character_state_deltas(store, "camp-1")
    assert deltas == []
    # But the pin still persists.
    state = await characters.get_state(ref, "camp-1")
    assert state.tier_pin == ContextTier.LOCK_IN


# ---------------------------------------------------------------------------
# Drift cadence (spec characters-remaining §3)
# ---------------------------------------------------------------------------


async def test_mark_screen_time_bumps_appearance_counter(
    characters: CharactersService, store: StateStore
) -> None:
    """Each screen-time bump increments the drift-cadence counter."""
    await _seed_world(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")
    await characters.create("wod-london", _character_data())
    ref = "library:worlds/wod-london/characters/alistair"

    state = await characters.get_state(ref, "camp-1")
    assert state.appearances_since_last_drift_check == 0

    await characters.mark_screen_time(ref, "camp-1", "t_1")
    await characters.mark_screen_time(ref, "camp-1", "t_2")
    await characters.mark_screen_time(ref, "camp-1", "t_3")

    state = await characters.get_state(ref, "camp-1")
    assert state.appearances_since_last_drift_check == 3


async def test_maybe_check_drift_below_threshold_returns_none(
    library: LibraryService, mechanics: MechanicsService, store: StateStore
) -> None:
    """Counter below threshold → no checker call, no report."""
    await _seed_world(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")

    class _CountingChecker:
        def __init__(self) -> None:
            self.calls = 0

        async def evaluate(self, payload):  # type: ignore[no-untyped-def]
            self.calls += 1
            from grimoire.types.characters import DriftReport

            return DriftReport(
                character_ref=payload.character.id,
                window=payload.window,
                drift_score=0.0,
                evidence=[],
                corrective_context="",
            )

    checker = _CountingChecker()
    characters = CharactersService(
        library,
        mechanics,
        drift_checker=checker,
        config=CharactersConfig(drift=DriftConfig(check_every_n_appearances=5)),
    )
    await characters.create("wod-london", _character_data())
    ref = "library:worlds/wod-london/characters/alistair"

    # Two appearances, threshold is 5 → maybe_check_drift should noop.
    await characters.mark_screen_time(ref, "camp-1", "t_1")
    await characters.mark_screen_time(ref, "camp-1", "t_2")

    report = await characters.maybe_check_drift(ref, "camp-1", recent_posts=[])
    assert report is None
    assert checker.calls == 0
    # Counter is unchanged when we skip.
    state = await characters.get_state(ref, "camp-1")
    assert state.appearances_since_last_drift_check == 2


async def test_maybe_check_drift_at_threshold_runs_and_resets(
    library: LibraryService, mechanics: MechanicsService, store: StateStore
) -> None:
    """Counter reaches threshold → checker runs, counter resets."""
    await _seed_world(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")

    class _CountingChecker:
        def __init__(self) -> None:
            self.calls = 0

        async def evaluate(self, payload):  # type: ignore[no-untyped-def]
            self.calls += 1
            from grimoire.types.characters import DriftReport

            return DriftReport(
                character_ref=payload.character.id,
                window=payload.window,
                drift_score=0.1,
                evidence=[],
                corrective_context="",
            )

    checker = _CountingChecker()
    characters = CharactersService(
        library,
        mechanics,
        drift_checker=checker,
        config=CharactersConfig(drift=DriftConfig(check_every_n_appearances=3)),
    )
    await characters.create("wod-london", _character_data())
    ref = "library:worlds/wod-london/characters/alistair"

    await characters.mark_screen_time(ref, "camp-1", "t_1")
    await characters.mark_screen_time(ref, "camp-1", "t_2")
    await characters.mark_screen_time(ref, "camp-1", "t_3")

    report = await characters.maybe_check_drift(ref, "camp-1", recent_posts=[])
    assert report is not None
    assert checker.calls == 1
    state = await characters.get_state(ref, "camp-1")
    assert state.appearances_since_last_drift_check == 0


async def test_maybe_check_drift_force_bypasses_threshold(
    library: LibraryService, mechanics: MechanicsService, store: StateStore
) -> None:
    """``force=True`` always runs, even with a zero counter."""
    await _seed_world(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")

    class _CountingChecker:
        def __init__(self) -> None:
            self.calls = 0

        async def evaluate(self, payload):  # type: ignore[no-untyped-def]
            self.calls += 1
            from grimoire.types.characters import DriftReport

            return DriftReport(
                character_ref=payload.character.id,
                window=payload.window,
                drift_score=0.0,
                evidence=[],
                corrective_context="",
            )

    checker = _CountingChecker()
    characters = CharactersService(
        library,
        mechanics,
        drift_checker=checker,
        config=CharactersConfig(drift=DriftConfig(check_every_n_appearances=100)),
    )
    await characters.create("wod-london", _character_data())
    ref = "library:worlds/wod-london/characters/alistair"

    report = await characters.maybe_check_drift(ref, "camp-1", recent_posts=[], force=True)
    assert report is not None
    assert checker.calls == 1


# ---------------------------------------------------------------------------
# Drift UI surfacing (spec characters-remaining §4)
# ---------------------------------------------------------------------------


async def test_check_drift_emits_event_when_threshold_crossed(
    library: LibraryService, mechanics: MechanicsService, store: StateStore
) -> None:
    """When drift_score >= threshold, the sink receives a DriftEvent."""
    from grimoire.characters.drift import DriftEvent

    await _seed_world(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")

    events: list[DriftEvent] = []

    async def sink(event: DriftEvent) -> None:
        events.append(event)

    characters = CharactersService(
        library,
        mechanics,
        drift_event_sink=sink,
        config=CharactersConfig(drift=DriftConfig(threshold=0.4)),
    )
    await characters.create("wod-london", _character_data())
    ref = "library:worlds/wod-london/characters/alistair"

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
    assert report.drift_score >= 0.4
    assert len(events) == 1
    event = events[0]
    assert event.character_ref == ref
    assert event.campaign_id == "camp-1"
    assert event.drift_score == report.drift_score
    assert event.threshold == 0.4
    assert event.report is report


async def test_check_drift_does_not_emit_below_threshold(
    library: LibraryService, mechanics: MechanicsService, store: StateStore
) -> None:
    """Score below threshold → no event."""
    from grimoire.characters.drift import DriftEvent

    await _seed_world(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")

    events: list[DriftEvent] = []

    async def sink(event: DriftEvent) -> None:
        events.append(event)

    characters = CharactersService(
        library,
        mechanics,
        drift_event_sink=sink,
        config=CharactersConfig(drift=DriftConfig(threshold=0.99)),
    )
    await characters.create("wod-london", _character_data())
    ref = "library:worlds/wod-london/characters/alistair"

    # In-voice line → low drift score.
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
    await characters.check_drift(ref, "camp-1", recent_posts=posts)
    assert events == []


async def test_check_drift_swallows_sink_exceptions(
    library: LibraryService, mechanics: MechanicsService, store: StateStore
) -> None:
    """A failing sink must not block drift detection."""
    await _seed_world(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")

    async def sink(event) -> None:  # type: ignore[no-untyped-def]
        raise RuntimeError("sink exploded")

    characters = CharactersService(
        library,
        mechanics,
        drift_event_sink=sink,
        config=CharactersConfig(drift=DriftConfig(threshold=0.0)),
    )
    await characters.create("wod-london", _character_data())
    ref = "library:worlds/wod-london/characters/alistair"

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
    # Should NOT raise.
    report = await characters.check_drift(ref, "camp-1", recent_posts=posts)
    assert report.drift_score >= 0.0


# ---------------------------------------------------------------------------
# Event-driven cache invalidation
# ---------------------------------------------------------------------------


async def test_event_bus_invalidates_character_cache(
    library: LibraryService,
    mechanics: MechanicsService,
    store: StateStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from grimoire.event_bus import Event, EventBus

    bus = EventBus()
    chars = CharactersService(library, mechanics, event_bus=bus)

    await _seed_world(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")
    await chars.create("wod-london", _character_data())
    ref = "library:worlds/wod-london/characters/alistair"

    counts = _patch_render_counters(monkeypatch)
    await chars.get_full_card(ref, "camp-1")
    assert counts["full"] == 1

    # Emit event — should invalidate the cache
    await bus.emit(Event(type="library_entity_changed", payload={"kind": "character"}))
    await chars.get_full_card(ref, "camp-1")
    assert counts["full"] == 2


async def test_event_bus_ignores_non_character_kinds(
    library: LibraryService,
    mechanics: MechanicsService,
    store: StateStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from grimoire.event_bus import Event, EventBus

    bus = EventBus()
    chars = CharactersService(library, mechanics, event_bus=bus)

    await _seed_world(store, "wod-london")
    await _bind_campaign(store, "camp-1", "wod-london")
    await chars.create("wod-london", _character_data())
    ref = "library:worlds/wod-london/characters/alistair"

    counts = _patch_render_counters(monkeypatch)
    await chars.get_full_card(ref, "camp-1")
    assert counts["full"] == 1

    # Non-character entity change should NOT invalidate
    await bus.emit(Event(type="library_entity_changed", payload={"kind": "location"}))
    await chars.get_full_card(ref, "camp-1")
    assert counts["full"] == 1
