"""Structured-LLM extraction strategy (spec 04 §2).

The Extractor sends the model:
  - the response text under analysis,
  - a compact snapshot of relevant prior state,
  - the JSON schema for the expected output.

The model returns a JSON payload conforming to that schema. We tolerate
small deviations (e.g. a markdown code fence around the JSON) but fail
loudly enough that empty/garbled responses surface as flags rather than
silently dropping deltas.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field

from grimoire.extractor.config import ExtractorConfig
from grimoire.extractor.schema import empty_payload, output_schema
from grimoire.templates import render as render_template
from grimoire.types.common import CampaignId, Duration, EntityKind, Scope, TurnId
from grimoire.types.extraction import EntityCandidate, ExtractionFlag, FlagLevel
from grimoire.types.llm import CompletionRequest, Message, MessageRole
from grimoire.types.scene import Scene
from grimoire.types.state import DeltaKind, StateDelta, StateSnapshot

logger = logging.getLogger(__name__)


# Match either a fenced ```json block, a fenced ``` block, or raw JSON.
_FENCED_JSON = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_BARE_JSON = re.compile(r"(\{.*\})", re.DOTALL)


class LLMGatewayLike:
    """Structural type — anything with the gateway's `complete` shape."""

    async def complete(
        self,
        task: str,
        request: CompletionRequest,
        campaign_id: CampaignId | None = None,
        turn_id: TurnId | None = None,
    ):
        raise NotImplementedError


@dataclass
class LLMStrategyOutput:
    """Outputs of the structured-LLM strategy before merging."""

    deltas: list[StateDelta] = field(default_factory=list)
    candidates: list[EntityCandidate] = field(default_factory=list)
    flags: list[ExtractionFlag] = field(default_factory=list)
    confidence_avg: float = 0.0


def _extract_json_payload(text: str) -> dict | None:
    """Best-effort extraction of a JSON object from a model response."""
    if not text:
        return None
    fenced = _FENCED_JSON.search(text)
    if fenced is not None:
        try:
            return json.loads(fenced.group(1))
        except json.JSONDecodeError:
            pass
    # Try the whole string first
    stripped = text.strip()
    try:
        loaded = json.loads(stripped)
        if isinstance(loaded, dict):
            return loaded
    except json.JSONDecodeError:
        pass
    bare = _BARE_JSON.search(text)
    if bare is not None:
        try:
            loaded = json.loads(bare.group(1))
            if isinstance(loaded, dict):
                return loaded
        except json.JSONDecodeError:
            return None
    return None


def _make_system_prompt() -> str:
    schema_json = json.dumps(output_schema(), separators=(",", ":"))
    return render_template("extractor_system", schema_json=schema_json)


def _compact_snapshot(snapshot: StateSnapshot | None, scene: Scene | None) -> str:
    """A small text view of state for the model."""
    lines: list[str] = []
    if scene is not None:
        lines.append(f"scene: {scene.id} ({scene.title or 'untitled'})")
        if scene.location_ref:
            lines.append(f"location: {scene.location_ref}")
        if scene.present_character_refs:
            lines.append("present: " + ", ".join(scene.present_character_refs))
    if snapshot is not None:
        if snapshot.character_states:
            names = [cs.character_ref for cs in snapshot.character_states[:8]]
            lines.append("known characters: " + ", ".join(names))
        if snapshot.open_commitments:
            cs = []
            for c in snapshot.open_commitments[:5]:
                cid = c.get("id") if isinstance(c, dict) else None
                text = c.get("text") if isinstance(c, dict) else str(c)
                cs.append(f"{cid or '?'}: {text}")
            lines.append("open commitments:\n  - " + "\n  - ".join(cs))
        if snapshot.recent_facts:
            facts = []
            for f in snapshot.recent_facts[:8]:
                if isinstance(f, dict):
                    facts.append(str(f.get("text", "")))
                else:
                    facts.append(str(f))
            lines.append("recent facts:\n  - " + "\n  - ".join(facts))
    return "\n".join(lines)


