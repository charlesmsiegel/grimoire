"""Per-kind typed field descriptors (#37).

Fields are extra string scalars in the entity's frontmatter, so entity_hash,
sync conflict detection, and campaign copy-on-write cover them for free. The
frontend mirrors this table as ENTITY_FIELDS in frontend/src/api/client.ts —
keep the two in sync. Widgets are "text" only for now; ref-valued fields and
game mechanics are deferred (issues #221/#222).
"""

from __future__ import annotations

import math
from typing import Callable

FIELDS: dict[str, tuple[dict[str, str], ...]] = {
    "locations": (
        {"key": "climate", "label": "Climate", "widget": "text"},
        {"key": "persistence", "label": "Weather persistence", "widget": "text"},
        {"key": "weather_zone", "label": "Weather zone", "widget": "text"},
    ),
    "items": (
        {"key": "item_type", "label": "Type", "widget": "text"},
        {"key": "rarity", "label": "Rarity", "widget": "text"},
    ),
    "groups": (
        {"key": "group_type", "label": "Type", "widget": "text"},
    ),
    "creatures": (
        {"key": "creature_type", "label": "Type", "widget": "text"},
        {"key": "threat", "label": "Threat", "widget": "text"},
    ),
}


def field_keys(kind: str) -> tuple[str, ...]:
    return tuple(f["key"] for f in FIELDS.get(kind, ()))


def invalid_keys(kind: str, fields: dict) -> list[str]:
    allowed = set(field_keys(kind))
    return sorted(k for k in fields if k not in allowed)


def _valid_climate(value: str) -> bool:
    from . import climates  # imported lazily: climates imports paths, not this
    return climates.get(value) is not None


def _valid_persistence(value: str) -> bool:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(parsed) and 0.0 <= parsed <= 1.0


# Per-field value checks. Names are validated for every kind by invalid_keys;
# only fields listed here have their contents checked as well.
VALIDATORS: dict[str, dict[str, Callable[[str], bool]]] = {
    "locations": {"climate": _valid_climate, "persistence": _valid_persistence},
}


def invalid_values(kind: str, fields: dict) -> list[str]:
    """Field keys whose values fail their check.

    Leniency is right in the turn loop and wrong here. The resolver falls back
    silently so a bad value can never take a turn down, which means the save
    boundary is the only place a typo can be reported at all: without this,
    `climate: "temperate-costal"` saves cleanly, weather resolves from a
    climate the user did not choose, and nothing anywhere says so.

    An empty string is "clear this field", not a value: `EntityEditor` sends
    `""` for a field the user never set, and `entities.update_entity` removes
    empties — but only *after* route validation, so they must be skipped here
    or every ordinary location save is rejected.
    """
    checks = VALIDATORS.get(kind, {})
    bad = []
    for key, value in (fields or {}).items():
        if value is None or value == "":
            continue
        check = checks.get(key)
        if check and not check(value):
            bad.append(key)
    return sorted(bad)
