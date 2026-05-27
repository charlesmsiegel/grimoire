"""Time-advance result types.

`InGameTime` and `Duration` themselves live in `common.py` to avoid a cycle
with the modules they describe events about.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from .common import Duration, InGameTime, Json
from .continuity import Commitment
from .state import StateDelta


class TimeAdvanceReason(StrEnum):
    EXPLICIT_USER = "explicit_user"
    SCENE_NARRATION = "scene_narration"
    SCENE_BREAK = "scene_break"
    ACTIVITY_DURATION = "activity_duration"
    SCHEDULED_EVENT = "scheduled_event"


class SharedEvent(BaseModel):
    """An inter-NPC event produced by the shared-events pre-pass (§2).

    Generated once per advance, seeded with the full list of significant NPCs,
    then surfaced to each participating NPC's individual tick so their
    individual summaries can reference the same factual occurrence rather
    than each NPC inventing an independent version of "we had a party".
    """

    id: str
    participants: list[str] = Field(default_factory=list)
    summary: str
    in_game_at: InGameTime
    details: Json = Field(default_factory=dict)


class DriftWarning(BaseModel):
    """An NPC-tick coherence flag from the post-tick drift check (§9)."""

    character_id: str
    severity: Literal["info", "warning", "critical"] = "warning"
    summary: str
    evidence: list[str] = Field(default_factory=list)


class ScheduledEvent(BaseModel):
    id: str
    campaign_id: str
    at: InGameTime
    label: str
    kind: str  # 'holiday', 'recurring', 'one_off', 'plot_beat'
    payload: Json = Field(default_factory=dict)
    triggered: bool = False


class WeatherChange(BaseModel):
    location_ref: str
    at: InGameTime
    summary: str
    details: Json = Field(default_factory=dict)


class NpcTickSummary(BaseModel):
    character_id: str
    duration: Duration
    state_at_end: Json
    activities: list[str]
    relationships_changed: list[Json] = Field(default_factory=list)
    new_facts_about_them: list[Json] = Field(default_factory=list)
    secrets_kept: list[str] = Field(default_factory=list)
    next_intent: str = ""
    should_seek_pc: bool = False
    events_pc_would_witness: list[str] = Field(default_factory=list)


class FactionTickSummary(BaseModel):
    faction_id: str
    duration: Duration
    goal_progress: Json = Field(default_factory=dict)
    resource_changes: Json = Field(default_factory=dict)
    notable_actions: list[str] = Field(default_factory=list)


class FactionConflict(BaseModel):
    """An inter-faction conflict surfaced by the faction shared-events pass (§5)."""

    factions: list[str] = Field(default_factory=list)
    summary: str
    intensity: Literal["latent", "open", "resolved"] = "latent"
    details: Json = Field(default_factory=dict)


class CheckpointSuggestion(BaseModel):
    """Returned from ``propose_advance`` (§8) when the projected advance
    exceeds the configured checkpoint threshold; the caller exchanges the
    ``token`` for an actual ``advance`` to confirm."""

    token: str
    campaign_id: str
    from_time: InGameTime
    to_time: InGameTime
    duration: Duration
    reason: TimeAdvanceReason
    threshold_exceeded: bool
    scene_id: str | None = None
    activity_ref: str | None = None


class TimeAdvanceResult(BaseModel):
    from_time: InGameTime
    to_time: InGameTime
    duration: Duration
    npc_summaries: dict[str, NpcTickSummary] = Field(default_factory=dict)
    faction_summaries: dict[str, FactionTickSummary] = Field(default_factory=dict)
    scheduled_events_triggered: list[ScheduledEvent] = Field(default_factory=list)
    weather_changes: list[WeatherChange] = Field(default_factory=list)
    commitments_due: list[Commitment] = Field(default_factory=list)
    commitments_overdue: list[Commitment] = Field(default_factory=list)
    mechanics_deltas: list[StateDelta] = Field(default_factory=list)
    digest: str = ""
    shared_events: list[SharedEvent] = Field(default_factory=list)
    drift_warnings: list[DriftWarning] = Field(default_factory=list)
    faction_conflicts: list[FactionConflict] = Field(default_factory=list)
    scheduled_events_upcoming: list[ScheduledEvent] = Field(default_factory=list)
    """Events that fell into the pre-notice window but have not yet triggered."""