def _build_request(
    *,
    response_text: str,
    snapshot: StateSnapshot | None,
    scene: Scene | None,
    config: ExtractorConfig,
) -> CompletionRequest:
    system = _make_system_prompt()
    user = render_template(
        "extractor_user",
        response_text=response_text.strip(),
        context=_compact_snapshot(snapshot, scene),
    )
    return CompletionRequest(
        model="",  # the gateway fills in the route's model
        messages=[Message(role=MessageRole.USER, content=user)],
        system=system,
        max_tokens=config.llm_max_output_tokens,
        temperature=config.llm_temperature,
    )


def _make_fact_delta(item: dict, *, campaign_id: CampaignId, source: str) -> StateDelta:
    about = item.get("about") or {}
    text = str(item.get("text", ""))
    # Hash the fact text so distinct proposed facts don't merge into one another.
    text_hash = hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]
    return StateDelta(
        kind=DeltaKind.FACT_ADD,
        target_scope=Scope.CAMPAIGN_SQLITE,
        target_id=f"fact:{text_hash}",
        target_table="facts",
        after={
            "text": text,
            "about": about,
            "speaker_id": item.get("speaker_id"),
            "campaign_id": campaign_id,
        },
        confidence=float(item.get("confidence", 0.0)),
        source=source,
        evidence=str(item.get("evidence", "")),
        extra={"strategy": "structured_llm", "category": "facts"},
    )


def _make_character_update_delta(item: dict, *, campaign_id: CampaignId, source: str) -> StateDelta:
    return StateDelta(
        kind=DeltaKind.CHARACTER_STATE_UPDATE,
        target_scope=Scope.CAMPAIGN_SQLITE,
        target_id=f"character_state:{item.get('character_id', 'unknown')}",
        target_table="character_state",
        after={
            "character_id": item.get("character_id"),
            "field": item.get("field"),
            "before": item.get("before"),
            "after": item.get("after"),
            "campaign_id": campaign_id,
        },
        confidence=float(item.get("confidence", 0.0)),
        source=source,
        evidence=str(item.get("evidence", "")),
        extra={"strategy": "structured_llm", "category": "character_updates"},
    )


def _make_scene_change_delta(item: dict, *, campaign_id: CampaignId, source: str) -> StateDelta:
    return StateDelta(
        kind=DeltaKind.SCENE_CHANGE,
        target_scope=Scope.CAMPAIGN_SQLITE,
        target_id=f"scene_change:{item.get('kind', 'unknown')}",
        target_table="scenes",
        after={
            "kind": item.get("kind"),
            "to_location": item.get("to_location"),
            "campaign_id": campaign_id,
        },
        confidence=float(item.get("confidence", 0.0)),
        source=source,
        evidence=str(item.get("evidence", "")),
        extra={"strategy": "structured_llm", "category": "scene_changes"},
    )


def _make_time_advance_delta(item: dict, *, campaign_id: CampaignId, source: str) -> StateDelta:
    iso = str(item.get("delta", "PT0S")) or "PT0S"
    duration = Duration(iso8601=iso)
    return StateDelta(
        kind=DeltaKind.TIME_ADVANCE,
        target_scope=Scope.CAMPAIGN_SQLITE,
        target_id=f"time:{iso}",
        target_table="calendar",
        after={
            "duration": duration.model_dump(mode="json"),
            "campaign_id": campaign_id,
        },
        confidence=float(item.get("confidence", 0.0)),
        source=source,
        evidence=str(item.get("evidence", "")),
        extra={"strategy": "structured_llm", "category": "time_advances"},
    )


def _make_commitment_delta(item: dict, *, campaign_id: CampaignId, source: str) -> StateDelta:
    return StateDelta(
        kind=DeltaKind.COMMITMENT_ADD,
        target_scope=Scope.CAMPAIGN_SQLITE,
        target_id="commitment:new",
        target_table="commitments",
        after={
            "kind": item.get("kind"),
            "text": item.get("text"),
            "from": item.get("from"),
            "to": item.get("to"),
            "due": item.get("due"),
            "campaign_id": campaign_id,
        },
        confidence=float(item.get("confidence", 0.0)),
        source=source,
        evidence=str(item.get("evidence", "")),
        extra={"strategy": "structured_llm", "category": "commitments"},
    )


