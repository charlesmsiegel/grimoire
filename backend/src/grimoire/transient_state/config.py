"""TransientStateConfig — per-campaign overrides."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from grimoire.files import load_yaml
from grimoire.transient_state.decay import DEFAULT_DECAY, DecaySpec, merge_overrides
from grimoire.types.transient import EntityKind


@dataclass(frozen=True, slots=True)
class PromoteToFactConfig:
    reinforcement_count: int = 5
    require_evidence_diversity: bool = True


@dataclass(frozen=True, slots=True)
class VacuumConfig:
    enabled: bool = True
    retain_superseded_days: int = 30


def _default_decay_table() -> dict[EntityKind, dict[str, DecaySpec]]:
    return {kind: dict(spec) for kind, spec in DEFAULT_DECAY.items()}


@dataclass(frozen=True, slots=True)
class TransientStateConfig:
    auto_apply_threshold: float = 0.85
    review_threshold: float = 0.60
    conflict_window_posts: int = 10
    promote_to_fact: PromoteToFactConfig = field(default_factory=PromoteToFactConfig)
    vacuum: VacuumConfig = field(default_factory=VacuumConfig)
    decay_table: dict[EntityKind, dict[str, DecaySpec]] = field(
        default_factory=_default_decay_table
    )

    @classmethod
    def from_yaml(cls, path: Path) -> TransientStateConfig:
        if not path.exists():
            return cls()
        raw = load_yaml(path) or {}
        if not isinstance(raw, dict):
            return cls()
        promote_raw = raw.get("promote_to_fact") or {}
        vacuum_raw = raw.get("vacuum") or {}
        decay_raw = raw.get("decay") or {}
        return cls(
            auto_apply_threshold=float(raw.get("auto_apply_threshold", 0.85)),
            review_threshold=float(raw.get("review_threshold", 0.60)),
            conflict_window_posts=int(raw.get("conflict_window_posts", 10)),
            promote_to_fact=PromoteToFactConfig(
                reinforcement_count=int(promote_raw.get("reinforcement_count", 5)),
                require_evidence_diversity=bool(
                    promote_raw.get("require_evidence_diversity", True)
                ),
            ),
            vacuum=VacuumConfig(
                enabled=bool(vacuum_raw.get("enabled", True)),
                retain_superseded_days=int(vacuum_raw.get("retain_superseded_days", 30)),
            ),
            decay_table=merge_overrides(decay_raw),
        )
