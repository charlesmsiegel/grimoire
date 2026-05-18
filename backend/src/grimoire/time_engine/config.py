"""Configuration for the Time Engine.

Mirrors the YAML block in spec 07 §Configuration. Defaults are tuned for the
"a few NPCs tick at once, narrative digest from a cheap model" path; tests
override the parts they need.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Literal

TimePrecision = Literal["minute", "hour", "day", "season"]


@dataclass(frozen=True)
class SignificanceConfig:
    """Inputs to the NPC tick significance filter (spec §NPC tick architecture).

    A character ticks if **any** of these rules apply. The filter is a hard
    cap; without it a 50+ NPC campaign would melt the LLM budget on every
    skip.
    """

    always_tick_roles: tuple[str, ...] = ("pc", "major_npc")
    tick_with_open_commitment: bool = True
    tick_in_household: bool = True
    recent_post_window: int = 20
    max_npcs_per_advance: int = 15


@dataclass(frozen=True)
class TimeEngineConfig:
    npc_tick_task: str = "npc_tick"
    digest_task: str = "scene_summary"
    npc_tick_parallelism: int = 4
    significance: SignificanceConfig = field(default_factory=SignificanceConfig)
    faction_tick_resolution: timedelta = field(default_factory=lambda: timedelta(days=30))
    digest_narrative: bool = True
    scheduled_event_pre_notice: timedelta = field(default_factory=lambda: timedelta(days=7))
    commitment_stale_threshold: timedelta = field(default_factory=lambda: timedelta(days=180))
    # The default in-game time for a campaign that has never had advance()
    # called. Matches the calendar table being NULL on first read.
    default_initial_time_iso: str | None = None
    # §10 Configurable time precision per campaign — quantizes both
    # ``from_time`` and ``to_time`` of every advance to the requested
    # granularity. Affects digest rendering through the same anchors.
    precision: TimePrecision = "minute"
    # §8 Reversibility checkpointing — when ``propose_advance`` sees a
    # duration greater than this, the returned ``CheckpointSuggestion``
    # carries ``threshold_exceeded=True`` and emits a
    # ``time_advance_checkpoint_suggested`` event for the UI.
    checkpoint_threshold: timedelta = field(default_factory=lambda: timedelta(days=7))
    # §9 Drift check — when enabled, the engine runs the injected drift
    # check callable per NPC tick summary and surfaces the warnings on the
    # ``TimeAdvanceResult``.
    drift_check_enabled: bool = True
    # §5 Faction tick depth — controls slow resource decay applied per
    # month of advancement. ``leader_tick`` produces a notable-action entry
    # per faction with a populated ``leaders`` list.
    faction_resource_decay_per_month: float = 0.02
    faction_leader_tick: bool = True
    # §2 Shared inter-NPC events pre-pass — when disabled, the engine skips
    # the pre-pass entirely. Helpful for tests that want to assert on
    # individual ticks in isolation.
    shared_events_enabled: bool = True


__all__ = ["SignificanceConfig", "TimeEngineConfig", "TimePrecision"]
