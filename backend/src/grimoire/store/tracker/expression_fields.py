"""Sprite choices belong to a post's presentation, not its narrative facts.

The two decision-owned fields share tracker storage and field switches, but
never ride narrative prompts or predecessor fingerprints. Keeping that boundary
here lets expression refresh change the picture without invalidating the story;
another presentation selector can use the same boundary and source binding.
"""

import copy

KEYS = frozenset({"expression-outfit", "expression-mood"})
DEFINITIONS = (
    {"key": "expression-outfit", "label": "Expression outfit", "type": "text",
     "aware": "present", "computed": True, "hint": "Sprite outfit selected by the Decision model."},
    {"key": "expression-mood", "label": "Expression mood", "type": "text",
     "aware": "present", "computed": True, "hint": "Sprite expression selected for that outfit."},
)


def narrative_fields(fields: list[dict] | tuple[dict, ...]) -> list[dict]:
    return [field for field in fields if field["key"] not in KEYS]


def narrative_body(body: dict) -> dict:
    """The old proof payload, byte-compatible when no sprite data is present.

    Do not discard timestamps, model metadata or arbitrary computed fields:
    those are still evidence that a narrative record changed. Only this
    decision producer owns the fields and envelope entry removed here.
    """
    result = copy.deepcopy(body)
    result.pop("expressions", None)
    for actor in result.get("snapshot", {}).values():
        for key in KEYS:
            actor.get("fields", {}).pop(key, None)
    return result
