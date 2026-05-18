"""World module — world container behaviors over Library storage.

Implements spec 09. Builds on :class:`grimoire.library.LibraryService` for
file-mediated CRUD and adds per-campaign behaviors (composition-aware
listing, spatial queries, lore keyword triggers, procedural weather,
calendar/season/holiday, faction state).
"""

from grimoire.world.calendar import holiday_at, parse_calendar, season_for
from grimoire.world.config import (
    CompositionPolicyConfig,
    LoreConfig,
    WeatherConfig,
    WorldConfig,
)
from grimoire.world.errors import (
    CompositionError,
    WorldError,
    WorldNotFoundError,
)
from grimoire.world.service import WorldService
from grimoire.world.weather import generate_weather

__all__ = [
    "CompositionError",
    "CompositionPolicyConfig",
    "LoreConfig",
    "WeatherConfig",
    "WorldConfig",
    "WorldError",
    "WorldNotFoundError",
    "WorldService",
    "generate_weather",
    "holiday_at",
    "parse_calendar",
    "season_for",
]
