"""Procedural weather generation.

Deterministic per campaign: given the same campaign id, location, and
in-game time, ``generate_weather`` returns the same :class:`Weather`. Uses
``hashlib.blake2b`` over the inputs as the seed source so we don't have to
keep an RNG instance per campaign — same inputs in, same outputs out, forks
preserve.
"""

from __future__ import annotations

import hashlib
import random

from grimoire.types.common import InGameTime
from grimoire.types.world import (
    Season,
    Weather,
    WeatherKind,
    WorldCalendar,
)

from .calendar import season_for

# Default bias used when neither a season nor a climate zone overrides it.
_DEFAULT_BIAS: dict[str, float] = {
    WeatherKind.CLEAR.value: 0.35,
    WeatherKind.OVERCAST.value: 0.25,
    WeatherKind.RAIN.value: 0.15,
    WeatherKind.STORM.value: 0.05,
    WeatherKind.SNOW.value: 0.05,
    WeatherKind.FOG.value: 0.05,
    WeatherKind.WIND.value: 0.05,
    WeatherKind.HEAT.value: 0.03,
    WeatherKind.COLD.value: 0.02,
}

# Adjustments applied on top of the season bias for a few common climates.
_CLIMATE_TWEAKS: dict[str, dict[str, float]] = {
    "temperate-oceanic": {
        WeatherKind.RAIN.value: 0.10,
        WeatherKind.FOG.value: 0.05,
    },
    "desert": {
        WeatherKind.CLEAR.value: 0.30,
        WeatherKind.HEAT.value: 0.20,
        WeatherKind.RAIN.value: -0.10,
        WeatherKind.SNOW.value: -0.05,
    },
    "arctic": {
        WeatherKind.SNOW.value: 0.30,
        WeatherKind.COLD.value: 0.20,
        WeatherKind.CLEAR.value: -0.10,
    },
    "tropical": {
        WeatherKind.RAIN.value: 0.20,
        WeatherKind.STORM.value: 0.10,
        WeatherKind.HEAT.value: 0.10,
        WeatherKind.SNOW.value: -0.05,
    },
}


def generate_weather(
    *,
    campaign_id: str,
    location_ref: str,
    when: InGameTime,
    calendar: WorldCalendar | None,
    climate_zone: str | None,
    indoor: bool = False,
) -> Weather:
    """Deterministic weather for ``(campaign, location, hour)``.

    Indoor locations always return ``WeatherKind.CLEAR`` with an empty
    summary; outdoor weather is sampled from a season+climate bias.
    """
    if indoor:
        return Weather(kind=WeatherKind.CLEAR, source="procedural", summary="")

    bucket = _hour_bucket(when)
    seed = _seed(campaign_id, location_ref, bucket)
    rng = random.Random(seed)

    season = season_for(calendar, when) if calendar else None
    bias = _bias_for(season, climate_zone)
    kind = _weighted_pick(rng, bias)
    summary = _summary_for(kind, season)
    temperature = _temperature_for(kind, season, climate_zone, rng)
    return Weather(
        kind=kind,
        summary=summary,
        temperature_c=temperature,
        humidity=round(rng.uniform(0.2, 0.95), 2),
        wind_kph=round(rng.uniform(0.0, 35.0), 1),
        palette=(season.palette if season else ""),
        source="procedural",
    )


def _hour_bucket(when: InGameTime) -> str:
    """Group time at the hour level so weather is stable for a scene."""
    m = when.moment
    return f"{m.year:04d}-{m.month:02d}-{m.day:02d}T{m.hour:02d}"


def _seed(campaign_id: str, location_ref: str, bucket: str) -> int:
    digest = hashlib.blake2b(
        f"{campaign_id}|{location_ref}|{bucket}".encode(),
        digest_size=8,
    ).digest()
    return int.from_bytes(digest, "big", signed=False)


def _bias_for(season: Season | None, climate_zone: str | None) -> dict[str, float]:
    base: dict[str, float] = dict(_DEFAULT_BIAS)
    if season and season.weather_bias:
        for k, v in season.weather_bias.items():
            base[k] = float(v)
    if climate_zone and climate_zone in _CLIMATE_TWEAKS:
        for k, delta in _CLIMATE_TWEAKS[climate_zone].items():
            base[k] = max(0.0, base.get(k, 0.0) + delta)
    if not any(v > 0 for v in base.values()):
        base[WeatherKind.CLEAR.value] = 1.0
    return base


def _weighted_pick(rng: random.Random, weights: dict[str, float]) -> WeatherKind:
    total = sum(max(0.0, v) for v in weights.values())
    if total <= 0:
        return WeatherKind.CLEAR
    pick = rng.uniform(0.0, total)
    running = 0.0
    for kind, weight in weights.items():
        running += max(0.0, weight)
        if pick <= running:
            try:
                return WeatherKind(kind)
            except ValueError:
                return WeatherKind.CLEAR
    return WeatherKind.CLEAR


def _summary_for(kind: WeatherKind, season: Season | None) -> str:
    season_name = season.name if season else ""
    phrases: dict[WeatherKind, str] = {
        WeatherKind.CLEAR: "clear skies",
        WeatherKind.OVERCAST: "low grey overcast",
        WeatherKind.RAIN: "steady rain",
        WeatherKind.STORM: "thunder rolling overhead",
        WeatherKind.SNOW: "snow drifting down",
        WeatherKind.FOG: "thick fog",
        WeatherKind.WIND: "gusting wind",
        WeatherKind.HEAT: "oppressive heat",
        WeatherKind.COLD: "biting cold",
    }
    phrase = phrases.get(kind, "")
    if season_name:
        return f"{phrase}, {season_name}".strip(", ")
    return phrase


def _temperature_for(
    kind: WeatherKind,
    season: Season | None,
    climate_zone: str | None,
    rng: random.Random,
) -> float:
    base = 12.0
    name = season.name.lower() if season else ""
    if "winter" in name:
        base = 2.0
    elif "spring" in name:
        base = 12.0
    elif "summer" in name:
        base = 22.0
    elif "autumn" in name or "fall" in name:
        base = 10.0
    if climate_zone == "arctic":
        base -= 15.0
    elif climate_zone == "tropical":
        base += 10.0
    elif climate_zone == "desert":
        base += 8.0
    if kind == WeatherKind.HEAT:
        base += 8.0
    elif kind == WeatherKind.COLD:
        base -= 8.0
    elif kind == WeatherKind.SNOW:
        base = min(base, 0.0)
    return round(base + rng.uniform(-2.0, 2.0), 1)
