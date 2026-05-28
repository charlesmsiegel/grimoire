"""Heuristic strategy tests."""

from __future__ import annotations

from grimoire.extractor.heuristics import (
    detect_missing_context_names,
    detect_missing_mechanics,
    find_proper_noun_candidates,
    run_heuristics,
)
from grimoire.types.extraction import FlagLevel
from grimoire.types.scene import Scene
from grimoire.types.state import StateSnapshot


def test_proper_noun_finds_new_name():
    text = "julian noticed Margaux carrying the tea tray."
    cands = find_proper_noun_candidates(text, known_names={"julian"}, max_candidates=5)
    names = [c.proposed_name for c in cands]
    assert "Margaux" in names
    assert "julian" not in names


def test_proper_noun_ignores_sentence_initial_capitals():
    text = "The orchard was quiet. He sighed."
    cands = find_proper_noun_candidates(text, known_names=set(), max_candidates=5)
    assert all(c.proposed_name not in {"The", "He"} for c in cands)


def test_proper_noun_caps_candidates():
    # Use mid-sentence positions so the sentence-initial filter doesn't drop them.
    names = ["Anya", "Boris", "Cordelia", "Dimitri", "Elena", "Fyodor"]
    text = "She greeted " + ", and ".join(names) + " warmly."
    cands = find_proper_noun_candidates(text, known_names=set(), max_candidates=3)
    assert len(cands) == 3


def test_wound_without_roll_flags_missing_mechanic():
    flags = detect_missing_mechanics("julian took heavy damage.", pre_roll_resolved=False)
    assert any(f.code == "wound_without_roll" for f in flags)


def test_wound_with_roll_hint_skips_flag():
    flags = detect_missing_mechanics(
        "He rolled three successes. julian took heavy damage.",
        pre_roll_resolved=False,
    )
    assert flags == []


def test_wound_with_pre_roll_resolved_skips_flag():
    flags = detect_missing_mechanics("julian took heavy damage.", pre_roll_resolved=True)
    assert flags == []


def test_missing_context_name_flagged_when_repeated():
    text = "Margaux brought the tea. Margaux smiled at the cook."
    scene = Scene(
        id="s",
        campaign_id="c",
        ordinal=1,
        slug="s",
        file_path="/tmp/s.md",
        present_character_refs=["winifred"],
    )
    flags = detect_missing_context_names(text, scene=scene, snapshot=None)
    assert any(f.level == FlagLevel.MISSING_CONTEXT and "Margaux" in f.message for f in flags)


def test_run_heuristics_returns_candidates_and_flags():
    text = "Margaux brought the tea. julian took heavy damage."
    scene = Scene(
        id="s",
        campaign_id="c",
        ordinal=1,
        slug="s",
        file_path="/tmp/s.md",
        present_character_refs=["julian"],
    )
    snapshot = StateSnapshot(campaign_id="c", scene_id="s")
    out = run_heuristics(
        text,
        scene=scene,
        snapshot=snapshot,
        pre_roll_resolved=False,
        max_candidates=5,
        campaign_id="c",
    )
    assert any(c.proposed_name == "Margaux" for c in out.candidates)
    assert any(f.code == "wound_without_roll" for f in out.flags)