def _make_inventory_delta(item: dict, *, campaign_id: CampaignId, source: str) -> StateDelta:
    actor = item.get("character_id", "unknown")
    return StateDelta(
        kind=DeltaKind.INVENTORY_CHANGE,
        target_scope=Scope.CAMPAIGN_SQLITE,
        target_id=f"{actor}:{item.get('item', '')}",
        target_table="character_state",
        after={
            "character_id": actor,
            "item": item.get("item"),
            "delta": item.get("delta"),
            "campaign_id": campaign_id,
        },
        confidence=float(item.get("confidence", 0.0)),
        source=source,
        evidence=str(item.get("evidence", "")),
        extra={"strategy": "structured_llm", "category": "inventory_changes"},
    )


def _make_mechanical_delta(item: dict, *, campaign_id: CampaignId, source: str) -> StateDelta:
    return StateDelta(
        kind=DeltaKind.MECHANICAL_EVENT,
        target_scope=Scope.CAMPAIGN_SQLITE,
        target_id=f"{item.get('kind', 'event')}:{item.get('character_id', 'unknown')}",
        target_table="deltas",
        after={
            "event_kind": item.get("kind"),
            "actor_ref": item.get("character_id"),
            "target_ref": item.get("target_id"),
            "amount": item.get("amount"),
            "campaign_id": campaign_id,
        },
        confidence=float(item.get("confidence", 0.0)),
        source=source,
        evidence=str(item.get("evidence", "")),
        extra={"strategy": "structured_llm", "category": "mechanical_events"},
    )


def _make_relationship_delta(item: dict, *, campaign_id: CampaignId, source: str) -> StateDelta:
    return StateDelta(
        kind=DeltaKind.RELATIONSHIP_UPDATE,
        target_scope=Scope.CAMPAIGN_SQLITE,
        target_id=f"rel:{item.get('from', '?')}->{item.get('to', '?')}",
        target_table="relationships",
        after={
            "from": item.get("from"),
            "to": item.get("to"),
            "field": item.get("field"),
            "delta": item.get("delta"),
            "campaign_id": campaign_id,
        },
        confidence=float(item.get("confidence", 0.0)),
        source=source,
        evidence=str(item.get("evidence", "")),
        extra={"strategy": "structured_llm", "category": "relationship_changes"},
    )


def _make_commitment_resolution_delta(
    item: dict, *, campaign_id: CampaignId, source: str
) -> StateDelta:
    return StateDelta(
        kind=DeltaKind.COMMITMENT_RESOLVE,
        target_scope=Scope.CAMPAIGN_SQLITE,
        target_id=str(item.get("commitment_id", "commitment:unknown")),
        target_table="commitments",
        after={
            "commitment_id": item.get("commitment_id"),
            "outcome": item.get("outcome"),
            "campaign_id": campaign_id,
        },
        confidence=float(item.get("confidence", 0.0)),
        source=source,
        evidence=str(item.get("evidence", "")),
        extra={"strategy": "structured_llm", "category": "commitment_resolutions"},
    )


