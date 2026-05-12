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
    new_character = {
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
            "character_id": {"type": "string"},
            "item": {"type": "string"},
            "delta": {"type": "string"},
            "evidence": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        },
        "required": ["character_id", "item", "delta", "confidence"],
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
    return {
        "type": "object",
        "properties": {
            "facts": {"type": "array", "items": fact},
            "character_updates": {"type": "array", "items": character_update},
            "new_characters": {"type": "array", "items": new_character},
            "scene_changes": {"type": "array", "items": scene_change},
            "time_advances": {"type": "array", "items": time_advance},
            "commitments": {"type": "array", "items": commitment},
            "inventory_changes": {"type": "array", "items": inventory_change},
            "mechanical_events": {"type": "array", "items": mechanical_event},
            "relationship_changes": {"type": "array", "items": relationship_change},
            "commitment_resolutions": {"type": "array", "items": commitment_resolution},
        },
    }


def empty_payload() -> dict:
    """A schema-shaped payload with all categories empty."""
    return {
        "facts": [],
        "character_updates": [],
        "new_characters": [],
        "scene_changes": [],
        "time_advances": [],
        "commitments": [],
        "inventory_changes": [],
        "mechanical_events": [],
        "relationship_changes": [],
        "commitment_resolutions": [],
    }


__all__ = ["empty_payload", "output_schema"]
