"""Default per-field decay table + override merging."""

from __future__ import annotations

from grimoire.transient_state.decay import (
    DEFAULT_DECAY,
    DecaySpec,
    decay_for,
    merge_overrides,
)
from grimoire.types.transient import EntityKind


def test_character_mood_default():
    spec = decay_for(EntityKind.CHARACTER, "mood")
    assert spec.posts == 10
    assert spec.in_game_seconds == 3600


def test_character_internal_thought_default():
    spec = decay_for(EntityKind.CHARACTER, "internal_thought")
    assert spec.posts == 1


def test_unknown_field_returns_zero_spec():
    spec = decay_for(EntityKind.CHARACTER, "unknown_field")
    assert spec == DecaySpec()


def test_scene_scoped_location_field():
    spec = decay_for(EntityKind.LOCATION, "ambient_mood")
    assert spec.scene_scope is True


def test_relationship_tone_reinforce_extends():
    spec = decay_for(EntityKind.CHARACTER, "relationship_tone_toward_pc")
    assert spec.scene_scope is True
    assert spec.reinforce_extends is True


def test_merge_overrides_replaces_field_spec():
    overrides = {"character": {"mood": {"posts": 20, "in_game_seconds": 7200}}}
    merged = merge_overrides(overrides)
    spec = merged[EntityKind.CHARACTER]["mood"]
    assert spec.posts == 20
    assert spec.in_game_seconds == 7200


def test_merge_overrides_preserves_unmentioned_fields():
    overrides = {"character": {"mood": {"posts": 99}}}
    merged = merge_overrides(overrides)
    assert merged[EntityKind.CHARACTER]["intent"] == DEFAULT_DECAY[EntityKind.CHARACTER]["intent"]


def test_merge_overrides_supports_in_game_hours():
    overrides = {"character": {"mood": {"in_game_hours": 2}}}
    merged = merge_overrides(overrides)
    assert merged[EntityKind.CHARACTER]["mood"].in_game_seconds == 7200


def test_merge_overrides_ignores_unknown_kind():
    overrides = {"phantom": {"mood": {"posts": 5}}}
    merged = merge_overrides(overrides)
    assert EntityKind.CHARACTER in merged
