"""JSON schema for the structured-LLM extraction strategy (spec 04 §Output schema)."""

from __future__ import annotations

from grimoire.types.common import JsonSchema


def output_schema() -> JsonSchema:
    """Schema describing the structured-extraction LLM output.

    Kept conservative (additionalProperties: False on inner objects) so a
    permissive model can't smuggle untyped fields through. The top-level
    object is itself open since we may add new categories.
    """
    fact = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "text": {"type": "string"},
            "about": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "character_ids": {"type": "array", "items": {"type": "string"}},
                    "location_ids": {"type": "array", "items": {"type": "string"}},
                    "faction_ids": {"type": "array", "items": {"type": "string"}},
                    "item_ids": {"type": "array", "items": {"type": "string"}},
                },
            },
            "speaker_id": {"type": ["string", "null"]},
            "evidence": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        },
        "required": ["text", "confidence"],
    }
    character_update = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "character_id": {"type": "string"},
            "field": {"type": "string"},
            "before": {"type": ["string", "null"]},
            "after": {"type": ["string", "null"]},
            "evidence": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        },
        "required": ["character_id", "field", "after", "confidence"],
    }
    # Shared schema for every new_* candidate array — characters, locations,
    # factions, and items all carry the same proposal shape (spec
    # extractor-remaining §8); the kind comes from which array the item is in.
    new_entity = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "proposed_id": {"type": "string"},
            "proposed_name": {"type": "string"},
            "role": {"type": "string"},
            "evidence": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        },
        "required": ["proposed_name", "confidence"],
    }
    scene_change = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "kind": {"type": "string"},
            "to_location": {"type": ["string", "null"]},
            "evidence": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        },
        "required": ["kind", "confidence"],
    }
    # A known character entering or leaving the current scene (#464). The
    # Orchestrator resolves ``character_id`` against the read cascade; the
    # change is always review-gated before the scene cast is touched.
    cast_change = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "character_id": {"type": "string"},
            "change": {"type": "string", "enum": ["enter", "leave"]},
            "evidence": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        },
        "required": ["character_id", "change", "confidence"],
    }
    time_advance = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "delta": {"type": "string"},
            "evidence": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        },
        "required": ["delta", "confidence"],
    }
    commitment = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "kind": {"type": "string"},
            "text": {"type": "string"},
            "from": {"type": ["string", "null"]},
            "to": {"type": ["string", "null"]},
            "due": {"type": ["string", "null"]},
            "evidence": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        },
        "required": ["kind", "text", "confidence"],
    }
    inventory_change = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "action": {
                "type": "string",
                "enum": ["acquire", "drop", "transfer", "consume", "adjust", "equip", "unequip"],
            },
            "item": {"type": "string"},
            "holder": {"type": "string"},
            "to": {"type": ["string", "null"]},
            "quantity": {"type": ["integer", "null"]},
            "equipped": {"type": ["boolean", "null"]},
            "provenance": {"type": ["string", "null"]},
            "evidence": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        },
        "required": ["action", "item", "holder", "confidence"],
    }
    mechanical_event = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "kind": {"type": "string"},
            "character_id": {"type": ["string", "null"]},
            "target_id": {"type": ["string", "null"]},
            "amount": {"type": ["string", "null"]},
            "evidence": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        },
        "required": ["kind", "confidence"],
    }
    relationship_change = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "from": {"type": "string"},
            "to": {"type": "string"},
            "field": {"type": "string"},
            "delta": {"type": ["string", "number"]},
            "evidence": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        },
        "required": ["from", "to", "confidence"],
    }
    commitment_resolution = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "commitment_id": {"type": "string"},
            "outcome": {"type": "string"},
            "evidence": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        },
        "required": ["commitment_id", "outcome", "confidence"],
    }
    transient_update = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "entity_kind": {
                "type": "string",
                "enum": ["character", "location", "faction", "scene"],
            },
            "entity_id": {"type": "string"},
            "field": {"type": "string"},
            "value": {},
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "evidence": {"type": "string"},
        },
        "required": ["entity_kind", "entity_id", "field", "value", "confidence"],
    }
    return {
        "type": "object",
        "properties": {
            "facts": {"type": "array", "items": fact},
            "character_updates": {"type": "array", "items": character_update},
            "new_characters": {"type": "array", "items": new_entity},
            "new_locations": {"type": "array", "items": new_entity},
            "new_factions": {"type": "array", "items": new_entity},
            "new_items": {"type": "array", "items": new_entity},
            "scene_changes": {"type": "array", "items": scene_change},
            "cast_changes": {"type": "array", "items": cast_change},
            "time_advances": {"type": "array", "items": time_advance},
            "commitments": {"type": "array", "items": commitment},
            "inventory_changes": {"type": "array", "items": inventory_change},
            "mechanical_events": {"type": "array", "items": mechanical_event},
            "relationship_changes": {"type": "array", "items": relationship_change},
            "commitment_resolutions": {"type": "array", "items": commitment_resolution},
            "transient_updates": {"type": "array", "items": transient_update},
        },
    }


def empty_payload() -> dict:
    """A schema-shaped payload with all categories empty."""
    return {
        "facts": [],
        "character_updates": [],
        "new_characters": [],
        "new_locations": [],
        "new_factions": [],
        "new_items": [],
        "scene_changes": [],
        "cast_changes": [],
        "time_advances": [],
        "commitments": [],
        "inventory_changes": [],
        "mechanical_events": [],
        "relationship_changes": [],
        "commitment_resolutions": [],
        "transient_updates": [],
    }


__all__ = ["empty_payload", "output_schema"]
