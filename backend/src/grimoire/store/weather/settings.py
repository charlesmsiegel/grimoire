"""Which climate, zone and persistence apply at a location.

Lenient throughout, and deliberately so: this runs inside prompt assembly, so
anything that raises here takes a turn down. A typo resolves to the campaign
default rather than an error — which is why the authoring surface validates
strictly instead, where the user is present to be told.
"""

from __future__ import annotations

import json
import math

from .. import campaigns, climates, entities, overlay


def _campaign_default(cid: str) -> dict:
    """The campaign's default climate, or the shipped preset."""
    path = campaigns.campaign_root(cid) / "climate.json"
    try:
        wanted = json.loads(path.read_text(encoding="utf-8")).get("default_climate")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, AttributeError):
        # AttributeError covers a JSON scalar: `json.loads("7").get` is not a
        # thing, and this file is hand-editable.
        wanted = None
    # Non-string ids are treated as unset. A hand-edited
    # `{"default_climate": ["temperate-interior"]}` is truthy, and passing a
    # list to the registry's dict lookup raises `TypeError: unhashable` — from
    # inside prompt assembly, where nothing may raise.
    if not isinstance(wanted, str) or not wanted:
        wanted = None
    return (climates.get(wanted) if wanted else None) or climates.get(climates.FALLBACK_ID)


def _fields(cid: str, location_id: str) -> dict:
    """A location's frontmatter, or {} if it no longer exists.

    Deleting a location does not clean the scene histories naming it, so this
    is reached in ordinary use — `context.py` already wraps the same read for
    the setting block.
    """
    try:
        return overlay.read_entity(cid, "locations", location_id).get("meta", {})
    except (entities.EntityNotFound, KeyError, OSError):
        return {}


def _persistence(raw, fallback: float) -> float:
    """A finite number in [0, 1], or the fallback.

    "2", "-1" and "NaN" all parse successfully and are all invalid — accepting
    them looks like a working setting while producing nonsense.
    """
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return fallback
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        return fallback
    return value


def resolve(cid: str, location_id: str | None) -> dict:
    """{climate, zone, persistence} for a location. Never raises."""
    default = _campaign_default(cid)
    if not location_id:
        return {"climate": default, "zone": "_default",
                "persistence": default.get("persistence", 0.5)}

    meta = _fields(cid, location_id)
    climate = climates.get(meta.get("climate", "")) or default
    return {
        "climate": climate,
        "zone": meta.get("weather_zone") or location_id,
        "persistence": _persistence(meta.get("persistence"), climate.get("persistence", 0.5)),
    }