def _slugify_id(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "unknown"


def _make_entity_candidate(item: dict, *, kind: EntityKind) -> EntityCandidate:
    name = str(item.get("proposed_name", "")).strip()
    proposed_id = str(item.get("proposed_id", "")).strip() or _slugify_id(name)
    confidence = float(item.get("confidence", 0.0))
    return EntityCandidate(
        kind=kind,
        proposed_id=proposed_id,
        proposed_name=name,
        role_hint=str(item.get("role", "")),
        evidence=str(item.get("evidence", "")),
        confidence=confidence,
        suggested_card={"name": name, "scope": "campaign-local"},
    )


# Per spec extractor-remaining §8 we let the LLM strategy classify candidate
# kinds by *which array* the item appears in, rather than running a fragile
# heuristic classifier over freeform names.
_CANDIDATE_KIND_BY_KEY: dict[str, EntityKind] = {
    "new_characters": EntityKind.CHARACTER,
    "new_locations": EntityKind.LOCATION,
    "new_factions": EntityKind.FACTION,
    "new_items": EntityKind.ITEM,
}


_BUILDER_MAP = {
    "facts": ("FACT_ADD", _make_fact_delta),
    "character_updates": ("CHARACTER_STATE_UPDATE", _make_character_update_delta),
    "scene_changes": ("SCENE_CHANGE", _make_scene_change_delta),
    "time_advances": ("TIME_ADVANCE", _make_time_advance_delta),
    "commitments": ("COMMITMENT_ADD", _make_commitment_delta),
    "inventory_changes": ("INVENTORY_CHANGE", _make_inventory_delta),
    "mechanical_events": ("MECHANICAL_EVENT", _make_mechanical_delta),
    "relationship_changes": ("RELATIONSHIP_UPDATE", _make_relationship_delta),
    "commitment_resolutions": ("COMMITMENT_RESOLVE", _make_commitment_resolution_delta),
}


def parse_llm_payload(
    payload: dict,
    *,
    campaign_id: CampaignId,
    source: str,
    max_new_entities: int,
) -> LLMStrategyOutput:
    """Convert a raw JSON payload into typed deltas + candidates."""
    out = LLMStrategyOutput()
    confidences: list[float] = []
    candidate_budget = max_new_entities
    template = empty_payload()
    for key, default in template.items():
        items = payload.get(key, default) or []
        if not isinstance(items, list):
            continue
        candidate_kind = _CANDIDATE_KIND_BY_KEY.get(key)
        if candidate_kind is not None:
            for item in items:
                if candidate_budget <= 0:
                    break
                if not isinstance(item, dict):
                    continue
                cand = _make_entity_candidate(item, kind=candidate_kind)
                out.candidates.append(cand)
                confidences.append(cand.confidence)
                candidate_budget -= 1
            continue
        builder_entry = _BUILDER_MAP.get(key)
        if builder_entry is None:
            continue
        _kind_name, builder = builder_entry
        for item in items:
            if not isinstance(item, dict):
                continue
            delta = builder(item, campaign_id=campaign_id, source=source)
            out.deltas.append(delta)
            confidences.append(delta.confidence)
    if confidences:
        out.confidence_avg = sum(confidences) / len(confidences)
    return out


_REPAIR_INSTRUCTION = (
    "Your previous response could not be parsed as JSON. "
    "Reply again with ONLY a JSON object matching the schema — no prose, "
    "no markdown fences, no commentary."
)


async def extract_with_llm(
    *,
    response_text: str,
    scene: Scene | None,
    snapshot: StateSnapshot | None,
    campaign_id: CampaignId,
    gateway: LLMGatewayLike,
    config: ExtractorConfig,
    source: str,
    turn_id: TurnId | None = None,
) -> LLMStrategyOutput:
    """Run the structured-LLM strategy against the gateway."""
    request = _build_request(
        response_text=response_text,
        snapshot=snapshot,
        scene=scene,
        config=config,
    )
    attempts_remaining = max(0, config.retry_on_parse_failure) + 1
    last_text = ""
    current_request = request
    while attempts_remaining > 0:
        attempts_remaining -= 1
        try:
            completion = await gateway.complete(
                config.task_name, current_request, campaign_id=campaign_id, turn_id=turn_id
            )
        except Exception as exc:  # flag-and-continue is the contract
            logger.warning("structured-llm extraction failed: %s", exc)
            return LLMStrategyOutput(
                flags=[
                    ExtractionFlag(
                        level=FlagLevel.WARNING,
                        code="llm_call_failed",
                        message=f"structured extraction failed: {type(exc).__name__}",
                        evidence=str(exc),
                    )
                ]
            )

        last_text = completion.text
        payload = _extract_json_payload(completion.text)
        if payload is not None:
            return parse_llm_payload(
                payload,
                campaign_id=campaign_id,
                source=source,
                max_new_entities=config.max_new_entities_per_turn,
            )
        if attempts_remaining > 0:
            current_request = _build_retry_request(current_request, completion.text)

    return LLMStrategyOutput(
        flags=[
            ExtractionFlag(
                level=FlagLevel.WARNING,
                code="llm_json_unparseable",
                message="structured extraction returned unparseable JSON",
                evidence=last_text[:500],
            )
        ]
    )


def _build_retry_request(previous: CompletionRequest, previous_text: str) -> CompletionRequest:
    """Append the prior bad reply + a repair instruction so the model can self-correct."""
    new_messages = list(previous.messages)
    new_messages.append(Message(role=MessageRole.ASSISTANT, content=previous_text))
    new_messages.append(Message(role=MessageRole.USER, content=_REPAIR_INSTRUCTION))
    return previous.model_copy(update={"messages": new_messages})


__all__ = [
    "LLMGatewayLike",
    "LLMStrategyOutput",
    "extract_with_llm",
    "parse_llm_payload",
]
