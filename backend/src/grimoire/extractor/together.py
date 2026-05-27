"""Together-mode tracker parser + delta projection.

The main model emits a JSON block alongside its prose, delimited by
``<!-- TRACKER -->`` / ``<!-- /TRACKER -->``. The frontend strips it
before render and POSTs the captured text back; this module parses it
and projects each section into typed `StateDelta`s. Unknown keys are
ignored (forward-compat); a malformed payload raises
`TrackerMalformedError` so the caller can fall back to `SEPARATE`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from grimoire.types.common import Scope
from grimoire.types.extraction import EntityCandidate
from grimoire.types.state import DeltaKind, StateDelta

DELIMITER_OPEN = "<!-- TRACKER -->"
DELIMITER_CLOSE = "<!-- /TRACKER -->"

_DEFAULT_CONFIDENCE = 0.9
_REQUIRED_KEYS: tuple[str, ...] = ("facts", "character_updates")


class TrackerMalformedError(Exception):
    """Raised when the tracker text fails to parse or is structurally invalid."""


@dataclass
class ParsedTracker:
    facts: list[dict[str, Any]] = field(default_factory=list)
    character_updates: list[dict[str, Any]] = field(default_factory=list)
    location_updates: list[dict[str, Any]] = field(default_factory=list)
    faction_updates: list[dict[str, Any]] = field(default_factory=list)
    commitments_added: list[dict[str, Any]] = field(default_factory=list)
    commitments_resolved: list[dict[str, Any]] = field(default_factory=list)
    new_entities: list[dict[str, Any]] = field(default_factory=list)
    advance_time: dict[str, Any] | None = None
    change_location: dict[str, Any] | None = None


def strip_tracker_block(text: str) -> str:
    """Remove the ``<!-- TRACKER -->…<!-- /TRACKER -->`` block from *text*."""
    start = text.find(DELIMITER_OPEN)
    if start < 0:
        return text
    end = text.find(DELIMITER_CLOSE, start + len(DELIMITER_OPEN))
    if end < 0:
        return text
    return (text[:start] + text[end + len(DELIMITER_CLOSE) :]).strip()


def extract_tracker_block(text: str) -> str | None:
    """Pull the tracker JSON out of a streamed response.

    Returns `None` when no complete delimiter pair is present. Used by
    the backend when the frontend hasn't already stripped the block (e.g.
    in tests, or non-streaming providers).
    """
    if not text:
        return None
    start = text.find(DELIMITER_OPEN)
    if start < 0:
        return None
    after_open = start + len(DELIMITER_OPEN)
    end = text.find(DELIMITER_CLOSE, after_open)
    if end < 0:
        return None
    return text[after_open:end].strip()


def parse_tracker_text(
    raw: str,
    *,
    required: tuple[str, ...] = _REQUIRED_KEYS,
) -> ParsedTracker:
    """Parse the tracker JSON text into a typed `ParsedTracker`.

    `required` lists the top-level keys that must be present (default
    ``facts`` and ``character_updates``); missing keys raise
    `TrackerMalformedError`. Unknown extra keys are dropped silently to
    keep the parser forward-compatible.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise TrackerMalformedError("empty tracker text")
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise TrackerMalformedError(f"json decode: {exc}") from exc
    if not isinstance(obj, dict):
        raise TrackerMalformedError("top-level is not an object")
    for key in required:
        if key not in obj:
            raise TrackerMalformedError(f"missing required key: {key}")
    return ParsedTracker(
        facts=_as_list_of_dicts(obj.get("facts")),
        character_updates=_as_list_of_dicts(obj.get("character_updates")),
        location_updates=_as_list_of_dicts(obj.get("location_updates")),
        faction_updates=_as_list_of_dicts(obj.get("faction_updates")),
        commitments_added=_as_list_of_dicts(obj.get("commitments_added")),
        commitments_resolved=_as_list_of_dicts(obj.get("commitments_resolved")),
        new_entities=_as_list_of_dicts(obj.get("new_entities")),
        advance_time=obj.get("advance_time") if isinstance(obj.get("advance_time"), dict) else None,
        change_location=(
            obj.get("change_location") if isinstance(obj.get("change_location"), dict) else None
        ),
    )


