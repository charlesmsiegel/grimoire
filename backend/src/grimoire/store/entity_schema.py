"""Per-kind typed field descriptors (#37).

Fields are extra string scalars in the entity's frontmatter, so entity_hash,
sync conflict detection, and campaign copy-on-write cover them for free. The
frontend mirrors this table as ENTITY_FIELDS in frontend/src/api/client.ts —
keep the two in sync. Widgets are "text" only for now; ref-valued fields and
game mechanics are deferred (issues #221/#222).
"""

from __future__ import annotations

FIELDS: dict[str, tuple[dict[str, str], ...]] = {
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
