"""In-game time, duration, and time-advancement types."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .continuity import Commitment
    from .state import StateDelta


@dataclass(frozen=True)
class InGameTime:
    """A point in a campaign's in-game calendar.

    Wraps a `datetime`. Calendars may be Earth-Gregorian or setting-defined;
    setting-specific calendar metadata lives on the `SettingMeta`.
    """

    moment: datetime
    calendar_id: str | None = None  # setting calendar id; None = Gregorian


@dataclass(frozen=True)
class Duration:
    """A span of in-game time.

    Stored as an ISO 8601 duration string plus a resolved `timedelta` so the
    callable surface stays simple. Months/years cannot be exactly represented
    by `timedelta`; the canonical form is `iso8601`.
    """

    iso8601: str
    delta: timedelta = field(default_factory=timedelta)


class TimeAdvanceReason(StrEnum):
    EXPLICIT_USER = "explicit_user"
    SCENE_NARRATION = "scene_narration"
    SCENE_BREAK = "scene_break"
    ACTIVITY_DURATION = "activity_duration"
    SCHEDULED_EVENT = "scheduled_event"


@dataclass
class ScheduledEvent:
    id: str
    campaign_id: str
    at: InGameTime
    label: str
    kind: str  # 'holiday', 'recurring', 'one_off', 'plot_beat'
    payload: dict = field(default_factory=dict)
    triggered: bool = False


@dataclass
class WeatherChange:
    location_ref: str
    at: InGameTime
    summary: str
    details: dict = field(default_factory=dict)


@dataclass
class NpcTickSummary:
    character_id: str
    duration: Duration
    state_at_end: dict
    activities: list[str]
    relationships_changed: list[dict] = field(default_factory=list)
    new_facts_about_them: list[dict] = field(default_factory=list)
    secrets_kept: list[str] = field(default_factory=list)
    next_intent: str = ""
    should_seek_pc: bool = False
    events_pc_would_witness: list[str] = field(default_factory=list)


@dataclass
class FactionTickSummary:
    faction_id: str
    duration: Duration
    goal_progress: dict = field(default_factory=dict)
    resource_changes: dict = field(default_factory=dict)
    notable_actions: list[str] = field(default_factory=list)


@dataclass
class TimeAdvanceResult:
    from_time: InGameTime
    to_time: InGameTime
    duration: Duration
    npc_summaries: dict[str, NpcTickSummary] = field(default_factory=dict)
    faction_summaries: dict[str, FactionTickSummary] = field(default_factory=dict)
    scheduled_events_triggered: list[ScheduledEvent] = field(default_factory=list)
    weather_changes: list[WeatherChange] = field(default_factory=list)
    commitments_due: list[Commitment] = field(default_factory=list)
    commitments_overdue: list[Commitment] = field(default_factory=list)
    mechanics_deltas: list[StateDelta] = field(default_factory=list)
    digest: str = ""
