"""Tests for the InclusionReason enum + ContextSource.inclusion_reasons field."""

from __future__ import annotations

from grimoire.types.context import ContextSource
from grimoire.types.inclusion_reasons import InclusionReason
from grimoire.types.state import ContextTier


def test_enum_has_all_canonical_reasons() -> None:
    assert InclusionReason.PRESENT_IN_SCENE.value == "present_in_scene"
    assert InclusionReason.MENTIONED_IN_RECENT_POSTS.value == "mentioned_in_recent_posts"
    assert InclusionReason.COMMITMENT_OPEN_TO_PC.value == "commitment_open_to_pc"
    assert InclusionReason.KEYWORD_TRIGGERED.value == "keyword_triggered"
    assert InclusionReason.RELATIONSHIP_TO_PRESENT.value == "relationship_to_present"
    assert InclusionReason.PINNED_BY_USER.value == "pinned_by_user"
    assert InclusionReason.SCENE_ANCHOR.value == "scene_anchor"
    assert InclusionReason.MECHANICS_RELEVANT.value == "mechanics_relevant"
    assert InclusionReason.STYLE_GUIDE_ACTIVE.value == "style_guide_active"
    assert InclusionReason.PC_CARD.value == "pc_card"
    assert InclusionReason.COMPOSITION_DEFAULT.value == "composition_default"
    assert InclusionReason.EXTRAS_PINNED_TO_HUD.value == "extras_pinned_to_hud"
    assert InclusionReason.EXTRAS_DEFAULT_VISIBLE.value == "extras_default_visible"
    assert InclusionReason.LORE_BEFORE_CAST.value == "lore_before_cast"
    assert InclusionReason.LORE_AFTER_CAST.value == "lore_after_cast"
    assert InclusionReason.LORE_AT_DEPTH.value == "lore_at_depth"
    assert InclusionReason.LORE_ARCHIVE.value == "lore_archive"
    assert InclusionReason.TRANSIENT_STATE_ACTIVE.value == "transient_state_active"


def test_context_source_default_reasons_empty() -> None:
    s = ContextSource(
        kind="character",
        scope="library",
        owner_id="x",
        tier=ContextTier.SPOTLIGHT,
    )
    assert s.inclusion_reasons == []
    assert s.source_id == ""


def test_context_source_carries_reasons_list() -> None:
    s = ContextSource(
        kind="character",
        scope="campaign-local",
        owner_id="char_florence",
        tier=ContextTier.SPOTLIGHT,
        inclusion_reasons=[
            InclusionReason.PRESENT_IN_SCENE,
            InclusionReason.COMMITMENT_OPEN_TO_PC,
        ],
    )
    assert len(s.inclusion_reasons) == 2
    assert InclusionReason.PRESENT_IN_SCENE in s.inclusion_reasons
    assert InclusionReason.COMMITMENT_OPEN_TO_PC in s.inclusion_reasons


def test_context_source_serialization_round_trip() -> None:
    s = ContextSource(
        kind="lore",
        scope="library",
        owner_id="library:worlds/w1/lore/l1",
        tier=ContextTier.ARCHIVE,
        inclusion_reasons=[InclusionReason.LORE_ARCHIVE, InclusionReason.KEYWORD_TRIGGERED],
        source_id="src_abcdef123456",
    )
    dumped = s.model_dump(mode="json")
    assert dumped["inclusion_reasons"] == ["lore_archive", "keyword_triggered"]
    reloaded = ContextSource.model_validate(dumped)
    assert reloaded.inclusion_reasons == s.inclusion_reasons
    assert reloaded.source_id == "src_abcdef123456"
