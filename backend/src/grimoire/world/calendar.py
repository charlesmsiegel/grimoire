"""Calendar utilities for the World module.

A world's calendar lives in ``world.yaml`` under the ``calendar`` key.
Helpers here translate the loose YAML form into typed ``WorldCalendar``
values and answer the cross-cutting "what season / holiday is this?"
questions Time Engine and Context Builder need.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from grimoire.types.common import InGameTime
from grimoire.types.world import (
    Holiday,
    Month,
    Season,
    WorldCalendar,
)


def parse_calendar(world_id: str, raw: dict[str, Any] | None) -> WorldCalendar:
    """Build a typed calendar from a free-form ``world.yaml`` calendar block."""
    raw = raw or {}
    months = [
        Month(name=str(m.get("name") or f"M{i + 1}"), days=int(m.get("days") or 30))
        for i, m in enumerate(raw.get("months") or [])
    ]
    seasons = [
        Season(
            name=str(s.get("name") or ""),
            start_month=int(s.get("start_month") or 1),
            start_day=int(s.get("start_day") or 1),
            palette=str(s.get("palette") or ""),
            weather_bias=dict(s.get("weather_bias") or {}),
        )
        for s in raw.get("seasons") or []
    ]
    holidays = [
        Holiday(
            name=str(h.get("name") or ""),
            month=int(h.get("month") or 1),
            day=int(h.get("day") or 1),
            description=str(h.get("description") or ""),
            tags=list(h.get("tags") or []),
        )
        for h in raw.get("holidays") or []
    ]
    return WorldCalendar(
        world_id=world_id,
        epoch=_parse_dt(raw.get("epoch")),
        months=months,
        days_per_week=int(raw.get("days_per_week") or 7),
        week_day_names=list(raw.get("week_day_names") or []),
        seasons=seasons,
        holidays=holidays,
    )


def season_for(calendar: WorldCalendar, when: InGameTime) -> Season | None:
    """Return the season ``when`` falls into.

    Seasons are interpreted as starts only: each season runs from its
    ``(start_month, start_day)`` up to (but not including) the next season's
    start. Wrap-around handled. With no seasons configured, returns ``None``.
    """
    seasons = list(calendar.seasons)
    if not seasons:
        return _hemisphere_default(when)
    moment = when.moment
    target = (moment.month, moment.day)
    ordered = sorted(seasons, key=lambda s: (s.start_month, s.start_day))
    selected: Season | None = None
    for s in ordered:
        if (s.start_month, s.start_day) <= target:
            selected = s
    if selected is None:
        # ``target`` is before the first season-start; the year wraps to the
        # last configured season.
        selected = ordered[-1]
    return selected


def holiday_at(calendar: WorldCalendar, when: InGameTime) -> Holiday | None:
    moment = when.moment
    for h in calendar.holidays:
        if h.month == moment.month and h.day == moment.day:
            return h
    return None


def _parse_dt(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _hemisphere_default(when: InGameTime) -> Season:
    """Fallback Gregorian-ish seasons for worlds without their own."""
    m = when.moment.month
    if m in (12, 1, 2):
        return Season(name="winter", start_month=12, start_day=1)
    if m in (3, 4, 5):
        return Season(name="spring", start_month=3, start_day=1)
    if m in (6, 7, 8):
        return Season(name="summer", start_month=6, start_day=1)
    return Season(name="autumn", start_month=9, start_day=1)
