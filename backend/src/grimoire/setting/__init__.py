"""Setting module — world container behaviors over Library storage.

Implements spec 09. Builds on :class:`grimoire.library.LibraryService` for
file-mediated CRUD and adds per-campaign behaviors (composition-aware
listing, spatial queries, lore keyword triggers, procedural weather,
calendar/season/holiday, faction state).
"""

from grimoire.setting.calendar import holiday_at, parse_calendar, season_for
from grimoire.setting.errors import (
    CompositionError,
    SettingError,
    SettingNotFoundError,
)
from grimoire.setting.service import SettingService
from grimoire.setting.weather import generate_weather

__all__ = [
    "CompositionError",
    "SettingError",
    "SettingNotFoundError",
    "SettingService",
    "generate_weather",
    "holiday_at",
    "parse_calendar",
    "season_for",
]
