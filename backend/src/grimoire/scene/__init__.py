"""Played chat turns (035): posts, generated takes, and who heard what. Pure: no store, provider or clock.

The model and codec load alone, so the chat service and storage that read turn files never load 027's
generation package (or its evals). Who heard what (``transcript``) needs 027's heard-line shape, so its names
load on first use. A turn's proposed effects are 021's pure values (``grimoire.effects``).
"""

from .branches import Branch, Fork, Lineage, arrange
from .codec import TURN_SCHEMA, turn_from_payload, turn_to_payload
from .model import (
    CALL_STATES, EFFECT_STATES, INTENTION_OUTCOMES, LABEL_STATES, LINE_STATES, MAX_CHAT_ID, MAX_EFFECTS, MAX_LABELS,
    MAX_TAGGED_CHAT_ID, MAX_TAKES, ORIGINS, POST_KINDS, TAKE_STATES, TURN_OUTCOMES, CallRecord, Intention,
    IntentionOption, Label, Post, SceneLine, SceneRefused, SetAside, Take, TurnEffect, TurnRecord, parse_turn_id, turn_id, usage_totals,
)

_TRANSCRIPT = frozenset({"NARRATOR", "ImportedHistory", "audible", "heard_for", "imported_history", "render",
                         "segments", "table_lines"})


def __getattr__(name: str):
    if name in _TRANSCRIPT:
        from . import transcript
        return getattr(transcript, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "Branch", "CALL_STATES", "CallRecord", "EFFECT_STATES", "Fork", "INTENTION_OUTCOMES", "ImportedHistory", "Intention",
    "IntentionOption", "LABEL_STATES", "LINE_STATES",
    "Label", "Lineage", "MAX_CHAT_ID", "MAX_EFFECTS", "MAX_LABELS", "MAX_TAGGED_CHAT_ID", "MAX_TAKES", "NARRATOR",
    "ORIGINS", "POST_KINDS", "Post", "SceneLine", "SceneRefused", "SetAside", "TAKE_STATES", "TURN_OUTCOMES",
    "TURN_SCHEMA", "Take", "TurnEffect", "TurnRecord", "arrange", "audible", "heard_for", "imported_history",
    "parse_turn_id", "render", "segments", "table_lines", "turn_from_payload", "turn_id", "turn_to_payload",
    "usage_totals",
]
