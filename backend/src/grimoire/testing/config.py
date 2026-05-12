"""Configuration for the testing infrastructure (spec 17 §Configuration)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


class LLMMode(StrEnum):
    REPLAY = "replay"
    MOCK = "mock"
    REAL = "real"


@dataclass(frozen=True)
class FrozenCampaignConfig:
    enforce_strict: bool = True


@dataclass(frozen=True)
class PerformanceConfig:
    regression_threshold_percent: float = 20.0
    benchmark_iterations: int = 5


@dataclass(frozen=True)
class ConformanceConfig:
    run_on_plugin_load: bool = True
    run_in_ci: bool = True


@dataclass(frozen=True)
class TestingConfig:
    llm_mode: LLMMode = LLMMode.MOCK
    fixture_directory: Path = Path("tests/fixtures")
    fail_on_missing_fixture: bool = True
    frozen_campaign: FrozenCampaignConfig = field(default_factory=FrozenCampaignConfig)
    performance: PerformanceConfig = field(default_factory=PerformanceConfig)
    conformance: ConformanceConfig = field(default_factory=ConformanceConfig)


__all__ = [
    "ConformanceConfig",
    "FrozenCampaignConfig",
    "LLMMode",
    "PerformanceConfig",
    "TestingConfig",
]
