"""Top-level World YAML config (spec 09 §Configuration).

Nested pydantic models with sensible defaults and an optional ``from_yaml``
loader. Threaded into :class:`WorldService` at construction time.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from grimoire.files import load_yaml


class WeatherConfig(BaseModel):
    model_config = ConfigDict(frozen=True, protected_namespaces=())

    enabled: bool = True
    seed_per_campaign: bool = True
    model: str = "rule_based"


class LoreConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    keyword_match: bool = True
    keyword_min_length: int = 4
    max_lore_in_archive: int = 5


class CompositionPolicyConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    multiple_calendars_policy: Literal["pick", "merge_warn", "error"] = "pick"


class WorldConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    weather: WeatherConfig = WeatherConfig()
    lore: LoreConfig = LoreConfig()
    atmosphere_auto_generate: bool = True
    composition: CompositionPolicyConfig = CompositionPolicyConfig()

    @classmethod
    def from_yaml(cls, path: Path) -> WorldConfig:
        if not path.exists():
            return cls()
        raw = load_yaml(path) or {}
        if not isinstance(raw, dict):
            return cls()
        return cls.model_validate(raw)


__all__ = [
    "CompositionPolicyConfig",
    "LoreConfig",
    "WeatherConfig",
    "WorldConfig",
]
