"""Configuration knobs for the Mechanics module.

Mirrors the YAML structure from spec 06 §Configuration. Defaults match the
spec so a fresh install discovers mechanics modules under
``data/mechanics/`` and validates sheets on write.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ValidationConfig:
    strict_sheets: bool = True
    strict_events: bool = False


@dataclass(frozen=True)
class RngConfig:
    per_branch_seed: bool = True


@dataclass(frozen=True)
class DefaultsConfig:
    no_mechanics_warning: bool = False


@dataclass(frozen=True)
class MechanicsConfig:
    root: Path
    reload_on_file_change: bool = False
    validation: ValidationConfig = field(default_factory=ValidationConfig)
    rng: RngConfig = field(default_factory=RngConfig)
    defaults: DefaultsConfig = field(default_factory=DefaultsConfig)

    @classmethod
    def for_data_root(cls, data_root: Path) -> MechanicsConfig:
        return cls(root=data_root / "mechanics")


__all__ = [
    "DefaultsConfig",
    "MechanicsConfig",
    "RngConfig",
    "ValidationConfig",
]
