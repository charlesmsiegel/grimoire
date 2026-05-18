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
    # Spec 17 §Open questions resolved: default is to run conformance once at
    # install (i.e. the rescan that first sees a plugin) and skip re-running
    # on subsequent loads. Set ``run_on_plugin_load=True`` to re-check on
    # every load — useful when iterating on a plugin locally.
    run_on_install: bool = True
    run_on_plugin_load: bool = False
    run_in_ci: bool = True


@dataclass(frozen=True)
class TestingConfig:
    __test__ = False  # pytest: this is a config dataclass, not a test class

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
