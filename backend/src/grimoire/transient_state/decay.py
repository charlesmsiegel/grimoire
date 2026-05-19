"""Per-field decay defaults + override merging.

Spec values:
    posts             - decay after N posts in the field's entity's scene
    in_game_seconds   - decay after N in-game seconds since last set
    scene_scope       - decay at scene end (resets unless reinforced)
    reinforce_extends - new write extends the previous deadline
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from grimoire.types.transient import EntityKind


@dataclass(frozen=True, slots=True)
class DecaySpec:
    posts: int | None = None
    in_game_seconds: int | None = None
    scene_scope: bool = False
    reinforce_extends: bool = False


_ONE_HOUR = 3600
_ONE_DAY = 86_400


DEFAULT_DECAY: dict[EntityKind, dict[str, DecaySpec]] = {
    EntityKind.CHARACTER: {
        "mood": DecaySpec(posts=10, in_game_seconds=_ONE_HOUR),
        "intent": DecaySpec(posts=5, scene_scope=True),
        "current_action": DecaySpec(posts=1),
        "posture": DecaySpec(posts=3),
        "internal_thought": DecaySpec(posts=1),
        "focus_of_attention": DecaySpec(posts=2),
        "relationship_tone_toward_pc": DecaySpec(scene_scope=True, reinforce_extends=True),
        "energy_level": DecaySpec(in_game_seconds=_ONE_DAY),
    },
    EntityKind.LOCATION: {
        "ambient_mood": DecaySpec(scene_scope=True),
        "noteworthy_detail": DecaySpec(scene_scope=True),
        "occupancy_summary": DecaySpec(scene_scope=True),
    },
    EntityKind.FACTION: {
        "alert_level": DecaySpec(),
        "internal_mood": DecaySpec(),
    },
    EntityKind.SCENE: {
        "emotional_temperature": DecaySpec(scene_scope=True),
        "dominant_mood": DecaySpec(scene_scope=True),
        "pacing": DecaySpec(scene_scope=True),
    },
}


def decay_for(kind: EntityKind, field_name: str) -> DecaySpec:
    return DEFAULT_DECAY.get(kind, {}).get(field_name, DecaySpec())


def merge_overrides(
    overrides: Mapping[str, Mapping[str, Mapping[str, object]]],
) -> dict[EntityKind, dict[str, DecaySpec]]:
    """Merge a per-campaign override map onto DEFAULT_DECAY."""
    result: dict[EntityKind, dict[str, DecaySpec]] = {
        kind: dict(defaults) for kind, defaults in DEFAULT_DECAY.items()
    }
    for kind_str, fields in (overrides or {}).items():
        try:
            kind = EntityKind(kind_str)
        except ValueError:
            continue
        bucket = result.setdefault(kind, {})
        for field_name, spec_dict in (fields or {}).items():
            bucket[field_name] = _spec_from_dict(spec_dict)
    return result


def _spec_from_dict(d: Mapping[str, object]) -> DecaySpec:
    posts = d.get("posts")
    igs = d.get("in_game_seconds")
    if igs is None and (hrs := d.get("in_game_hours")) is not None:
        igs = int(hrs) * _ONE_HOUR  # type: ignore[arg-type]
    return DecaySpec(
        posts=int(posts) if posts is not None else None,  # type: ignore[arg-type]
        in_game_seconds=int(igs) if igs is not None else None,  # type: ignore[arg-type]
        scene_scope=bool(d.get("scene_scope", False)),
        reinforce_extends=bool(d.get("reinforce_extends", False)),
    )
