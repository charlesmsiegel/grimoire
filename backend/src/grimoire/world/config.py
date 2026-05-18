"""Top-level World YAML config (spec 09 §Configuration).

Mirrors the OrchestratorConfig shape: nested dataclasses with sensible
defaults, an optional ``from_yaml`` loader. Threaded into
:class:`WorldService` at construction time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_CALENDAR_POLICIES: frozenset[str] = frozenset({"pick", "merge_warn", "error"})


@dataclass(frozen=True, slots=True)
class WeatherConfig:
    enabled: bool = True
    seed_per_campaign: bool = True
    model: str = "rule_based"


@dataclass(frozen=True, slots=True)
class LoreConfig:
    keyword_match: bool = True
    keyword_min_length: int = 4
    max_lore_in_archive: int = 5


@dataclass(frozen=True, slots=True)
class CompositionPolicyConfig:
    # 'pick' | 'merge_warn' | 'error'.
    multiple_calendars_policy: str = "pick"


@dataclass(frozen=True, slots=True)
class WorldConfig:
    weather: WeatherConfig = field(default_factory=WeatherConfig)
    lore: LoreConfig = field(default_factory=LoreConfig)
    atmosphere_auto_generate: bool = True
    composition: CompositionPolicyConfig = field(default_factory=CompositionPolicyConfig)

    @classmethod
    def from_yaml(cls, path: Path) -> WorldConfig:
        if not path.exists():
            return cls()
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            return cls()
        return cls._from_mapping(raw)

    @classmethod
    def _from_mapping(cls, raw: dict[str, Any]) -> WorldConfig:
        w = raw.get("weather") or {}
        lo = raw.get("lore") or {}
        comp = raw.get("composition") or {}
        policy = str(comp.get("multiple_calendars_policy") or "pick")
        if policy not in _CALENDAR_POLICIES:
            raise ValueError(
                f"multiple_calendars_policy must be one of {sorted(_CALENDAR_POLICIES)!r}, "
                f"got {policy!r}"
            )
        return cls(
            weather=WeatherConfig(
                enabled=bool(w.get("enabled", True)),
                seed_per_campaign=bool(w.get("seed_per_campaign", True)),
                model=str(w.get("model") or "rule_based"),
            ),
            lore=LoreConfig(
                keyword_match=bool(lo.get("keyword_match", True)),
                keyword_min_length=int(lo.get("keyword_min_length", 4)),
                max_lore_in_archive=int(lo.get("max_lore_in_archive", 5)),
            ),
            atmosphere_auto_generate=bool(raw.get("atmosphere_auto_generate", True)),
            composition=CompositionPolicyConfig(multiple_calendars_policy=policy),
        )


__all__ = [
    "CompositionPolicyConfig",
    "LoreConfig",
    "WeatherConfig",
    "WorldConfig",
]
