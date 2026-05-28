from __future__ import annotations

from datetime import datetime

from grimoire.scenes.boundary import BoundaryConfig, detect_scene_break
from grimoire.scenes.types import Scene


def _scene(**overrides) -> Scene:
    base = Scene(
        id="campaign-a:0001-elysium-opening",
        campaign_id="campaign-a",
        ordinal=1,
        slug="elysium-opening",
        title="Elysium",
        location_ref="elysium",
        in_game_start=datetime(2024, 10, 31, 22, 0, 0),
        in_game_end=datetime(2024, 10, 31, 23, 0, 0),
        present_character_refs=["alistair", "prince-of-london"],
        present_pc_refs=["alistair"],
    )
    for k, v in overrides.items():
        setattr(base, k, v)
    return base


def test_user_signal_forces_break_with_full_confidence() -> None:
    decision = detect_scene_break(_scene(), "/end scene")
    assert decision.is_break is True
    assert decision.confidence == 1.0
    assert decision.reason == "user_signal"


def test_time_jump_prose_marker_is_high_confidence() -> None:
    decision = detect_scene_break(_scene(), "Hours later, the streets are quiet.")
    assert decision.is_break is True
    assert decision.reason == "explicit"
    assert decision.confidence >= 0.8


def test_explicit_location_change_dominates() -> None:
    decision = detect_scene_break(
        _scene(),
        "We adjourned to the library.",
        proposed_location_ref="library",
    )
    assert decision.is_break is True
    assert decision.reason == "location_change"


def test_no_signals_no_break() -> None:
    decision = detect_scene_break(_scene(), "I sip the wine and wait.")
    assert decision.is_break is False
    assert decision.reason == "none"


def test_time_gap_against_scene_end_triggers() -> None:
    decision = detect_scene_break(
        _scene(),
        "I rise.",
        now_in_game=datetime(2024, 11, 1, 6, 0, 0),
    )
    assert decision.is_break is True
    assert decision.reason == "time_gap"


def test_cast_change_detection() -> None:
    decision = detect_scene_break(
        _scene(),
        "I push through the doors.",
        proposed_present_cast=["beatrice", "vance"],
    )
    assert decision.is_break is True
    assert decision.reason in {"cast_change", "location_change"}


def test_confidence_threshold_prompt_gates_borderline() -> None:
    cfg = BoundaryConfig(confidence_threshold_prompt=0.95)
    decision = detect_scene_break(
        _scene(),
        "We adjourned to the library.",
        config=cfg,
    )
    # 0.65 confidence for prose-only location transition < 0.95 threshold.
    assert decision.is_break is False
    assert decision.confidence > 0.0


def test_no_active_scene_returns_no_break() -> None:
    decision = detect_scene_break(None, "Hours later, dawn breaks.")
    assert decision.is_break is False


def test_tonal_shift_caps_and_exclamation() -> None:
    decision = detect_scene_break(_scene(), "ENOUGH! I will not hear another word!")
    assert decision.reason == "tonal_shift"
    assert decision.confidence == 0.55
    # 0.55 sits above the default prompt threshold (0.5) — surface to user.
    assert decision.is_break is True
    assert decision.proposed_new_scene is not None
    assert decision.proposed_new_scene.campaign_id == "campaign-a"


def test_tonal_shift_two_registers() -> None:
    decision = detect_scene_break(
        _scene(),
        "I weep openly, then snarl through the tears.",
    )
    assert decision.reason == "tonal_shift"
    assert decision.confidence == 0.55


def test_tonal_shift_yields_to_stronger_signal() -> None:
    decision = detect_scene_break(
        _scene(),
        "Hours later, I scream into the empty street!",
    )
    # Time-jump prose (0.85) outranks the tonal_shift signal (0.55).
    assert decision.reason == "explicit"
    assert decision.confidence >= 0.8


def test_tonal_shift_below_threshold_does_not_break() -> None:
    cfg = BoundaryConfig(confidence_threshold_prompt=0.6)
    decision = detect_scene_break(
        _scene(),
        "ENOUGH! I will not hear another word!",
        config=cfg,
    )
    assert decision.reason == "tonal_shift"
    assert decision.is_break is False


def test_tonal_shift_quiet_prose_does_not_trigger() -> None:
    decision = detect_scene_break(_scene(), "I nod and consider his offer.")
    assert decision.reason == "none"
    assert decision.is_break is False
