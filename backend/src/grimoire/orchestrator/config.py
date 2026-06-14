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
class BackgroundWorkConfig:
    drift_check_sampling: float = 0.25


@dataclass
class ErrorConfig:
    retry_extractor_on_parse_failure: int = 1
    surface_partial_response_on_llm_error: bool = True


@dataclass
class SwipesConfig:
    """Retention policy for per-post alternates (swipes).

    ``max_alternates_per_post`` caps the non-primary, non-pinned alternates
    kept on each post; when ``regenerate_post`` would exceed the cap the
    oldest eligible alternate is purged. ``auto_purge_older_than_days`` is
    the threshold used by :meth:`OrchestratorService.purge_stale_alternates`
    when a caller (or background sweep) runs vacuum.
    """

    max_alternates_per_post: int = 5
    auto_purge_older_than_days: int = 30


@dataclass
class SpeakerLoopConfig:
    timeout_seconds: float = 300.0
    speaker_select_max_tokens: int = 50


@dataclass
class OrchestratorConfig:
    turn_timeout_seconds: float = 180.0
    main_llm_task: str = "main"
    scene_break: SceneBreakConfig = None  # type: ignore[assignment]
    pre_roll: PreRollConfig = None  # type: ignore[assignment]
    background_work: BackgroundWorkConfig = None  # type: ignore[assignment]
    errors: ErrorConfig = None  # type: ignore[assignment]
    heartbeat: HeartbeatConfig = None  # type: ignore[assignment]
    swipes: SwipesConfig = None  # type: ignore[assignment]
    speaker_loop: SpeakerLoopConfig = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.scene_break is None:
            self.scene_break = SceneBreakConfig()
        if self.pre_roll is None:
            self.pre_roll = PreRollConfig()
        if self.background_work is None:
            self.background_work = BackgroundWorkConfig()
        if self.errors is None:
            self.errors = ErrorConfig()
        if self.heartbeat is None:
            self.heartbeat = HeartbeatConfig()
        if self.swipes is None:
            self.swipes = SwipesConfig()
        if self.speaker_loop is None:
            self.speaker_loop = SpeakerLoopConfig()


__all__ = [
    "BackgroundWorkConfig",
    "ErrorConfig",
    "HeartbeatConfig",
    "OrchestratorConfig",
    "PreRollConfig",
    "SceneBreakConfig",
    "SpeakerLoopConfig",
    "SwipesConfig",
]
