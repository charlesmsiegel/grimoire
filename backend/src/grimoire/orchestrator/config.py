"""Configuration for :class:`OrchestratorService` (spec 01 §Configuration)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SceneBreakConfig:
    auto_threshold: float = 0.8
    prompt_threshold: float = 0.5


@dataclass
class PreRollConfig:
    # 'always' | 'never' | 'high_stakes_only'. Spec 01.
    confirm_before_executing: str = "never"


@dataclass
class MultiPCConfig:
    advance_required: bool = True


@dataclass
class BackgroundWorkConfig:
    drift_check_sampling: float = 0.25
    npc_tick_after_each_turn: bool = True


@dataclass
class ErrorConfig:
    retry_extractor_on_parse_failure: int = 1
    surface_partial_response_on_llm_error: bool = True


@dataclass
class OrchestratorConfig:
    per_campaign_concurrency: int = 1
    turn_timeout_seconds: float = 180.0
    stream_response: bool = True
    main_llm_task: str = "main"
    scene_break: SceneBreakConfig = None  # type: ignore[assignment]
    pre_roll: PreRollConfig = None  # type: ignore[assignment]
    multi_pc: MultiPCConfig = None  # type: ignore[assignment]
    background_work: BackgroundWorkConfig = None  # type: ignore[assignment]
    errors: ErrorConfig = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.scene_break is None:
            self.scene_break = SceneBreakConfig()
        if self.pre_roll is None:
            self.pre_roll = PreRollConfig()
        if self.multi_pc is None:
            self.multi_pc = MultiPCConfig()
        if self.background_work is None:
            self.background_work = BackgroundWorkConfig()
        if self.errors is None:
            self.errors = ErrorConfig()


__all__ = [
    "BackgroundWorkConfig",
    "ErrorConfig",
    "MultiPCConfig",
    "OrchestratorConfig",
    "PreRollConfig",
    "SceneBreakConfig",
]
