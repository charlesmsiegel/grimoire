"""Tests for Scene type additions."""

from grimoire.scenes.types import Scene


def test_pc_absent_true_when_no_pcs():
    scene = Scene(
        id="s1",
        campaign_id="c1",
        ordinal=1,
        slug="test",
        title="Test",
        present_character_refs=["npc-a", "npc-b"],
        present_pc_refs=[],
    )
    assert scene.pc_absent is True


def test_pc_absent_false_when_pcs_present():
    scene = Scene(
        id="s1",
        campaign_id="c1",
        ordinal=1,
        slug="test",
        title="Test",
        present_character_refs=["pc-alice", "npc-b"],
        present_pc_refs=["pc-alice"],
    )
    assert scene.pc_absent is False
