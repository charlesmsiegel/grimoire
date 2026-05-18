"""WorldConfig nested dataclass (§1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.world.config import WorldConfig


def test_defaults_match_spec() -> None:
    cfg = WorldConfig()
    assert cfg.weather.enabled is True
    assert cfg.weather.seed_per_campaign is True
    assert cfg.weather.model == "rule_based"
    assert cfg.lore.keyword_match is True
    assert cfg.lore.keyword_min_length == 4
    assert cfg.lore.max_lore_in_archive == 5
    assert cfg.atmosphere_auto_generate is True
    assert cfg.composition.multiple_calendars_policy == "pick"


def test_from_yaml_parses_block(tmp_path: Path) -> None:
    path = tmp_path / "world.yaml"
    path.write_text(
        "weather:\n"
        "  enabled: false\n"
        "  seed_per_campaign: false\n"
        "  model: stochastic\n"
        "lore:\n"
        "  keyword_match: false\n"
        "  keyword_min_length: 6\n"
        "  max_lore_in_archive: 3\n"
        "atmosphere_auto_generate: false\n"
        "composition:\n"
        "  multiple_calendars_policy: error\n",
        encoding="utf-8",
    )
    cfg = WorldConfig.from_yaml(path)
    assert cfg.weather.enabled is False
    assert cfg.weather.seed_per_campaign is False
    assert cfg.weather.model == "stochastic"
    assert cfg.lore.keyword_match is False
    assert cfg.lore.keyword_min_length == 6
    assert cfg.lore.max_lore_in_archive == 3
    assert cfg.atmosphere_auto_generate is False
    assert cfg.composition.multiple_calendars_policy == "error"


def test_from_yaml_missing_file_returns_defaults(tmp_path: Path) -> None:
    cfg = WorldConfig.from_yaml(tmp_path / "nope.yaml")
    assert cfg == WorldConfig()


def test_from_yaml_unknown_calendar_policy_rejected(tmp_path: Path) -> None:
    path = tmp_path / "world.yaml"
    path.write_text("composition:\n  multiple_calendars_policy: surprise\n", encoding="utf-8")
    with pytest.raises(ValueError, match="multiple_calendars_policy"):
        WorldConfig.from_yaml(path)