def project_tracker_to_deltas(
    parsed: ParsedTracker,
    *,
    campaign_id: str,
    source: str = "extractor:together",
) -> list[StateDelta]:
    """Map each tracker section into typed `StateDelta`s.

    Confidence defaults to ``0.9`` (tracker baseline) but per-delta
    overrides in the JSON take precedence. The Extractor's existing
    merge step then combines these with the cheap rule-based / heuristic
    sanity layer.
    """
    deltas: list[StateDelta] = []

    for fact in parsed.facts:
        text = str(fact.get("text") or "").strip()
        if not text:
            continue
        deltas.append(
            StateDelta(
                kind=DeltaKind.FACT_ADD,
                target_scope=Scope.CAMPAIGN_LOCAL,
                target_id=str(fact.get("id") or _fact_id(text)),
                after={
                    "text": text,
                    "about": fact.get("about", {}),
                    "tags": fact.get("tags", []),
                },
                confidence=_confidence(fact),
                source=source,
                evidence=str(fact.get("evidence") or ""),
            )
        )

    for upd in parsed.character_updates:
        character_id = str(upd.get("character_id") or "").strip()
        field_name = str(upd.get("field") or "").strip()
        if not character_id or not field_name:
            continue
        deltas.append(
            StateDelta(
                kind=DeltaKind.CHARACTER_STATE_UPDATE,
                target_scope=Scope.CAMPAIGN_LOCAL,
                target_id=character_id,
                after={
                    "character_id": character_id,
                    "field": field_name,
                    "after": upd.get("after"),
                    "before": upd.get("before"),
                },
                confidence=_confidence(upd),
                source=source,
                evidence=str(upd.get("evidence") or ""),
            )
        )

    for upd in parsed.location_updates:
        location_id = str(upd.get("location_id") or "").strip()
        if not location_id:
            continue
        deltas.append(
            StateDelta(
                kind=DeltaKind.LOCATION_STATE_UPDATE,
                target_scope=Scope.CAMPAIGN_LOCAL,
                target_id=location_id,
                after={
                    "location_id": location_id,
                    "field": upd.get("field"),
                    "after": upd.get("after"),
                    "before": upd.get("before"),
                },
                confidence=_confidence(upd),
                source=source,
                evidence=str(upd.get("evidence") or ""),
            )
        )

    for upd in parsed.faction_updates:
        faction_id = str(upd.get("faction_id") or "").strip()
        if not faction_id:
            continue
        deltas.append(
            StateDelta(
                kind=DeltaKind.FACTION_STATE_UPDATE,
                target_scope=Scope.CAMPAIGN_LOCAL,
                target_id=faction_id,
                after={
                    "faction_id": faction_id,
                    "field": upd.get("field"),
                    "after": upd.get("after"),
                    "before": upd.get("before"),
                },
                confidence=_confidence(upd),
                source=source,
                evidence=str(upd.get("evidence") or ""),
            )
        )

    for c in parsed.commitments_added:
        text = str(c.get("text") or "").strip()
        if not text:
            continue
        deltas.append(
            StateDelta(
                kind=DeltaKind.COMMITMENT_ADD,
                target_scope=Scope.CAMPAIGN_LOCAL,
                target_id=str(c.get("id") or _fact_id(text)),
                after={
                    "text": text,
                    "from": c.get("from"),
                    "to": c.get("to"),
                    "due_by": c.get("due_by"),
                },
                confidence=_confidence(c),
                source=source,
                evidence=str(c.get("evidence") or ""),
            )
        )

    for c in parsed.commitments_resolved:
        commitment_id = str(c.get("commitment_id") or c.get("id") or "").strip()
        if not commitment_id:
            continue
        deltas.append(
            StateDelta(
                kind=DeltaKind.COMMITMENT_RESOLVE,
                target_scope=Scope.CAMPAIGN_LOCAL,
                target_id=commitment_id,
                after={
                    "commitment_id": commitment_id,
                    "outcome": c.get("outcome"),
                },
                confidence=_confidence(c),
                source=source,
                evidence=str(c.get("evidence") or ""),
            )
        )

    if parsed.advance_time is not None:
        delta_str = parsed.advance_time.get("delta") or parsed.advance_time.get("duration")
        if isinstance(delta_str, str) and delta_str:
            deltas.append(
                StateDelta(
                    kind=DeltaKind.TIME_ADVANCE,
                    target_scope=Scope.CAMPAIGN_LOCAL,
                    target_id=campaign_id,
                    after={"delta": delta_str},
                    confidence=_confidence(parsed.advance_time),
                    source=source,
                    evidence=str(parsed.advance_time.get("evidence") or ""),
                )
            )

    if parsed.change_location is not None:
        to_loc = parsed.change_location.get("to_location") or parsed.change_location.get("to")
        if isinstance(to_loc, str) and to_loc:
            deltas.append(
                StateDelta(
                    kind=DeltaKind.SCENE_CHANGE,
                    target_scope=Scope.CAMPAIGN_LOCAL,
                    target_id=to_loc,
                    after={
                        "to_location": to_loc,
                    },
                    confidence=_confidence(parsed.change_location),
                    source=source,
                    evidence=str(parsed.change_location.get("evidence") or ""),
                )
            )

    return deltas


def project_tracker_to_candidates(parsed: ParsedTracker) -> list[EntityCandidate]:
    """Map ``new_entities`` entries to `EntityCandidate`s for review."""
    from grimoire.types.common import EntityKind

    out: list[EntityCandidate] = []
    for entry in parsed.new_entities:
        kind_raw = str(entry.get("kind") or "character").lower()
        try:
            kind = EntityKind(kind_raw)
        except ValueError:
            kind = EntityKind.CHARACTER
        proposed_id = str(entry.get("proposed_id") or entry.get("id") or "").strip()
        proposed_name = str(entry.get("proposed_name") or entry.get("name") or "").strip()
        if not proposed_id or not proposed_name:
            continue
        out.append(
            EntityCandidate(
                kind=kind,
                proposed_id=proposed_id,
                proposed_name=proposed_name,
                role_hint=str(entry.get("role_hint") or ""),
                evidence=str(entry.get("evidence") or ""),
                confidence=_confidence(entry),
                suggested_card=entry.get("suggested_card", {}) or {},
            )
        )
    return out


def _confidence(payload: dict[str, Any]) -> float:
    raw = payload.get("confidence", _DEFAULT_CONFIDENCE)
    try:
        c = float(raw)
    except (TypeError, ValueError):
        return _DEFAULT_CONFIDENCE
    if c < 0.0:
        return 0.0
    if c > 1.0:
        return 1.0
    return c


def _as_list_of_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _fact_id(text: str) -> str:
    import hashlib

    return "tracker:" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


__all__ = [
    "DELIMITER_CLOSE",
    "DELIMITER_OPEN",
    "ParsedTracker",
    "TrackerMalformedError",
    "extract_tracker_block",
    "parse_tracker_text",
    "project_tracker_to_candidates",
    "project_tracker_to_deltas",
    "strip_tracker_block",
]
