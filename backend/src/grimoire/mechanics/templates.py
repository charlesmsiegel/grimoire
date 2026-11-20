"""Starting points for an author: V0.2.1's reference modules in this format (086).

``d20-basic`` (a flat d20 plus modifiers against a difficulty) and ``pool-basic`` (d10 pools counting
faces at a target) keep the shapes of V0.2.1's built-in examples, so their sheets and checks carry over.
What this format adds beyond their simplifying assumptions: every name a formula uses is checked
statically, a single-die name (``natural``) or a pool name (``successes``) is offered only to a check of
that shape, outcomes are type-checked conditions, and derived values are ordered and loop-checked.
V0.2.1's ``ref`` fields, creation pools and advancement costs are not carried: references need
source-qualified records and progression is later work.

``template`` returns a fresh, mutable copy for an author to edit; nothing here is a module until
``parse_module`` validates it.
"""

from __future__ import annotations

from grimoire.mechanics.module import FORMAT

_D20_OUTCOMES = [
    {"label": "critical success", "when": "natural == 20"},
    {"label": "critical failure", "when": "natural == 1"},
    {"label": "success", "when": "margin >= 0"},
    {"label": "failure", "when": "margin < 0"},
]
_D20_ATTRIBUTES = [
    {"key": "strength", "label": "Strength", "type": "number", "min": 1, "max": 20, "default": 10},
    {"key": "dexterity", "label": "Dexterity", "type": "number", "min": 1, "max": 20, "default": 10},
    {"key": "mind", "label": "Mind", "type": "number", "min": 1, "max": 20, "default": 10},
    {"key": "athletics", "label": "Athletics", "type": "number", "min": 0, "max": 10, "default": 0},
    {"key": "stealth", "label": "Stealth", "type": "number", "min": 0, "max": 10, "default": 0},
    {"key": "arcana", "label": "Arcana", "type": "number", "min": 0, "max": 10, "default": 0},
]
_D20_DERIVED = {"str_mod": "floor((strength - 10) / 2)", "dex_mod": "floor((dexterity - 10) / 2)",
                "mind_mod": "floor((mind - 10) / 2)"}

D20_BASIC = {
    "format": FORMAT,
    "id": "d20_basic",
    "name": "Basic d20",
    "version": "0.1",
    "family": "d20",
    "notes": "A flat d20 plus modifiers against a difficulty, after V0.2.1's d20-basic example.",
    "sheets": {
        "warrior": {
            "label": "Warrior",
            "fields": _D20_ATTRIBUTES + [
                {"key": "hp", "label": "Hit Points", "type": "resource", "max": 12},
                {"key": "gear", "label": "Gear", "type": "list"},
            ],
            "derived": {**_D20_DERIVED, "melee_bonus": "str_mod + 2"},
        },
        "adept": {
            "label": "Adept",
            "fields": _D20_ATTRIBUTES + [
                {"key": "hp", "label": "Hit Points", "type": "resource", "max": 8},
                {"key": "spell_slots", "label": "Spell Slots", "type": "track", "max": 4},
                {"key": "spells", "label": "Spells", "type": "list"},
            ],
            "derived": {**_D20_DERIVED, "spell_bonus": "mind_mod + 2"},
        },
    },
    "checks": {
        "athletics": {"label": "Athletics", "sheet": "warrior", "difficulty": 12,
                      "dice": {"count": "1", "sides": 20},
                      "total": "natural + athletics + str_mod + modifier", "outcomes": _D20_OUTCOMES},
        "stealth": {"label": "Stealth", "sheet": "warrior", "difficulty": 12,
                    "dice": {"count": "1", "sides": 20},
                    "total": "natural + stealth + dex_mod + modifier", "outcomes": _D20_OUTCOMES},
        "arcana": {"label": "Arcana", "sheet": "adept", "difficulty": 12,
                   "dice": {"count": "1", "sides": 20},
                   "total": "natural + arcana + mind_mod + modifier", "outcomes": _D20_OUTCOMES},
    },
}

_POOL_OUTCOMES = [
    {"label": "botch", "when": "successes == 0 and ones > 0"},
    {"label": "exceptional success", "when": "successes >= 5"},
    {"label": "success", "when": "successes >= 1"},
    {"label": "failure", "when": "successes == 0"},
]
_POOL_TRAITS = [
    {"key": "vigor", "label": "Vigor", "type": "dots", "max": 5, "default": 1},
    {"key": "grace", "label": "Grace", "type": "dots", "max": 5, "default": 1},
    {"key": "wits", "label": "Wits", "type": "dots", "max": 5, "default": 1},
    {"key": "brawl", "label": "Brawl", "type": "dots", "max": 5},
    {"key": "occult", "label": "Occult", "type": "dots", "max": 5},
    {"key": "empathy", "label": "Empathy", "type": "dots", "max": 5},
]

POOL_BASIC = {
    "format": FORMAT,
    "id": "pool_basic",
    "name": "Basic Pool",
    "version": "0.1",
    "family": "pool",
    "notes": "d10 pools against a target number with successes counted, after V0.2.1's pool-basic example.",
    "sheets": {
        "medium": {
            "label": "Medium",
            "fields": _POOL_TRAITS + [
                {"key": "essence", "label": "Essence", "type": "resource", "max": 10},
                {"key": "health", "label": "Health", "type": "track", "max": 7},
                {"key": "quirk", "label": "Quirk", "type": "text"},
                {"key": "gear", "label": "Gear", "type": "list"},
            ],
            "derived": {"sight_pool": "wits + occult", "awareness": "wits + grace"},
        },
    },
    "checks": {
        "brawl": {"label": "Vigor + Brawl", "sheet": "medium", "difficulty": 6,
                  "dice": {"count": "max(1, vigor + brawl + modifier)", "sides": 10, "target": "difficulty"},
                  "outcomes": _POOL_OUTCOMES},
        "perception": {"label": "Wits + Occult", "sheet": "medium", "difficulty": 6,
                       "dice": {"count": "max(1, sight_pool + modifier)", "sides": 10, "target": "difficulty"},
                       "outcomes": _POOL_OUTCOMES},
    },
}

TEMPLATES = {"d20_basic": D20_BASIC, "pool_basic": POOL_BASIC}


def _copy(value: object) -> object:
    if isinstance(value, dict):
        return {key: _copy(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_copy(child) for child in value]
    return value


def template(name: str) -> dict[str, object]:
    """A fresh copy of template ``name`` (``d20_basic`` or ``pool_basic``) to edit."""
    if name not in TEMPLATES:
        raise KeyError(f"no template {name!r}; the templates are {', '.join(sorted(TEMPLATES))}")
    return _copy(TEMPLATES[name])  # type: ignore[return-value]


__all__ = ["D20_BASIC", "POOL_BASIC", "TEMPLATES", "template"]
