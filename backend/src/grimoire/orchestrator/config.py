"""Configuration for :class:`OrchestratorService` (spec 01 §Configuration)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SceneBreakConfig:
    auto_threshold: float = 0.8
    prompt_threshold: float = 0.5
    prompt_resume_timeout_seconds: float = 60.0


@dataclass
class HeartbeatConfig:
    enabled: bool = True
    interval_seconds: float = 10.0


@dataclass
class PreRollConfig:
    """When to surface ``ProposedRoll``s to the player before resolving.

    - ``"never"`` (default): the orchestrator resolves every proposal
      inline and threads results into the prompt; the player never sees a
      confirmation step.
    - ``"always"``: every proposal is paused for confirmation. The turn
      emits ``pre_roll_pending`` and waits for
      :meth:`OrchestratorService.resolve_pre_roll`.
    - ``"high_stakes"``: only proposals flagged ``high_stakes=True`` pause
      the turn. Other proposals resolve inline.
    """

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
    heartbeat: HeartbeatConfig = None  # type: ignore[assignment]

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
        if self.heartbeat is None:
            self.heartbeat = HeartbeatConfig()


__all__ = [
    "BackgroundWorkConfig",
    "ErrorConfig",
    "HeartbeatConfig",
    "MultiPCConfig",
    "OrchestratorConfig",
    "PreRollConfig",
    "SceneBreakConfig",
]
