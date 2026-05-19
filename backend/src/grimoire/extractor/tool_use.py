"""Tool-use mode: tool declarations + delta projection.

Provider-native tool / function calls. One tool per delta kind; the
LLM accumulates calls during streaming and hands the complete list to
the extractor at finish. `project_tool_calls` turns those into typed
`StateDelta`s. Unknown tool names are skipped silently (a malformed
arg dict drops the call rather than raising) so a single bad call
doesn't poison the rest of the turn.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from grimoire.types.common import Scope
from grimoire.types.extraction import EntityCandidate
from grimoire.types.state import DeltaKind, StateDelta

SOURCE = "extractor:tool_use"


@dataclass(frozen=True, slots=True)
class ToolDeclaration:
    """Provider-agnostic tool schema. Gateways translate to the wire format."""

    name: str
    description: str
    schema: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ToolCall:
    """One tool invocation produced by the LLM during streaming."""

    name: str
    args: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------- #
# Tool declarations — one per delta kind.
# ---------------------------------------------------------------------------- #

RECORD_FACT_TOOL = ToolDeclaration(
    name="record_fact",
    description="Record a new fact established by this turn.",
    schema={
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "confidence": {"type": "number"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "about": {"type": "object"},
        },
        "required": ["text"],
    },
)

UPDATE_CHARACTER_STATE_TOOL = ToolDeclaration(
    name="update_character_state",
    description="Update a single field on a character's transient state.",
    schema={
        "type": "object",
        "properties": {
            "character_id": {"type": "string"},
            "field": {"type": "string"},
            "after": {},
            "before": {},
            "confidence": {"type": "number"},
        },
        "required": ["character_id", "field", "after"],
    },
)

ADVANCE_TIME_TOOL = ToolDeclaration(
    name="advance_time",
    description="Advance the in-game clock by an ISO-8601 duration.",
    schema={
        "type": "object",
        "properties": {
            "delta": {"type": "string"},
            "confidence": {"type": "number"},
        },
        "required": ["delta"],
    },
)

CHANGE_LOCATION_TOOL = ToolDeclaration(
    name="change_location",
    description="Move the scene to a new location ref.",
    schema={
        "type": "object",
        "properties": {
            "to_location": {"type": "string"},
            "confidence": {"type": "number"},
        },
        "required": ["to_location"],
    },
)

PROPOSE_NEW_ENTITY_TOOL = ToolDeclaration(
    name="propose_new_entity",
    description="Surface a newly named entity for the review queue.",
    schema={
        "type": "object",
        "properties": {
            "kind": {"type": "string"},
            "proposed_id": {"type": "string"},
            "proposed_name": {"type": "string"},
            "role_hint": {"type": "string"},
            "confidence": {"type": "number"},
        },
        "required": ["proposed_id", "proposed_name"],
    },
)

CREATE_COMMITMENT_TOOL = ToolDeclaration(
    name="create_commitment",
    description="Open a new commitment / obligation between two refs.",
    schema={
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "from": {"type": "string"},
            "to": {"type": "string"},
            "due_by": {"type": "string"},
            "confidence": {"type": "number"},
        },
        "required": ["text"],
    },
)

UPDATE_COMMITMENT_TOOL = ToolDeclaration(
    name="update_commitment",
    description="Update or resolve an existing commitment by id.",
    schema={
        "type": "object",
        "properties": {
            "commitment_id": {"type": "string"},
            "outcome": {"type": "string"},
            "confidence": {"type": "number"},
        },
        "required": ["commitment_id"],
    },
)

CLOSE_THREAD_TOOL = ToolDeclaration(
    name="close_thread",
    description="Mark a narrative thread / commitment as concluded.",
    schema={
        "type": "object",
        "properties": {
            "commitment_id": {"type": "string"},
            "outcome": {"type": "string"},
        },
        "required": ["commitment_id"],
    },
)


ALL_TOOLS: list[ToolDeclaration] = [
    RECORD_FACT_TOOL,
    UPDATE_CHARACTER_STATE_TOOL,
    ADVANCE_TIME_TOOL,
    CHANGE_LOCATION_TOOL,
    PROPOSE_NEW_ENTITY_TOOL,
    CREATE_COMMITMENT_TOOL,
    UPDATE_COMMITMENT_TOOL,
    CLOSE_THREAD_TOOL,
]


# ---------------------------------------------------------------------------- #
# Projection
# ---------------------------------------------------------------------------- #


def project_tool_calls(
    calls: list[ToolCall],
    *,
    campaign_id: str,
    scene_branch_id: str | None = None,
) -> tuple[list[StateDelta], list[EntityCandidate]]:
    """Project a stream of tool calls into deltas and entity candidates.

    `propose_new_entity` calls produce `EntityCandidate`s; everything
    else produces `StateDelta`s. Unknown / malformed calls are dropped
    silently — the auto-disable counters live a layer up.
    """
    deltas: list[StateDelta] = []
    candidates: list[EntityCandidate] = []
    for call in calls:
        if call.name == RECORD_FACT_TOOL.name:
            delta = _record_fact(call)
        elif call.name == UPDATE_CHARACTER_STATE_TOOL.name:
            delta = _update_character_state(call)
        elif call.name == ADVANCE_TIME_TOOL.name:
            delta = _advance_time(call, campaign_id=campaign_id)
        elif call.name == CHANGE_LOCATION_TOOL.name:
            delta = _change_location(call, scene_branch_id=scene_branch_id)
        elif call.name == CREATE_COMMITMENT_TOOL.name:
            delta = _create_commitment(call)
        elif call.name in (UPDATE_COMMITMENT_TOOL.name, CLOSE_THREAD_TOOL.name):
            delta = _update_commitment(call)
        elif call.name == PROPOSE_NEW_ENTITY_TOOL.name:
            cand = _propose_entity(call)
            if cand is not None:
                candidates.append(cand)
            continue
        else:
            continue
        if delta is not None:
            deltas.append(delta)
    return deltas, candidates


def _record_fact(call: ToolCall) -> StateDelta | None:
    text = str(call.args.get("text") or "").strip()
    if not text:
        return None
    return StateDelta(
        kind=DeltaKind.FACT_ADD,
        target_scope=Scope.CAMPAIGN_LOCAL,
        target_id=_fact_id(text),
        after={
            "text": text,
            "about": call.args.get("about", {}) or {},
            "tags": call.args.get("tags", []) or [],
        },
        confidence=_confidence(call.args),
        source=SOURCE,
    )


def _update_character_state(call: ToolCall) -> StateDelta | None:
    character_id = str(call.args.get("character_id") or "").strip()
    field_name = str(call.args.get("field") or "").strip()
    if not character_id or not field_name or "after" not in call.args:
        return None
    return StateDelta(
        kind=DeltaKind.CHARACTER_STATE_UPDATE,
        target_scope=Scope.CAMPAIGN_LOCAL,
        target_id=character_id,
        after={
            "character_id": character_id,
            "field": field_name,
            "after": call.args.get("after"),
            "before": call.args.get("before"),
        },
        confidence=_confidence(call.args),
        source=SOURCE,
    )


def _advance_time(call: ToolCall, *, campaign_id: str) -> StateDelta | None:
    delta_str = call.args.get("delta")
    if not isinstance(delta_str, str) or not delta_str:
        return None
    return StateDelta(
        kind=DeltaKind.TIME_ADVANCE,
        target_scope=Scope.CAMPAIGN_LOCAL,
        target_id=campaign_id,
        after={"delta": delta_str},
        confidence=_confidence(call.args),
        source=SOURCE,
    )


def _change_location(call: ToolCall, *, scene_branch_id: str | None) -> StateDelta | None:
    to_loc = call.args.get("to_location")
    if not isinstance(to_loc, str) or not to_loc:
        return None
    return StateDelta(
        kind=DeltaKind.SCENE_CHANGE,
        target_scope=Scope.CAMPAIGN_LOCAL,
        target_id=to_loc,
        after={"to_location": to_loc, "branch_id": scene_branch_id},
        confidence=_confidence(call.args),
        source=SOURCE,
    )


def _create_commitment(call: ToolCall) -> StateDelta | None:
    text = str(call.args.get("text") or "").strip()
    if not text:
        return None
    return StateDelta(
        kind=DeltaKind.COMMITMENT_ADD,
        target_scope=Scope.CAMPAIGN_LOCAL,
        target_id=_fact_id(text),
        after={
            "text": text,
            "from": call.args.get("from"),
            "to": call.args.get("to"),
            "due_by": call.args.get("due_by"),
        },
        confidence=_confidence(call.args),
        source=SOURCE,
    )


def _update_commitment(call: ToolCall) -> StateDelta | None:
    commitment_id = str(call.args.get("commitment_id") or "").strip()
    if not commitment_id:
        return None
    return StateDelta(
        kind=DeltaKind.COMMITMENT_RESOLVE,
        target_scope=Scope.CAMPAIGN_LOCAL,
        target_id=commitment_id,
        after={
            "commitment_id": commitment_id,
            "outcome": call.args.get("outcome"),
        },
        confidence=_confidence(call.args),
        source=SOURCE,
    )


def _propose_entity(call: ToolCall) -> EntityCandidate | None:
    from grimoire.types.common import EntityKind

    proposed_id = str(call.args.get("proposed_id") or "").strip()
    proposed_name = str(call.args.get("proposed_name") or "").strip()
    if not proposed_id or not proposed_name:
        return None
    kind_raw = str(call.args.get("kind") or "character").lower()
    try:
        kind = EntityKind(kind_raw)
    except ValueError:
        kind = EntityKind.CHARACTER
    return EntityCandidate(
        kind=kind,
        proposed_id=proposed_id,
        proposed_name=proposed_name,
        role_hint=str(call.args.get("role_hint") or ""),
        confidence=_confidence(call.args),
    )


def _confidence(args: dict[str, Any]) -> float:
    raw = args.get("confidence", 0.9)
    try:
        c = float(raw)
    except (TypeError, ValueError):
        return 0.9
    if c < 0.0:
        return 0.0
    if c > 1.0:
        return 1.0
    return c


def _fact_id(text: str) -> str:
    return "tool:" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


__all__ = [
    "ADVANCE_TIME_TOOL",
    "ALL_TOOLS",
    "CHANGE_LOCATION_TOOL",
    "CLOSE_THREAD_TOOL",
    "CREATE_COMMITMENT_TOOL",
    "PROPOSE_NEW_ENTITY_TOOL",
    "RECORD_FACT_TOOL",
    "SOURCE",
    "UPDATE_CHARACTER_STATE_TOOL",
    "UPDATE_COMMITMENT_TOOL",
    "ToolCall",
    "ToolDeclaration",
    "project_tool_calls",
]
